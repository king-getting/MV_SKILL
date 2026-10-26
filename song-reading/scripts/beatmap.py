"""Beat / bar / section / vocal-activity analysis -> song_map.json (the objective layer).

Pure numpy signal processing: spectral-flux onsets, FFT autocorrelation tempo,
Ellis-style dynamic-programming beat tracking, bar-phase search for downbeats,
checkerboard novelty for section boundaries, band-energy + tonality for vocal
activity. No librosa, no model, fully deterministic and re-runnable.

Usage:
  python beatmap.py <audio> --out work/song_map.json [--bpm 92] [--sections overrides.json]
"""
from __future__ import annotations

import argparse
import hashlib
import os

import numpy as np

import lib

BANDS = [
    ("low", 40.0, 160.0, 1.0),
    ("body", 160.0, 1200.0, 1.2),
    ("presence", 1200.0, 5000.0, 1.0),
    ("air", 5000.0, 11000.0, 0.6),
]
SECTION_TYPES = ("intro", "verse", "prechorus", "chorus", "bridge", "outro", "interlude")


# ------------------------------------------------------------------ basics --

def onset_envelope(spec: np.ndarray, sr: int = lib.SR) -> tuple[np.ndarray, dict]:
    """Weighted positive spectral flux over perceptual bands."""
    bands = {}
    flux = np.zeros(spec.shape[1], dtype=np.float64)
    for name, lo, hi, weight in BANDS:
        e = lib.to_db(lib.band_energy(spec, lo, hi, sr))
        e = e - e.mean()
        bands[name] = e
        d = np.diff(e, prepend=e[0])
        d = np.maximum(d, 0.0)
        s = d.std()
        if s > 1e-9:
            flux += weight * (d / s)
    flux = lib.smooth(flux.astype(np.float32), 0.03, sr)
    return flux, bands


def estimate_bpm(onset: np.ndarray, sr: int = lib.SR, hop: int = lib.HOP) -> tuple[float, float]:
    """Autocorrelation + harmonic comb. Returns (bpm, confidence 0..1)."""
    o = onset.astype(np.float64)
    o = o - o.mean()
    n = o.size
    nfft = 1 << int(np.ceil(np.log2(max(2 * n, 1024))))
    f = np.fft.rfft(o, nfft)
    ac = np.fft.irfft(f * np.conj(f), nfft)[:n]
    ac = ac / (ac[0] + 1e-12)

    bpms = np.arange(58.0, 196.0, 0.1)
    lags = 60.0 / bpms * sr / hop
    score = np.zeros(bpms.size)
    for k, w in ((1, 1.0), (2, 0.6), (3, 0.4), (4, 0.25)):
        li = np.rint(lags * k).astype(int)
        ok = li < n
        score[ok] += w * ac[li[ok]]
    prior = np.where((bpms >= 70.0) & (bpms <= 152.0), 1.0, 0.86)
    score *= prior

    i = int(np.argmax(score))
    bpm = float(bpms[i])
    best = score[i]

    def at(b: float) -> float:
        j = int(np.argmin(np.abs(bpms - b)))
        return float(score[j])

    if bpm < 76.0 and at(bpm * 2) > 0.82 * best:
        bpm *= 2.0
    elif bpm > 158.0 and at(bpm / 2) > 0.82 * best:
        bpm /= 2.0

    conf = float(np.clip(best / (np.median(score) + 1e-9) - 1.0, 0.0, 1.0))
    return round(bpm, 2), round(conf, 3)


def track_beats(onset: np.ndarray, bpm: float, sr: int = lib.SR, hop: int = lib.HOP) -> np.ndarray:
    """Dynamic-programming beat tracker (Ellis 2007), returns beat times in seconds."""
    period = 60.0 / bpm * sr / hop
    loc = lib.smooth(onset.astype(np.float32), 0.05, sr, hop).astype(np.float64)
    std = loc.std()
    if std > 1e-9:
        loc = loc / std
    n = loc.size
    lo = max(2, int(round(period * 0.5)))
    hi = max(lo + 2, int(round(period * 2.0)))
    tightness = 100.0

    cum = np.empty(n, dtype=np.float64)
    back = np.full(n, -1, dtype=np.int32)
    for i in range(n):
        j0 = max(0, i - hi)
        j1 = i - lo
        if j1 < j0:
            cum[i] = loc[i]
            continue
        js = np.arange(j0, j1 + 1)
        tx = -tightness * np.log(np.maximum(js, 1) / period / 1.0) ** 2
        cand = cum[js] + tx
        k = int(np.argmax(cand))
        cum[i] = loc[i] + cand[k]
        back[i] = int(js[k])

    tail_start = max(0, n - int(round(2 * hi)))
    t = tail_start + int(np.argmax(cum[tail_start:]))
    frames = []
    while t >= 0:
        frames.append(t)
        t = int(back[t])
    frames.reverse()

    times = np.array([f * hop / sr for f in frames], dtype=np.float64)
    return _clean_beats(times, period * hop / sr)


def _clean_beats(times: np.ndarray, period: float) -> np.ndarray:
    if times.size == 0:
        return times
    keep = [times[0]]
    for t in times[1:]:
        if t - keep[-1] >= period * 0.45:
            keep.append(t)
    out = [keep[0]]
    for t in keep[1:]:
        gap = t - out[-1]
        if gap > period * 1.75:
            missing = int(round(gap / period)) - 1
            for m in range(1, missing + 1):
                out.append(out[-1] + gap / (missing + 1))
        out.append(t)
    return np.array(out, dtype=np.float64)


def _phase_coherence(times: np.ndarray, weights: np.ndarray, period: float, bins: int = 256) -> tuple[float, float]:
    """How tightly do onset peaks fold onto a grid of `period`? -> (score, offset)."""
    frac = np.mod(times, period) / period
    hist = np.bincount((frac * bins).astype(int) % bins, weights=weights, minlength=bins)
    hist = (np.roll(hist, 1) + np.roll(hist, -1) + 2.0 * hist) / 4.0
    k = int(np.argmax(hist))
    return float(hist[k]), float((k + 0.5) / bins * period)


def fit_grid(onset: np.ndarray, bpm: float, duration: float, tracked: np.ndarray | None = None,
             sr: int = lib.SR, hop: int = lib.HOP) -> tuple[np.ndarray, float, float]:
    """Fit a constant-tempo beat grid straight from onset peaks.

    Beat trackers quantize their lag to whole frames (a 0.65s beat at a 256-hop
    is 56.2 frames), which accumulates into half a second of drift by the end of
    a song. Folding onset peaks instead recovers the period to ~0.01ms, which is
    what picture cutting needs. Returns (grid, period, deviation_to_tracked).
    """
    period0 = 60.0 / bpm
    loc = np.maximum(lib.smooth(onset.astype(np.float32), 0.05, sr, hop).astype(np.float64), 0.0)
    if loc.size < 32 or loc.max() <= 0.0:
        return (tracked if tracked is not None else np.array([])), period0, 99.0

    thr = float(np.percentile(loc, 75))
    inner = loc[1:-1]
    peaks = np.where((inner >= loc[:-2]) & (inner >= loc[2:]) & (inner > thr))[0] + 1
    times = peaks * hop / sr
    weights = loc[peaks]
    if times.size < 16:
        return (tracked if tracked is not None else np.array([])), period0, 99.0

    coarse = np.linspace(period0 * 0.97, period0 * 1.03, 241)
    scored = [(p, *_phase_coherence(times, weights, p)) for p in coarse]
    best_p = max(scored, key=lambda item: item[1])[0]
    fine = np.linspace(best_p * 0.999, best_p * 1.001, 121)
    scored = [(p, *_phase_coherence(times, weights, p)) for p in fine]
    best_p, _, offset = max(scored, key=lambda item: item[1])

    n0 = int(np.floor((0.0 - offset) / best_p)) - 1
    n1 = int(np.ceil((duration - offset) / best_p)) + 1
    grid = offset + np.arange(n0, n1 + 1) * best_p
    grid = grid[(grid >= 0.0) & (grid <= duration)]

    dev = 99.0
    if tracked is not None and tracked.size >= 8 and grid.size >= 8:
        dev = float(np.median(np.abs(tracked - grid[np.clip(
            np.searchsorted(grid, tracked), 0, grid.size - 1)])))
    return grid, float(best_p), dev


def downbeat_phase(beats: np.ndarray, onset: np.ndarray, sr: int, hop: int) -> int:
    loc = lib.smooth(onset.astype(np.float32), 0.05, sr, hop).astype(np.float64)
    loc = loc / (loc.std() + 1e-9)
    idx = np.clip(np.rint(beats * sr / hop).astype(int), 0, loc.size - 1)
    best_p, best_s = 0, -1e18
    for p in range(4):
        sel = idx[p::4]
        if sel.size == 0:
            continue
        others = np.concatenate([idx[q::4] for q in range(4) if q != p] or [np.array([], int)])
        s = loc[sel].mean() - (loc[others].mean() if others.size else 0.0)
        if s > best_s:
            best_s, best_p = s, p
    return best_p


# ---------------------------------------------------------------- sections --

def _chroma_matrix(spec: np.ndarray, sr: int = lib.SR, n_fft: int = lib.N_FFT) -> np.ndarray:
    freqs = np.fft.rfftfreq(n_fft, 1.0 / sr)
    mask = (freqs >= 55.0) & (freqs <= 2200.0)
    f = freqs[mask]
    pc = np.rint(12 * np.log2(f / 440.0)).astype(int) % 12
    m = np.zeros((12, freqs.size), dtype=np.float32)
    m[pc, np.where(mask)[0]] = 1.0
    return m @ spec


def _bar_features(spec, chroma, rms, vocal, bar_times, sr, hop, n_fft, onset=None):
    """Per-bar timbre/density descriptor (used for boundaries) and chroma (used for repeats).

    Boundaries deliberately ignore chroma: plenty of songs loop one 4-bar
    progression for a whole section, and harmony-based novelty then fires every
    4 bars. Instrumentation, density and energy track section structure far better.
    """
    nf = spec.shape[1]
    feats, chromas = [], []
    for i in range(len(bar_times) - 1):
        f0 = int(np.clip(bar_times[i] * sr / hop, 0, nf - 1))
        f1 = int(np.clip(bar_times[i + 1] * sr / hop, f0 + 1, nf))
        cr = chroma[:, f0:f1].mean(axis=1)
        n = np.linalg.norm(cr)
        cr = cr / n if n > 1e-9 else cr
        bands = [np.log1p(lib.band_energy(spec[:, f0:f1], lo, hi, sr, n_fft).mean()) for _, lo, hi, _ in BANDS]
        brightness = bands[3] - bands[1]
        sub = spec[:, f0:f1] + 1e-8
        centroid = float((np.arange(sub.shape[0]) * sub.sum(axis=1)).sum() / (sub.sum() + 1e-9))
        dens = float(onset[f0:f1].sum()) if onset is not None and onset.size > f1 else 0.0
        feats.append(np.array(bands + [brightness, rms[f0:f1].mean(), vocal[f0:f1].mean(),
                                       centroid / 100.0, dens / max(f1 - f0, 1)], dtype=np.float64))
        chromas.append(cr)
    return np.asarray(feats, dtype=np.float64), np.asarray(chromas, dtype=np.float64)


def _segment_cost(prefix, prefix_sq, i: int, j: int) -> float:
    n = j - i
    seg = prefix[j] - prefix[i]
    return float((prefix_sq[j] - prefix_sq[i]).sum() - (seg * seg / n).sum())


def _segment_dp(feats: np.ndarray, lam: float, min_bars: int) -> list[int]:
    z = (feats - feats.mean(axis=0)) / (feats.std(axis=0) + 1e-9)
    nb = z.shape[0]
    prefix = np.vstack([np.zeros((1, z.shape[1])), np.cumsum(z, axis=0)])
    prefix_sq = np.vstack([np.zeros((1, z.shape[1])), np.cumsum(z * z, axis=0)])

    dp = np.full(nb + 1, np.inf)
    prev = np.full(nb + 1, -1, dtype=int)
    dp[0] = 0.0
    for j in range(min_bars, nb + 1):
        best, best_i = np.inf, -1
        for i in range(0, j - min_bars + 1):
            if not np.isfinite(dp[i]):
                continue
            v = dp[i] + _segment_cost(prefix, prefix_sq, i, j) + lam
            if v < best:
                best, best_i = v, i
        dp[j], prev[j] = best, best_i

    cuts, j = [], nb
    while j > 0 and prev[j] > 0:
        j = int(prev[j])
        cuts.append(j)
    return sorted(cuts)


def segment_sections(feats: np.ndarray, target: int, min_bars: int = 4) -> list[int]:
    """Change-point segmentation with a complexity penalty, tuned to a target count.

    A threshold on novelty fires wherever the arrangement wiggles (a vocal that
    rests every other bar looks like a boundary). Penalized least squares looks
    at the whole song, so it only spends a boundary where the split actually
    explains the variance.
    """
    nb = feats.shape[0]
    target = max(1, min(target, nb // min_bars))
    lo, hi = 1e-6, 1e7
    seen: dict[int, list[int]] = {}
    for _ in range(28):
        mid = float(np.sqrt(lo * hi))
        cuts = _segment_dp(feats, mid, min_bars)
        seen[len(cuts)] = cuts
        if len(cuts) > target - 1:
            lo = mid
        elif len(cuts) < target - 1:
            hi = mid
        else:
            break
    if target - 1 in seen:
        return seen[target - 1]
    return sorted(seen.items(), key=lambda kv: (abs(kv[0] - (target - 1)), -kv[0]))[0][1]


def _label_sections(sections: list[dict]) -> None:
    rms = np.array([s["energy"] for s in sections])
    med = float(np.median(rms)) if rms.size else 0.0
    hi_thr = float(np.percentile(rms, 72)) if rms.size > 2 else med
    for s in sections:
        s["label"] = "verse"
        s["label_conf"] = 0.35

    for i, s in enumerate(sections):
        vocal = s["vocal"]
        if i == 0 and (s["energy"] < 0.62 * med or vocal < 0.12):
            s["label"], s["label_conf"] = "intro", 0.7
        elif i == len(sections) - 1 and (s["energy"] < 0.62 * med or vocal < 0.12):
            s["label"], s["label_conf"] = "outro", 0.7
        elif vocal < 0.08:
            s["label"], s["label_conf"] = "interlude", 0.55
        elif s["energy"] >= hi_thr and vocal >= 0.25:
            s["label"], s["label_conf"] = "chorus", 0.7

    # bridge: low-energy sung section sandwiched between two hotter sections
    for i in range(1, len(sections) - 1):
        s, a, b = sections[i], sections[i - 1], sections[i + 1]
        if (s["energy"] < 0.78 * med and 0.15 <= s["vocal"] < 0.6
                and a["energy"] > s["energy"] * 1.25 and b["energy"] > s["energy"] * 1.25):
            s["label"], s["label_conf"] = "bridge", 0.5

    _mark_repeats(sections)


def _mark_repeats(sections: list[dict], threshold: float = 0.80) -> None:
    """Same-label sections that restate an earlier one's harmony are variants."""
    for i, s in enumerate(sections):
        for j in range(i):
            other = sections[j]
            if other["label"] != s["label"]:
                continue
            sim = float(np.dot(s["_chroma"], other["_chroma"]))
            if sim > threshold:
                s["repeat_of"] = other["id"]
                s["repeat_similarity"] = round(sim, 3)
                break


def _exact_sections(items: list[dict], spec, chroma, rms, vocal, duration: float,
                    sr: int, bar_sec: float, n_frames: int) -> list[dict]:
    """Build sections from explicit times supplied by the caller (no bar snapping)."""
    max_rms = float(rms.max()) + 1e-9
    sections: list[dict] = []
    for i, item in enumerate(items):
        t0 = 0.0 if i == 0 else float(item["start"])
        t1 = float(item["end"]) if "end" in item else (
            float(items[i + 1]["start"]) if i + 1 < len(items) else duration)
        t1 = min(t1, duration)
        if t1 - t0 < 0.05:
            continue
        f0 = int(np.clip(t0 * sr / lib.HOP, 0, n_frames - 1))
        f1 = int(np.clip(t1 * sr / lib.HOP, f0 + 1, n_frames))
        cr = chroma[:, f0:f1].mean(axis=1)
        norm = np.linalg.norm(cr)
        sections.append({
            "id": f"S{len(sections) + 1}",
            "start": round(t0, 3),
            "end": round(t1, 3),
            "bars": int(round((t1 - t0) / bar_sec)) if bar_sec > 0 else 0,
            "energy": round(float(rms[f0:f1].mean() / max_rms), 3),
            "vocal": round(float(vocal[f0:f1].mean()), 3),
            "_chroma": cr / norm if norm > 1e-9 else cr,
            "label": item.get("label", "verse"),
            "label_conf": 1.0,
        })
    if sections:
        sections[0]["start"] = 0.0
        sections[-1]["end"] = round(duration, 3)
    _mark_repeats(sections)
    return sections


# -------------------------------------------------------------------- vocal --

def vocal_activity(spec: np.ndarray, sr: int = lib.SR, hop: int = lib.HOP) -> tuple[np.ndarray, list[list[float]]]:
    """Crude singing-voice activity: presence-band energy + spectral tonality."""
    band = lib.band_energy(spec, 200.0, 4000.0, sr)
    e = lib.to_db(band)
    e_s = lib.smooth(e.astype(np.float32), 0.10, sr, hop)

    freqs = np.fft.rfftfreq(spec.shape[0] * 2 - 2, 1.0 / sr)
    idx = np.where((freqs >= 200.0) & (freqs < 4000.0))[0]
    sub = spec[idx] + 1e-7
    flat = np.exp(np.log(sub).mean(axis=0)) / (sub.mean(axis=0) + 1e-9)
    flat_s = lib.smooth(flat.astype(np.float32), 0.10, sr, hop)

    lo_thr = float(np.median(e_s) + 0.40 * (np.percentile(e_s, 92) - np.median(e_s)))
    tonal = flat_s < float(np.percentile(flat_s, 55))
    act = (e_s > lo_thr) & tonal

    act = _morph(act, sr, hop, close_sec=0.18, open_sec=0.12, min_sec=0.35)
    return act.astype(np.uint8), _phrases(act, sr, hop)


def _morph(act: np.ndarray, sr: int, hop: int, close_sec: float, open_sec: float, min_sec: float) -> np.ndarray:
    a = act.astype(bool).copy()
    if not a.any():
        return a
    # close small gaps
    gap = max(1, int(close_sec * sr / hop))
    run = 0
    for i in range(a.size):
        if not a[i]:
            run += 1
        else:
            if 0 < run <= gap:
                a[i - run:i] = True
            run = 0
    # remove short blips
    min_len = max(1, int(min_sec * sr / hop))
    i = 0
    while i < a.size:
        if a[i]:
            j = i
            while j < a.size and a[j]:
                j += 1
            if j - i < min_len:
                a[i:j] = False
            i = j
        else:
            i += 1
    # erode tiny silences again
    gap = max(1, int(open_sec * sr / hop))
    run = 0
    for i in range(a.size):
        if not a[i]:
            run += 1
        else:
            if 0 < run <= gap:
                a[i - run:i] = True
            run = 0
    return a


def _phrases(act: np.ndarray, sr: int, hop: int) -> list[list[float]]:
    out: list[list[float]] = []
    i = 0
    n = act.size
    while i < n:
        if act[i]:
            j = i
            while j < n and act[j]:
                j += 1
            out.append([round(i * hop / sr, 3), round(j * hop / sr, 3)])
            i = j
        else:
            i += 1
    return out


# --------------------------------------------------------------------- main --

def analyze(path: str, sr: int = lib.SR, bpm_hint: float | None = None,
            section_override: list[dict] | None = None) -> dict:
    x, sr = lib.decode_mono(path, sr)
    duration = round(x.size / sr, 3)
    spec = lib.stft_mag(x)
    n_frames = spec.shape[1]
    rms = np.sqrt(np.maximum((spec ** 2).sum(axis=0), 0.0))
    rms = lib.smooth(rms.astype(np.float32), 0.25, sr)

    onset, _ = onset_envelope(spec, sr)
    bpm, bpm_conf = estimate_bpm(onset, sr)
    if bpm_hint:
        bpm, bpm_conf = float(bpm_hint), 1.0

    beats = track_beats(onset, bpm, sr)
    if beats.size < 4:
        lib.die(f"beat tracking failed (found {beats.size} beats); pass --bpm explicitly")

    grid, period, dev = fit_grid(onset, bpm, duration, beats, sr, lib.HOP)
    if grid.size < 8:
        lib.die("could not fit a beat grid; pass --bpm explicitly")

    # Trust the fitted grid unless it lands on far weaker onsets than the
    # tracker did — that is what a tempo change looks like.
    loc = lib.smooth(onset.astype(np.float32), 0.05, sr, lib.HOP).astype(np.float64)
    loc = np.maximum(loc, 0.0)
    gi = np.clip(np.rint(grid * sr / lib.HOP).astype(int), 0, loc.size - 1)
    bi = np.clip(np.rint(beats * sr / lib.HOP).astype(int), 0, loc.size - 1)
    score_grid = float(loc[gi].mean())
    score_tracked = float(loc[bi].mean()) + 1e-9
    if score_grid < 0.80 * score_tracked:
        grid, period = beats, float(np.median(np.diff(beats)))
        grid_mode = "tracked"
    else:
        grid_mode = "regular"
    bpm = round(60.0 / period, 2)

    phase = downbeat_phase(grid, onset, sr, lib.HOP)
    downbeats = grid[phase::4]
    bar_sec = float(np.median(np.diff(downbeats))) if downbeats.size > 1 else 4.0 * 60.0 / bpm
    beats = grid

    vocal, phrases = vocal_activity(spec, sr)
    chroma = _chroma_matrix(spec, sr)

    bar_times = [float(t) for t in downbeats if 0.0 <= t <= duration]
    if len(bar_times) < 4:
        bar_times = [float(t) for t in beats[::4] if t <= duration]
    if len(bar_times) < 2:
        lib.die("could not establish a bar grid; pass --bpm explicitly")

    feats, chromas = _bar_features(spec, chroma, rms, vocal, bar_times + [duration], sr,
                                   lib.HOP, lib.N_FFT, onset)
    nb = feats.shape[0]

    exact = section_override.get("sections") if isinstance(section_override, dict) else None

    if exact:
        sections = _exact_sections(exact, spec, chroma, rms, vocal, duration, sr, bar_sec, n_frames)
        if len(sections) < 2:
            lib.die("--sections produced fewer than 2 valid sections; check start/end values")
    else:
        if section_override:
            idx = [max(0, min(nb, int(np.argmin([abs(bar_times[j] - s["start"]) for j in range(len(bar_times))]))))
                   for s in section_override]
            bounds = sorted(set([0] + idx + [nb]))
        else:
            target = int(np.clip(round(duration / 30.0), 4, 12))
            cuts = segment_sections(feats, target, min_bars=4)
            bounds = [0] + cuts + [nb]

        max_rms = float(rms.max()) + 1e-9
        sections = []
        for i in range(len(bounds) - 1):
            b0, b1 = bounds[i], bounds[i + 1]
            if b1 <= b0:
                continue
            t0 = 0.0 if b0 == 0 else float(bar_times[b0])
            t1 = duration if b1 >= nb else float(bar_times[b1])
            f0 = int(np.clip(t0 * sr / lib.HOP, 0, n_frames - 1))
            f1 = int(np.clip(t1 * sr / lib.HOP, f0 + 1, n_frames))
            sections.append({
                "id": f"S{len(sections) + 1}",
                "start": round(t0, 3),
                "end": round(t1, 3),
                "bars": int(b1 - b0),
                "energy": round(float(rms[f0:f1].mean() / max_rms), 3),
                "vocal": round(float(vocal[f0:f1].mean()), 3),
                "_chroma": chromas[min(b0, nb - 1)],
            })

        if len(sections) > 1:
            sections[0]["start"] = 0.0
        sections[-1]["end"] = duration
        _label_sections(sections)
        if section_override:
            for s, o in zip(sections, section_override):
                s["label"] = o.get("label", s["label"])
                s["label_conf"] = 1.0

    for s in sections:
        s["chroma"] = [round(float(v), 3) for v in s.pop("_chroma")]

    target_hz = 20.0
    step = max(1, int(round(sr / lib.HOP / target_hz)))
    curve_hz = (sr / lib.HOP) / step
    curve = [round(float(v), 3) for v in vocal[::step]]

    e_step = max(1, int(round(sr / lib.HOP / 10.0)))
    e_hz = (sr / lib.HOP) / e_step
    peak = float(rms.max()) + 1e-9
    energy = [round(float(v) / peak, 3) for v in rms[::e_step]]

    warnings = []
    if grid_mode == "tracked":
        warnings.append("the fixed grid lands on much weaker onsets than tracked beats (%.2f vs %.2f): "
                        "the tempo probably changes; using tracked beats" % (score_grid, score_tracked))
    if bpm_conf < 0.15:
        warnings.append(f"tempo confidence low ({bpm_conf}); pass --bpm if the grid looks wrong")
    if not phrases:
        warnings.append("no vocal activity detected: lyric alignment will fall back to proportional mapping")
    if len(sections) < 3:
        warnings.append("only %d sections detected; a full song usually has 5+" % len(sections))
    if duration < 20:
        warnings.append("very short audio; section detection is unreliable under ~20s")

    return {
        "version": 1,
        "generator": "song-to-mv/beatmap.py",
        "audio": {
            "path": os.path.abspath(path),
            "duration": duration,
            "sample_rate": sr,
            "hash": hashlib.sha1(open(path, "rb").read(1 << 20)).hexdigest()[:16],
        },
        "grid": {
            "bpm": bpm,
            "bpm_confidence": bpm_conf,
            "beat_period": round(60.0 / bpm, 4),
            "bar_seconds": round(bar_sec, 4),
            "beats_per_bar": 4,
            "grid_mode": grid_mode,
            "grid_fit_deviation": round(dev, 4),
            "downbeat_phase": phase,
            "bars": len(bar_times) - 1,
        },
        "beats": [round(float(t), 3) for t in beats],
        "downbeats": [round(float(t), 3) for t in downbeats],
        "bar_times": [round(float(t), 3) for t in bar_times],
        "sections": sections,
        "vocal": {"curve_hz": round(curve_hz, 4), "curve": curve, "phrases": phrases},
        "energy": {"hz": round(e_hz, 4), "values": energy},
        "warnings": warnings,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("audio")
    ap.add_argument("--out", default="song_map.json")
    ap.add_argument("--bpm", type=float, default=None)
    ap.add_argument("--sr", type=int, default=lib.SR)
    ap.add_argument("--sections", default=None, help="JSON [{start,end,label}] to override boundaries")
    args = ap.parse_args()

    override = lib.read_json(args.sections) if args.sections else None
    data = analyze(args.audio, args.sr, args.bpm, override)
    lib.write_json(args.out, data)

    g = data["grid"]
    print(f"bpm {g['bpm']} (conf {g['bpm_confidence']})  bar {g['bar_seconds']}s  "
          f"{len(data['beats'])} beats  {len(data['sections'])} sections  "
          f"{len(data['vocal']['phrases'])} vocal phrases")
    for s in data["sections"]:
        print(f"  {s['id']:<3} {s['label']:<10} {lib.fmt_ts(s['start']):>8} - {lib.fmt_ts(s['end']):>8}  "
              f"{s['bars']:>2} bars  E{s['energy']:.2f} V{s['vocal']:.2f}"
              + (f"  = {s['repeat_of']}" if s.get("repeat_of") else ""))
    for w in data["warnings"]:
        print(f"  ! {w}")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as exc:
        lib.die(str(exc))

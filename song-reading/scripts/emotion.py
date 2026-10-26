"""Section-level acoustic features for *reading* a song (not for cutting picture).

`energy` is loudness; it is not emotion. A quiet bright section and a quiet dark
one score the same on RMS. This fills in the axes that actually carry mood:

  brightness  spectral centroid                     -> dark/cold ... bright/open
  density     onset peaks per second (refractory)   -> sparse ... crowded
  crest       peak-to-RMS                           -> compressed ... dynamic
  tail        energy left 0.1-0.6s after an attack  -> dry/close ... sustained/wet
  key         Krumhansl-style chroma correlation    -> major/minor tendency
  vocal       LRC coverage (contact) + detector share (prominence in the mix)

Arousal mixes energy, brightness and density. Valence mixes key mode, brightness
and crest. Both are heuristics and every score ships with a `why` list so a
reading can cite *why* it claims what it claims — the numbers are evidence, not
verdict.

Usage:
  python emotion.py <audio> work/song_map.json [--lyrics work/lyric_map.json] --out work/emotion.json
"""
from __future__ import annotations

import argparse

import numpy as np

import beatmap
import lib

NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])


def _chroma(spec: np.ndarray, sr: int = lib.SR, n_fft: int = lib.N_FFT) -> np.ndarray:
    freqs = np.fft.rfftfreq(n_fft, 1.0 / sr)
    mask = (freqs >= 55.0) & (freqs <= 2200.0)
    pc = np.rint(12 * np.log2(freqs[mask] / 440.0)).astype(int) % 12
    m = np.zeros((12, freqs.size), dtype=np.float32)
    m[pc, np.where(mask)[0]] = 1.0
    return m @ spec


def estimate_key(chroma: np.ndarray) -> tuple[str, str, float, list[dict]]:
    """Krumhansl-style correlation. Chroma index 0 is A (440 Hz reference), so the
    pitch-class name is (shift + 9) mod 12, not shift — getting that wrong
    transposes every reported key by a minor sixth."""
    vec = chroma / (np.linalg.norm(chroma) + 1e-9)
    scored = []
    for shift in range(12):
        rotated = np.roll(vec, -shift)
        for mode, tpl in (("major", MAJOR), ("minor", MINOR)):
            scored.append((float(np.corrcoef(rotated, tpl)[0, 1]), NAMES[(shift + 9) % 12], mode))
    scored.sort(reverse=True)
    r, tonic, mode = scored[0]
    conf = float(np.clip((r - 0.15) / 0.60, 0.0, 1.0))
    alts = [{"label": f"{t} {m}", "r": round(rr, 3)} for rr, t, m in scored[1:3]]
    return tonic, mode, round(conf, 3), alts


def _peaks(x: np.ndarray, sr: int, hop: int, pct: float = 88.0,
           min_gap: float = 0.07, limit: int = 400) -> np.ndarray:
    """Local maxima above a percentile, non-maximum suppressed by min_gap."""
    if x.size < 5:
        return np.array([], dtype=int)
    thr = float(np.percentile(x, pct))
    inner = x[1:-1]
    cand = np.where((inner > thr) & (inner >= x[:-2]) & (inner >= x[2:]))[0] + 1
    if cand.size == 0:
        return cand
    gap = max(1, int(round(min_gap * sr / hop)))
    order = cand[np.argsort(x[cand])[::-1]]
    kept: list[int] = []
    for i in order:
        if all(abs(int(i) - k) >= gap for k in kept):
            kept.append(int(i))
        if len(kept) >= limit:
            break
    return np.array(sorted(kept), dtype=int)


def _norm(values: np.ndarray) -> np.ndarray:
    lo, hi = float(values.min()), float(values.max())
    if hi - lo < 1e-9:
        return np.full_like(values, 0.5)
    # a tiny spread must not be amplified into a full 0..1 swing
    if (hi - lo) < 0.08 * max(abs(hi), 1e-6):
        return np.full_like(values, 0.5)
    return (values - lo) / (hi - lo)


def _vocal_coverage(section: dict, lines: list[dict]) -> float:
    span = max(section["end"] - section["start"], 1e-6)
    covered = 0.0
    for ln in lines:
        a = max(float(ln["start"]), section["start"])
        b = min(float(ln["end"]), section["end"])
        if b > a:
            covered += b - a
    return float(np.clip(covered / span, 0.0, 1.0))


def analyse(audio: str, song_map: dict, lyric_map: dict | None = None) -> dict:
    x, sr = lib.decode_mono(audio)
    spec = lib.stft_mag(x)
    freqs = np.fft.rfftfreq(lib.N_FFT, 1.0 / sr)
    onset, _ = beatmap.onset_envelope(spec, sr)
    chroma_frames = _chroma(spec, sr)
    n_frames = spec.shape[1]

    key_tonic, key_mode, key_conf, key_alts = estimate_key(chroma_frames.mean(axis=1))
    vocal = np.array(song_map["vocal"]["curve"], dtype=np.float64)
    vocal_hz = float(song_map["vocal"]["curve_hz"])
    all_lines = [ln for ln in (lyric_map or {}).get("lines", []) if ln.get("kind", "lyric") == "lyric"]

    raw = []
    for sec in song_map["sections"]:
        t0, t1 = float(sec["start"]), float(sec["end"])
        f0 = int(np.clip(t0 * sr / lib.HOP, 0, n_frames - 1))
        f1 = int(np.clip(t1 * sr / lib.HOP, f0 + 2, n_frames))
        seg = spec[:, f0:f1]
        total = seg.sum(axis=0) + 1e-9
        centroid = float(((freqs[:, None] * seg).sum(axis=0) / total).mean()) / (sr / 2.0)
        hf_ratio = float(seg[freqs >= 4000.0].sum() / (seg.sum() + 1e-9))

        wave = x[int(t0 * sr):int(t1 * sr)]
        crest = float(np.max(np.abs(wave)) / (np.sqrt(np.mean(wave ** 2)) + 1e-9)) if wave.size else 0.0

        o = onset[f0:f1]
        peaks = _peaks(o, sr, lib.HOP, pct=88.0, min_gap=0.07)
        density = peaks.size / max(t1 - t0, 1e-6)

        frame_db = lib.to_db(np.sqrt(np.maximum((seg ** 2).sum(axis=0), 0.0)) + 1e-9)
        frame_db = lib.smooth(frame_db.astype(np.float32), 0.03, sr).astype(np.float64)
        tails = []
        for i in peaks:
            a, b = int(i + 0.10 * sr / lib.HOP), int(i + 0.60 * sr / lib.HOP)
            if b < frame_db.size:
                tails.append(float(frame_db[a:b].mean() - frame_db[int(i)]))
        tail_db = float(np.median(tails)) if tails else -12.0

        v0, v1 = int(t0 * vocal_hz), int(t1 * vocal_hz)
        vocal_share = float(vocal[v0:v1].mean()) if v1 > v0 else 0.0

        raw.append({
            "id": sec["id"], "label": sec["label"], "start": round(t0, 3), "end": round(t1, 3),
            "energy": float(sec["energy"]), "brightness": centroid, "hf_ratio": hf_ratio,
            "crest": crest, "density": density, "tail_db": tail_db, "vocal_share": vocal_share,
            "vocal_coverage": _vocal_coverage(sec, all_lines) if all_lines else None,
        })

    if not raw:
        lib.die("song_map has no sections")

    energy_n = _norm(np.array([r["energy"] for r in raw]))
    bright_n = _norm(np.array([r["brightness"] for r in raw]))
    crest_n = _norm(np.array([r["crest"] for r in raw]))
    dens_n = _norm(np.array([r["density"] for r in raw]))

    mode_sign = 1.0 if key_mode == "major" else -1.0
    sections = []
    for i, r in enumerate(raw):
        arousal = 0.45 * energy_n[i] + 0.30 * bright_n[i] + 0.25 * dens_n[i]
        valence = (0.45 * mode_sign * (0.4 + 0.6 * key_conf)
                   + 0.35 * (2 * bright_n[i] - 1)
                   + 0.20 * (0.5 - crest_n[i]))
        valence = float(np.clip(valence, -1.0, 1.0))

        why = [
            f"能量 {r['energy']:.2f}",
            f"亮度 {bright_n[i]:.2f}" + ("（偏暗）" if bright_n[i] < 0.4 else "（偏亮）" if bright_n[i] > 0.6 else ""),
            f"起音密度 {r['density']:.2f}/s" + ("（疏）" if dens_n[i] < 0.35 else "（密）" if dens_n[i] > 0.66 else ""),
            f"尾音衰减 {r['tail_db']:+.1f}dB" + ("（拖得住）" if r["tail_db"] > -6 else "（收得快）" if r["tail_db"] < -14 else ""),
        ]
        if r["vocal_coverage"] is not None:
            why.append(f"人声覆盖 {r['vocal_coverage']:.0%}（按歌词行）")
        why.append(f"混音里人声占比 {r['vocal_share']:.0%}（检测值，厚混音会偏低）")

        sections.append({
            "id": r["id"], "label": r["label"], "start": r["start"], "end": r["end"],
            "features": {
                "energy": round(r["energy"], 3),
                "brightness": round(float(bright_n[i]), 3),
                "hf_ratio": round(r["hf_ratio"], 3),
                "crest": round(r["crest"], 2),
                "onset_density": round(r["density"], 2),
                "tail_db": round(r["tail_db"], 1),
                "vocal_share": round(r["vocal_share"], 3),
                "vocal_coverage": (round(r["vocal_coverage"], 3)
                                   if r["vocal_coverage"] is not None else None),
            },
            "arousal": round(float(arousal), 3),
            "valence": round(valence, 3),
            "why": why,
        })

    warnings = []
    if key_conf < 0.25:
        warnings.append("调性置信度低，不要把大小调当结论")
    if lyric_map is None:
        warnings.append("没给 lyric_map：人声覆盖不可信，只有检测占比")
    elif all(s["features"]["vocal_share"] < 0.25 for s in sections):
        warnings.append("检测到的人声占比普遍偏低（厚混音常见）：用 vocal_coverage 判断'有没有唱'，"
                        "用 vocal_share 判断'人声是否被推到前面'")

    return {
        "version": 1,
        "generator": "song-reading/emotion.py",
        "audio": song_map["audio"],
        "key": {"tonic": key_tonic, "mode": key_mode, "confidence": key_conf,
                "label": f"{key_tonic} {key_mode}", "alternatives": key_alts},
        "method": ("arousal = 0.45*energy + 0.30*brightness + 0.25*density; "
                   "valence = 0.45*mode + 0.35*brightness + 0.20*(1-crest) — heuristics, cite the features"),
        "sections": sections,
        "warnings": warnings,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("audio")
    ap.add_argument("song_map")
    ap.add_argument("--lyrics", default=None, help="lyric_map.json, for LRC-based vocal coverage")
    ap.add_argument("--out", default="emotion.json")
    args = ap.parse_args()

    sm = lib.read_json(args.song_map)
    lm = lib.read_json(args.lyrics) if args.lyrics else None
    data = analyse(args.audio, sm, lm)
    lib.write_json(args.out, data)

    print(f"key {data['key']['label']} ({data['key']['confidence']})  {len(data['sections'])} sections")
    for s in data["sections"]:
        bar = "█" * int(round(max(s["arousal"], 0) * 20))
        print(f"  {s['id']:<3} {s['label']:<9} arousal {s['arousal']:.2f} valence {s['valence']:+.2f}  {bar}")
    for w in data["warnings"]:
        print(f"  ! {w}")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as exc:
        lib.die(str(exc))

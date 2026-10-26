"""Generate a synthetic but musically-structured test song + matching lyrics.

Purpose: validate beatmap.py / align.py against known ground truth (92 BPM,
6 sections, line-level lyric timings) without shipping copyrighted audio.

Usage: python make_fixture.py <outdir>
Writes: fixture.wav, lyrics.txt, fixture_truth.json
"""
from __future__ import annotations

import json
import os
import sys
import wave

import numpy as np

SR = 44100
BPM = 92.0
BEAT = 60.0 / BPM
BAR = 4 * BEAT

# C  G  Am  F  (4-bar loop, one chord per bar) — deliberately loop-friendly so
# the section detector cannot cheat by looking at harmony alone.
CHORDS = {
    "C": [130.81, 164.81, 196.00],
    "G": [98.00, 123.47, 146.83],
    "Am": [110.00, 130.81, 164.81],
    "F": [87.31, 110.00, 130.81],
}
PROG = ["C", "G", "Am", "F"]


def env(n: int, attack: float, decay: float, sr: int = SR) -> np.ndarray:
    t = np.arange(n) / sr
    a = np.clip(t / max(attack, 1e-4), 0, 1)
    d = np.exp(-t * decay)
    return (a * d).astype(np.float32)


def tone(freq: float, dur: float, harmonics: int = 6, decay: float = 2.0,
         detune: float = 0.0, sr: int = SR) -> np.ndarray:
    n = int(dur * sr)
    t = np.arange(n) / sr
    out = np.zeros(n, dtype=np.float32)
    for h in range(1, harmonics + 1):
        f = freq * h * (1.0 + detune * (h - 1))
        out += (1.0 / h ** 1.3) * np.sin(2 * np.pi * f * t).astype(np.float32)
    return out * env(n, 0.02, decay, sr)


def kick(sr: int = SR) -> np.ndarray:
    dur = 0.28
    n = int(dur * sr)
    t = np.arange(n) / sr
    f = 48.0 + 60.0 * np.exp(-t * 32.0)
    phase = 2 * np.pi * np.cumsum(f) / sr
    return (np.sin(phase) * np.exp(-t * 11.0)).astype(np.float32)


def snare(sr: int = SR) -> np.ndarray:
    dur = 0.2
    n = int(dur * sr)
    t = np.arange(n) / sr
    noise = np.random.default_rng(7).normal(0, 1, n).astype(np.float32)
    body = np.sin(2 * np.pi * 185.0 * t).astype(np.float32)
    return (0.65 * noise + 0.35 * body) * np.exp(-t * 26.0).astype(np.float32)


def hat(sr: int = SR) -> np.ndarray:
    dur = 0.07
    n = int(dur * sr)
    t = np.arange(n) / sr
    noise = np.random.default_rng(11).normal(0, 1, n).astype(np.float32)
    hp = np.diff(noise, prepend=noise[0])  # crude high-pass
    return (hp * np.exp(-t * 70.0)).astype(np.float32)


def add(buf: np.ndarray, sig: np.ndarray, at_sec: float, gain: float = 1.0) -> None:
    i = int(round(at_sec * SR))
    j = min(buf.size, i + sig.size)
    if i >= buf.size:
        return
    buf[i:j] += gain * sig[: j - i]


# lyric lines: (text, section, bar_index_within_section, bars_used)
V1 = [
    "（Verse）",
    "第一句歌词写在雨夜的窗上",
    "第二句歌词跟着心跳慢慢晃",
    "第三句我把名字念得很轻",
    "第四句灯灭了房间还是很安静",
    "第五句风从门缝里挤进来",
    "第六句我数着没寄出的告白",
    "第七句时针走到凌晨三点",
    "第八句我把自己折进信封里面",
]
C1 = [
    "（Chorus）",
    "如果明天醒来你还在",
    "我就把整座城市点亮",
    "如果明天醒来你还在",
    "我就一直跑到天亮",
    "别问我怕不怕",
    "别问我敢不敢",
    "只要你还在我的视线",
    "我就敢把全世界掀翻",
]
V2 = [
    "（Verse）",
    "窗外的雨忽然停了",
    "霓虹在积水里开始燃烧",
    "我把那封信举过头顶",
    "让每一句都看得见心跳",
]
OUT = ["（Outro）", "明天醒来", "明天醒来"]


def melody_for(n_bars: int) -> list[tuple[float, float, float]]:
    """Return (start_bar, dur_bars, midi) events that sound like sung phrases."""
    rng = np.random.default_rng(3)
    notes = []
    bar = 0
    scale = [0, 2, 4, 7, 9]
    while bar < n_bars:
        line_len = 2.0
        n_notes = 6
        for k in range(n_notes):
            start = bar + k * (line_len * 0.78 / n_notes)
            dur = line_len * 0.78 / n_notes * 0.85
            deg = scale[int(rng.integers(0, len(scale)))]
            notes.append((start, dur, 62 + deg))
        bar += line_len
    return notes


def render(path: str) -> tuple[list[dict], float]:
    # bar counts per section
    plan = [("intro", 8), ("verse", 16), ("chorus", 16), ("verse", 8), ("chorus", 16), ("outro", 8)]
    total_bars = sum(b for _, b in plan)
    dur = total_bars * BAR + 1.2
    buf = np.zeros(int(dur * SR), dtype=np.float32)

    lines: list[dict] = []
    line_texts = []
    vocal_notes = []

    bar0 = 0
    for si, (kind, bars) in enumerate(plan):
        t0 = bar0 * BAR
        for b in range(bars):
            chord = PROG[(bar0 + b) % 4]
            notes = CHORDS[chord]
            gain = {"intro": 0.30, "verse": 0.15, "chorus": 0.34, "outro": 0.15}[kind]
            for i, f in enumerate(notes):
                sig = tone(f * (1.0 if i else 0.5), BAR * 1.02, harmonics=5, decay=1.1)
                add(buf, sig, t0 + b * BAR, gain / 3.0)
                if kind == "chorus":
                    add(buf, tone(f * 2.0, BAR * 1.02, harmonics=3, decay=1.5), t0 + b * BAR, gain / 9.0)
            # bass
            if kind != "outro":
                add(buf, tone(notes[0] / 2, BEAT * 1.8, harmonics=3, decay=3.0), t0 + b * BAR, 0.22)
                add(buf, tone(notes[0] / 2, BEAT * 1.8, harmonics=3, decay=3.0), t0 + b * BAR + 2 * BEAT, 0.20)

            if kind in ("verse", "chorus"):
                big = kind == "chorus"
                for beat in range(4):
                    bt = t0 + b * BAR + beat * BEAT
                    if big or beat in (0, 2):
                        add(buf, kick(), bt, 0.66 if big else 0.32)
                    if beat in (1, 3):
                        add(buf, snare(), bt, 0.36 if big else 0.14)
                    add(buf, hat(), bt + BEAT / 2, 0.11 if big else 0.05)
                    if big:
                        add(buf, hat(), bt, 0.06)
        bar0 += bars

    # vocal lines with explicit timing
    def place(texts: list[str], kind: str, start_bar: int, bars: int) -> None:
        starts = []
        for i in range(bars // 2):
            starts.append(start_bar + i * 2)
        for text, sb in zip(texts, starts):
            t = sb * BAR + 0.06
            d = 2 * BAR * 0.86
            lines.append({"text": text, "start": round(t, 3), "end": round(t + d, 3), "section": kind})
            line_texts.append(text)
            vocal_notes.append((t, d))

    # verse 1: 8 lines over 16 bars, chorus: 8 lines over 16 bars
    v1 = [l for l in V1[1:]]
    c1 = [l for l in C1[1:]]
    v2 = [l for l in V2[1:]]
    place(v1, "verse", 8, 16)
    place(c1, "chorus", 24, 16)
    place(v2, "verse", 40, 8)
    place(c1, "chorus", 48, 16)
    place(OUT[1:], "outro", 64, 8)

    # a sung-ish voice: harmonics with vibrato, gated per line (gaps between lines)
    rng = np.random.default_rng(5)
    for (t, d) in vocal_notes:
        n = int(d * SR)
        tt = np.arange(n) / SR
        f0 = 220.0 * 2 ** (rng.integers(-3, 3) / 12.0)
        vib = 1 + 0.004 * np.sin(2 * np.pi * 5.5 * tt)
        sig = np.zeros(n, dtype=np.float32)
        for h in range(1, 9):
            sig += (1.0 / h ** 1.15) * np.sin(2 * np.pi * f0 * h * vib * tt).astype(np.float32)
        sig *= np.minimum(1.0, tt / 0.05) * np.minimum(1.0, (d - tt) / 0.08)
        add(buf, sig, t, 0.16)

    peak = float(np.max(np.abs(buf))) or 1.0
    buf = (buf / peak * 0.85 * 32767).astype("<i2")

    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        wf.writeframes(buf.tobytes())

    return lines, total_bars * BAR, plan


def main() -> None:
    outdir = sys.argv[1] if len(sys.argv) > 1 else "."
    os.makedirs(outdir, exist_ok=True)
    wav = os.path.join(outdir, "fixture.wav")
    lines, musical_dur, plan = render(wav)

    txt = (
        "[Intro]\n"
        "\n"
        "[Verse 1]\n" + "\n".join(V1[1:]) + "\n\n"
        "[Chorus]\n" + "\n".join(C1[1:]) + "\n\n"
        "[Verse 2]\n" + "\n".join(V2[1:]) + "\n\n"
        "[Chorus]\n" + "\n".join(C1[1:]) + "\n\n"
        "[Outro]\n" + "\n".join(OUT[1:]) + "\n"
    )
    with open(os.path.join(outdir, "lyrics.txt"), "w", encoding="utf-8") as fh:
        fh.write(txt)

    sections, acc = [], 0
    for kind, bars in plan:
        sections.append({"type": kind, "start": round(acc * BAR, 3),
                         "end": round((acc + bars) * BAR, 3)})
        acc += bars

    truth = {"bpm": BPM, "bar_seconds": BAR, "musical_duration": round(musical_dur, 3),
             "sections": sections, "lines": lines}
    with open(os.path.join(outdir, "fixture_truth.json"), "w", encoding="utf-8") as fh:
        json.dump(truth, fh, ensure_ascii=False, indent=2)

    print(f"wrote {wav}  {musical_dur:.1f}s  {len(lines)} lines")


if __name__ == "__main__":
    main()

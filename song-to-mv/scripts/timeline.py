"""Render a QA timeline PNG: energy, vocal activity, sections, lyric lines, shots.

Looking at one picture beats reading three JSON files when you need to know
whether the cut grid actually sits on the chorus.

Usage:
  python timeline.py work/song_map.json --lyrics work/lyric_map.json \
      --shots work/shotlist.json --out work/timeline.png
"""
from __future__ import annotations

import argparse
import os

from PIL import Image, ImageDraw, ImageFont

import lib

BG = (18, 20, 26)
FG = (232, 236, 244)
MUTED = (120, 132, 152)
GRID = (42, 50, 64)
GRID_BAR = (70, 82, 104)
ENERGY = (56, 132, 255)
VOCAL = (245, 158, 11)
LYRIC = (167, 139, 250)
SHOT = (34, 197, 94)
SHOT_ALT = (16, 150, 90)

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\segoeui.ttf",
]


def load_font(size: int):
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    try:
        return ImageFont.load_default(size=size)  # Pillow >= 10.1
    except TypeError:
        return ImageFont.load_default()


def fmt_clock(t: float) -> str:
    m = int(t // 60)
    s = t - 60 * m
    return f"{m}:{s:04.1f}"


def render(song_map: dict, lyric_map: dict | None, shotlist: dict | None,
           width: int = 2400) -> Image.Image:
    duration = float(song_map["audio"]["duration"])
    pad = 40
    plot_w = width - 2 * pad

    lanes = [("energy", 118), ("vocal", 30), ("sections", 46), ("lyrics", 56), ("shots", 66)]
    gap = 14
    top = 86
    height = top + sum(h for _, h in lanes) + gap * len(lanes) + 28

    img = Image.new("RGB", (width, height), BG)
    dr = ImageDraw.Draw(img)
    f_title = load_font(21)
    f_small = load_font(13)
    f_tiny = load_font(11)

    def x_of(t: float) -> float:
        return pad + max(0.0, min(1.0, t / duration)) * plot_w

    # ---- header
    g = song_map["grid"]
    head = (f"BPM {g['bpm']:g}   {fmt_clock(duration)}   {len(song_map['sections'])} sections   "
            f"{len(shotlist['shots']) if shotlist else '?'} shots   beat {g['beat_period']:.3f}s")
    dr.text((pad, 30), head, font=f_title, fill=FG)
    dr.text((width - pad, 34), "song-to-mv / timeline.py", font=f_small, fill=MUTED, anchor="ra")

    y = top
    y_energy = y
    y_vocal = y_energy + lanes[0][1] + gap
    y_sec = y_vocal + lanes[1][1] + gap
    y_lyr = y_sec + lanes[2][1] + gap
    y_shot = y_lyr + lanes[3][1] + gap

    # ---- beat grid behind everything
    for t in song_map["beats"]:
        dr.line([(x_of(t), y_energy), (x_of(t), y_shot + lanes[4][1])], fill=GRID, width=1)
    for t in song_map["downbeats"]:
        dr.line([(x_of(t), y_energy), (x_of(t), y_shot + lanes[4][1])], fill=GRID_BAR, width=1)

    # ---- energy
    vals = song_map.get("energy", {}).get("values", [])
    if vals:
        hz = float(song_map["energy"]["hz"])
        pts = [(x_of(i / hz), y_energy + lanes[0][1] * (1.0 - v)) for i, v in enumerate(vals)]
        poly = [(x_of(0.0), y_energy + lanes[0][1])] + pts + [(x_of(duration), y_energy + lanes[0][1])]
        dr.polygon(poly, fill=(28, 48, 82))
        dr.line(pts, fill=ENERGY, width=2)
    dr.text((pad + 6, y_energy + 4), "energy", font=f_tiny, fill=MUTED)

    # ---- vocal activity
    voc = song_map.get("vocal", {})
    curve, hz = voc.get("curve", []), float(voc.get("curve_hz", 20.0))
    for i, v in enumerate(curve):
        if v >= 0.5:
            x0 = x_of(i / hz)
            x1 = x_of((i + 1) / hz)
            dr.rectangle([x0, y_vocal, max(x1, x0 + 1), y_vocal + lanes[1][1]], fill=VOCAL)
    dr.text((pad + 6, y_vocal + 6), "vocal", font=f_tiny, fill=(80, 62, 20))

    # ---- sections
    for i, sec in enumerate(song_map["sections"]):
        x0, x1 = x_of(sec["start"]), x_of(sec["end"])
        fill = (30, 39, 52) if i % 2 == 0 else (37, 47, 62)
        dr.rectangle([x0, y_sec, x1, y_sec + lanes[2][1]], fill=fill)
        label = f"{sec['id']} {sec['label']}"
        if sec.get("repeat_of"):
            label += f" ={sec['repeat_of']}"
        if x1 - x0 > 60:
            dr.text((x0 + 6, y_sec + 6), label, font=f_small, fill=FG)
            dr.text((x0 + 6, y_sec + 24), f"{sec['bars']}b E{sec['energy']:.2f} V{sec['vocal']:.2f}",
                    font=f_tiny, fill=MUTED)
        else:
            dr.text((x0 + 3, y_sec + 14), sec["id"], font=f_tiny, fill=FG)

    # ---- lyrics
    dr.rectangle([pad, y_lyr, width - pad, y_lyr + lanes[3][1]], fill=(24, 27, 34))
    if lyric_map:
        for ln in lyric_map.get("lines", []):
            x0, x1 = x_of(ln["start"]), x_of(ln["end"])
            dr.rectangle([x0 + 1, y_lyr + 8, max(x1 - 2, x0 + 1), y_lyr + 32], fill=(72, 56, 128))
            if x1 - x0 >= 30:
                dr.rectangle([x0 + 1, y_lyr + 8, max(x1 - 2, x0 + 1), y_lyr + 32], outline=LYRIC, width=1)
                dr.text((x0 + 3, y_lyr + 33), ln["id"], font=f_tiny, fill=LYRIC)
            if x1 - x0 >= 96:
                text = ln["text"]
                while text and dr.textlength(text, font=f_tiny) > (x1 - x0 - 8):
                    text = text[:-1]
                dr.text((x0 + 4, y_lyr + 15), text, font=f_tiny, fill=(226, 232, 240))
    dr.text((pad + 4, y_lyr - 12), "lyric lines", font=f_tiny, fill=MUTED)

    # ---- shots
    dr.rectangle([pad, y_shot, width - pad, y_shot + lanes[4][1]], fill=(24, 27, 34))
    if shotlist:
        sec_index = {s["id"]: i for i, s in enumerate(song_map["sections"])}
        for sh in shotlist.get("shots", []):
            x0, x1 = x_of(sh["start"]), x_of(sh["end"])
            fill = SHOT if sec_index.get(sh["section"], 0) % 2 == 0 else SHOT_ALT
            if sh.get("instrumental"):
                fill = (52, 84, 66)
            dr.rectangle([x0 + 1, y_shot + 10, max(x1 - 2, x0 + 2), y_shot + 48], fill=fill)
            if x1 - x0 >= 44:
                dr.text((x0 + 4, y_shot + 22), sh["id"], font=f_tiny, fill=(6, 24, 14))
            if x1 - x0 >= 92:
                dr.text((x0 + 4, y_shot + 48), f"{sh['duration']:.1f}s", font=f_tiny, fill=MUTED)
    dr.text((pad + 4, y_shot - 12), "shots (dark green = instrumental)", font=f_tiny, fill=MUTED)

    # ---- time ruler
    y_axis = y_shot + lanes[4][1] + 8
    step = 15.0 if duration <= 240 else 30.0
    t = 0.0
    while t <= duration + 0.01:
        x = x_of(t)
        dr.line([(x, y_axis), (x, y_axis + 5)], fill=MUTED, width=1)
        dr.text((x, y_axis + 6), fmt_clock(t), font=f_tiny, fill=MUTED, anchor="ma")
        t += step

    return img


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("song_map")
    ap.add_argument("--lyrics", default=None)
    ap.add_argument("--shots", default=None)
    ap.add_argument("--out", default="timeline.png")
    ap.add_argument("--width", type=int, default=2400)
    args = ap.parse_args()

    sm = lib.read_json(args.song_map)
    lm = lib.read_json(args.lyrics) if args.lyrics else None
    sl = lib.read_json(args.shots) if args.shots else None
    img = render(sm, lm, sl, args.width)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    img.save(args.out)
    print(f"wrote {args.out}  {img.width}x{img.height}")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as exc:
        lib.die(str(exc))

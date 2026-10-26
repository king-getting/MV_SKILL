"""Render a playable animatic from shotlist.json — no image model required.

Every shot becomes a framed placeholder: the colour arc of the film, a figure
sized to the shot's 景别, the camera move, the shot id, a running timecode and
the lyric line it carries. Transitions are honoured (cut / dissolve / white
flash / fade out), and the song is muxed in. It is not a music video; it is the
thing you watch to decide whether the *cut* works before paying for any frames.

Usage:
  python animatic.py work/shotlist.json --audio work/song.mp3 --bible work/mv_bible.json \
      --out work/animatic.mp4 [--size 1280x720] [--fps 24] [--duration 20]
"""
from __future__ import annotations

import argparse
import math
import os
import subprocess
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

import lib
from timeline import load_font

NIGHT = (30, 46, 66)
DAWN = (236, 208, 172)
WARM = (201, 123, 60)

SIZE_SCALE = {
    "大远景": 0.07, "远景": 0.16, "全景": 0.34, "中景": 0.5,
    "中近景": 0.66, "近景": 0.66, "特写": 0.95, "大特写": 1.45,
    "俯拍特写": 0.95, "大全景": 0.07, "空镜": 0.0, "大特写 ": 1.45,
}

DISSOLVE = 0.6
FLASH = 0.16
FADE_OUT = 1.6


def _lerp(a, b, t: float):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def _background(w: int, h: int, warmth: float, dim: float) -> Image.Image:
    top = _lerp(NIGHT, DAWN, warmth * 0.85)
    bottom = _lerp((18, 26, 38), DAWN, warmth * 0.55)
    top = tuple(int(c * dim) for c in top)
    bottom = tuple(int(c * dim) for c in bottom)
    grad = np.zeros((h, w, 3), dtype=np.uint8)
    ramp = np.linspace(0.0, 1.0, h)[:, None, None]
    for c in range(3):
        grad[:, :, c] = (top[c] * (1 - ramp[:, :, 0]) + bottom[c] * ramp[:, :, 0]).astype(np.uint8)
    img = Image.fromarray(grad, "RGB")

    # a light source that travels with warmth: the visual "sun" of the arc
    glow = Image.new("L", (w, h), 0)
    gd = ImageDraw.Draw(glow)
    gx, gy, gr = w * 0.68, h * (0.34 - 0.10 * warmth), w * 0.45
    gd.ellipse([gx - gr, gy - gr * 0.7, gx + gr, gy + gr * 0.7], fill=int(70 + 90 * warmth))
    glow = glow.filter(ImageFilter.GaussianBlur(w // 10))
    tint = Image.new("RGB", (w, h), _lerp((90, 110, 140), (255, 220, 170), warmth))
    img = Image.composite(tint, img, glow.point(lambda v: int(v * 0.45)))

    # gentle vignette — keep it readable, this is a previz not a mood piece
    mask = Image.new("L", (w, h), 0)
    md = ImageDraw.Draw(mask)
    md.ellipse([-w * 0.3, -h * 0.4, w * 1.3, h * 1.4], fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(w // 10))
    dark = Image.new("RGB", (w, h), (0, 0, 0))
    img = Image.blend(img, Image.composite(img, dark, mask), 0.55)
    return img


def _figure(draw: ImageDraw.ImageDraw, cx: float, base_y: float, scale: float, h: int, warm: float) -> None:
    if scale <= 0.01:
        return
    body_h = h * scale
    head_r = body_h * 0.105
    shoulder = body_h * 0.20
    col = _lerp((44, 52, 64), (58, 44, 34), warm)
    edge = _lerp((118, 138, 164), (255, 216, 165), warm)
    head_y = base_y - body_h + head_r
    neck = head_y + head_r * 0.85
    draw.ellipse([cx - head_r, head_y - head_r, cx + head_r, head_y + head_r], fill=col)
    draw.rounded_rectangle([cx - shoulder, neck, cx + shoulder, base_y],
                           radius=int(body_h * 0.09), fill=col)
    # rim light on the key side so the figure separates from the ground
    draw.arc([cx - head_r, head_y - head_r, cx + head_r, head_y + head_r],
             start=-80, end=70, fill=edge, width=max(1, int(body_h * 0.018)))
    draw.line([(cx + shoulder - 2, neck + body_h * 0.03), (cx + shoulder - 2, base_y - 2)],
              fill=edge, width=max(1, int(body_h * 0.018)))


def _guides(draw: ImageDraw.ImageDraw, w: int, h: int) -> None:
    ink = (255, 255, 255, 18)
    for fx in (1 / 3, 2 / 3):
        draw.line([(w * fx, 0), (w * fx, h)], fill=ink, width=1)
    for fy in (1 / 3, 2 / 3):
        draw.line([(0, h * fy), (w, h * fy)], fill=ink, width=1)


def _section_light(shots: list[dict]) -> dict[str, float]:
    """Per-section exposure 0..1, from the song's shape rather than clock time.

    Clock time says the solo at 2:00 is "late, therefore bright" — wrong: the
    solo is still night, and the light only breaks at the last choruses. So the
    level comes from the section type plus which occurrence of that type it is:
    the third chorus is the brightest thing in the song, the interlude goes back
    down after the first one.
    """
    order: dict[str, int] = {}
    total: dict[str, int] = {}
    for s in shots:
        total[s["section_label"]] = total.get(s["section_label"], 0) + 1

    level: dict[str, float] = {}
    for s in shots:
        label = s["section_label"]
        idx = order.get(label, 0)
        order[label] = idx + 1
        frac = idx / (total[label] - 1) if total[label] > 1 else 0.0
        level[s["section"]] = {
            "intro": 0.0, "verse": 0.05 + 0.06 * frac, "prechorus": 0.2,
            "chorus": 0.28 + 0.62 * frac, "bridge": 0.12,
            "interlude": 0.10, "outro": 0.85,
        }.get(label, 0.3)
    return level


def _base_frame(shot: dict, progress: float, w: int, h: int, warmth: float) -> Image.Image:
    dim = 0.55 + 0.45 * (1.0 - abs(progress - 0.5) * 0.6)
    img = _background(w, h, warmth, dim)
    draw = ImageDraw.Draw(img, "RGBA")
    _guides(draw, w, h)

    camera = shot.get("camera") or ""
    zoom = 1.0
    if "推" in camera:
        zoom = 1.0 + 0.10 * progress
    elif "拉" in camera:
        zoom = 1.10 - 0.10 * progress
    jitter = math.sin(progress * 37.0) * h * 0.002 if "手持" in camera else 0.0
    pan = (progress - 0.5) * w * 0.06 if ("摇" in camera or "移" in camera or "航拍" in camera) else 0.0

    scale = SIZE_SCALE.get(shot.get("size") or "", 0.5) * zoom
    cx = w * 0.5 + pan
    base_y = h * 0.78 + jitter
    _figure(draw, cx, base_y, scale, h, warmth)

    # horizon / frame edge so the empty shots still read as composed
    draw.line([(0, h * 0.78), (w, h * 0.78)], fill=(255, 255, 255, 26), width=1)
    draw.rectangle([0, 0, w - 1, h - 1], outline=(255, 255, 255, 30), width=1)
    return img


def _hud(img: Image.Image, shot: dict, t: float, total: float, f_title, f_meta) -> None:
    w, h = img.size
    draw = ImageDraw.Draw(img, "RGBA")
    bar = h - 6
    draw.rectangle([0, bar, w, h], fill=(0, 0, 0, 120))
    draw.rectangle([0, bar, int(w * (t / max(total, 1e-6))), h], fill=(255, 255, 255, 90))

    pad = int(w * 0.018)
    draw.text((pad, pad), f"{shot['id']}  {shot.get('title') or ''}", font=f_title, fill=(255, 255, 255, 230))
    draw.text((pad, pad + 26), f"{shot.get('size') or ''} · {shot.get('camera') or ''}", font=f_meta, fill=(255, 255, 255, 165))

    code = f"{int(t // 60):02d}:{t % 60:05.2f}"
    draw.text((w - pad, pad), code, font=f_title, fill=(255, 255, 255, 230), anchor="ra")
    draw.text((w - pad, pad + 26), f"{shot['duration']:.2f}s · {shot.get('section_label') or ''}",
              font=f_meta, fill=(255, 255, 255, 165), anchor="ra")

    lyric = shot.get("lyric")
    text = lyric if lyric else "（器乐）"
    draw.text((w / 2, h * 0.90), text, font=f_title, fill=(255, 255, 255, 235), anchor="mm")


def _find_shot(shots: list[dict], t: float) -> int:
    lo, hi = 0, len(shots) - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if t < shots[mid]["end"]:
            hi = mid
        else:
            lo = mid + 1
    return lo


def render(shotlist: dict, audio: str, out: str, size: tuple[int, int] = (1280, 720),
           fps: int = 24, limit: float | None = None) -> None:
    shots = shotlist.get("shots", [])
    if not shots:
        lib.die("shotlist has no shots")
    total = float(shotlist["audio"]["duration"])
    if limit:
        total = min(total, float(limit))
    w, h = size
    n_frames = int(round(total * fps))

    f_title = load_font(max(16, int(h * 0.030)))
    f_meta = load_font(max(12, int(h * 0.020)))

    ff = lib.find_binary("ffmpeg")
    cmd = [ff, "-v", "error", "-y",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", str(fps), "-i", "-",
           "-i", audio,
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", out]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    cache: dict[tuple[str, int], Image.Image] = {}
    prev_base: Image.Image | None = None
    prev_id: str | None = None
    light = _section_light(shots)
    warmth_of = lambda s: light.get(s["section"], 0.3)

    try:
        for i in range(n_frames):
            t = i / fps
            idx = _find_shot(shots, t)
            shot = shots[idx]
            span = max(shot["end"] - shot["start"], 1e-6)
            progress = min(1.0, max(0.0, (t - shot["start"]) / span))

            step = int(round(progress * 80))
            key = (shot["id"], step)
            base = cache.get(key)
            if base is None:
                base = _base_frame(shot, step / 80.0, w, h, warmth_of(shot))
                cache[key] = base
                if len(cache) > 4000:
                    cache.clear()

            img = base.copy()

            # dissolve in from the previous shot's last frame
            if (shot.get("transition_in") == "叠化" and prev_base is not None
                    and prev_id != shot["id"] and t - shot["start"] < DISSOLVE):
                a = 1.0 - (t - shot["start"]) / DISSOLVE
                img = Image.blend(prev_base, img, 1.0 - a)
            if (shot.get("transition_in") == "白闪" and t - shot["start"] < FLASH):
                a = 1.0 - (t - shot["start"]) / FLASH
                img = Image.blend(Image.new("RGB", (w, h), (255, 255, 255)), img, 1.0 - a)

            _hud(img, shot, t, total, f_title, f_meta)

            if shot.get("transition_out") == "淡出至黑" and shot["end"] - t < FADE_OUT:
                k = (shot["end"] - t) / FADE_OUT
                img = Image.blend(Image.new("RGB", (w, h), (0, 0, 0)), img, max(0.0, k))

            if proc.stdin is None:
                lib.die("ffmpeg stdin closed")
            proc.stdin.write(img.tobytes())
            prev_base, prev_id = base, shot["id"]

            if i % (fps * 10) == 0:
                print(f"  {t:6.1f}s / {total:.1f}s  ({i}/{n_frames})", flush=True)
    finally:
        if proc.stdin:
            proc.stdin.close()
        err = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
        code = proc.wait()
        if code != 0:
            lib.die(f"ffmpeg failed ({code}):\n{err[-3000:]}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("shotlist")
    ap.add_argument("--audio", required=True)
    ap.add_argument("--out", default="animatic.mp4")
    ap.add_argument("--size", default="1280x720")
    ap.add_argument("--fps", type=int, default=24)
    ap.add_argument("--duration", type=float, default=None, help="render only the first N seconds (smoke test)")
    args = ap.parse_args()

    w, h = (int(v) for v in args.size.lower().split("x"))
    sl = lib.read_json(args.shotlist)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    print(f"rendering {args.out}  {w}x{h}@{args.fps}  {'first ' + str(args.duration) + 's' if args.duration else 'full'}")
    render(sl, args.audio, args.out, (w, h), args.fps, args.duration)
    mb = os.path.getsize(args.out) / 1e6
    print(f"done: {args.out}  {mb:.1f} MB")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as exc:
        lib.die(str(exc))
    except BrokenPipeError:
        lib.die("ffmpeg closed the pipe (encoder error)")

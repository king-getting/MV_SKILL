"""Probe an audio file: container/stream facts + integrated loudness.

Usage: python probe.py <audio> [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

import lib


def probe(path: str) -> dict:
    if not os.path.isfile(path):
        lib.die(f"audio file not found: {path}")
    fp = lib.find_binary("ffprobe")
    raw = lib.run([
        fp, "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", path,
    ])
    meta = json.loads(raw)

    audio = next((s for s in meta.get("streams", []) if s.get("codec_type") == "audio"), None)
    if audio is None:
        lib.die(f"no audio stream in {path}")

    fmt = meta.get("format", {})
    duration = float(fmt.get("duration") or audio.get("duration") or 0.0)

    out = {
        "path": path,
        "duration": round(duration, 3),
        "codec": audio.get("codec_name"),
        "sample_rate": int(audio.get("sample_rate") or 0),
        "channels": int(audio.get("channels") or 0),
        "bit_rate": int(fmt.get("bit_rate") or 0),
        "container": fmt.get("format_name"),
        "tags": {k.lower(): v for k, v in (fmt.get("tags") or {}).items()},
    }

    # Integrated loudness (LUFS) + loudness range: cheap quality/level signal.
    try:
        ff = lib.find_binary("ffmpeg")
        err = lib.run_allow_error([ff, "-v", "info", "-i", path, "-af", "ebur128", "-f", "null", "-"])
        m = re.search(r"I:\s*(-?\d+\.\d+)\s*LUFS", err)
        if m:
            out["lufs_integrated"] = float(m.group(1))
        m = re.search(r"LRA:\s*(-?\d+\.\d+)\s*LU", err)
        if m:
            out["lra_lu"] = float(m.group(1))
    except Exception as exc:  # loudness is a nice-to-have, never fatal
        out["loudness_error"] = str(exc)

    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("audio")
    ap.add_argument("--json", help="write result here (default: stdout)")
    args = ap.parse_args()

    info = probe(args.audio)
    if args.json:
        lib.write_json(args.json, info)
        print(f"{info['duration']:.2f}s  {info['sample_rate']}Hz  {info['channels']}ch  {info['codec']}")
    else:
        print(json.dumps(info, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as exc:
        lib.die(str(exc))
    except FileNotFoundError as exc:
        lib.die(f"file not found: {exc.filename}")
    sys.exit(0)

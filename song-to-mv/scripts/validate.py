"""Cross-check song_map / lyric_map / shotlist and print a human verdict.

Usage: python validate.py work/song_map.json work/lyric_map.json work/shotlist.json
Exit code 1 when any hard error is found, so it can gate a workflow step.
"""
from __future__ import annotations

import argparse

import numpy as np

import lib

EPS_COVER = 0.05
EPS_EDGE = 0.03


def validate(song_map: dict, lyric_map: dict, shotlist: dict) -> list[dict]:
    issues: list[dict] = []

    def err(code: str, msg: str) -> None:
        issues.append({"level": "error", "code": code, "msg": msg})

    def warn(code: str, msg: str) -> None:
        issues.append({"level": "warn", "code": code, "msg": msg})

    duration = float(song_map["audio"]["duration"])
    sections = song_map["sections"]
    shots = shotlist.get("shots", [])
    lines = lyric_map.get("lines", [])
    beats = np.array(song_map["beats"], dtype=np.float64)

    if not shots:
        err("no-shots", "shotlist has no shots")
        return issues

    # ---- timing structure
    ids = [s["id"] for s in shots]
    if len(ids) != len(set(ids)):
        err("duplicate-ids", "duplicate shot ids")
    if abs(shots[0]["start"] - 0.0) > EPS_COVER:
        err("head-gap", f"first shot starts at {shots[0]['start']:.2f}s, not 0")
    if abs(shots[-1]["end"] - duration) > 0.15:
        err("tail-gap", f"last shot ends at {shots[-1]['end']:.2f}s, track is {duration:.2f}s")
    for a, b in zip(shots, shots[1:]):
        if b["start"] < a["start"] - 1e-6:
            err("order", f"{b['id']} starts before {a['id']}")
        gap = b["start"] - a["end"]
        if abs(gap) > EPS_EDGE:
            kind = "gap" if gap > 0 else "overlap"
            err("coverage", f"{kind} of {abs(gap):.2f}s between {a['id']} and {b['id']}")

    # ---- containment in one section
    sec_by_id = {s["id"]: s for s in sections}
    for sh in shots:
        sec = sec_by_id.get(sh["section"])
        if sec is None:
            err("section-ref", f"{sh['id']} references unknown section {sh['section']}")
            continue
        if sh["start"] < sec["start"] - 0.1 or sh["end"] > sec["end"] + 0.1:
            err("section-bounds", f"{sh['id']} ({sh['start']:.2f}-{sh['end']:.2f}) leaves {sec['id']} "
                                   f"({sec['start']:.2f}-{sec['end']:.2f})")

    # ---- duration sanity
    pol = shotlist.get("policy", {})
    lo = float(pol.get("min_shot_seconds", 1.0))
    hi = float(pol.get("max_shot_seconds", 10.0))
    short = [s["id"] for s in shots if s["duration"] < lo - 1e-6]
    long = [s["id"] for s in shots if s["duration"] > hi + 1e-6]
    if short:
        warn("short-shots", f"{len(short)} shots under {lo}s: {', '.join(short[:6])}")
    if long:
        err("long-shots", f"{len(long)} shots over {hi}s: {', '.join(long[:6])}")

    # ---- beat alignment (only meaningful when cuts are bar-anchored)
    if beats.size and shotlist.get("policy", {}).get("anchor", "bar") == "bar":
        off = []
        for sh in shots[1:]:
            d = float(np.min(np.abs(beats - sh["start"])))
            if d > 0.035:
                off.append((sh["id"], d))
        if off:
            warn("off-beat", f"{len(off)} shots do not start on a beat "
                             f"(worst {max(o[1] for o in off):.3f}s)")

    # ---- lyrics
    sung = [ln for ln in lines if ln.get("kind", "lyric") == "lyric"]
    for a, b in zip(sung, sung[1:]):
        if b["end"] < a["start"] - 1e-9:
            err("lyric-order", f"{a['id']} / {b['id']} are out of order")
        if b["start"] < a["end"] - EPS_EDGE:
            warn("lyric-overlap", f"{a['id']} and {b['id']} overlap by {a['end'] - b['start']:.2f}s")
    covered = {lid for sh in shots for lid in sh.get("lines", [])}
    missing = [ln["id"] for ln in sung if ln["id"] not in covered]
    if missing:
        err("unbound-lyrics", f"{len(missing)} lyric lines are in no shot: {', '.join(missing[:8])}")

    total = sum(float(s["duration"]) for s in shots)
    if total < 0.98 * duration:
        warn("coverage", f"shots cover {total:.1f}s of {duration:.1f}s")

    return issues


def validate_creative(shotlist: dict, bible: dict) -> list[dict]:
    """Checks that only make sense once the creative slots are filled."""
    issues: list[dict] = []

    def err(code: str, msg: str) -> None:
        issues.append({"level": "error", "code": code, "msg": msg})

    def warn(code: str, msg: str) -> None:
        issues.append({"level": "warn", "code": code, "msg": msg})

    style = bible.get("style_block", "")
    chars = {c["id"] for c in bible.get("characters", [])}
    locs = {l["id"] for l in bible.get("locations", [])}
    shots = shotlist.get("shots", [])

    empty = [s["id"] for s in shots if not s.get("prompt")]
    if empty:
        err("empty-prompts", f"{len(empty)} shots have no prompt: {', '.join(empty[:8])}")

    bad_style = [s["id"] for s in shots if s.get("prompt") and not s["prompt"].startswith(style)]
    if bad_style:
        err("style-drift", f"{len(bad_style)} prompts do not start with the bible style_block: "
                           f"{', '.join(bad_style[:8])}")

    no_neg = [s["id"] for s in shots if not s.get("negative")]
    if no_neg:
        warn("no-negative", f"{len(no_neg)} shots have no negative prompt")

    unknown_subject = sorted({s["subject"] for s in shots
                              if s.get("subject") and s["subject"] not in chars | locs})
    if unknown_subject:
        err("unknown-subject", f"subjects not defined in the bible: {', '.join(unknown_subject)}")

    unknown_refs = sorted({r for s in shots for r in (s.get("refs") or []) if r not in chars})
    if unknown_refs:
        warn("unknown-refs", f"refs that are not bible characters: {', '.join(unknown_refs)}")

    too_long = [s["id"] for s in shots if s.get("prompt") and len(s["prompt"]) > 240]
    if too_long:
        warn("long-prompt", f"{len(too_long)} prompts over 240 chars: {', '.join(too_long[:6])}")

    return issues


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("song_map")
    ap.add_argument("lyric_map")
    ap.add_argument("shotlist")
    ap.add_argument("--bible", default=None, help="also check the creative slots against mv_bible.json")
    args = ap.parse_args()

    sm = lib.read_json(args.song_map)
    lm = lib.read_json(args.lyric_map)
    sl = lib.read_json(args.shotlist)
    issues = validate(sm, lm, sl)
    if args.bible:
        issues += validate_creative(sl, lib.read_json(args.bible))

    errors = [i for i in issues if i["level"] == "error"]
    warns = [i for i in issues if i["level"] == "warn"]
    print(f"{sl['stats']['shots']} shots / {lm['stats']['lines']} lines / "
          f"{sm['audio']['duration']:.1f}s  ->  {len(errors)} errors, {len(warns)} warnings")
    for i in issues:
        mark = "ERR " if i["level"] == "error" else "warn"
        print(f"  {mark} [{i['code']}] {i['msg']}")
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

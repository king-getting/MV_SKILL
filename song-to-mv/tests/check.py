"""Regression check for the fixture: compare song_map/lyrics against known truth.

Usage: python check.py <song_map.json> [--truth work/fixture/fixture_truth.json]
                          [--lines work/fixture/lines.json]
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))


def load(p: str):
    with open(p, encoding="utf-8-sig") as fh:
        return json.load(fh)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("song_map")
    ap.add_argument("--truth", default=os.path.join("work", "fixture", "fixture_truth.json"))
    ap.add_argument("--lines", default=None)
    args = ap.parse_args()

    sm = load(args.song_map)
    truth = load(args.truth)

    print(f"bpm      truth {truth['bpm']:.2f}   got {sm['grid']['bpm']:.2f}   "
          f"err {abs(sm['grid']['bpm'] - truth['bpm']):.2f}")

    bounds = [s["start"] for s in sm["sections"]] + [sm["sections"][-1]["end"]]
    print("\nsection boundaries (truth -> nearest detected):")
    worst = 0.0
    for sec in truth["sections"] + [{"start": truth["musical_duration"], "type": "END"}]:
        t = sec["start"]
        near = min(bounds, key=lambda b: abs(b - t))
        err = near - t
        worst = max(worst, abs(err))
        flag = "ok " if abs(err) <= 2.0 else "BAD"
        print(f"  {flag} {t:7.2f}  {sec['type']:<9} -> {near:7.2f}  ({err:+.2f}s)")

    print(f"\nmax boundary error: {worst:.2f}s   sections: {len(sm['sections'])} "
          f"(truth {len(truth['sections'])})")

    print("\nlabels at truth-section midpoints:")
    bad = 0
    for sec in truth["sections"]:
        mid = (sec["start"] + sec["end"]) / 2
        hit = next((s for s in sm["sections"] if s["start"] <= mid < s["end"]), None)
        got = hit["label"] if hit else "-"
        ok = got == sec["type"]
        bad += 0 if ok else 1
        print(f"  {'ok ' if ok else 'BAD'} {sec['type']:<9} got {got:<9} mid {mid:7.2f}")
    print(f"label mismatches: {bad}")

    if args.lines:
        lines = load(args.lines)
        truth_lines = truth["lines"]
        errs = []
        for got, exp in zip(lines.get("lines", []), truth_lines):
            errs.append(abs(got["start"] - exp["start"]))
        if errs:
            errs.sort()
            print(f"\nline alignment: n={len(errs)} median {errs[len(errs)//2]:.2f}s  "
                  f"p90 {errs[int(len(errs)*0.9)]:.2f}s  max {errs[-1]:.2f}s")


if __name__ == "__main__":
    main()

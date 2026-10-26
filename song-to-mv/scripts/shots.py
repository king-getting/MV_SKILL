"""Turn the song map into a shot grid -> shotlist.json.

Two anchoring modes, because two kinds of song:

* ``anchor: "bar"`` (default) — cut on bar lines. Right for anything with a
  steady pulse: pop, rock, electronic.
* ``anchor: "line"`` — cut where a lyric phrase starts. Right for rubato
  ballads where a constant tempo grid is a fiction; the phrase is the pulse.

Instrumental stretches (intro, solo, tail) are chunked by ``max_shot_seconds``
either way. The creative fields stay null for the model to fill; timing never
comes from the model.

Usage:
  python shots.py work/song_map.json work/lyric_map.json --out work/shotlist.json
  python shots.py ... --policy references/policy.ballad.json
"""
from __future__ import annotations

import argparse

import numpy as np

import lib

DEFAULT_POLICY = {
    "anchor": "bar",
    "bars_per_shot": {
        "intro": 4, "verse": 2, "prechorus": 1, "chorus": 2,
        "bridge": 4, "interlude": 4, "outro": 4,
    },
    "lines_per_shot": {
        "intro": 0, "verse": 2, "prechorus": 1, "chorus": 1,
        "bridge": 1, "interlude": 0, "outro": 1,
    },
    "chorus_final_factor": 0.5,
    "max_shot_seconds": 8.0,
    "min_shot_seconds": 1.2,
    "snap_tolerance": 0.35,
    "lead_in_seconds": 0.15,
}

CREATIVE_SLOTS = ["title", "size", "camera", "subject", "action", "prompt", "negative",
                  "refs", "transition_in", "transition_out", "seed", "status"]


def _nearest(values: np.ndarray, t: float, tol: float) -> float | None:
    if values.size == 0:
        return None
    i = int(np.argmin(np.abs(values - t)))
    return float(values[i]) if abs(values[i] - t) <= tol else None


def _split_long(edges: list[float], max_sec: float) -> list[float]:
    out = [edges[0]]
    for a, b in zip(edges, edges[1:]):
        n = max(1, int(np.ceil((b - a) / max_sec - 1e-9)))
        for k in range(1, n):
            out.append(a + (b - a) * k / n)
        out.append(b)
    return out


def _merge_short(edges: list[float], min_sec: float) -> list[float]:
    out = list(edges)
    changed = True
    while changed and len(out) > 2:
        changed = False
        for i in range(1, len(out) - 1):
            left, right = out[i] - out[i - 1], out[i + 1] - out[i]
            if min(left, right) < min_sec:
                out.pop(i)
                changed = True
                break
    return out


def _bar_edges(section: dict, song_map: dict, policy: dict, factor: float) -> list[float]:
    start, end = float(section["start"]), float(section["end"])
    span = end - start
    bar_sec = float(song_map["grid"]["bar_seconds"])
    bar_times = np.array(song_map["bar_times"], dtype=np.float64)
    beats = np.array(song_map["beats"], dtype=np.float64)

    k = float(policy["bars_per_shot"].get(section["label"], 2)) * factor
    n = max(1, int(round((span / bar_sec) / max(k, 0.25))))
    while span / n > policy["max_shot_seconds"]:
        n += 1
    while n > 1 and span / n < policy["min_shot_seconds"]:
        n -= 1

    edges = [start]
    tol = policy["snap_tolerance"]
    for i in range(1, n):
        target = start + span * i / n
        snapped = _nearest(bar_times, target, tol) or _nearest(beats, target, tol)
        edges.append(float(snapped if snapped is not None else target))
    edges.append(end)
    return edges


def _line_edges(section: dict, section_lines: list[dict], policy: dict) -> list[float]:
    start, end = float(section["start"]), float(section["end"])
    lead = float(policy["lead_in_seconds"])
    n_per = max(1, int(policy["lines_per_shot"].get(section["label"], 1) or 1))

    groups = [section_lines[i:i + n_per] for i in range(0, len(section_lines), n_per)]
    edges = [start]
    for grp in groups[1:]:
        edges.append(max(edges[-1] + policy["min_shot_seconds"], grp[0]["start"] - lead))
    edges.append(end)
    return edges


def build_shotlist(song_map: dict, lyric_map: dict, policy: dict | None = None) -> dict:
    pol = {**DEFAULT_POLICY, **(policy or {})}
    sections = song_map["sections"]
    lines = [ln for ln in lyric_map.get("lines", []) if ln.get("kind", "lyric") == "lyric"]
    bar_sec = float(song_map["grid"]["bar_seconds"])
    anchor = pol.get("anchor", "bar")

    chorus_ids = [s["id"] for s in sections if s["label"] == "chorus"]
    final_chorus = chorus_ids[-1] if chorus_ids else None

    shots: list[dict] = []
    for sec in sections:
        start, end = float(sec["start"]), float(sec["end"])
        if end - start <= 0.05:
            continue

        section_lines = [ln for ln in lines if start - 0.01 <= ln["start"] < end - 0.01]
        if anchor == "line" and section_lines:
            edges = _line_edges(sec, section_lines, pol)
        elif anchor == "line":
            # instrumental stretch: even chunks, no bar grid to lean on
            n = max(1, int(np.ceil((end - start) / pol["max_shot_seconds"] - 1e-9)))
            edges = [start + (end - start) * i / n for i in range(n + 1)]
        else:
            factor = pol["chorus_final_factor"] if sec["id"] == final_chorus else 1.0
            edges = _bar_edges(sec, song_map, pol, factor)

        edges = _merge_short(_split_long(edges, pol["max_shot_seconds"]), pol["min_shot_seconds"])

        for i in range(len(edges) - 1):
            s0, s1 = edges[i], edges[i + 1]
            # a line belongs to the shot it *starts* in: deterministic, and it
            # cannot leak across a boundary the way overlap tests do
            bound = [ln for ln in lines if s0 - 0.01 <= ln["start"] < s1 - 0.01]
            primary = bound[0] if bound else None
            shot = {
                "id": f"{sec['id']}-{i + 1:02d}",
                "section": sec["id"],
                "section_label": sec["label"],
                "start": round(float(s0), 3),
                "end": round(float(s1), 3),
                "duration": round(float(s1 - s0), 3),
                "bars": round(float(s1 - s0) / bar_sec, 2),
                "lines": [ln["id"] for ln in bound],
                "lyric": primary["text"] if primary else None,
                "instrumental": primary is None,
            }
            for slot in CREATIVE_SLOTS:
                shot[slot] = None
            shot["status"] = "todo"
            shots.append(shot)

    total = sum(s["duration"] for s in shots)
    durations = [s["duration"] for s in shots]
    return {
        "version": 1,
        "generator": "song-to-mv/shots.py",
        "audio": song_map["audio"],
        "policy": pol,
        "stats": {
            "anchor": anchor,
            "shots": len(shots),
            "covered_seconds": round(total, 2),
            "coverage": round(total / max(song_map["audio"]["duration"], 1e-6), 3),
            "median_shot_seconds": round(float(np.median(durations)), 3) if durations else 0.0,
            "instrumental_shots": sum(1 for s in shots if s["instrumental"]),
        },
        "shots": shots,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("song_map")
    ap.add_argument("lyric_map")
    ap.add_argument("--out", default="shotlist.json")
    ap.add_argument("--policy", default=None)
    args = ap.parse_args()

    sm = lib.read_json(args.song_map)
    lm = lib.read_json(args.lyric_map)
    pol = lib.read_json(args.policy) if args.policy else None
    data = build_shotlist(sm, lm, pol)
    lib.write_json(args.out, data)

    st = data["stats"]
    print(f"{st['shots']} shots ({st['anchor']})  median {st['median_shot_seconds']}s  "
          f"instrumental {st['instrumental_shots']}  coverage {st['coverage']:.0%}")
    for sh in data["shots"][:6]:
        print(f"  {sh['id']:<7} {lib.fmt_ts(sh['start']):>8} - {lib.fmt_ts(sh['end']):>8} "
              f"{sh['duration']:5.2f}s {sh['section_label']:<9} {'—' if sh['instrumental'] else sh['lyric']}")
    if st["shots"] > 6:
        print(f"  ... {st['shots'] - 6} more")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as exc:
        lib.die(str(exc))

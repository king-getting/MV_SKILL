"""Align lyrics to the song map -> line-level timestamps.

Two input kinds, very different accuracy:

* **LRC** (``[mm:ss.xx]line``): exact, taken as given. Credits lines are flagged
  ``kind: "credit"`` so they never get bound to shots. This is the path to use
  whenever it exists.
* **Plain text**: the audio tells us where the bars are and where singing
  happens, the text tells us what is sung and in what order. Lines are placed by
  proportional division of their section (weighted by text length), then snapped
  to the beat grid. A line can be half a second off a real singer's breath, but
  the section and the order are right. Word-level accuracy would need ASR.

Usage:
  python align.py work/song_map.json lyrics.lrc --out work/lyric_map.json
"""
from __future__ import annotations

import argparse
import re

import numpy as np

import lib

TAG_RE = re.compile(r"^\s*[\[【（(]\s*(.+?)\s*[\]】）)]\s*$")
LRC_TS = re.compile(r"\[(\d{1,3}):(\d{1,2}(?:[.:]\d{1,3})?)\]")
LRC_OFFSET = re.compile(r"\[offset:\s*([+-]?\d+)\s*\]", re.IGNORECASE)
CJK_RE = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")
WORD_RE = re.compile(r"[A-Za-z][A-Za-z'’-]*")
STRONG_END = "。.!?！？…"
SOFT_END = "，,、;；"

# LRC files from Chinese music services often carry production credits; they are
# timestamped like lyrics but must not be visualised as lyrics.
CREDIT_WORDS = {
    "作词", "作曲", "编曲", "制作", "制作人", "录音", "混音", "母带", "监制", "发行", "出品",
    "吉他", "贝斯", "鼓", "键盘", "和声", "弦乐", "钢琴", "策划", "统筹", "词", "曲", "演唱",
}

KEYWORDS = {
    "intro": ("intro", "前奏", "序曲", "opening"),
    "verse": ("verse", "主歌", "v1", "v2", "v3"),
    "prechorus": ("pre-chorus", "prechorus", "导歌", "预副歌"),
    "chorus": ("chorus", "副歌", "hook", "refrain"),
    "bridge": ("bridge", "桥段", "过门"),
    "interlude": ("interlude", "间奏", "solo", "instrumental"),
    "outro": ("outro", "尾奏", "尾声", "ending", "coda"),
}


# ------------------------------------------------------------------- LRC ----

def parse_lrc(text: str) -> list[tuple[float, str]] | None:
    """Parse timestamped lyrics. Returns [(seconds, text)] or None if not LRC."""
    if not LRC_TS.search(text):
        return None
    m = LRC_OFFSET.search(text)
    offset = int(m.group(1)) / 1000.0 if m else 0.0

    entries: list[tuple[float, str]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("{") or line.startswith("//"):
            continue
        stamps = LRC_TS.findall(line)
        if not stamps:
            continue
        body = LRC_TS.sub("", line).strip()
        if not body:
            continue
        for mm, ss in stamps:
            try:
                t = int(mm) * 60 + float(ss.replace(":", "."))
            except ValueError:
                continue
            entries.append((max(0.0, t + offset), body))
    if not entries:
        return None
    entries.sort(key=lambda e: e[0])

    # drop exact duplicates (same time, same text) that some taggers emit
    out: list[tuple[float, str]] = []
    for t, body in entries:
        if out and abs(out[-1][0] - t) < 1e-3 and out[-1][1] == body:
            continue
        out.append((t, body))
    return out


def is_credit(text: str) -> bool:
    head = re.split(r"[：:]", text, maxsplit=1)[0].strip()
    return len(head) <= 6 and head in CREDIT_WORDS


def _section_for(t: float, sections: list[dict]) -> dict:
    for sec in sections:
        if sec["start"] <= t < sec["end"]:
            return sec
    return sections[-1] if t >= sections[-1]["start"] else sections[0]


def lines_from_lrc(entries: list[tuple[float, str]], sections: list[dict], duration: float,
                   beats: np.ndarray, bars: np.ndarray) -> list[dict]:
    # LRC gives starts only. A line lasts about as long as its neighbours, so use
    # the typical inter-line gap instead of a fixed cap — otherwise a 40s solo
    # gets swallowed as "the previous line's tail".
    diffs = [b[0] - a[0] for a, b in zip(entries, entries[1:])]
    typical = float(np.median([d for d in diffs if 0.8 <= d <= 8.0])) if any(
        0.8 <= d <= 8.0 for d in diffs) else 4.5
    max_len = max(2.5, typical * 1.35)

    out: list[dict] = []
    for i, (t, body) in enumerate(entries):
        if i + 1 < len(entries):
            end = min(entries[i + 1][0] - 0.12, t + max_len)
        else:
            end = min(duration, t + typical)
        end = max(end, t + 0.8)
        end = min(end, duration)
        sec = _section_for(t, sections)
        near_beat = float(np.min(np.abs(beats - t))) if beats.size else 99.0
        near_bar = float(np.min(np.abs(bars - t))) if bars.size else 99.0
        out.append({
            "id": "",
            "index": 0,
            "text": body,
            "tag": None,
            "kind": "credit" if is_credit(body) else "lyric",
            "section": sec["id"],
            "start": round(float(t), 3),
            "end": round(float(end), 3),
            "on_beat": bool(near_beat <= 0.10),
            "on_bar": bool(near_bar <= 0.10),
        })
    return out


# ------------------------------------------------------------- plain text ----

def parse_lyrics(text: str) -> list[dict]:
    """Return [{'tag': str|None, 'text': str}] in order; tags carry forward."""
    blocks: list[dict] = []
    tag = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("//"):
            continue
        m = TAG_RE.match(line)
        if m:
            tag = m.group(1).strip()
            continue
        blocks.append({"tag": tag, "text": line})
    return blocks


def tag_kind(tag: str | None) -> str | None:
    if not tag:
        return None
    low = tag.lower()
    for kind, words in KEYWORDS.items():
        if any(w in low for w in words):
            return kind
    return None


def line_weight(text: str) -> float:
    """Rough sung-length weight: CJK chars plus latin word estimates."""
    cjk = len(CJK_RE.findall(text))
    words = len(WORD_RE.findall(text))
    weight = cjk + 1.6 * words
    if weight <= 0:
        weight = max(1.0, len(text) / 2.0)
    if text.rstrip().endswith(tuple(STRONG_END)):
        weight *= 1.12
    elif text.rstrip().endswith(tuple(SOFT_END)):
        weight *= 1.04
    return weight


def assign_blocks_to_sections(blocks: list[dict], sections: list[dict]) -> dict[int, list[int]]:
    """Map lyric blocks onto section indices in order, using tag keywords."""
    block_ids: list[list[int]] = []
    for i, b in enumerate(blocks):
        if block_ids and b["tag"] is not None and blocks[block_ids[-1][0]]["tag"] == b["tag"]:
            block_ids[-1].append(i)
        else:
            block_ids.append([i])

    grouped: dict[int, list[int]] = {}
    cursor = 0
    for ids in block_ids:
        kind = tag_kind(blocks[ids[0]]["tag"])
        target = None
        if kind:
            for j in range(cursor, len(sections)):
                if sections[j]["label"] == kind:
                    target = j
                    break
        if target is None:
            for j in range(cursor, len(sections)):
                if j not in grouped:
                    target = j
                    break
        if target is None:
            target = len(sections) - 1
        grouped.setdefault(target, []).extend(ids)
        cursor = target + 1
    return grouped


def distribute_untagged(blocks: list[dict], sections: list[dict]) -> dict[int, list[int]]:
    """No usable tags: spread lines over sections by how much singing each carries."""
    weights = np.array([max(s["vocal"], 0.02) ** 1.5 * (s["end"] - s["start"]) for s in sections])
    weights = weights / weights.sum()
    counts = np.zeros(len(sections), dtype=int)
    while counts.sum() < len(blocks):
        counts[int(np.argmax(weights - counts / max(len(blocks), 1)))] += 1
    while counts.sum() > len(blocks):
        counts[int(np.argmax(counts))] -= 1
    grouped, idx = {}, 0
    for si, c in enumerate(counts):
        if c > 0:
            grouped[si] = list(range(idx, idx + c))
            idx += c
    return grouped


def repair_silent_sections(grouped: dict[int, list[int]], sections: list[dict]) -> dict[int, list[int]]:
    """Move lines out of sections that carry no singing (a mis-split outro, say)."""
    if not grouped:
        return grouped
    sung = [j for j, s in enumerate(sections) if s["vocal"] >= 0.15]
    for si in sorted(grouped):
        if sections[si]["vocal"] >= 0.08 or not sung:
            continue
        target = min(sung, key=lambda j: (abs(j - si), -sections[j]["vocal"]))
        grouped.setdefault(target, []).extend(grouped.pop(si))
    return grouped


def map_section_lines(lines: list[str], section: dict) -> list[tuple[float, float]]:
    start, end = float(section["start"]), float(section["end"])
    weights = np.array([line_weight(t) for t in lines], dtype=np.float64)
    cum = np.concatenate([[0.0], np.cumsum(weights)])
    edges = start + (end - start) * (cum / cum[-1])
    return [(float(edges[k]), float(edges[k + 1])) for k in range(len(lines))]


def snap_to_beats(spans, beats: np.ndarray, tol: float, lo: float, hi: float):
    snapped: list[tuple[float, float]] = []
    flags: list[bool] = []
    prev_end = lo - 0.01
    for t0, t1 in spans:
        flag = False
        if beats.size:
            i = int(np.argmin(np.abs(beats - t0)))
            if abs(beats[i] - t0) <= tol and beats[i] > prev_end:
                t0, flag = float(beats[i]), True
        t0 = float(np.clip(t0, prev_end + 0.01, hi - 0.2))
        t1 = float(np.clip(max(t1, t0 + 0.3), t0 + 0.05, hi))
        snapped.append((t0, t1))
        flags.append(flag)
        prev_end = t1
    return snapped, flags


# ------------------------------------------------------------------- main ----

def align(song_map: dict, lyrics_text: str, snap_tol: float = 0.30) -> dict:
    sections = song_map["sections"]
    duration = float(song_map["audio"]["duration"])
    beats = np.array(song_map["beats"], dtype=np.float64)
    bars = np.array(song_map["downbeats"], dtype=np.float64)

    lrc = parse_lrc(lyrics_text)
    if lrc:
        lines = lines_from_lrc(lrc, sections, duration, beats, bars)
        source = "lrc timestamps"
        tagged = True
        warnings: list[str] = []
    else:
        blocks = parse_lyrics(lyrics_text)
        if not blocks:
            lib.die("no lyric lines found in the input text")
        tagged = any(b["tag"] for b in blocks)
        grouped = assign_blocks_to_sections(blocks, sections) if tagged else distribute_untagged(blocks, sections)
        grouped = repair_silent_sections(grouped, sections)
        if not grouped:
            grouped = {0: list(range(len(blocks)))}

        lines = []
        for si in sorted(grouped):
            ids = grouped[si]
            sec = sections[si]
            spans = map_section_lines([blocks[i]["text"] for i in ids], sec)
            spans, flags = snap_to_beats(spans, beats, snap_tol, sec["start"], sec["end"])
            for local, (i, (t0, t1)) in enumerate(zip(ids, spans)):
                lines.append({
                    "id": "", "index": 0, "text": blocks[i]["text"], "tag": blocks[i]["tag"],
                    "kind": "lyric", "section": sec["id"],
                    "start": round(float(t0), 3), "end": round(float(t1), 3),
                    "on_beat": bool(flags[local]),
                    "on_bar": bool(round(float(t0), 3) in {round(float(b), 3) for b in bars}),
                })
        source = "proportional in-section warp + beat snap (no ASR)"
        warnings = []

    lines.sort(key=lambda ln: ln["start"])
    for n, ln in enumerate(lines):
        ln["id"] = f"L{n + 1:02d}"
        ln["index"] = n

    sung = [ln for ln in lines if ln["kind"] == "lyric"]
    durations = [ln["end"] - ln["start"] for ln in sung]
    covered = float(sum(durations))
    credits = len(lines) - len(sung)

    if covered < 0.10 * duration:
        warnings.append("lyrics cover under 10% of the track: check that the text matches the audio")
    if not lrc and len(lines) > 2.2 * max(len(song_map["vocal"]["phrases"]), 1):
        warnings.append(f"{len(lines)} lines vs {len(song_map['vocal']['phrases'])} vocal phrases: "
                        "line boundaries are approximate without an LRC")
    if not lrc and not tagged:
        warnings.append("no [section] tags in the lyrics: lines were distributed by vocal weight")
    if credits:
        warnings.append(f"{credits} credit lines detected and excluded from shot binding")

    return {
        "version": 1,
        "generator": "song-to-mv/align.py",
        "audio": song_map["audio"],
        "timing_source": source,
        "tagged": tagged,
        "stats": {
            "lines": len(lines),
            "lyric_lines": len(sung),
            "credit_lines": credits,
            "median_line_seconds": round(float(np.median(durations)), 3) if durations else 0.0,
            "covered_seconds": round(covered, 2),
            "snapped_to_beat": int(sum(1 for ln in sung if ln["on_beat"])),
        },
        "lines": lines,
        "warnings": warnings,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("song_map")
    ap.add_argument("lyrics")
    ap.add_argument("--out", default="lyric_map.json")
    ap.add_argument("--snap-tolerance", type=float, default=0.30)
    args = ap.parse_args()

    sm = lib.read_json(args.song_map)
    with open(args.lyrics, "r", encoding="utf-8-sig") as fh:
        text = fh.read()
    data = align(sm, text, args.snap_tolerance)
    lib.write_json(args.out, data)

    st = data["stats"]
    print(f"{st['lyric_lines']} lyric lines (+{st['credit_lines']} credits)  "
          f"median {st['median_line_seconds']}s  source: {data['timing_source']}")
    for ln in data["lines"][:5]:
        print(f"  {ln['id']} {lib.fmt_ts(ln['start']):>8} - {lib.fmt_ts(ln['end']):>8}  "
              f"{ln['section']:<4} {ln['text'][:26]}")
    if len(data["lines"]) > 5:
        print(f"  ... {len(data['lines']) - 5} more")
    for w in data["warnings"]:
        print(f"  ! {w}")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as exc:
        lib.die(str(exc))

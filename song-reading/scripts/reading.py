"""Build the evidence pack for a reading, then render the reading to markdown.

Two phases, deliberately:

  pack    song_map + lyric_map + emotion -> reading_input.json
          Every number and every line a reading is allowed to cite, in one file.
  render  reading.json -> 解读.md
          Turns the structured reading into something a human reads.

The reading itself is written by a model (or a person) from the pack, because
"what does this song mean" is not a signal-processing problem. What *is* the
script's job is making sure every claim has something to stand on: the schema
requires line ids and timestamps for the evidence.

Usage:
  python reading.py pack work/song_map.json work/lyric_map.json work/emotion.json \
      --title "这一生关于你的风景" --out work/reading_input.json
  python reading.py render work/reading.json --out work/解读.md
"""
from __future__ import annotations

import argparse
import os

import lib


# -------------------------------------------------------------------- pack --

def build_pack(song_map: dict, lyric_map: dict, emotion: dict, title: str) -> dict:
    duration = float(song_map["audio"]["duration"])
    emo_by = {e["id"]: e for e in emotion["sections"]}
    lines = [ln for ln in lyric_map.get("lines", []) if ln.get("kind", "lyric") == "lyric"]

    sections = []
    for sec in song_map["sections"]:
        emo = emo_by.get(sec["id"], {})
        sec_lines = [ln for ln in lines if ln["section"] == sec["id"]]
        span_min = max((sec["end"] - sec["start"]) / 60.0, 1e-6)
        sections.append({
            "id": sec["id"],
            "label": sec["label"],
            "start": round(float(sec["start"]), 3),
            "end": round(float(sec["end"]), 3),
            "bars": sec.get("bars"),
            "repeat_of": sec.get("repeat_of"),
            "arousal": emo.get("arousal"),
            "valence": emo.get("valence"),
            "features": emo.get("features", {}),
            "why": emo.get("why", []),
            "line_count": len(sec_lines),
            "lines_per_minute": round(len(sec_lines) / span_min, 2),
            "lines": [{"id": ln["id"], "t": ln["start"], "text": ln["text"]} for ln in sec_lines],
        })

    by_id = {s["id"]: s for s in sections}
    repeats = []
    for s in sections:
        prev = by_id.get(s.get("repeat_of") or "")
        if not prev:
            continue
        repeats.append({
            "section": s["id"], "of": prev["id"],
            "arousal_delta": round((s["arousal"] or 0) - (prev["arousal"] or 0), 3),
            "valence_delta": round((s["valence"] or 0) - (prev["valence"] or 0), 3),
            "brightness_delta": round(s["features"].get("brightness", 0) - prev["features"].get("brightness", 0), 3),
            "energy_delta": round(s["features"].get("energy", 0) - prev["features"].get("energy", 0), 3),
        })

    gaps = []
    for a, b in zip(lines, lines[1:]):
        gap = b["start"] - a["end"]
        if gap >= 6.0:
            gaps.append({"after_line": a["id"], "before_line": b["id"],
                         "start": round(a["end"], 2), "end": round(b["start"], 2),
                         "seconds": round(gap, 2)})

    return {
        "version": 1,
        "title": title,
        "duration": round(duration, 2),
        "key": emotion.get("key", {}),
        "grid": {"bpm": song_map["grid"]["bpm"],
                 "beat_confidence": song_map["grid"].get("bpm_confidence"),
                 "grid_mode": song_map["grid"].get("grid_mode")},
        "counts": {
            "sections": len(sections),
            "lyric_lines": len(lines),
            "instrumental_sections": [s["id"] for s in sections if s["line_count"] == 0],
            "choruses": [s["id"] for s in sections if s["label"] == "chorus"],
        },
        "sections": sections,
        "repeats": repeats,
        "silent_gaps": gaps,
        "warnings": list(song_map.get("warnings", [])) + list(emotion.get("warnings", [])),
        "how_to_read": [
            "arousal/valence 由 emotion.py 的启发式给出，不是结论；引用时说明依据（why）",
            "每一条判断都必须挂到 line id 或时间码上",
            "先看 repeats 和 silent_gaps：歌曲的转折几乎都在那里",
        ],
    }


# ------------------------------------------------------------------ render --

def _fmt(t: float) -> str:
    m = int(t // 60)
    s = t - 60 * m
    return f"{m}:{s:05.2f}"


def _require(reading: dict, key: str, path: str) -> list[str]:
    node = reading
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return [f"reading.json 缺少 `{path}`（{key}）"]
        node = node[part]
    return []


def render(reading: dict, pack: dict | None = None) -> str:
    problems: list[str] = []
    problems += _require(reading, "主题", "theme.one_line")
    problems += _require(reading, "情绪", "emotion.curve")
    if not reading.get("theme", {}).get("evidence"):
        problems.append("reading.json 的 `theme.evidence` 是空的：主题没有证据支撑")
    if problems:
        lib.die("; ".join(problems))

    title = reading.get("title") or (pack or {}).get("title") or "未命名"
    out: list[str] = [f"# 解读 · {title}", ""]

    theme = reading["theme"]
    out += [f"> {theme['one_line']}", ""]
    if theme.get("evidence"):
        cites = "；".join(f"{e.get('quote', '')}（{e.get('line', '')} @ {_fmt(float(e['t']))}）"
                          if e.get("t") is not None else f"{e.get('quote', '')}（{e.get('line', '')}）"
                          for e in theme["evidence"][:4])
        out += [f"**依据**：{cites}", ""]

    narrative = reading.get("narrative") or {}
    if narrative:
        out += ["## 叙事", ""]
        for key, label in (("pov", "视角"), ("addressee", "说话对象"), ("mode", "口吻")):
            if narrative.get(key):
                out.append(f"- **{label}**：{narrative[key]}")
        if narrative.get("summary"):
            out += ["", narrative["summary"]]
        arc = narrative.get("arc") or []
        if arc:
            out += ["", "| 幕 | 段落 | 转折 |", "|---|---|---|"]
            for a in arc:
                out.append(f"| {a.get('act', '')} | {', '.join(a.get('sections', []))} | {a.get('turn', '')} |")
        out.append("")

    curve = reading.get("emotion", {}).get("curve", [])
    if curve:
        out += ["## 情绪曲线", "", "| 段落 | 入点 | arousal | valence | 依据 |", "|---|---|---|---|---|"]
        for c in curve:
            why = "、".join(c.get("why", [])) if isinstance(c.get("why"), list) else str(c.get("why", ""))
            out.append(f"| {c.get('section', '')} | {_fmt(float(c.get('t', 0)))} | "
                       f"{c.get('arousal', '')} | {c.get('valence', '')} | {why} |")
        out.append("")
        for turn in reading["emotion"].get("turns", []):
            out.append(f"- **转折 {_fmt(float(turn.get('t', 0)))}**：{turn.get('what', '')}")
        if reading["emotion"].get("turns"):
            out.append("")

    imagery = reading.get("imagery") or []
    if imagery:
        out += ["## 意象", ""]
        for img in imagery:
            where = "、".join(img.get("lines", [])[:6])
            out.append(f"- **{img.get('id', '')}**" + (f"（{where}）" if where else "") + f"：{img.get('function', '')}")
        out.append("")

    rhetoric = reading.get("rhetoric") or []
    if rhetoric:
        out += ["## 修辞与句式", ""]
        for r in rhetoric:
            examples = "；".join(f"「{e}」" for e in r.get("examples", [])[:3])
            out.append(f"- **{r.get('device', '')}**：{r.get('note', '')}" + (f" —— {examples}" if examples else ""))
        out.append("")

    sonic = reading.get("sonic") or {}
    if sonic:
        out += ["## 声音", ""]
        if sonic.get("key"):
            out.append(f"- **调性**：{sonic['key']}")
        for item in sonic.get("notable", []):
            t = f" @ {_fmt(float(item['t']))}" if item.get("t") is not None else ""
            out.append(f"- **{item.get('what', '')}**{t}：{item.get('why', '')}")
        if sonic.get("arrangement_arc"):
            out += ["", f"**编曲走向**：{sonic['arrangement_arc']}"]
        out.append("")

    atmosphere = reading.get("atmosphere") or {}
    if atmosphere:
        out += ["## 氛围", ""]
        for key, label in (("temperature", "温度"), ("space", "空间"), ("density", "密度"), ("light", "光")):
            if atmosphere.get(key):
                out.append(f"- **{label}**：{atmosphere[key]}")
        if atmosphere.get("palette"):
            out += ["", "**色板**：" + "、".join(atmosphere["palette"])]
        out.append("")

    if pack:
        out += ["## 证据索引", ""]
        out.append(f"- 段落：{pack['counts']['sections']} 段，歌词 {pack['counts']['lyric_lines']} 行")
        for r in pack.get("repeats", []):
            out.append(f"- {r['section']} 重复 {r['of']}：arousal {r['arousal_delta']:+.2f}，"
                       f"亮度 {r['brightness_delta']:+.2f}")
        for g in pack.get("silent_gaps", []):
            out.append(f"- 无词区间 {_fmt(g['start'])}–{_fmt(g['end'])}（{g['seconds']}s）"
                       f"，在 {g['after_line']} 与 {g['before_line']} 之间")
        out.append("")

    return "\n".join(out).rstrip() + "\n"


# -------------------------------------------------------------------- main --

def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_pack = sub.add_parser("pack", help="build reading_input.json")
    p_pack.add_argument("song_map")
    p_pack.add_argument("lyric_map")
    p_pack.add_argument("emotion")
    p_pack.add_argument("--title", default="")
    p_pack.add_argument("--out", default="reading_input.json")

    p_render = sub.add_parser("render", help="reading.json -> 解读.md")
    p_render.add_argument("reading")
    p_render.add_argument("--pack", default=None)
    p_render.add_argument("--out", default="解读.md")

    args = ap.parse_args()

    if args.cmd == "pack":
        pack = build_pack(lib.read_json(args.song_map), lib.read_json(args.lyric_map),
                          lib.read_json(args.emotion), args.title)
        lib.write_json(args.out, pack)
        print(f"pack: {pack['counts']['sections']} sections, {pack['counts']['lyric_lines']} lines, "
              f"{len(pack['repeats'])} repeats, {len(pack['silent_gaps'])} silent gaps -> {args.out}")
    else:
        reading = lib.read_json(args.reading)
        pack = lib.read_json(args.pack) if args.pack else None
        text = render(reading, pack)
        os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"wrote {args.out}  {len(text.splitlines())} lines")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as exc:
        lib.die(str(exc))

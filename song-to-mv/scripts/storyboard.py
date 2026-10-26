"""Render shotlist.json (+ mv_bible.json) into a human-readable storyboard.md.

The JSON is for machines; this is the version you read on your phone on set, send
to a composer, or paste into a generation queue.

Usage:
  python storyboard.py work/kumu/shotlist.json --bible work/kumu/mv_bible.json \
      --out work/kumu/storyboard.md
"""
from __future__ import annotations

import argparse
import os
from collections import defaultdict

import lib


def fmt_clock(t: float) -> str:
    m = int(t // 60)
    s = t - 60 * m
    return f"{m}:{s:04.1f}"


def render(shotlist: dict, bible: dict | None) -> str:
    shots = shotlist.get("shots", [])
    dur = float(shotlist.get("audio", {}).get("duration", 0.0))
    st = shotlist.get("stats", {})
    out: list[str] = []

    title = (bible or {}).get("song", {}).get("title") or os.path.basename(str(shotlist.get("audio", {}).get("path", "")))
    artist = (bible or {}).get("song", {}).get("artist")
    head = f"# 分镜表 · {title}" + (f" — {artist}" if artist else "")
    out += [head, ""]

    meta = [f"**时长** {fmt_clock(dur)}", f"**镜头** {st.get('shots', len(shots))} 个",
            f"**中位时长** {st.get('median_shot_seconds', 0)}s",
            f"**锚点** {st.get('anchor', 'bar')}",
            f"**覆盖** {st.get('coverage', 0):.0%}"]
    if bible:
        meta.append(f"**画幅** {bible['song'].get('aspect', '—')}")
    out += [" · ".join(meta), ""]

    if bible:
        out += [f"> {bible.get('logline', '')}", "",
                f"**style_block**：`{bible.get('style_block', '')}`", "",
                f"**负面词**：`{bible.get('negative_base', '')}`", ""]
        motifs = bible.get("motifs") or []
        if motifs:
            out.append("**母题**：" + "；".join(f"`{m['id']}` {m['meaning']}" for m in motifs))
            out.append("")
        chars = bible.get("characters") or []
        if chars:
            out.append("**角色**：" + "；".join(f"`{c['id']}` {c['look']}" for c in chars))
            out.append("")

    # per-section summary
    by_sec: dict[str, list[dict]] = defaultdict(list)
    for s in shots:
        by_sec[s["section"]].append(s)
    out += ["## 全片节奏", "",
            "| 段落 | 入点 | 时长 | 镜头 | 平均 | 标签 |", "|---|---|---|---|---|---|"]
    for sec_id, group in by_sec.items():
        span = group[0]["start"] - group[0]["start"] + sum(g["duration"] for g in group)
        out.append(f"| {sec_id} | {fmt_clock(group[0]['start'])} | {span:.1f}s | {len(group)} | "
                   f"{span / len(group):.1f}s | {group[0].get('section_label', '')} |")
    out.append("")

    unfilled = [s for s in shots if not s.get("prompt")]
    if unfilled:
        out += [f"> ⚠️ {len(unfilled)} 个镜头的创作字段还是空的："
                f"{', '.join(s['id'] for s in unfilled[:10])}", ""]

    current = None
    for s in shots:
        if s["section"] != current:
            current = s["section"]
            out += ["", f"## {current} · {s.get('section_label', '')} · {fmt_clock(s['start'])}", ""]
        out.append(f"### {s['id']} · {fmt_clock(s['start'])}–{fmt_clock(s['end'])} · {s['duration']}s"
                   + (f" · {s['title']}" if s.get("title") else ""))
        out.append("")
        out.append(f"- **词**：{s.get('lyric') or '（器乐 / 无词）'}")
        out.append(f"- **镜**：{s.get('size') or '—'} / {s.get('camera') or '—'} / "
                   f"{s.get('subject') or '—'}")
        if s.get("action"):
            out.append(f"- **面**：{s['action']}")
        if s.get("prompt"):
            out.append(f"- **提示词**：`{s['prompt']}`")
        if s.get("refs"):
            out.append(f"- **参考图**：{', '.join(s['refs'])}")
        out.append(f"- **转场**：{s.get('transition_in') or '—'} → {s.get('transition_out') or '—'} · "
                   f"seed {s.get('seed')} · {s.get('status')}")
        out.append("")

    return "\n".join(out).rstrip() + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("shotlist")
    ap.add_argument("--bible", default=None)
    ap.add_argument("--out", default="storyboard.md")
    args = ap.parse_args()

    sl = lib.read_json(args.shotlist)
    bb = lib.read_json(args.bible) if args.bible else None
    text = render(sl, bb)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(text)
    print(f"wrote {args.out}  {len(text.splitlines())} lines  "
          f"{sum(1 for s in sl.get('shots', []) if s.get('prompt'))}/{len(sl.get('shots', []))} shots filled")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as exc:
        lib.die(str(exc))

# shotlist.json 字段语义

`shots.py` 只写时间字段，创作字段全为 `null`。第 6 步只填创作字段，**不要动时间字段**。

```jsonc
{
  "version": 1,
  "audio": {"path": "...", "duration": 189.03, "hash": "..."},
  "policy": {"anchor": "bar", "bars_per_shot": {...}, "max_shot_seconds": 8.0, ...},
  "stats": {"anchor": "bar", "shots": 43, "coverage": 1.0, "median_shot_seconds": 5.217,
            "instrumental_shots": 8},
  "shots": [
    {
      "id": "S3-04",              // 段落id + 段内序号；稳定、可寻址、可单条重生成
      "section": "S3",
      "section_label": "chorus",
      "start": 78.261, "end": 83.478,
      "duration": 5.217,
      "bars": 2.0,                 // 2 小节
      "lines": ["L12"],            // 覆盖到的歌词行 id
      "lyric": "我就一直跑到天亮",  // 主歌词（按重叠最长选）
      "instrumental": false,       // true = 该镜无歌词（前奏/间奏/尾奏）

      "title": null,               // 创作槽位从这里开始
      "size": null,                // 景别
      "camera": null,              // 运镜
      "subject": null,             // 主体，引用 bible 里的 id
      "action": null,              // 一个视觉动作
      "prompt": null,              // style_block + 本镜内容
      "negative": null,
      "refs": null,                // 参考图，字符串数组
      "transition_in": null,
      "transition_out": null,
      "seed": null,
      "status": "todo"             // todo|gen|ok|reroll|drop
    }
  ]
}
```

## 两种锚点

| anchor | 边界怎么来 | 什么时候用 |
|---|---|---|
| `bar` | 小节线 + `bars_per_shot` | 有稳定脉动。切点落在 `bar_times` 上，`validate` 会检查是否踩拍 |
| `line` | 歌词句首，`lines_per_shot` 句一镜 | 无鼓、rubato、抒情慢歌。网格不可信时唯一可靠的选择 |

`line` 模式下，镜头的 `start` 是句首提前 `lead_in_seconds`（默认 0.15s），这样画面切换略早于开口，不会"人声先出画面后到"。纯器乐段按 `max_shot_seconds` 均分，`instrumental: true`。

## 时间字段的规则

- 相邻镜头首尾严格相接，整条覆盖 `0 .. duration`，`validate.py` 会检查空隙和重叠。
- `bar` 模式：镜头起点对齐拍点；段内优先对齐小节线。
- 时长受 policy 约束（默认 1.2s ≤ 时长 ≤ 8s；抒情歌可放宽到 10s）。
- 一句歌词只属于**它起始所在**的那个镜头，不会跨镜重复绑定。

## 创作字段的填法

| 字段 | 约束 |
|---|---|
| `prompt` | **必须以 bible 的 `style_block` 原文开头**，再接本镜内容；总长 ≤ 2 句 |
| `subject` | 只用 bible 里定义过的 `char_*` / `loc_*` / `prop_*`，不临时造新角色 |
| `action` | 一个动作。写"抬头，雨停，第一束光打到侧脸"，不要写"回忆过去，情绪复杂" |
| `refs` | 角色一致性靠它；同一角色所有镜头用同一组参考图 |
| `seed` | 固定 seed 便于重生成时保持构图；换 seed 是有意的选择，不是随手 |
| `status` | `reroll` 是给返工镜头的标记，不要直接改成 `todo` 丢失历史 |

## 镜头密度经验

| 段落 | 每镜小节 | 理由 |
|---|---|---|
| intro / outro | 4 | 长镜头留呼吸，观众在等 |
| verse | 2 | 叙事推进 |
| prechorus | 1 | 加速，制造期待 |
| chorus | 2，末段副歌 1 | 密集但不碎；末段加密提升高潮 |
| bridge | 4 | 突然静下来才有效果 |

约 3 分钟的流行歌会得到 40–60 个镜头。明显少于 30 个，多半是 policy 太松；多于 90 个，生成成本会失控。

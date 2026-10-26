---
name: song-reading
description: 解读一首歌：主题、叙事视角、情感曲线、意象系统、修辞、氛围。输入音频（+歌词或 .lrc），输出带时间码证据的结构化解读 reading.json 和中文解读稿。当用户要"解读/分析这首歌在讲什么""这首歌的情绪/氛围/主题是什么""帮我读懂这首歌词"时使用。要做分镜或生成 MV 请用 song-to-mv。
---

# 歌曲解读

产物是**一篇有证据的解读**，不是分镜、不是提示词。每条判断都要能指回某一句词、某一秒、某个特征值——否则它说的是任何一首歌。

## 输入

| 必需 | 说明 |
|---|---|
| 音频 | ffmpeg 能解的任意格式 |
| 歌词 | **`.lrc` 最好**（时间戳精确，还能算出"人声覆盖"）；纯文本也行，但句级时间是估的 |

## 输出

| 文件 | 内容 |
|---|---|
| `song_map.json` | 时间轴：拍点、段落、能量、人声（与 song-to-mv 同源） |
| `lyric_map.json` | 每句歌词的时间与归属段落 |
| `emotion.json` | 每段的亮度、起音密度、动态、尾音、调性、人声占比，以及 arousal/valence 和 `why` |
| `reading_input.json` | 证据包：段落 + 特征 + 每句词 + 重复关系 + 无词区间 |
| `reading.json` | 结构化解读（模型写） |
| `解读.md` | 人读稿（脚本渲染） |

字段语义见 `references/reading.schema.md`。

## 五步

```powershell
$SK = "<本 skill 目录的绝对路径>"
$PY = (Get-ChildItem "$env:DSH_HOME\dsh-runtimes\*\dependencies\python\python.exe" |
       Select-Object -First 1).FullName

# 1 时间轴
& $PY "$SK\scripts\beatmap.py" work\song.mp3 --out work\song_map.json

# 2 歌词对轴（LRC 直接用时间戳）
& $PY "$SK\scripts\align.py" work\song_map.json work\song.lrc --out work\lyric_map.json

# 3 声学特征：亮度/密度/动态/尾音/调性/人声
& $PY "$SK\scripts\emotion.py" work\song.mp3 work\song_map.json --lyrics work\lyric_map.json --out work\emotion.json

# 4 证据包
& $PY "$SK\scripts\reading.py" pack work\song_map.json work\lyric_map.json work\emotion.json --title "歌名 — 歌手" --out work\reading_input.json

# 5 读一遍证据包，写 reading.json（这一步是模型的活），再渲染
& $PY "$SK\scripts\reading.py" render work\reading.json --pack work\reading_input.json --out work\解读.md
```

第 5 步不要跳：`render` 会检查 `theme.one_line`、`emotion.curve`、以及**主题有没有证据**，缺了直接报错。

## 怎么写解读

1. **每条判断挂证据。** `theme.evidence` 至少两条，写 `line` + `t` + 原文。情绪表里的 `why` 引用 `emotion.json` 的特征，不要凭空断。
2. **arousal / valence 是启发式，不是结论。** 它们是特征混合出来的相对曲线，引用时说明依据（亮度、密度、能量），别写成"这首歌的喜悦度是 -0.1"。
3. **可替换性自检。** 把歌名换成另一首还成立的句子（"表达了深深的思念"）就删掉。解读的价值在于只有这首歌能这么说。
4. **先看 `repeats` 和 `silent_gaps`。** 结构的转折几乎都在重复段和无词区间里；`repeats` 给了逐次差额，直接说"第三遍比第一遍亮 0.30"。
5. **允许不确定。** 写"也可能是过去的自己"，比硬下结论可信。调性置信度低时不要断言大小调。
6. **不要复述歌词。** 引用是证据，不是内容。

## 停下来问人的时机

- 段落边界和听感对不上：先用 `--sections` 修，再解读（否则情绪表的段落是错的）。
- `grid_mode` 是 `tracked` 或 `bpm_confidence` 近 0：这首歌没有稳定脉搏，不要引用"小节"做论据，用句子和时间码。
- 歌词行数和人声对不上：可能给了错的歌词文件。

## 能说什么，不能说什么

| 能 | 不能 |
|---|---|
| 主题、叙事视角、意象链、修辞、句式语气 | 逐字级演唱细节（没有 ASR） |
| 段落级情绪走向，及支撑它的声学特征 | 情绪的绝对量表（arousal/valence 是相对的） |
| 编曲密度、亮度、动态、尾音的变化 | 具体乐器识别（没有音源分离） |
| 调性倾向（附置信度） | 和弦进行、转调细节 |

## references

- `references/reading.schema.md` — reading.json 每个字段怎么写、什么算合格证据

## 维护

`lib.py` / `probe.py` / `beatmap.py` / `align.py` 与 `song-to-mv` 里的同名文件**逐字节相同**（时间轴内核两处各带一份，保证单个 skill 能独立使用）。改其中一份必须同步另一份，否则解读和分镜会基于不同的时间轴。

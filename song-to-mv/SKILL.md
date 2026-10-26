---
name: song-to-mv
description: 把一首歌（音频 + 歌词）编译成对拍的 MV 分镜：段落结构、句级歌词时间、逐镜头时间网格 shotlist.json、mv_bible 与逐镜提示词、人读分镜表，以及可播放的预演视频。用于 AI 生成 MV、卡点剪辑、音乐可视化。想解读歌曲的主题/情绪/氛围请用 song-reading。
---

# 歌曲 → MV 分镜

这个 skill 的产物**不是分析文字，而是一份对拍、可寻址的施工图**。它只解决"时间"和"结构"，以及把这些变成可生成的镜头；"这首歌在说什么"是 `song-reading` 的事。

## 输入

| 必需 | 说明 |
|---|---|
| 音频 | ffmpeg 能解的任何格式。缺失会直接报错 |
| 歌词 | **优先 `.lrc`**：带 `[mm:ss.xx]` 时间戳，歌词时间直接采用，秒级精确，制作名单会被识别并排除。纯文本次之：`[Verse 1]`、`【副歌】` 这类行当段落标签，时间只能按比例估算 |

BPM 已知时可以传 `--bpm`，能消掉最大的一个变量。

## 输出

| 文件 | 内容 | 谁生成 |
|---|---|---|
| `song_map.json` | 客观时间轴：bpm、拍点、小节、段落、能量/人声曲线 | 脚本 |
| `lyric_map.json` | 每句歌词的起止时间、归属段落；`timing_source` 写明是 LRC 还是估算 | 脚本 |
| `shotlist.json` | 镜头网格（时间已定）+ 创作槽位（空） | 脚本定时间，模型填内容 |
| `timeline.png` | 一条时间轴上的能量/人声/段落/歌词/镜头 | 脚本 |
| `storyboard.md` | 人读的分镜表：每镜的词、景别、运镜、动作、提示词、转场 | 脚本 |
| `animatic.mp4` | 可播放的预演：按镜头表画的构图/色板/景别 + 时间码 + 歌词 + 转场 + 音轨 | 脚本 |

字段语义见 `references/song_map.schema.md` 与 `references/shotlist.schema.md`。

## 七步

先把音频、歌词放进 `work/<slug>/`。以下命令在当前项目目录执行，先设两个变量：

```powershell
$SK = "<本 skill 目录的绝对路径>"                # 即 SKILL.md 所在目录，不是项目目录
$PY = (Get-ChildItem "$env:DSH_HOME\dsh-runtimes\*\dependencies\python\python.exe" |
       Select-Object -First 1).FullName         # 带 numpy 的解释器；PATH 上的 python 有 numpy 也可直接用
```

```powershell
# 1 探测：时长、采样率、整体响度
& $PY "$SK\scripts\probe.py" work\demo\song.mp3 --json work\demo\meta.json

# 2 时间轴：BPM/拍/小节/段落/人声
& $PY "$SK\scripts\beatmap.py" work\demo\song.mp3 --out work\demo\song_map.json

# 3 必须看一眼图，再往下走
& $PY "$SK\scripts\timeline.py" work\demo\song_map.json --out work\demo\timeline.png

# 4 歌词对轴（LRC 直接用时间戳；纯文本则按段估算）
& $PY "$SK\scripts\align.py" work\demo\song_map.json work\demo\lyrics.lrc --out work\demo\lyric_map.json

# 5 镜头网格
& $PY "$SK\scripts\shots.py" work\demo\song_map.json work\demo\lyric_map.json --out work\demo\shotlist.json

# 6 写 mv_bible.json + 填 shotlist 的创作槽位（见下）

# 7 自检（写了 bible 就加 --bible），0 error 才算完成
& $PY "$SK\scripts\validate.py" work\demo\song_map.json work\demo\lyric_map.json work\demo\shotlist.json
```

第 3 步不是可选项。段落切错时后面全是错的，而图上一眼就能看出来：副歌起点是否落在能量跳变处、前奏有没有被并进主歌、歌词块是否整体偏了半句。

## 切镜锚点：`bar` 还是 `line`

`shots.py` 有两种切分方式，选错会让整片"踩不上点"：

| anchor | 切点落在 | 适用 |
|---|---|---|
| `bar`（默认） | 小节线，按段落的 `bars_per_shot` | 有稳定脉动的歌：流行、摇滚、电子 |
| `line` | 歌词句首 | 无鼓 / rubato 的抒情歌。恒定 BPM 网格在这里是虚构的，**句子才是脉搏** |

判断依据看 `song_map.json` 的 `grid_fit_deviation` 和 `bpm_confidence`：偏差超过约 0.15s 或置信度接近 0，就别信小节线，改 `anchor: "line"`，policy 见 `references/policy.ballad.json`。

`line` 模式下 `lines_per_shot` 决定每镜几句：主歌 2 句（长镜头留呼吸）、副歌 1 句（加密）。纯器乐段（前奏/间奏/尾奏）按 `max_shot_seconds` 均分。

## 段落可以写死

自动分段的启发式在编曲稀疏、段落重复的歌上会失手（把 8 小节主歌并进副歌）。两个逃生口，都在 `--sections`：

```jsonc
// A 只给起点，吸附到最近小节线（网格可信时用）
[{"start": 0, "label": "intro"}, {"start": 30.2, "label": "verse"}]

// B 给完整 start/end，不吸附（无鼓、rubato、网格不可信时用）
{"sections": [
  {"start": 0.0,   "end": 29.94,  "label": "intro"},
  {"start": 29.94, "end": 68.049, "label": "verse"}
]}
```

有 LRC 时，段落边界直接从歌词块读：相邻句间隔超过约 8 秒的地方是间奏，重复出现的句子串是重复段落。

## 第 6 步：两遍写创作层

**第一遍只写全局**，输出 `mv_bible.json`：

```jsonc
{
  "logline": "一句话故事",
  "arc": [{"act": "起", "sections": ["S1", "S2"], "turn": "……"}],
  "style_block": "胶片颗粒、青橙对撞、浅景深、16mm 手持、低饱和……",  // 必须原样拼进每个 prompt
  "palette": ["#0E1A2B", "#C9482F"],
  "characters": [{"id": "char_01", "look": "……", "refs": ["char_01_front.png"]}],
  "locations": [{"id": "loc_rooftop", "look": "……"}],
  "motifs": [{"id": "rain", "meaning": "……", "first_seen": "S2"}],
  "rules": ["不出现文字字幕", "每段只用一种主光"]
}
```

**第二遍按段填镜头**，只填 `shotlist.json` 里已有的镜头，不改时间：

```jsonc
{ "id": "S3-04", "size": "中近景", "camera": "缓慢推近", "subject": "char_01 @loc_rooftop",
  "action": "抬头，雨停，第一束光打到侧脸", "prompt": "<style_block 原文> + 本镜内容",
  "negative": "文字, 水印, 多余手指", "refs": ["char_01_front.png"], "seed": 7712,
  "transition_out": "硬切", "status": "todo" }
```

词表、风格块模板、负面词见 `references/prompt-patterns.md`。

填完做两件事：自检 + 出人读分镜表。

```powershell
& $PY "$SK\scripts\validate.py" work\kumu\song_map.json work\kumu\lyric_map.json work\kumu\shotlist.json --bible work\kumu\mv_bible.json
& $PY "$SK\scripts\storyboard.py" work\kumu\shotlist.json --bible work\kumu\mv_bible.json --out work\kumu\storyboard.md
```

`validate --bible` 查四件事：每镜 prompt 是否都以 `style_block` **原文开头**（防画风漂移）、`subject` 是否都在 bible 里定义过（防临时造角色）、`refs` 是否都是已定义角色、有没有漏填的镜头。

## 预演（可选，但强烈建议）

没有生成模型也能先看节奏——`animatic.py` 用代码画出每镜的构图、景别、色板、运镜，配时间码、歌词和转场，混上音轨导出 mp4。

```powershell
& $PY "$SK\scripts\animatic.py" work\kumu\shotlist.json --audio work\kumu\song.mp3 --out work\kumu\animatic.mp4
# 先渲染 15 秒试机位和转场，别一上来就渲全片
& $PY "$SK\scripts\animatic.py" work\kumu\shotlist.json --audio work\kumu\song.mp3 --out work\kumu\test.mp4 --duration 15
```

它是**判断剪辑用的**，不是成片：人是一块剪影，颜色是渐变的。看完再决定要不要花生成成本。`--size 1920x1080` 可以出 1080p。

色板按**段落类型 + 第几次出现**给光（第三遍副歌最亮，间奏回落），不是按时间线性推进——否则 2:00 的间奏会比副歌还亮，跟故事反着来。

## 硬规则

1. **时间只来自脚本。** 不要手写、不要"估"时间戳；要改就改 `--sections` 或 policy 再重跑。
2. **先全局后局部。** 先定 style_block / 角色 / 母题，再逐镜填；否则几十个镜头会出现几十种画风。
3. **一镜一念。** 一个镜头一个视觉动作，prompt ≤ 2 句。别把整句歌词画成一张插画。
4. **重复段落做变体。** `repeat_of` 标出的段（通常是副歌二遍）用同母题换机位/换时间/换色温，不要复读同一批镜头。
5. **改完必验。** 任何人动过时间字段，都要重跑 `validate.py`。

## 停下来问人的时机

- `grid_mode` 是 `tracked`，或 `bpm_confidence < 0.15`：网格可能跟着 tempo 漂，先确认 BPM。
- 段落数和听感差很多（比如把 8 小节的主歌并进了副歌）：让用户确认段落，再用 `--sections` 覆盖。
- 歌词行数远多于检出的人声短语（`align.py` 会警告）：句级时间只是近似，要精确得上 LRC 或 ASR。
- 成片形态未定（竖屏 30s / 完整 3-4 分钟 / 横屏）：它会决定 policy，先问。

## 常见故障

| 现象 | 处理 |
|---|---|
| 拍点整体偏移半拍 | `--bpm` 传已知值重跑；`grid_mode` 会跟着变 |
| 无鼓慢歌，网格怎么调都不对 | 这歌本来就没有稳定脉搏，改 `anchor: "line"`，用歌词句首切 |
| 前奏被并进主歌 | `--sections` 写死边界 |
| 歌词整体早/晚一句 | 检查歌词标签与段落是否对得上；标签缺失时按人声权重分配，容易错位 |
| 制作名单被当成歌词 | 用 `.lrc`，`align.py` 会把 `作词/编曲/混音` 这类行标成 `credit` 并排除出镜头绑定 |
| 镜头太碎 / 太整 | 改 policy 的 `bars_per_shot` / `lines_per_shot`、`max_shot_seconds` |
| 某段全是被忽略的纯音乐 | 正常，`instrumental: true` 的镜头本来就没有歌词绑定 |

## 能力边界

- **没有 ASR**：不给歌词就只有节拍和段落。给 LRC 则歌词时间是精确的；给纯文本只有句级近似（误差通常 < 1s）；字级需要 ASR。
- **4/4 假设**：3/4、6/8 拍会算错小节，需要手动给 `--sections`。
- **恒定速度假设**：无鼓、rubato 的歌会让 `grid_fit_deviation` 变大，此时别用小节线切镜，改 `anchor: "line"`。
- 不生成画面、不做人声分离、不做响度归一化。

## references

- `references/song_map.schema.md` — 时间轴字段的准确含义
- `references/shotlist.schema.md` — 镜头字段、两种锚点、创作槽位
- `references/prompt-patterns.md` — 景别/运镜/转场词表、style block 模板、负面词
- `references/policy.example.json` — 卡点歌的镜头密度
- `references/policy.ballad.json` — 抒情歌的按句切分

## 维护

`lib.py` / `probe.py` / `beatmap.py` / `align.py` 与 `song-reading` 里的同名文件**逐字节相同**（时间轴内核两处各带一份，保证单个 skill 能独立使用）。改其中一份必须同步复制到另一份，否则两条链会给出不一致的时间轴。

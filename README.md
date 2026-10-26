# MV_SKILL

两个给 AI Agent 用的技能（skill）：一个**读懂**一首歌，一个把歌**拆成分镜**。

不依赖任何图像/视频生成模型，不需要 API key，不联网。只要 Python + numpy + Pillow + ffmpeg。

> Two agent skills for music videos: `song-reading` interprets a song (theme, emotion, imagery),
> `song-to-mv` turns it into a beat-accurate shot list plus a playable animatic.

## 包含

| skill | 回答什么问题 | 产物 |
|---|---|---|
| [`song-reading`](song-reading/SKILL.md) | 这首歌在说什么 | `emotion.json`、`reading.json`、`解读.md` |
| [`song-to-mv`](song-to-mv/SKILL.md) | 这首歌怎么拍 | `song_map.json`、`lyric_map.json`、`shotlist.json`、`storyboard.md`、`timeline.png`、`animatic.mp4` |

两个 skill 各自带一份时间轴内核（`lib.py` / `probe.py` / `beatmap.py` / `align.py`，逐字节相同），
所以可以单独安装、单独复制走，互不依赖。

## 安装

把 `song-reading/` 和 `song-to-mv/` 放进 agent 的 skills 目录：

```
~/.dsh/skills/song-reading/       # 全局
<project>/.dsh/skills/song-to-mv/ # 或项目内
```

目录结构必须是 `<skill-name>/SKILL.md`，`SKILL.md` 的 frontmatter 里写 `name` 和 `description` ——
agent 靠 description 决定什么时候加载哪个 skill。

## 依赖

- Python 3.9+
- `numpy`、`Pillow`
- `ffmpeg` / `ffprobe`（在 PATH 上，或用 `FFMPEG_PATH` / `FFPROBE_PATH` 指定）

Windows 上注意：WinGet 装的 ffmpeg 有时会在 PATH 留一个 0 字节的 stub，直接调用会报"不是有效应用程序"。
`lib.py` 里的 `find_binary()` 会先执行 `-version` 验证，再 locate 真正的二进制。

## 快速开始

```powershell
$SK = "<skill 目录>/song-to-mv"
$PY = python

& $PY "$SK/scripts/beatmap.py" song.mp3 --out work/song_map.json
& $PY "$SK/scripts/timeline.py" work/song_map.json --out work/timeline.png     # 先看一眼图
& $PY "$SK/scripts/align.py" work/song_map.json lyrics.lrc --out work/lyric_map.json
& $PY "$SK/scripts/shots.py" work/song_map.json work/lyric_map.json --out work/shotlist.json
& $PY "$SK/scripts/animatic.py" work/shotlist.json --audio song.mp3 --out work/animatic.mp4
```

解读：

```powershell
$SR = "<skill 目录>/song-reading"
& $PY "$SR/scripts/emotion.py" song.mp3 work/song_map.json --lyrics work/lyric_map.json --out work/emotion.json
& $PY "$SR/scripts/reading.py" pack work/song_map.json work/lyric_map.json work/emotion.json --out work/reading_input.json
# 读 reading_input.json，写 reading.json（这一步是模型的活）
& $PY "$SR/scripts/reading.py" render work/reading.json --pack work/reading_input.json --out work/解读.md
```

## 它们怎么工作

**时间轴内核**（两个 skill 共用）

1. 解码到 22050Hz 单声道，STFT，分频带做正向谱通量得到起音包络
2. FFT 自相关 + 谐波梳状打分估 BPM（带倍频修正）
3. Ellis 动态规划跟拍
4. 把起音峰按相位折叠到网格上做相干拟合，反推亚毫秒级周期 —— 跟拍器会把拍间隔量化到整帧，
   一首歌下来能漂半秒，这一步把它拉回来
5. 变点 DP（带复杂度惩罚）切段落；和声指纹判断重复段
6. 歌词：LRC 直接用时间戳；纯文本则在段落内按字重比例分配并吸附到拍点

**解读层**（song-reading）

六个声学轴：亮度、起音密度、动态、尾音残留、调性、人声。
arousal 混合能量/亮度/密度，valence 混合调式/亮度/动态；每个分数都带 `why`。
`reading.py` 分两段：`pack` 把"允许被引用的全部证据"打包，`render` 渲染解读稿，并**强制校验主题必须带证据**。

**生产层**（song-to-mv）

镜头网格有两种锚点：`bar`（切在小节线，适合有稳定脉动的歌）和 `line`（切在歌词句首，适合无鼓/rubato 的抒情歌）。
填充提示词后，`storyboard.py` 出人读分镜表，`animatic.py` 用代码画出每镜构图/景别/色板/时间码，配上音轨导出可播放的预演。

## 已知限制

- **没有 ASR**：不给 LRC 就只有节拍和段落；给纯文本歌词只有句级近似
- **4/4 假设**：3/4、6/8 拍会算错小节
- **恒定速度假设**：明显 rubato 的歌会触发 tracked 模式，此时改用 `anchor: "line"`
- **预演视频是示意图**，不是成片；写实画面需要图像/视频生成模型
- 不做人声分离、不做响度归一化

## 实测

一首 4:23 的流行民谣：BPM 误差 0.00、8 个段落、41 个镜头、100% 覆盖、自检 0 error。
在合成的已知真值曲上：段落边界 0.00s 误差、歌词句首中位误差 0.47s。

## License

MIT

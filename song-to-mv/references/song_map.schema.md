# song_map.json 字段语义

脚本产出、只读、可复现。同样的音频 + 参数必然得到同样的文件；除 `audio.path` 外没有随机成分。

```jsonc
{
  "version": 1,
  "audio": {
    "path": "D:/mv/song.mp3",
    "duration": 189.03,        // 秒，后面所有时间都以它为边界
    "sample_rate": 22050,      // 分析用采样率，不是原文件采样率
    "hash": "a1b2c3d4e5f6a7b8" // 源文件前 1MB 的 sha1，用来判断缓存是否失效
  },
  "grid": {
    "bpm": 92.0,
    "bpm_confidence": 1.0,     // < 0.15 说明自相关峰不明显，应让用户给 --bpm
    "beat_period": 0.6522,     // 秒/拍，已按相位相干拟合，不要自己从 bpm 反算
    "bar_seconds": 2.6087,     // 4 拍，即 4 * beat_period
    "beats_per_bar": 4,        // 固定 4/4
    "grid_mode": "regular",    // regular=恒定速度网格；tracked=检测到速度变化，退回跟拍
    "grid_fit_deviation": 0.06,// 跟踪拍点与拟合网格的中位偏差，秒
    "downbeat_phase": 1        // 网格中第几拍是小节第一拍，调试用
  },
  "beats": [0.0, 0.652, ...],  // 等间隔拍点；画面切点可落在这里
  "downbeats": [0.0, 2.609, ...], // 小节线；段落内分镜优先落在这里
  "bar_times": [0.0, 2.609, ...], // 等同 downbeats，末尾不含 duration
  "sections": [
    {
      "id": "S3",
      "label": "chorus",        // intro|verse|prechorus|chorus|bridge|interlude|outro
      "label_conf": 0.7,        // 启发式标签的置信度，低就人工改名
      "start": 62.608, "end": 104.347,
      "bars": 16,
      "energy": 0.47,           // 全曲归一化 RMS，0..1
      "vocal": 0.26,            // 该段人声活动占比，0..1
      "chroma": [0.0, 0.71, ...], // 12 维和声指纹，用于判断重复段
      "repeat_of": "S2",        // 可选：和前面某段和声高度相似
      "repeat_similarity": 0.91 // 可选
    }
  ],
  "vocal": {
    "curve_hz": 21.53,          // 曲线采样率，不是整数；索引换算务必用它
    "curve": [0.0, 1.0, ...],   // 0/1 人声活动
    "phrases": [[21.8, 22.78], ...] // 连续人声片段，用于判断对齐是否可信
  },
  "energy": {"hz": 10.0, "values": [0.0, 0.42, ...]}, // 归一化 RMS，画图/定光比用
  "warnings": ["…"]             // 非空时先读一遍再往下走
}
```

## 使用要点

- **切点用它，不用 BPM。** 直接用 `beats` / `downbeats` / `bar_times` 数组，别再拿 bpm 自己乘，网格已经拟合过、带相位。
- **先看 `grid_fit_deviation`。** 超过约 0.15s（相对 `beat_period`）说明这首歌唱的不是恒定速度——无鼓慢歌常见。这时小节线不可信，镜头改用 `anchor: "line"` 按句切。
- **section 首尾闭合。** `sections[0].start == 0`，`sections[-1].end == duration`，相邻首尾相接，可以直接当时间轴用。
- **`--sections` 有两种写法。** 数组 = 只给起点、吸附到小节线；`{"sections": [...]}` = 给完整 start/end、原样采用（`label_conf` 会是 1.0）。网格不可信时用后者。
- **`bars` 在 exact 模式下是估算值。** 由时长除以 `bar_seconds` 四舍五入得到，只作参考。
- **`repeat_of` 是变体指令，不是复制指令。** 同母题、换机位/时间/色温。
- **`vocal.curve` 是粗检。** 混音很厚的段落（大合唱、失真吉他）会漏检，不要用它当"有没有人唱"的真相；有 LRC 时以歌词为准。

## 已知偏差

| 项 | 精度 | 说明 |
|---|---|---|
| 拍点 | ±10ms | 相位相干拟合，恒定速度下很准 |
| 段落边界 | ±1 小节 | 边界只能落在小节线，本身受节拍网格约束 |
| BPM | ±0.05 | 报告值已按网格反算 |
| 段落标签 | 启发式 | `chorus` 认定依据是能量分位 + 人声占比，摇滚/电子容易误判 |

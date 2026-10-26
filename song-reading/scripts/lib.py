"""Shared helpers for the song-to-mv skill: ffmpeg discovery, audio decode, STFT, IO.

Only depends on numpy + the stdlib (no librosa/scipy), so it runs on the
bundled DSH interpreter out of the box.
"""
from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
import wave

import numpy as np

# Lyric text is usually non-ASCII; keep console output decodable regardless of
# the Windows code page.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # pragma: no cover - older/embedded interpreters
    pass

SR = 22050
N_FFT = 1024
HOP = 256


# ---------------------------------------------------------------- ffmpeg ----

def _probe_binary(path: str) -> bool:
    if not path or not os.path.isfile(path):
        return False
    if os.path.getsize(path) == 0:  # WinGet app-alias stubs are 0-byte files
        return False
    try:
        subprocess.run([path, "-version"], capture_output=True, timeout=20, check=False)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def find_binary(name: str = "ffmpeg") -> str:
    """Locate a *working* ffmpeg/ffprobe.

    On Windows a WinGet app-alias stub can sit on PATH as a 0-byte file that
    fails with 'not a valid application'; we therefore execute -version before
    trusting any candidate.
    """
    env_keys = ["DSH_" + name.upper(), name.upper() + "_PATH"]
    candidates = [os.environ.get(k, "") for k in env_keys]

    which = shutil.which(name)
    if which:
        candidates.append(which)

    roots = [
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WinGet", "Packages"),
        os.environ.get("ProgramFiles", ""),
        os.environ.get("ProgramFiles(x86)", ""),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs"),
    ]
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        pattern = os.path.join(root, "**", name + ".exe")
        candidates.extend(sorted(glob.glob(pattern, recursive=True))[:8])

    for cand in candidates:
        if _probe_binary(cand):
            return cand
    raise RuntimeError(
        f"no working {name} found. Set {name.upper()}_PATH to the executable, "
        f"or install ffmpeg. Tried: {[c for c in candidates if c]}"
    )


def run(cmd: list[str], timeout: int = 600) -> str:
    proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(
            f"command failed ({proc.returncode}): {' '.join(cmd)}\n{proc.stderr.decode('utf-8', 'replace')[:4000]}"
        )
    return proc.stdout.decode("utf-8", "replace")


def run_allow_error(cmd: list[str], timeout: int = 600) -> str:
    """Run a command and return combined stdout+stderr whatever the exit code."""
    proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
    return (proc.stdout + proc.stderr).decode("utf-8", "replace")


# ----------------------------------------------------------------- audio ----

def decode_mono(path: str, sr: int = SR) -> tuple[np.ndarray, int]:
    """Decode any ffmpeg-readable audio to a mono float32 buffer in [-1, 1]."""
    if not os.path.isfile(path):
        raise RuntimeError(f"audio file not found: {path}")
    ff = find_binary("ffmpeg")
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.close()
    try:
        run([ff, "-v", "error", "-y", "-i", path, "-vn", "-ac", "1", "-ar", str(sr),
             "-c:a", "pcm_s16le", tmp.name])
        with wave.open(tmp.name, "rb") as wf:
            n = wf.getnframes()
            raw = wf.readframes(n)
        x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
        return x, sr
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


def stft_mag(x: np.ndarray, n_fft: int = N_FFT, hop: int = HOP) -> np.ndarray:
    """Magnitude spectrogram (bins x frames), float32, chunked to stay in memory."""
    if x.size < n_fft:
        x = np.pad(x, (0, n_fft - x.size))
    pad = n_fft // 2
    xp = np.pad(x, (pad, pad + n_fft), mode="constant")
    n_frames = 1 + (xp.size - n_fft) // hop
    window = np.hanning(n_fft).astype(np.float32)
    out = np.empty((n_fft // 2 + 1, n_frames), dtype=np.float32)
    view = np.lib.stride_tricks.sliding_window_view(xp, n_fft)[::hop][:n_frames]
    step = 2048
    for i in range(0, n_frames, step):
        block = view[i:i + step] * window
        out[:, i:i + block.shape[0]] = np.abs(np.fft.rfft(block, axis=1)).T.astype(np.float32)
    return out


def frame_times(n_frames: int, sr: int = SR, hop: int = HOP) -> np.ndarray:
    return (np.arange(n_frames) * hop) / sr


def smooth(x: np.ndarray, win_sec: float, sr: int = SR, hop: int = HOP) -> np.ndarray:
    win = max(1, int(round(win_sec * sr / hop)))
    if win <= 1:
        return x
    kernel = np.hanning(win + 2)[1:-1]
    kernel /= kernel.sum()
    return np.convolve(x, kernel, mode="same")


def band_energy(spec: np.ndarray, lo: float, hi: float, sr: int = SR, n_fft: int = N_FFT) -> np.ndarray:
    freqs = np.fft.rfftfreq(n_fft, 1.0 / sr)
    idx = np.where((freqs >= lo) & (freqs < hi))[0]
    if idx.size == 0:
        return np.zeros(spec.shape[1], dtype=np.float32)
    return spec[idx].sum(axis=0)


def to_db(x: np.ndarray, floor: float = 1e-8) -> np.ndarray:
    return 20.0 * np.log10(np.maximum(x, floor))


# -------------------------------------------------------------------- io ----

def read_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8-sig") as fh:
        return json.load(fh)


def write_json(path: str, obj) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def fmt_ts(t: float) -> str:
    m = int(t // 60)
    s = t - 60 * m
    return f"{m:d}:{s:05.2f}"


def die(msg: str, code: int = 2) -> None:
    print(f"error: {msg}", file=sys.stderr)
    raise SystemExit(code)

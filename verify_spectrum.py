# -*- coding: utf-8 -*-
"""频谱验证：确认拿到的是真无损，不是 128k 升频的假无损"""
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

OUT = Path(r"D:\我的\程序\deepseek harness work\music-tool\下载")
TARGETS = [
    ("FLAC 无损", OUT / "鈴華ゆう子 - 永世のクレイドル.flac"),
    ("MP3 320k", OUT / "鈴華ゆう子 - 永世のクレイドル.mp3"),
    ("参照: 真128kMP3", None),
]


def spectrum_cutoff(path, seconds=20.0, start=60.0):
    cmd = ["ffmpeg", "-v", "error", "-ss", str(start), "-t", str(seconds),
           "-i", str(path), "-ac", "1", "-ar", "44100", "-f", "f32le", "-"]
    raw = subprocess.run(cmd, capture_output=True).stdout
    x = np.frombuffer(raw, dtype="<f4").astype(np.float64)
    if x.size < 44100:
        return None
    win = 8192
    n = x.size // win
    acc = np.zeros(win // 2 + 1)
    w = np.hanning(win)
    for i in range(n):
        seg = x[i * win:(i + 1) * win] * w
        acc += np.abs(np.fft.rfft(seg)) ** 2
    acc /= max(n, 1)
    db = 10 * np.log10(acc + 1e-20)
    db -= db.max()
    freqs = np.fft.rfftfreq(win, 1 / 44100)
    # 从高频往低频找第一个显著能量点
    idx = np.where(db > -85)[0]
    return freqs[idx[-1]] if idx.size else 0.0


print(f"{'文件':<16}{'频谱上限':>10}   判定")
print("-" * 52)
for name, p in TARGETS:
    if p is None:
        # 造一个真 128k 参照：把 flac 转 128k 再转回来测
        ref = OUT / "_ref_128k.mp3"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i",
                        str(TARGETS[0][1]), "-b:a", "128k", str(ref)],
                       capture_output=True)
        p, name = ref, "参照 128k"
    if not p.exists():
        print(f"{name:<16}{'--':>10}   文件缺失")
        continue
    cut = spectrum_cutoff(p)
    if cut is None:
        print(f"{name:<16}{'--':>10}   解码失败")
        continue
    if cut >= 20000:
        verdict = "真无损（CD 全频段）"
    elif cut >= 18000:
        verdict = "接近无损 / 高品质"
    elif cut >= 15000:
        verdict = "有损（约 320k 级别）"
    else:
        verdict = "低码率有损"
    print(f"{name:<16}{cut/1000:>8.1f} kHz   {verdict}")

for f in OUT.glob("_ref_128k.mp3"):
    f.unlink()

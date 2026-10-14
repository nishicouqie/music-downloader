# -*- coding: utf-8 -*-
"""校验下载结果：内嵌标签 / 封面 / 歌词时间轴 / 音频完整性"""
import os
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from mutagen.flac import FLAC
from mutagen.id3 import ID3

OUT = Path(r"D:\我的\程序\deepseek harness work\music-tool\下载")
FLAC_F = OUT / "鈴華ゆう子 - 永世のクレイドル.flac"
MP3_F = OUT / "鈴華ゆう子 - 永世のクレイドル.mp3"

TEXT_KEYS = {"LYRICS", "UNSYNCEDLYRICS", "LYRICS_ORIGINAL"}
SHORT = {"TIT2", "TPE1", "TALB", "TPE2", "TRCK", "TPOS", "TYER", "TCON",
         "COMM", "TXXX", "APIC", "USLT", "SYLT"}


def line(k, v, width=110):
    s = str(v).replace("\n", " / ")
    return f"  {k:<22} {s[:width]}{' …' if len(s) > width else ''}"


print("=" * 78)
print("FLAC:", FLAC_F.name, f"({os.path.getsize(FLAC_F)/1048576:.1f} MB)")
print("=" * 78)
f = FLAC(str(FLAC_F))
for k in sorted(f.keys()):
    v = f[k]
    if k in TEXT_KEYS:
        n = len(str(v).splitlines())
        print(line(k, f"<{n} 行歌词, {len(str(v))} 字>"))
    else:
        print(line(k, v))
for p in f.pictures:
    print(line("PICTURE", f"type={p.type} {p.mime} {p.width}x{p.height} "
                          f"{len(p.data)/1024:.0f}KB"))

print()
print("=" * 78)
print("MP3:", MP3_F.name, f"({os.path.getsize(MP3_F)/1048576:.1f} MB)")
print("=" * 78)
t = ID3(str(MP3_F))
for k in sorted(t.keys()):
    fr = t[k]
    if k.startswith("APIC"):
        print(line(k, f"{fr.mime} {len(fr.data)/1024:.0f}KB type={fr.type}"))
    elif k.startswith("USLT"):
        print(line(k, f"<{len(fr.text.splitlines())} 行, {len(fr.text)} 字>"))
    elif k.startswith("SYLT"):
        print(line(k, f"<{len(fr.text)} 条同步时间轴>"))
    elif k.startswith("TXXX"):
        print(line(k, fr.desc + "=" + ",".join(fr.text)))
    else:
        print(line(k, ",".join(str(x) for x in fr.text) if hasattr(fr, "text") else fr))

sylt = t.getall("SYLT")
if sylt:
    print("\n  SYLT 前 6 条:", [(ms, txt) for txt, ms in sylt[0].text[:6]])
    print("  SYLT 末 3 条:", [(ms, txt) for txt, ms in sylt[0].text[-3:]])
    # 时间轴单调性
    times = [ms for _, ms in sylt[0].text]
    print("  时间轴递增:", times == sorted(times), f"| 首 {times[0]}ms 末 {times[-1]}ms")

print()
print("=" * 78)
print("音频完整性（整轨解码到底，检查是否有损坏）")
print("=" * 78)
for p in (FLAC_F, MP3_F):
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(p), "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    status = "解码无错误" if not r.stderr.strip() else "有报错:" + r.stderr[:300]
    print(f"  {p.suffix[1:].upper():5s} {status}")

print()
print("=" * 78)
print("导出内嵌封面（人工确认用）")
print("=" * 78)
cov = OUT / "_校验_内嵌封面.jpg"
cov.write_bytes(f.pictures[0].data)
from PIL import Image
im = Image.open(cov)
print(f"  封面已导出: {cov.name}  {im.size[0]}x{im.size[1]}px  {im.mode}")

lrc = FLAC_F.with_suffix(".lrc")
print(f"\n旁挂歌词 {lrc.name}: {'存在' if lrc.exists() else '缺失'} "
      f"({len(lrc.read_text(encoding='utf-8').splitlines()) if lrc.exists() else 0} 行)")

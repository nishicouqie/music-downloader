# -*- coding: utf-8 -*-
"""生成应用图标 icon.ico（白色音符 + 靛蓝圆角底）"""
from PIL import Image, ImageDraw

ACCENT = (79, 70, 229, 255)      # #4f46e5
ACCENT2 = (99, 91, 240, 255)
WHITE = (255, 255, 255, 255)


def make(size):
    S = size * 4                       # 4 倍超采样再缩小，边缘更干净
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # 圆角底 + 竖向渐变
    radius = int(S * 0.235)
    grad = Image.new("RGBA", (S, S), ACCENT)
    gd = ImageDraw.Draw(grad)
    for y in range(S):
        t = y / S
        gd.line([(0, y), (S, y)], fill=(
            int(ACCENT[0] + (ACCENT2[0] - ACCENT[0]) * t),
            int(ACCENT[1] + (ACCENT2[1] - ACCENT[1]) * t),
            int(ACCENT[2] + (ACCENT2[2] - ACCENT[2]) * t), 255))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, S - 1, S - 1],
                                           radius=radius, fill=255)
    img.paste(grad, (0, 0), mask)

    # 双音符：两个符头 + 两条符干 + 一条符杠
    lx, ly, r = S * 0.295, S * 0.735, S * 0.128
    rx, ry = S * 0.700, S * 0.655
    stem_w = S * 0.062
    ltop, rtop = S * 0.300, S * 0.220

    d.ellipse([lx - r, ly - r * 0.86, lx + r, ly + r * 0.86], fill=WHITE)
    d.ellipse([rx - r, ry - r * 0.86, rx + r, ry + r * 0.86], fill=WHITE)
    d.rounded_rectangle([lx + r * 0.42, ltop, lx + r * 0.42 + stem_w, ly],
                        radius=stem_w / 2, fill=WHITE)
    d.rounded_rectangle([rx + r * 0.42, rtop, rx + r * 0.42 + stem_w, ry],
                        radius=stem_w / 2, fill=WHITE)
    d.polygon([(lx + r * 0.42, ltop),
               (rx + r * 0.42 + stem_w, rtop),
               (rx + r * 0.42 + stem_w, rtop + S * 0.105),
               (lx + r * 0.42, ltop + S * 0.115)], fill=WHITE)

    return img.resize((size, size), Image.LANCZOS)


sizes = [16, 24, 32, 48, 64, 128, 256]
imgs = [make(s) for s in sizes]
imgs[-1].save("icon.ico", format="ICO",
              sizes=[(s, s) for s in sizes], append_images=imgs[:-1])
imgs[-1].save("icon.png")
print("已生成 icon.ico / icon.png，尺寸:", sizes)

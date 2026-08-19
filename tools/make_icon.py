# -*- coding: utf-8 -*-
"""KimiQ 图标生成：奶白球 + 俩黑眼睛（与托盘图标同一套视觉）。
输出 A/B 两个候选到 assets\\，选中的改名为 icon.ico 后被 KimiQ.spec 引用。
改参数重跑即可重新生成。"""
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "assets"
OUT.mkdir(exist_ok=True)

BALL = "#F3F0EA"      # 球体奶白（作者默认色系）
EDGE = "#D9D3C7"      # 浅描边：白底图里把球轮廓托出来
EYE = "#1A1A1A"
SIZES = [16, 24, 32, 48, 64, 128, 256]


def render(size, draw_fn):
    """4 倍超采样画完再缩，抗锯齿"""
    big = size * 4
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw_fn(ImageDraw.Draw(img), big)
    return img.resize((size, size), Image.LANCZOS)


def variant_ball(d, s):
    """A：透明底 + 奶白球 + 黑眼睛（= 桌面上的球本体）"""
    m = int(s * 0.04)                     # 球贴边留 4%
    d.ellipse([m, m, s - m, s - m], fill=BALL, outline=EDGE, width=max(2, s // 128))
    for cx in (0.36, 0.64):               # 两眼位置（对齐 make_icon 的比例）
        w, h = s * 0.10, s * 0.17
        d.ellipse([s * cx - w / 2, s * 0.40 - h / 2,
                   s * cx + w / 2, s * 0.40 + h / 2], fill=EYE)


def variant_tile(d, s):
    """B：白底圆角块 + 直接画俩黑眼睛（极简脸）"""
    r = int(s * 0.22)
    d.rounded_rectangle([0, 0, s - 1, s - 1], radius=r, fill="#FFFFFF")
    for cx in (0.35, 0.65):
        w, h = s * 0.11, s * 0.20
        d.ellipse([s * cx - w / 2, s * 0.42 - h / 2,
                   s * cx + w / 2, s * 0.42 + h / 2], fill=EYE)


def make(name, draw_fn):
    imgs = [render(sz, draw_fn) for sz in SIZES]
    imgs[-1].save(OUT / ("preview_%s.png" % name))
    imgs[0].save(OUT / ("icon_%s.ico" % name),
                 sizes=[(im.width, im.height) for im in imgs],
                 append_images=imgs[1:])
    print("生成", name)


if __name__ == "__main__":
    make("a", variant_ball)
    make("b", variant_tile)

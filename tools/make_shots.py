# -*- coding: utf-8 -*-
"""KimiQ README 图鉴截图工具：抓真球 6 个代表状态拼成 docs/emotions.png。

流程：临时把球调大（拍完恢复原尺寸）→ 重启 exe → HTTP 逐状态驱动 →
PrintWindow 抓窗（BitBlt/ImageGrab 对硬件加速分层窗口是瞎的，别用）→
透明合成白底拼 3×2 图。依赖 PIL；拍摄的几秒里别动鼠标。
"""
import ctypes
import json
import os
import subprocess
import time
import urllib.request
from ctypes import wintypes
from ctypes import byref
from ctypes import windll
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
CONF_PATH = Path(os.environ["APPDATA"]) / "KimiQ" / "config.json"
EXE = ROOT / "dist" / "KimiQ.exe"
OUT = ROOT / "docs" / "emotions.png"

# (表情id, 图鉴标签, 触发后等几秒再截)：33 要等撒花飞起来，其余等弹簧动画稳下来
SHOTS = [("02", "standby", 1.0), ("30", "thinking", 1.0), ("40", "reading", 1.0),
         ("33", "done!", 0.5), ("21", "tilted", 1.0), ("14", "shy", 1.0)]

SHOOT_SIZE = 150          # 拍摄用球径（大点截图清楚），拍完恢复原值
CELL = 200                # 图鉴格子边长
PORT = 28765

u32 = windll.user32
gdi = windll.gdi32


def http(path):
    urllib.request.urlopen("http://127.0.0.1:%d%s" % (PORT, path), timeout=3).read()


def wait_alive(timeout=30):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            http("/sound?play=none")
            return True
        except Exception:
            time.sleep(0.5)
    return False


def pet_pid():
    out = subprocess.check_output(
        ["powershell", "-NoProfile", "-Command",
         "(Get-NetTCPConnection -LocalPort %d -State Listen"
         " -ErrorAction SilentlyContinue).OwningProcess" % PORT], text=True)
    return int(out.strip())


def restart_pet():
    subprocess.run(["powershell", "-NoProfile", "-Command",
                    "Get-Process | Where-Object { $_.Path -like '*KimiQ*' } "
                    "| ForEach-Object { Stop-Process -Id $_.Id -Force }"],
                   capture_output=True)
    time.sleep(1.5)
    subprocess.run(["powershell", "-NoProfile", "-Command",
                    "Start-Process '%s' -WorkingDirectory '%s'" % (EXE, EXE.parent)],
                   capture_output=True)
    if not wait_alive():
        raise RuntimeError("桌宠没起来")


def set_ball_size(px):
    conf = json.loads(CONF_PATH.read_text(encoding="utf-8"))
    conf["size"] = px
    CONF_PATH.write_text(json.dumps(conf, ensure_ascii=False, indent=1),
                         encoding="utf-8")


def grab_pet():
    """PrintWindow 抓桌宠窗口内容，透明区合成白底返回 RGB 图"""
    pid = pet_pid()
    hits = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, lp):
        p = wintypes.DWORD()
        u32.GetWindowThreadProcessId(hwnd, byref(p))
        if p.value == pid and u32.IsWindowVisible(hwnd):
            r = wintypes.RECT()
            u32.GetWindowRect(hwnd, byref(r))
            if 200 < r.right - r.left < 600:
                hits.append((hwnd, r))
        return True

    u32.EnumWindows(cb, 0)
    if not hits:
        raise RuntimeError("找不到桌宠窗口")
    hwnd, r = hits[0]
    w, h = r.right - r.left, r.bottom - r.top
    hdc = u32.GetWindowDC(hwnd)
    mem = gdi.CreateCompatibleDC(hdc)
    bmp = gdi.CreateCompatibleBitmap(hdc, w, h)
    gdi.SelectObject(mem, bmp)
    if not u32.PrintWindow(hwnd, mem, 2):   # PW_RENDERFULLCONTENT
        raise RuntimeError("PrintWindow 失败")

    class H(ctypes.Structure):
        _fields_ = [("s", wintypes.DWORD), ("w", wintypes.LONG), ("h", wintypes.LONG),
                    ("pl", wintypes.WORD), ("bc", wintypes.WORD), ("c", wintypes.DWORD),
                    ("i", wintypes.DWORD), ("x", wintypes.LONG), ("y", wintypes.LONG),
                    ("u", wintypes.DWORD), ("m", wintypes.DWORD)]

    class BI(ctypes.Structure):
        _fields_ = [("hdr", H), ("colors", wintypes.DWORD * 3)]

    bi = BI()
    bi.hdr.s = 40
    bi.hdr.w = w
    bi.hdr.h = -h
    bi.hdr.pl = 1
    bi.hdr.bc = 32
    buf = (ctypes.c_char * (w * h * 4))()
    gdi.GetDIBits(mem, bmp, 0, h, buf, byref(bi), 0)
    gdi.DeleteObject(bmp)
    gdi.DeleteDC(mem)
    u32.ReleaseDC(hwnd, hdc)
    img = Image.frombuffer("RGBA", (w, h), bytes(buf), "raw", "BGRA", 0, 1)
    # PrintWindow 把透明区渲染成不透明黑（alpha 全是 255）：按"近黑"抠回透明
    if img.getextrema()[3][0] == 255:
        from PIL import ImageChops
        r_, g_, b_, _ = img.split()
        darkest = ImageChops.darker(ImageChops.darker(r_, g_), b_)  # 三通道最小值
        img.putalpha(darkest.point(lambda v: 0 if v < 12 else 255))
    bg = Image.new("RGBA", (w, h), (255, 255, 255, 255))
    bg.alpha_composite(img)
    return bg.convert("RGB")


def shoot_all():
    imgs = []
    for emo, label, settle in SHOTS:
        time.sleep(1.4)          # 等桌宠最小停留闸门放开，保证状态真的切换
        http("/state?to=" + emo)
        time.sleep(settle)
        img = grab_pet()
        cx, cy = img.width // 2, img.height // 2
        imgs.append((label, img.crop((cx - CELL // 2, cy - CELL // 2,
                                      cx + CELL // 2, cy + CELL // 2))))
        print("截到", label)
    return imgs


def montage(imgs):
    cols, rows, pad, band = 3, 2, 14, 30
    W = cols * CELL + (cols + 1) * pad
    H = rows * (CELL + band) + (rows + 1) * pad
    canvas = Image.new("RGB", (W, H), "#FFFFFF")
    font = ImageFont.load_default(18)
    for i, (label, img) in enumerate(imgs):
        x = pad + (i % cols) * (CELL + pad)
        y = pad + (i // cols) * (CELL + band + pad)
        canvas.paste(img, (x, y))
        tw = font.getbbox(label)[2]
        ImageDraw.Draw(canvas).text((x + (CELL - tw) // 2, y + CELL + 6),
                                    label, fill="#555555", font=font)
    canvas.save(OUT)
    print("输出", OUT)


def main():
    conf = json.loads(CONF_PATH.read_text(encoding="utf-8"))
    old_size = conf.get("size", 120)
    try:
        set_ball_size(SHOOT_SIZE)
        restart_pet()
        time.sleep(8)            # exe 启动 + 页面渲染余量
        montage(shoot_all())
    finally:
        set_ball_size(old_size)  # 无论成败都把小土的球径恢复
        restart_pet()
        print("已恢复原球径", old_size)


if __name__ == "__main__":
    main()

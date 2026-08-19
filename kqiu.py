# -*- coding: utf-8 -*-
"""K球 · Kimi 桌面宠物 v1
无边框透明置顶小球：呼吸眨眼、鼠标注视、点击互动、
本地 HTTP 接口切换状态（任务完成提示等）。
用法：
  python kqiu.py            # 正常运行
  python kqiu.py --render out.png   # 离屏渲染状态图鉴（调试用）
HTTP 接口（127.0.0.1:28765）：
  /state?to=idle|working|done|sleepy|love   切换状态
  /sound?play=done|click                    只播音效
"""
import math
import random
import sys
import threading
import time
import winsound
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

from PySide6.QtCore import QPoint, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QRadialGradient, QCursor
from PySide6.QtWidgets import QApplication, QWidget

KIMI_BLUE = QColor(106, 92, 255)
KIMI_DEEP = QColor(74, 47, 208)
KIMI_CYAN = QColor(34, 211, 238)
CONFETTI_COLORS = [KIMI_BLUE, KIMI_CYAN, QColor(255, 176, 32), QColor(255, 92, 138), QColor(52, 211, 153)]

SOUNDS = {
    "done":  [(880, 90), (1109, 90), (1319, 90), (1760, 160)],
    "click": [(1568, 60)],
    "love":  [(1319, 70), (1760, 90)],
    "work":  [(988, 60), (988, 60)],
}


def play_sound(name):
    def _run():
        try:
            for freq, dur in SOUNDS.get(name, []):
                winsound.Beep(freq, dur)
                time.sleep(0.03)
        except Exception:
            pass
    threading.Thread(target=_run, daemon=True).start()


class Ball(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.resize(170, 190)

        self.state = "idle"          # idle/working/done/sleepy/love
        self.t = 0.0                 # 全局时间（秒）
        self.blink_until = 0.0
        self.next_blink = 3.0
        self.jump_t = -1.0           # done 跳跃起始
        self.particles = []          # 彩带
        self.drag_off = QPoint(0, 0)
        self.zzz_phase = 0.0

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(30)

    # ---------- 状态控制 ----------
    def set_state(self, s):
        if s not in ("idle", "working", "done", "sleepy", "love"):
            return
        self.state = s
        if s == "done":
            self.jump_t = self.t
            self.spawn_confetti()
            play_sound("done")
        elif s == "love":
            play_sound("love")

    def spawn_confetti(self):
        self.particles.clear()
        for _ in range(60):
            ang = random.uniform(0, math.pi * 2)
            spd = random.uniform(2.2, 5.5)
            self.particles.append({
                "x": 85.0, "y": 80.0,
                "vx": math.cos(ang) * spd, "vy": math.sin(ang) * spd - 3.5,
                "rot": random.uniform(0, 360), "vr": random.uniform(-9, 9),
                "w": random.uniform(4, 8), "h": random.uniform(6, 12),
                "color": random.choice(CONFETTI_COLORS), "life": random.uniform(1.2, 2.0),
            })

    # ---------- 主循环 ----------
    def tick(self):
        dt = 0.03
        self.t += dt
        if self.t > self.next_blink:
            self.blink_until = self.t + 0.12
            self.next_blink = self.t + random.uniform(2.5, 5.0)
        if self.state == "sleepy":
            self.zzz_phase += dt
        for p in self.particles:
            p["x"] += p["vx"]; p["y"] += p["vy"]; p["vy"] += 0.12
            p["rot"] += p["vr"]; p["life"] -= dt
        self.particles = [p for p in self.particles if p["life"] > 0]
        self.update()

    # ---------- 绘制 ----------
    def gaze_offset(self):
        gp = self.mapFromGlobal(QCursor.pos())
        dx = (gp.x() - 85) / 85.0
        dy = (gp.y() - 80) / 80.0
        m = max(1.0, math.hypot(dx, dy) / 4.0)
        return dx / m * 4.0, dy / m * 4.0

    def jump_offset(self):
        if self.jump_t < 0:
            return 0.0
        dt = self.t - self.jump_t
        if dt > 1.0:
            self.jump_t = -1.0
            return 0.0
        return -abs(math.sin(dt * math.pi * 3)) * 18 * (1 - dt)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        breathe = math.sin(self.t * 2.0) * 2.0
        cy = 80 + self.jump_offset()
        squash = 1.0 + breathe * 0.008

        # 影子
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 60))
        p.drawEllipse(QRectF(85 - 44, 158, 88, 12))

        # 身体（径向渐变球）
        grad = QRadialGradient(70, 55 + cy, 90, 85, 75 + cy)
        grad.setColorAt(0.0, KIMI_BLUE.lighter(135))
        grad.setColorAt(0.65, KIMI_BLUE)
        grad.setColorAt(1.0, KIMI_DEEP)
        p.setBrush(grad)
        p.drawEllipse(QRectF(85 - 55 * squash, cy - 55 / squash, 110 * squash, 110 / squash))

        gx, gy = self.gaze_offset()
        blinking = self.t < self.blink_until
        self.draw_face(p, cy, gx, gy, blinking)

        # working 旋转弧
        if self.state == "working":
            pen = QPen(KIMI_CYAN, 5)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            start = int((self.t * 240) % 360) * 16
            p.drawArc(QRectF(85 - 66, cy - 66, 132, 132), start, 100 * 16)

        # sleepy 的 Zzz
        if self.state == "sleepy":
            p.setPen(QColor(200, 210, 255))
            p.setFont(QFont("Segoe UI", 16 + int(4 * math.sin(self.zzz_phase * 2)), QFont.Bold))
            zy = 20 - (self.zzz_phase * 8) % 20
            p.drawText(QPoint(120, int(30 - zy)), "Z")
            p.drawText(QPoint(132, int(20 - zy)), "z")

        # 彩带
        for c in self.particles:
            p.save()
            p.translate(c["x"], c["y"])
            p.rotate(c["rot"])
            p.setPen(Qt.NoPen)
            p.setBrush(c["color"])
            p.drawRect(QRectF(-c["w"] / 2, -c["h"] / 2, c["w"], c["h"]))
            p.restore()
        p.end()

    def draw_face(self, p, cy, gx, gy, blinking):
        eye_y = cy - 8 + gy * 0.8
        eye_dx = 20
        if self.state == "sleepy" or blinking:
            # 闭眼：两条弧线
            pen = QPen(QColor(20, 16, 48), 4)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            for ex in (85 - eye_dx, 85 + eye_dx):
                p.drawArc(QRectF(ex - 10, eye_y - 4, 20, 12), 200 * 16, 140 * 16)
            return
        for ex in (85 - eye_dx, 85 + eye_dx):
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255))
            p.drawEllipse(QRectF(ex - 11, eye_y - 13, 22, 26))
            p.setBrush(QColor(20, 16, 48))
            if self.state == "done":
                # 星星眼
                self.star(p, ex + gx * 0.5, eye_y + gy * 0.5, 7)
            else:
                p.drawEllipse(QRectF(ex - 5 + gx, eye_y - 5 + gy, 10, 12))
                p.setBrush(QColor(255, 255, 255))
                p.drawEllipse(QRectF(ex - 3 + gx, eye_y - 4 + gy, 3, 4))

        # 腮红
        blush_a = 120 if self.state == "love" else 60
        p.setBrush(QColor(255, 120, 160, blush_a))
        p.drawEllipse(QRectF(85 - 38, cy + 10, 16, 9))
        p.drawEllipse(QRectF(85 + 22, cy + 10, 16, 9))

        # 嘴
        pen = QPen(QColor(20, 16, 48), 4)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        if self.state in ("done", "love"):
            p.setBrush(QColor(20, 16, 48))
            p.drawEllipse(QRectF(85 - 10, cy + 14, 20, 16))  # 张嘴笑
        elif self.state == "working":
            p.setBrush(QColor(20, 16, 48))
            p.drawEllipse(QRectF(85 - 7, cy + 16, 14, 14))  # o 嘴
        else:
            p.setBrush(Qt.NoBrush)
            p.drawArc(QRectF(85 - 12, cy + 12, 24, 14), 200 * 16, 140 * 16)  # 微笑

    def star(self, p, cx, cy, r):
        from PySide6.QtGui import QPolygonF
        from PySide6.QtCore import QPointF
        pts = []
        for i in range(10):
            ang = -math.pi / 2 + i * math.pi / 5
            rr = r if i % 2 == 0 else r * 0.45
            pts.append(QPointF(cx + rr * math.cos(ang), cy + rr * math.sin(ang)))
        p.setBrush(QColor(255, 200, 60))
        p.setPen(Qt.NoPen)
        p.drawPolygon(QPolygonF(pts))

    # ---------- 互动 ----------
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.drag_off = e.globalPosition().toPoint() - self.pos()
            if self.state != "done":
                self.set_state("love")

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.LeftButton:
            self.move(e.globalPosition().toPoint() - self.drag_off)

    def mouseReleaseEvent(self, e):
        if self.state == "love":
            self.set_state("idle")


# ---------- HTTP 接口 ----------
BALL = None

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        q = parse_qs(urlparse(self.path).query)
        if self.path.startswith("/state") and BALL:
            BALL.set_state(q.get("to", [""])[0])
            self.reply(b'{"ok":true}')
        elif self.path.startswith("/sound"):
            play_sound(q.get("play", [""])[0])
            self.reply(b'{"ok":true}')
        else:
            self.reply(b'{"ok":false}')

    def reply(self, body):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def render_sheet(path):
    """离屏渲染 5 状态图鉴"""
    from PySide6.QtGui import QImage
    app = QApplication(sys.argv)
    states = ["idle", "working", "done", "sleepy", "love"]
    img = QImage(170 * len(states), 190, QImage.Format_ARGB32)
    img.fill(QColor(24, 26, 34))
    w = Ball()
    for i, s in enumerate(states):
        w.state = s
        w.t = 1.0
        if s == "done":
            w.jump_t = 0.5
        frame = QImage(170, 190, QImage.Format_ARGB32)
        frame.fill(0)
        w.render(frame, QPoint(0, 0))
        qp = QPainter(img)
        qp.drawImage(170 * i, 0, frame)
        qp.setPen(QColor(180, 190, 210))
        qp.setFont(QFont("Segoe UI", 11))
        qp.drawText(170 * i + 60, 180, s)
        qp.end()
    img.save(path)
    print("rendered:", path)


def main():
    global BALL
    if "--render" in sys.argv:
        out = sys.argv[sys.argv.index("--render") + 1]
        render_sheet(out)
        return
    app = QApplication(sys.argv)
    BALL = Ball()
    BALL.move(app.primaryScreen().geometry().right() - 220,
              app.primaryScreen().geometry().bottom() - 260)
    BALL.show()
    server = HTTPServer(("127.0.0.1", 28765), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print("K球 running · HTTP 127.0.0.1:28765/state?to=done")
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

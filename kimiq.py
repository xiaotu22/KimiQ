# -*- coding: utf-8 -*-
"""KimiQ · Kimi 桌面宠物 v2.1
白底纯白球桌面宠物：完整复用 sam70361/emotion-ball 引擎与 32 套表情
（QWebEngineView 内嵌 harness.html，不重绘），Python 只管窗体壳、
本地计时、设置与 HTTP 控制接口，供 Kimi Code hooks 实时驱动。

用法：
  python kimiq.py             # 正常运行（无边框/透明/置顶）
  python kimiq.py --gallery   # 图鉴模式：自动巡演 32 套表情，←/→ 翻页，空格停/启

HTTP 接口（127.0.0.1:28765）：
  /state?to=<emotionId 或语义名>   如 to=33 / to=done / to=think
  /sound?play=done|click|love|work 只播音效

交互：拖动=移动窗口；拖到屏幕边缘松手=贴边隐藏（悬停探出，拖回还原）；
      按住不动=摸头害羞(14)；单击=甩彩带；双击=满意(19)；
      睡眠时鼠标靠近=惊醒(07)；右键/托盘=设置·图鉴·退出。
"""
import json
import os
import random
import re
import shutil
import subprocess
import sys
import threading
import time
import winreg
import winsound
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from PySide6.QtCore import (QObject, QPoint, QPropertyAnimation, QRect, Qt,
                            QTimer, QEasingCurve, Signal, Slot, Property)
from PySide6.QtGui import (QAction, QActionGroup, QColor, QCursor, QIcon,
                           QPainter, QPixmap, QRegion)
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog,
                               QHBoxLayout, QLabel, QMenu, QPushButton,
                               QSlider, QSystemTrayIcon, QVBoxLayout)

# 打包后资源在 sys._MEIPASS，开发时在脚本同目录
BASE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
HARNESS = BASE_DIR / "harness.html"

GALLERY_W, GALLERY_H = 360, 360   # 图鉴模式固定大窗
WIN_SIZE = 320                    # 主窗固定尺寸，永不动
SIZE_MIN, SIZE_MAX = 30, 160      # 球大小可调范围（小土拍板：30~160）
MINI_EXPOSE = 34                  # 贴边隐藏时露出的宽度(px)
MINI_PAD = 12                     # mini 缩窗时球外的留白(px)

# ---------------- 配置持久化（%APPDATA%\KimiQ\config.json） ----------------
CONF_PATH = Path(os.environ.get("APPDATA") or Path.home()) / "KimiQ" / "config.json"
# 改名前（kqiu 时代）留下的旧配置：新路径没有就搬过来
_OLD_CONF_PATH = Path(os.environ.get("APPDATA") or Path.home()) / "kqiu" / "config.json"
CONF = {
    "size": 120,        # 球窗尺寸，范围 SIZE_MIN~SIZE_MAX
    "sound": True,      # 音效开关
    "ontop": True,      # 置顶开关
    "pos": None,        # 窗口位置 [x, y]（位置记忆）
    "mini": None,       # 贴边隐藏 "left"/"right"/None
    "shape": "blob",    # 身体形状 blob 圆胖 / wedge 三角 / gem 菱形
    "sketch": False,    # 线稿模式
    "dnd": False,       # 勿扰模式：hooks 事件静默，球一直睡
    "bubble": "auto",   # 气泡模式：auto 状态变化自动浮现 / hover 仅悬停 / off 关闭
    "bubble_pos": "top",# 气泡位置：top 球顶 / side 球旁
    "detail": True,     # 气泡详细状态：气泡里拼上 Kimi 正在做什么的实时文本
    "autostart": False, # 开机自启（注册表 Run 项）
    "launch_kimi": True,# 启动 KimiQ 时若 Kimi CLI 没在跑，开个终端拉起它
}


def load_conf():
    try:
        if not CONF_PATH.exists() and _OLD_CONF_PATH.exists():
            CONF_PATH.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(_OLD_CONF_PATH, CONF_PATH)     # 旧配置搬家
        data = json.loads(CONF_PATH.read_text(encoding="utf-8"))
        for k in CONF:
            if k in data:
                CONF[k] = data[k]
        # 范围收紧后旧配置可能超界，夹紧
        CONF["size"] = max(SIZE_MIN, min(SIZE_MAX, int(CONF["size"])))
        # 旧配置没有/乱写的气泡项，回默认
        if CONF["bubble"] not in ("auto", "hover", "off"):
            CONF["bubble"] = "auto"
        if CONF["bubble_pos"] not in ("top", "side"):
            CONF["bubble_pos"] = "top"
    except Exception:
        pass   # 配置丢了就用默认，不耽误启动


def save_conf():
    try:
        CONF_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONF_PATH.write_text(json.dumps(CONF, ensure_ascii=False, indent=1),
                             encoding="utf-8")
    except Exception:
        pass

# ---------------- 状态映射（任务书第 5 节） ----------------
NAME_TO_ID = {
    "recv": "31", "receive": "31",          # 收到新任务
    "think": "30", "thinking": "30",        # 思考/规划
    "web": "36", "search": "36",            # 联网搜索
    "read": "40", "retrieve": "40",         # 读文件/检索代码
    "exec": "32", "work": "32", "busy": "32",   # 执行中
    "reply": "39", "output": "39",          # 写回复
    "done": "33",                           # 任务完成
    "error": "34", "fail": "34",            # 出错
    "deny": "38", "denied": "38",           # 权限被拒
    "stop": "41",                           # 中途停止
    "wait": "35", "waiting": "35",          # 等待输入
    "tired": "15",                          # 疲惫彩蛋
    "idle": "02", "dormant": "06", "sleep": "00",
    "happy": "10", "shy": "14", "content": "19",
}
# 连续工作计时覆盖这些状态（>30min 触发 15 疲惫彩蛋）
WORK_IDS = {"30", "31", "32", "36", "39", "40"}
SLEEP_IDS = {"06", "00"}

IDLE_STANDBY_S = 600      # 空闲 10min → 02 待机放空
IDLE_DORMANT_S = 1800     # 空闲 30min → 06 休眠
IDLE_SLEEP_S = 2100       # 空闲 35min → 00 睡眠
TIRED_AFTER_S = 1800      # 连续工作 30min → 15 疲惫（彩蛋）

MIN_DWELL_S = 1.2   # 状态最小停留秒数：hooks 连发时暂存去抖（只留最新），防表情乱闪
# 内心戏轮播：基础工作态停留过久就从表情池摇一个（含本体，可摇回），
# 真实 hooks 事件到达立即重锚或打断——轮播只是"等待时的内心戏"，不抢戏
VARIETY = {
    "30": ["16", "03", "20", "37", "04"],   # 思考久了：专注/好奇/困惑/回忆/发呆
    "40": ["37", "03"],                     # 翻资料久了：回忆/好奇
    "32": ["16"],                           # 干活久了：转专注
    "35": ["11", "18"],                     # 等输入久了：疑惑/无奈
}
VARIETY_AFTER_S = {"30": 6, "40": 5, "32": 7, "35": 8}   # 各基础态摇一次的停留间隔

TRANSIENT = {"01": 6, "05": 5}   # 过场表情（开机唤醒/勿扰解除）：到点没人接班自回 02

# 待命自言自语台词池（表情, 气泡文案）：自言自语 / 自己玩 / 关心主人三类，
# 把 10~21 情绪区的表情全盘活；只在 02 待机窗口出现，不打扰工作与睡眠
IDLE_CHATTER = [
    ("03", "咦？那是什么"),
    ("04", "发呆是一种修行"),
    ("10", "嘿嘿，心情不错"),
    ("11", "主人去哪儿了？"),
    ("12", "主人还没回来…"),
    ("13", "哇！…哦看错了"),
    ("16", "偷偷练习新表情"),
    ("18", "唉，没人理我"),
    ("20", "球生的意义…"),
    ("37", "想起主人的夸奖"),
    ("10", "主人加油！"),
    ("04", "喝口水休息下吧"),
]

# 悬停气泡文本：覆盖全部 32 套表情（Kimi 工作态与任务书第 5 节映射一一对应）
STATUS_TEXT = {
    "00": "Kimi：睡着了",
    "01": "KimiQ：醒来了",
    "02": "Kimi：待命中",
    "03": "KimiQ：好奇？",
    "04": "KimiQ：发呆中…",
    "05": "KimiQ：苏醒中…",
    "06": "Kimi：休眠中",
    "07": "KimiQ：被吵醒了…",
    "10": "开心！",
    "11": "KimiQ：疑惑？",
    "12": "KimiQ：失落…",
    "13": "KimiQ：惊讶！",
    "14": "嘿嘿…",
    "15": "Kimi：有点累了…",
    "16": "Kimi：专注中…",
    "17": "Kimi：有点慌…",
    "18": "KimiQ：无奈…",
    "19": "满意！",
    "20": "KimiQ：困惑？",
    "21": "Kimi：红温了！",
    "30": "Kimi：思考中…",
    "31": "Kimi：收到任务",
    "32": "Kimi：干活中…",
    "33": "Kimi：搞定了 ✓",
    "34": "Kimi：出错了",
    "35": "Kimi：等你输入",
    "36": "Kimi：联网搜索…",
    "37": "Kimi：回忆中…",
    "38": "Kimi：权限被拒",
    "39": "Kimi：写回复…",
    "40": "Kimi：翻资料…",
    "41": "Kimi：已停止",
}

SOUNDS = {
    "done":  [(880, 90), (1109, 90), (1319, 90), (1760, 160)],
    "click": [(1568, 60)],
    "love":  [(1319, 70), (1760, 90)],
    "work":  [(988, 60), (988, 60)],
}


def play_sound(name):
    if not CONF.get("sound", True) or CONF.get("dnd"):
        return
    def _run():
        try:
            for freq, dur in SOUNDS.get(name, []):
                winsound.Beep(freq, dur)
                time.sleep(0.03)
        except Exception:
            pass
    threading.Thread(target=_run, daemon=True).start()


def resolve_emotion(to):
    """emotionId（如 '33'）原样返回；语义名查表；都不认识返回 None"""
    if not to:
        return None
    to = to.strip().lower()
    if to.isdigit() and len(to) <= 2:
        return to.zfill(2)
    return NAME_TO_ID.get(to)


class TransparentPage(QWebEnginePage):
    """背景透明的 page，让桌面透过网页留白处显示"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setBackgroundColor(QColor(Qt.transparent))


class Bridge(QObject):
    """JS → Python 桥：拖拽移动窗口 / 交互上报 / 状态回报 / 右键菜单"""
    def __init__(self, win):
        super().__init__()
        self.win = win
        self._drag_off = QPoint(0, 0)

    @Slot(float, float)
    def dragStart(self, x, y):
        self.win.exit_mini(instant=True)   # 迷你贴边状态下直接拖出来
        self._drag_off = QPoint(int(x), int(y)) - self.win.frameGeometry().topLeft()

    @Slot(float, float)
    def dragMove(self, x, y):
        self.win.move(QPoint(int(x), int(y)) - self._drag_off)

    @Slot()
    def dragEnd(self):
        self.win.ctrl.touch()
        self.win.on_dropped()              # 松手判定：贴边→mini，否则记位置

    @Slot(str)
    def notify(self, kind):
        # hover_in/out：mini 模式探出/缩回；pet/pat/wake：本地互动
        if kind == "hover_in":
            self.win.peek()
        elif kind == "hover_out":
            self.win.unpeek()
        elif kind == "pat":
            play_sound("love")
        self.win.ctrl.touch()

    @Slot(str)
    def reportState(self, emo_id):
        # JS 侧自动切换（如 07 惊醒 → 自动落回 02）回报给 Python 保持台账一致
        self.win.ctrl.track(emo_id)

    @Slot(float, float)
    def menu(self, x, y):
        self.win.ctrl.touch()
        self.win.popup_menu()


class Controller(QObject):
    """GUI 线程内的状态中枢：HTTP 信号 → runJavaScript 切表情 + 本地计时"""
    set_requested = Signal(str, str)   # 语义名或 emotionId + 详情文本（HTTP 线程发来）
    sound_requested = Signal(str)
    baby_requested = Signal(str)  # 子代理小球 add/remove

    def __init__(self, view):
        super().__init__()
        self.view = view
        self.page_loaded = False
        self.pending_emotion = None
        self.last_activity = time.time()
        self.work_since = None        # 连续工作起点（None=不在工作态）
        self.tired_shown = False
        self.current_id = "02"
        self.last_switch = time.time()   # 上次可见切换时刻（最小停留判定用）
        self.pending = None              # 最小停留内暂存的状态（只留最新）
        self.base_id = None              # hooks 驱动的基础工作态（内心戏轮播的锚）
        self.variety_at = time.time()    # 上次轮播/真实事件/本地互动时刻
        self.fail_streak = 0             # 连续失败计数（红温升级用）
        self.next_chatter = time.time() + 40   # 下次待命自言自语时刻
        self.self_talk_until = 0         # 自语表情保护期（期间空闲阶梯不顶回 02）

        self.set_requested.connect(self._apply_state)
        self.sound_requested.connect(play_sound)
        self.baby_requested.connect(self._apply_baby)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._check_timers)
        self.timer.start(1000)   # 1s 一跳：空闲阶梯之外还要驱动内心戏轮播

    def touch(self):
        self.last_activity = time.time()

    def request(self, to, text=""):
        self.set_requested.emit(to, text)

    def _js_set(self, emo_id):
        if not self.page_loaded:
            self.pending_emotion = emo_id
            return
        self.view.page().runJavaScript("kqiu.set(%s)" % json.dumps(emo_id))

    def _js_status(self):
        text = STATUS_TEXT.get(self.current_id, "状态 " + self.current_id)
        if self.page_loaded:
            self.view.page().runJavaScript("kqiu.setStatus(%s)" % json.dumps(text))

    def _apply_state(self, to, text=""):
        if CONF.get("dnd"):
            return          # 勿扰模式：hooks 事件全部静默
        emo_id = resolve_emotion(to)
        if not emo_id:
            return
        self.touch()
        # 红温升级：同一回合内连续翻车 34 出错 → 17 慌张 → 21 红温。
        # 重试时的 PreToolUse（32/40）不重置，"改了又挂"才会一路升级；
        # 任务完成/新任务/被打断才消气
        if emo_id == "34":
            self.fail_streak += 1
            emo_id = {1: "34", 2: "17"}.get(self.fail_streak, "21")
        elif emo_id in ("33", "31", "41"):
            self.fail_streak = 0
        # 最小停留：距上次可见切换太近就暂存（只留最新），到点再放行
        wait = MIN_DWELL_S - (time.time() - self.last_switch)
        if wait > 0:
            self.pending = (emo_id, text)
            QTimer.singleShot(int(wait * 1000), self._drain_pending)
            return
        self._show(emo_id, text)

    def _drain_pending(self):
        """最小停留到点，放出暂存状态（期间更新的状态已顶替旧的）"""
        if self.pending is None:
            return
        (emo_id, text), self.pending = self.pending, None
        if CONF.get("dnd"):
            return
        wait = MIN_DWELL_S - (time.time() - self.last_switch)
        if wait > 0:                # 期间被别的可见切换插过队，继续等满
            self.pending = (emo_id, text)
            QTimer.singleShot(int(wait * 1000), self._drain_pending)
            return
        self._show(emo_id, text)

    def _show(self, emo_id, text=""):
        """一次可见切换：落表情+气泡+台账，并锚定内心戏轮播的基础态。
        text 是 hooks 捎来的实时详情（如"2/5 · 重打 exe"），详细开关开着就拼进气泡"""
        self.last_switch = time.time()
        self.variety_at = self.last_switch
        self.base_id = emo_id if emo_id in VARIETY else None
        self._set_id(emo_id)
        if emo_id == "33":
            play_sound("done")      # 任务完成：彩带由表情 33 自带，这里补音效
        if emo_id in TRANSIENT:
            QTimer.singleShot(TRANSIENT[emo_id] * 1000,
                              lambda e=emo_id: self._transient_done(e))
        base = STATUS_TEXT.get(emo_id, "")
        if text and CONF.get("detail", True):
            base = "%s · %s" % (base, text)
        self._js_flash(base)

    def _transient_done(self, emo_id):
        """过场表情到点：没人接班（还是它且没有工作锚）就回 02 待机"""
        if self.current_id == emo_id and self.base_id is None:
            self._set_id("02")

    def _js_call(self, js):
        if self.page_loaded:
            self.view.page().runJavaScript(js)

    def _js_flash(self, text, sec=2.5):
        """让气泡主动浮现几秒（页面按用户设置的模式自行决定是否真显示）"""
        self._js_call("kqiu.flashBubble(%s, %s)" % (json.dumps(text), sec))

    def _apply_baby(self, op):
        if CONF.get("dnd") or not self.page_loaded:
            return
        fn = "kqiu.addBaby()" if op == "add" else "kqiu.removeBaby()"
        self.view.page().runJavaScript(fn)
        self.touch()

    def set_dnd(self, on):
        """勿扰开关：开启就睡下并静默 hooks，关闭走 05 苏醒过场回待机"""
        CONF["dnd"] = on
        save_conf()
        self.pending = None
        self.base_id = None
        if on:
            self._set_id("06")   # 勿扰：直接睡，不走过场
        else:
            self._show("05")     # 解除勿扰：加载苏醒过场，到点自回 02
        self.touch()

    def track(self, emo_id):
        """只记台账不切页面（JS 侧自己切的表情回报走这里）"""
        if not resolve_emotion(emo_id):
            return
        self.current_id = emo_id
        self.variety_at = time.time()   # 本地互动（摸头等）优先，轮播往后让
        self._track_work(emo_id)
        self._js_status()

    def _track_work(self, emo_id):
        # 连续工作计时：进入工作态开始计时，离开即清零
        prev = getattr(self, "_prev_tracked", "02")
        if emo_id in WORK_IDS:
            if prev not in WORK_IDS:
                self.work_since = time.time()
                self.tired_shown = False
        else:
            self.work_since = None
            self.tired_shown = False
        self._prev_tracked = emo_id

    def _set_id(self, emo_id, track=True):
        self.current_id = emo_id
        self._js_set(emo_id)
        self._js_status()
        if track:
            self._track_work(emo_id)
        # track=False：内心戏轮播的装饰表情，不清连续工作计时

    def _check_timers(self):
        now = time.time()
        idle_for = now - self.last_activity
        cur = self.current_id
        # 彩蛋：连续工作超 30 分钟 → 15 疲惫（一次）
        if (self.work_since and not self.tired_shown
                and now - self.work_since > TIRED_AFTER_S):
            self.tired_shown = True
            self.base_id = None
            self._set_id("15")
            return
        # 空闲阶梯：10min 待机放空 → 30min 休眠 → 35min 睡眠
        if idle_for > IDLE_SLEEP_S:
            if cur != "00":
                self.base_id = None
                self._set_id("00")
        elif idle_for > IDLE_DORMANT_S:
            if cur not in SLEEP_IDS:
                self.base_id = None
                self._set_id("06")
        elif idle_for > IDLE_STANDBY_S:
            # 自语表情有 8s 保护期，别刚说话就被顶回 02
            if (cur not in SLEEP_IDS and cur != "02"
                    and now > self.self_talk_until):
                self.base_id = None
                self._set_id("02")
        # 内心戏轮播：基础工作态停够久 → 从池里摇一个（含本体，可摇回）
        elif self.base_id and not CONF.get("dnd"):
            if now - self.variety_at >= VARIETY_AFTER_S[self.base_id]:
                pool = [p for p in [self.base_id] + VARIETY[self.base_id]
                        if p != cur]
                if pool:
                    self.variety_at = now
                    self.last_switch = now
                    pick = random.choice(pool)
                    self._set_id(pick, track=False)
                    self._js_flash(STATUS_TEXT.get(pick, ""))
        # 待命自言自语：02 待机窗口（空闲 10~30min）每 25~45s 来一句或自己玩一下
        if (cur == "02" and self.base_id is None and not CONF.get("dnd")
                and IDLE_STANDBY_S < idle_for < IDLE_DORMANT_S
                and now >= self.next_chatter):
            self.next_chatter = now + random.uniform(25, 45)
            self.self_talk_until = now + 8
            self.last_switch = now
            if random.random() < 0.18:
                self._js_call("kqiu.spin(1)")       # 自己玩：甩圈彩带
                self._js_flash("我自己玩会儿", 3)
            else:
                emo, line = random.choice(IDLE_CHATTER)
                self._set_id(emo)
                self._js_flash(line, 4)

    def on_loaded(self):
        self.page_loaded = True
        # 应用外观设置（形状/线稿/球大小）
        if not self.view.gallery:
            self.view.apply_size(CONF["size"])
        if CONF.get("shape") and CONF["shape"] != "blob":
            self.view.page().runJavaScript("kqiu.setShape(%s)" % json.dumps(CONF["shape"]))
        if CONF.get("sketch"):
            self.view.page().runJavaScript("kqiu.setSketch(true)")
        # 气泡模式/位置同步给页面（页面默认 hover/top，以设置为准）
        self.view.page().runJavaScript(
            "kqiu.setBubbleMode(%s)" % json.dumps(CONF.get("bubble", "auto")))
        self.view.page().runJavaScript(
            "kqiu.setBubblePos(%s)" % json.dumps(CONF.get("bubble_pos", "top")))
        if self.pending_emotion:
            self._js_set(self.pending_emotion)
            self.pending_emotion = None
        self._js_status()   # 页面就绪后同步一次状态文本（覆盖页面默认值）


HELP_TEXT = """KimiQ 使用手册
====================

【基本互动】
· 拖 动：按住球移动位置；拖到屏幕左/右边缘松手 → 贴边隐藏
· 贴边后：鼠标移到贴边条上 → 滑出；从边上把球拖走 → 还原
· 摸 头：按住球不动 → 害羞；松开回待机
· 单 击：甩一圈彩带
· 双 击：满意彩蛋（几秒后自己回待机）
· 气 泡：状态一变自动浮现；右键「气泡」可改 仅悬停/关闭，位置贴球顶或靠球旁
· 睡着的球：鼠标碰一下 → 抖动惊醒
· 待命太久：球会自言自语、自己玩（32 个表情全有戏份）

【Kimi 状态联动】
· 新开 Kimi 会话即生效（hooks 读自 config.toml）：
  收到任务 / 思考 / 联网 / 翻资料 / 干活 / 写回复 / 完成撒花 / 出错…各有专属表情
· 思考/干活久了会"走神"（专注/好奇/困惑…随机换脸）；连续翻车会红温（出错→慌→怒）
· 球没在跑时新开 Kimi 会话 → 自动拉起球（SessionStart hook）
· 只有新会话才加载 hooks，老会话不会触发

【勿扰模式】
· 右键球或托盘 → 勾选「勿扰模式」：球直接睡觉，
  Kimi 的所有事件和提示音全部静默，适合专注/录屏
· 取消勾选 → 球醒来回待机

【图鉴】
· 右键 →「图鉴」：32 套表情自动巡演
· 翻页：←/→ 方向键，或点球左/右两侧
· 空格：暂停/继续巡演；点球中间：甩彩带

【设置】
· 球大小（30~160，实时预览）/ 形状（圆胖/三角/菱形）/ 线稿
· 音效开关 / 窗口置顶 / 开机自启 / 启动时拉起 Kimi CLI
· 位置自动记忆，下次启动原地出现

【HTTP 接口】（127.0.0.1:28765）
· /state?to=33 或 to=done → 切表情（语义名 think/web/read/exec/reply/
  done/error/deny/stop/wait/idle/sleep…）
· /sound?play=done → 播音效
· /baby?op=add|remove → 子代理小球

表情引擎：sam70361/emotion-ball（仅供学习交流许可，见 vendor 内 LICENSE）
"""


class HelpDialog(QDialog):
    """使用手册（只读说明窗）"""
    def __init__(self):
        super().__init__()
        self.setWindowTitle("KimiQ 使用手册")
        self.resize(520, 560)
        lay = QVBoxLayout(self)
        from PySide6.QtWidgets import QTextBrowser
        t = QTextBrowser()
        t.setPlainText(HELP_TEXT)
        lay.addWidget(t)
        ok = QPushButton("知道了")
        ok.clicked.connect(self.accept)
        lay.addWidget(ok)


class SettingsDialog(QDialog):
    """设置：球大小 / 形状 / 线稿 / 音效 / 置顶 / 开机自启。大小拖动即时预览"""
    SHAPES = [("blob", "圆胖"), ("wedge", "三角"), ("gem", "菱形")]

    def __init__(self, win):
        # 不传 parent：桌宠窗口带透明属性，会传染给对话框（黑底看不见字）
        super().__init__()
        self.win = win
        self.setWindowTitle("KimiQ 设置")
        self.setWindowFlags(Qt.Dialog | Qt.WindowCloseButtonHint)
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setAutoFillBackground(True)
        self._old = dict(CONF)

        lay = QVBoxLayout(self)

        row = QHBoxLayout()
        row.addWidget(QLabel("球大小"))
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(SIZE_MIN, SIZE_MAX)
        self.slider.setValue(CONF["size"])
        self.size_label = QLabel(str(CONF["size"]))
        self.slider.valueChanged.connect(self._preview)
        row.addWidget(self.slider)
        row.addWidget(self.size_label)
        lay.addLayout(row)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("身体形状"))
        self.combo_shape = QComboBox()
        for key, name in self.SHAPES:
            self.combo_shape.addItem(name, key)
        idx = [k for k, _ in self.SHAPES].index(CONF.get("shape", "blob"))
        self.combo_shape.setCurrentIndex(idx)
        self.combo_shape.currentIndexChanged.connect(self._preview_shape)
        row2.addWidget(self.combo_shape)
        lay.addLayout(row2)

        self.cb_sketch = QCheckBox("线稿模式")
        self.cb_sketch.setChecked(CONF.get("sketch", False))
        self.cb_sketch.toggled.connect(
            lambda on: self.win.page().runJavaScript("kqiu.setSketch(%s)" % ("true" if on else "false")))
        self.cb_sound = QCheckBox("音效（完成任务/摸头提示音）")
        self.cb_sound.setChecked(CONF["sound"])
        self.cb_ontop = QCheckBox("窗口置顶")
        self.cb_ontop.setChecked(CONF["ontop"])
        self.cb_autostart = QCheckBox("开机自启（随 Windows 登录启动）")
        self.cb_autostart.setChecked(CONF.get("autostart", False))
        self.cb_kimi = QCheckBox("启动时拉起 Kimi CLI（没在跑就开终端 kimi --continue 继续上次会话）")
        self.cb_kimi.setChecked(CONF.get("launch_kimi", True))
        lay.addWidget(self.cb_sketch)
        lay.addWidget(self.cb_sound)
        lay.addWidget(self.cb_ontop)
        lay.addWidget(self.cb_autostart)
        lay.addWidget(self.cb_kimi)

        btns = QHBoxLayout()
        ok = QPushButton("确定")
        cancel = QPushButton("取消")
        ok.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)
        btns.addStretch(1)
        btns.addWidget(ok)
        btns.addWidget(cancel)
        lay.addLayout(btns)

    def _preview(self, v):
        self.size_label.setText(str(v))
        # 窗口固定不动，只缩放球（320 大窗框住最大号，不裁切不移位）
        self.win.apply_size(v)

    def _preview_shape(self, i):
        self.win.page().runJavaScript(
            "kqiu.setShape(%s)" % json.dumps(self.combo_shape.itemData(i)))

    def accept(self):
        CONF["size"] = self.slider.value()
        CONF["shape"] = self.combo_shape.currentData()
        CONF["sketch"] = self.cb_sketch.isChecked()
        CONF["sound"] = self.cb_sound.isChecked()
        CONF["ontop"] = self.cb_ontop.isChecked()
        CONF["autostart"] = self.cb_autostart.isChecked()
        CONF["launch_kimi"] = self.cb_kimi.isChecked()
        self.win.apply_size(CONF["size"])
        self.win.apply_ontop()
        apply_autostart(CONF["autostart"])
        save_conf()
        super().accept()

    def reject(self):
        # 取消：球大小/形状/线稿还原（窗口本来就没动过）
        self.win.apply_size(self._old["size"])
        self.win.page().runJavaScript(
            "kqiu.setShape(%s)" % json.dumps(self._old.get("shape", "blob")))
        self.win.page().runJavaScript(
            "kqiu.setSketch(%s)" % ("true" if self._old.get("sketch") else "false"))
        super().reject()


def apply_autostart(on):
    """开机自启：HKCU Run 项。打包版登记 exe 自身；源码版优先指 dist exe，否则 pythonw + 脚本"""
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0,
                            winreg.KEY_SET_VALUE) as k:
            if on:
                if getattr(sys, "frozen", False):
                    cmd = '"%s"' % sys.executable
                else:
                    exe = BASE_DIR / "dist" / "KimiQ.exe"
                    if exe.exists():
                        cmd = '"%s"' % exe
                    else:
                        pyw = Path(sys.executable).with_name("pythonw.exe")
                        cmd = '"%s" "%s"' % (pyw, BASE_DIR / "kimiq.py")
                winreg.SetValueEx(k, "KimiQ", 0, winreg.REG_SZ, cmd)
            else:
                try:
                    winreg.DeleteValue(k, "KimiQ")
                except FileNotFoundError:
                    pass
    except OSError:
        pass


def kimi_running():
    """Kimi CLI 是否在跑。按可执行路径认 `\\.kimi-code\\` 下的 kimi.exe，
    避免把 Kimi 桌面端（也叫 Kimi.exe）误判成 CLI"""
    try:
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command",
             "(Get-Process -Name kimi -ErrorAction SilentlyContinue "
             "| Where-Object { $_.Path -like '*\\.kimi-code\\*' } "
             "| Measure-Object).Count"],
            text=True, timeout=8, errors="ignore")
        return out.strip() not in ("", "0")
    except Exception:
        return False   # 探测失败就当没在跑，多拉一个窗口用户自己关就行


def maybe_launch_kimi():
    """启动 KimiQ 时顺手拉起 Kimi CLI（新终端窗口跑 kimi，相当于手动敲命令）。
    球的状态全靠 Kimi hooks 驱动，Kimi 不跑球就没戏演；
    SessionStart hook 拉起球的场景下 Kimi 已在跑，这里会自然跳过"""
    if not CONF.get("launch_kimi", True) or kimi_running():
        return
    try:
        # --continue：接着这个目录上次的会话聊，而不是新开空白会话
        wt = shutil.which("wt")
        if wt:
            subprocess.Popen([wt, "kimi", "--continue"])   # Win11 自带 Windows Terminal
        else:
            subprocess.Popen(["cmd", "/c", "start", "", "kimi", "--continue"])
    except OSError:
        pass


# ---------------- hooks 联动自愈（稳定路径，防改名/搬家断联） ----------------
APPDATA_DIR = Path(os.environ.get("APPDATA") or Path.home()) / "KimiQ"
HOOK_STABLE = APPDATA_DIR / "hooks" / "kimiq-hook.mjs"
KIMI_CONFIG = Path.home() / ".kimi-code" / "config.toml"


def write_home_json():
    """登记本体位置：hook 转发器的 autostart 靠它找 exe，路径以后再改也不怕"""
    try:
        APPDATA_DIR.mkdir(parents=True, exist_ok=True)
        frozen = getattr(sys, "frozen", False)
        home = {"repo": str(Path(sys.executable).parent if frozen else BASE_DIR),
                "exe": sys.executable if frozen else ""}
        (APPDATA_DIR / "home.json").write_text(
            json.dumps(home, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass


def install_hook():
    """把转发器装到 %APPDATA% 稳定副本：config.toml 只指这里，仓库改名/搬家不影响联动"""
    try:
        src = BASE_DIR / "hooks" / "kimiq-hook.mjs"
        if not src.exists():
            return
        data = src.read_bytes()
        HOOK_STABLE.parent.mkdir(parents=True, exist_ok=True)
        if not HOOK_STABLE.exists() or HOOK_STABLE.read_bytes() != data:
            HOOK_STABLE.write_bytes(data)
    except OSError:
        pass


def ensure_hooks_config():
    """修 config.toml 里的转发器路径：凡叫 kqiu-hook/kimiq-hook 的命令统一指向稳定副本。
    只正则替换命令路径，别的配置一个字不碰"""
    try:
        if not KIMI_CONFIG.exists():
            return
        text = KIMI_CONFIG.read_text(encoding="utf-8")
        new = re.sub(r"node\s+\S*(?:kqiu|kimiq)-hook\.mjs",
                     "node " + HOOK_STABLE.as_posix(), text)
        if new != text:
            KIMI_CONFIG.write_text(new, encoding="utf-8")
    except OSError:
        pass


def make_icon(size=64):
    """托盘图标：手绘一个白球笑脸（不依赖外部素材）"""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#F3F0EA"))                      # 球体（作者默认色）
    p.drawEllipse(4, 4, size - 8, size - 8)
    p.setBrush(QColor("#1A1A1A"))                      # 两只眼
    p.drawEllipse(int(size * 0.30), int(size * 0.38), int(size * 0.10), int(size * 0.18))
    p.drawEllipse(int(size * 0.60), int(size * 0.38), int(size * 0.10), int(size * 0.18))
    p.end()
    return QIcon(pm)


class BallWindow(QWebEngineView):
    """主窗。gallery=True 时是图鉴窗（大、无 mini/托盘逻辑）"""
    def __init__(self, gallery=False):
        super().__init__()
        self.gallery = gallery
        self.mini = None            # None / "left" / "right"
        self.peeking = False
        self._anim = None

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool |
                            (Qt.WindowStaysOnTopHint if CONF["ontop"] else Qt.WindowFlags()))
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setStyleSheet("background: transparent;")
        if gallery:
            # 图鉴是"查看器"：带边框标题栏（有 X 可关），白底不透明
            self.setWindowFlags(Qt.Window | Qt.WindowCloseButtonHint)
            self.setAttribute(Qt.WA_TranslucentBackground, False)
            self.setStyleSheet("")
            self.resize(GALLERY_W, GALLERY_H)
            self.setWindowTitle("KimiQ 图鉴")
        else:
            # 主窗固定最大尺寸永不动；球大小=页面内缩放；mask 裁剪命中区
            self.ball_size = CONF["size"]
            self.resize(WIN_SIZE, WIN_SIZE)

        self.ctrl = Controller(self)
        page = QWebEnginePage(self) if gallery else TransparentPage(self)
        self.setPage(page)

        self.bridge = Bridge(self)
        channel = QWebChannel(page)
        channel.registerObject("kqiuBridge", self.bridge)
        page.setWebChannel(channel)

        page.loadFinished.connect(self.ctrl.on_loaded)

        url = "file:///" + HARNESS.as_posix()
        if gallery:
            url += "#gallery"
        self.load(url)
        if not gallery:
            self._update_mask()     # 命中区先行，页面加载后再补 JS 缩放

    # ---------- 球大小（窗口不动，只缩放球 + 更新命中 mask） ----------
    def apply_size(self, size):
        self.ball_size = size
        if self.ctrl.page_loaded:
            self.page().runJavaScript("kqiu.setBallSize(%d)" % size)
        self._update_mask()

    def _update_mask(self):
        """命中区 = 球圆（外扩彩带余量）∪ 气泡条；mask 外点击穿透到下层窗口"""
        w = self.width()
        cx = w // 2
        cy = self.height() // 2
        r = int(self.ball_size * 0.72) + 8
        region = QRegion(QRect(cx - r, cy - r, r * 2, r * 2), QRegion.Ellipse)
        # 气泡关闭时不留气泡条，命中区只剩球圆（少一块挡桌面的死区）
        if not self.mini and w >= WIN_SIZE and CONF.get("bubble") != "off":
            if CONF.get("bubble_pos") == "side":
                # 气泡靠球右侧：留球右到窗边的横条
                x0 = cx + self.ball_size // 2 - 4
                region = region.united(QRect(x0, cy - 26, w - x0, 52))
            else:
                # 气泡贴球顶上方（harness 里按球径算位置）：留出它那条区域
                ball_top = cy - self.ball_size // 2
                region = region.united(QRect(cx - 70, max(0, ball_top - 46), 140, 52))
        self.setMask(region)

    def apply_ontop(self):
        was = self.isVisible()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool |
                            (Qt.WindowStaysOnTopHint if CONF["ontop"] else Qt.WindowFlags()))
        self.setAttribute(Qt.WA_TranslucentBackground)
        if was:
            self.show()

    # ---------- 右键/托盘菜单 ----------
    def popup_menu(self):
        m = QMenu()
        if self.gallery:
            # 图鉴窗右键：只给关闭
            act_close = QAction("关闭图鉴", m)
            act_close.triggered.connect(self.close)
            m.addAction(act_close)
            m.exec(QCursor.pos())
            return
        act_set = QAction("设置…", m)
        act_gallery = QAction("图鉴（32 套表情）", m)
        act_help = QAction("使用手册", m)
        act_dnd = QAction("勿扰模式", m, checkable=True)
        act_dnd.setChecked(CONF.get("dnd", False))
        act_quit = QAction("退出 KimiQ", m)
        act_set.triggered.connect(self.open_settings)
        act_gallery.triggered.connect(open_gallery)
        act_help.triggered.connect(lambda: HelpDialog().exec())
        act_dnd.toggled.connect(self.ctrl.set_dnd)
        act_quit.triggered.connect(QApplication.quit)
        # 气泡子菜单：模式（自动/悬停/关闭）+ 位置（球顶/球旁），单选即时生效
        sub = QMenu("气泡", m)
        grp = QActionGroup(sub)
        grp.setExclusive(True)
        for label, val in [("自动浮现", "auto"), ("仅悬停显示", "hover"), ("关闭气泡", "off")]:
            a = QAction(label, sub, checkable=True)
            a.setChecked(CONF.get("bubble", "auto") == val)
            a.triggered.connect(lambda _c, v=val: self.set_bubble(v))
            grp.addAction(a)
            sub.addAction(a)
        sub.addSeparator()
        grp2 = QActionGroup(sub)
        grp2.setExclusive(True)
        for label, val in [("贴在球顶", "top"), ("靠在球旁", "side")]:
            a = QAction(label, sub, checkable=True)
            a.setChecked(CONF.get("bubble_pos", "top") == val)
            a.triggered.connect(lambda _c, v=val: self.set_bubble_pos(v))
            grp2.addAction(a)
            sub.addAction(a)
        sub.addSeparator()
        act_detail = QAction("详细状态（显示 Kimi 在做什么）", sub, checkable=True)
        act_detail.setChecked(CONF.get("detail", True))
        act_detail.toggled.connect(self.set_detail)
        sub.addAction(act_detail)
        m.addAction(act_set)
        m.addAction(act_gallery)
        m.addAction(act_help)
        m.addAction(act_dnd)
        m.addMenu(sub)
        m.addSeparator()
        m.addAction(act_quit)
        m.exec(QCursor.pos())

    def open_settings(self):
        SettingsDialog(self).exec()

    # ---------- 气泡显示（右键直接调，即时生效并存档） ----------
    def set_bubble(self, mode):
        CONF["bubble"] = mode
        save_conf()
        self.page().runJavaScript("kqiu.setBubbleMode(%s)" % json.dumps(mode))
        self._update_mask()          # 关闭气泡后命中区去掉气泡条

    def set_bubble_pos(self, pos):
        CONF["bubble_pos"] = pos
        save_conf()
        self.page().runJavaScript("kqiu.setBubblePos(%s)" % json.dumps(pos))
        self._update_mask()

    def set_detail(self, on):
        """气泡详细状态开关：关了就只显示写死的状态文案"""
        CONF["detail"] = bool(on)
        save_conf()

    # ---------- mini 贴边隐藏 ----------
    def on_dropped(self):
        """拖拽松手：贴边 → mini；否则记住位置"""
        if self.gallery:
            return
        screen = QApplication.primaryScreen().geometry()
        x, w = self.x(), self.width()
        if x <= screen.left() + 8:
            self._enter_mini("left")
        elif x + w >= screen.right() - 8:
            self._enter_mini("right")
        else:
            self._save_pos()

    def _save_pos(self):
        CONF["pos"] = [self.x(), self.y()]
        CONF["mini"] = self.mini
        save_conf()

    def _mini_target(self, side, peek):
        screen = QApplication.primaryScreen().geometry()
        w, y = self.width(), self.y()
        if side == "right":
            x = screen.right() - w + 1 if peek else screen.right() - MINI_EXPOSE + 1
        else:
            x = screen.left() if peek else screen.left() - w + MINI_EXPOSE - 1
        return QPoint(x, y)

    def _enter_mini(self, side, instant=False):
        self.mini = side
        self.peeking = False
        self._js_mini(True)
        # 球在 320 大窗中央，直接贴边会看不见球 → 窗口缩到球大小再贴边
        d = self.ball_size + MINI_PAD
        c = self.geometry().center()
        self.setGeometry(c.x() - d // 2, c.y() - d // 2, d, d)
        self._update_mask()
        target = self._mini_target(side, peek=False)
        if instant:
            self.move(target)
        else:
            self._slide(target)
        self._save_pos()

    def exit_mini(self, instant=False):
        if not self.mini:
            return
        self._restore_big()
        target = self._mini_target(self.mini, peek=True)
        self.mini = None
        self.peeking = False
        self._js_mini(False)
        if instant:
            self.move(target)
        else:
            self._slide(target)

    def peek(self):
        """mini 状态下悬停 → 展开大窗滑出完整球"""
        if self.mini and not self.peeking:
            self.peeking = True
            self._restore_big()
            self._slide(self._mini_target(self.mini, peek=True))

    def unpeek(self):
        if self.mini and self.peeking:
            self.peeking = False
            self._slide(self._mini_target(self.mini, peek=False))
            # 滑回去之后再缩窗（避免半路球不见）
            QTimer.singleShot(200, self._shrink_to_ball)

    def _restore_big(self):
        """mini 窗口恢复为 320 大窗，球心不动"""
        if self.width() == WIN_SIZE:
            return
        c = self.geometry().center()
        self.setGeometry(c.x() - WIN_SIZE // 2, c.y() - WIN_SIZE // 2, WIN_SIZE, WIN_SIZE)
        self._update_mask()

    def _shrink_to_ball(self):
        """mini 收回：窗口缩到球大小"""
        if not self.mini or self.peeking:
            return
        d = self.ball_size + MINI_PAD
        c = self.geometry().center()
        self.setGeometry(c.x() - d // 2, c.y() - d // 2, d, d)
        self._update_mask()

    def _js_mini(self, on):
        if self.ctrl.page_loaded:
            self.page().runJavaScript("kqiu.setMini(%s)" % ("true" if on else "false"))

    def _slide(self, target):
        if self._anim:
            self._anim.stop()
        a = QPropertyAnimation(self, b"pos", self)
        a.setDuration(180)
        a.setStartValue(self.pos())
        a.setEndValue(target)
        a.setEasingCurve(QEasingCurve.OutCubic)
        a.start()
        self._anim = a

    def closeEvent(self, e):
        if not self.gallery:
            self._save_pos()
        super().closeEvent(e)


# ---------------- 图鉴窗（全局单例） ----------------
GALLERY_WIN = None


def open_gallery():
    global GALLERY_WIN
    if GALLERY_WIN is None:
        GALLERY_WIN = BallWindow(gallery=True)
        GALLERY_WIN.setAttribute(Qt.WA_DeleteOnClose)
        GALLERY_WIN.destroyed.connect(lambda: globals().__setitem__("GALLERY_WIN", None))
        screen = QApplication.primaryScreen().geometry()
        GALLERY_WIN.move(screen.center() - GALLERY_WIN.rect().center())
    GALLERY_WIN.show()
    GALLERY_WIN.raise_()


# ---------------- 单实例锁（Windows 命名互斥体，防多开抢 28765 端口） ----------------
_MUTEX = None


def already_running():
    global _MUTEX
    import ctypes
    _MUTEX = ctypes.windll.kernel32.CreateMutexW(None, False, "kimiq_single_instance")
    return ctypes.windll.kernel32.GetLastError() == 183   # ERROR_ALREADY_EXISTS


# ---------------- HTTP 接口 ----------------
CTRL = None


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        q = parse_qs(urlparse(self.path).query)
        if self.path.startswith("/state") and CTRL:
            to = q.get("to", [""])[0]
            text = q.get("text", [""])[0][:60]   # 详情文本限长，气泡就两行
            ok = resolve_emotion(to) is not None
            if ok:
                CTRL.request(to, text)
            self.reply(b'{"ok":%s}' % (b"true" if ok else b"false"))
        elif self.path.startswith("/sound"):
            play_sound(q.get("play", [""])[0])
            self.reply(b'{"ok":true}')
        elif self.path.startswith("/baby") and CTRL:
            op = q.get("op", [""])[0]
            if op in ("add", "remove"):
                CTRL.baby_requested.emit(op)
                self.reply(b'{"ok":true}')
            else:
                self.reply(b'{"ok":false}')
        else:
            self.reply(b'{"ok":false}')

    def reply(self, body):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def main():
    global CTRL
    # GPU 抽风兜底：本机实测 QtWebEngine 的 ANGLE/D3D 通道会 fatal、整窗透明；
    # 桌宠是轻量 2D 动画，软件渲染完全够，强制 swiftshader 换"永远不黑窗"
    os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--use-angle=swiftshader")
    load_conf()
    gallery_only = "--gallery" in sys.argv
    app = QApplication(sys.argv)
    # 正常模式有托盘，关窗不退程序；纯图鉴模式关掉图鉴窗就退出
    app.setQuitOnLastWindowClosed(gallery_only)

    if gallery_only:
        open_gallery()
    else:
        if already_running():
            print("KimiQ 已在运行，忽略重复启动")
            sys.exit(0)
        win = BallWindow()
        CTRL = win.ctrl
        # 联动自愈三件套：登记本体 → 装稳定副本 → 修 config 指向（改名/搬家不断联）
        write_home_json()
        install_hook()
        ensure_hooks_config()
        screen = app.primaryScreen().geometry()
        # 位置记忆：配置里有就用（越界则回默认右下角）
        pos = CONF.get("pos")
        if (isinstance(pos, list) and len(pos) == 2
                and screen.contains(QPoint(pos[0], pos[1]))):
            win.move(pos[0], pos[1])
        else:
            win.move(screen.right() - win.width() - 40,
                     screen.bottom() - win.height() - 60)
        win.show()
        win.ctrl.request("01")   # 开机过场：01 唤醒打个招呼，几秒后自回待机
        maybe_launch_kimi()      # Kimi CLI 没在跑就顺手拉起（设置里可关）
        # 上次是贴边隐藏的，恢复 mini 状态
        if CONF.get("mini") in ("left", "right"):
            win._enter_mini(CONF["mini"], instant=True)

        server = HTTPServer(("127.0.0.1", 28765), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()

        tray = QSystemTrayIcon(make_icon(), app)
        tray.setToolTip("KimiQ · Kimi 桌面宠物")
        menu = QMenu()
        act_set = QAction("设置…", menu)
        act_gallery = QAction("图鉴（32 套表情）", menu)
        act_help = QAction("使用手册", menu)
        act_dnd = QAction("勿扰模式", menu, checkable=True)
        act_dnd.setChecked(CONF.get("dnd", False))
        act_quit = QAction("退出 KimiQ", menu)
        act_set.triggered.connect(win.open_settings)
        act_gallery.triggered.connect(open_gallery)
        act_help.triggered.connect(lambda: HelpDialog().exec())
        act_dnd.toggled.connect(win.ctrl.set_dnd)
        act_quit.triggered.connect(QApplication.quit)
        menu.addAction(act_set)
        menu.addAction(act_gallery)
        menu.addAction(act_help)
        menu.addAction(act_dnd)
        menu.addSeparator()
        menu.addAction(act_quit)
        tray.setContextMenu(menu)
        tray.activated.connect(
            lambda r: win.open_settings() if r == QSystemTrayIcon.DoubleClick else None)
        tray.show()

    print("KimiQ v2.1 running · HTTP 127.0.0.1:28765/state?to=33"
          + (" · gallery" if gallery_only else ""))
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

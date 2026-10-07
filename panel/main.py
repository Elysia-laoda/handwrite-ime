"""hwime Windows 悬浮手写面板（v2.7：UI 全面加料 + 笔锋增强）。

布局：[候选条: 大字预览 + 路 B 胶囊] [已上屏墨迹回显] [手写大格行] [操作条]
交互：桌面常驻**悬浮球**——点击开/关面板（可拖，位置持久化），热键等效。

视觉（v2.7 加料）：
- 毛玻璃：Win11 亚克力（SetWindowCompositionAttribute）+ 渐变底 + 磨砂
  噪点 + 斜向高光 + 上亮下暗渐变描边（API 失效时纯 paint 层兜底）
- 粒子：常驻环境漂浮粒子 + 特效爆发粒子（落笔收笔、纠正、上屏、清空）
- 悬浮球：径向渐变球体 + 脉冲光晕（收起时更亮）+ 环绕粒子 + 悬停增亮

书写渲染：
- 增量绘制 + 笔尖预测（~10ms 外推，整行重绘防残影）
- 笔锋包络：起笔轻入 45%、行笔微鼓、收笔出锋收到 ~12%（显示层）
- 数位板优先：板活跃期忽略合成鼠标流
- 压感：驱动实测不可用（恒 ~0.02），线宽由速度模拟

出血线：内虚线框 + 右出界线（渐变橙）+ 右 1/4 判定区（写入且停顿
1.2s → 自动上屏）。每次「送出」笔迹落盘 data/captures.jsonl。
面板永不夺焦；悬浮球/面板位置存 data/ui_state.json。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import math
import os
import sys
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)                       # handwrite-ime/
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)                        # appcfg/settings 同目录
if os.path.join(_ROOT, "engine") not in sys.path:
    sys.path.insert(0, os.path.join(_ROOT, "engine"))

import appcfg                                        # noqa: E402

appcfg.load()                                        # 先于任何面板构建

from PySide6.QtCore import (QAbstractNativeEventFilter, QEvent, QPointF,
                            QRectF, QThread, Qt, QTimer, Signal)
from PySide6.QtGui import (QBrush, QColor, QFontMetricsF, QIcon, QImage,
                           QLinearGradient, QPainter, QPainterPath, QPen,
                           QPixmap, QPolygonF, QRadialGradient)
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QMenu,
                               QMessageBox, QPushButton, QSystemTrayIcon,
                               QWidget)

from hwengine.ink import InkLine, render_cells_image
from hwengine.pipeline import Pipeline

WRAP_CHECK_MS = 250
WRAP_IDLE_MS = 1200
IDLE_PREDICT_MS = 600
N_ROWS = 1                  # 手写行数（单行大格）
ROW_H = 170                 # 行高（大格，书写更舒展）
ECHO_H = 64
BAR_H = 70
CELL = 40
CELL_GAP = 4
ECHO_X0 = 88
PREVIEW_X0 = 14
PREVIEW_FONT = 26

CAPTURE_FILE = os.path.join(_ROOT, "data", "captures.jsonl")
UI_STATE_FILE = os.path.join(_ROOT, "data", "ui_state.json")

# ---- 视觉基调（v2.7.1：更透的毛玻璃 + 更猛的笔锋 + 更细的粒子）----
RADIUS = 15                              # 面板圆角（过圆显违和，收一档）
DEFAULT_OPACITY = 1.0                    # 透明度交给 paint 层 + 亚克力，不用窗口整体透明
WIN_BUILD = sys.getwindowsversion().build
ACRYLIC_TINT_PANEL = 0x46EDF2F5          # ABGR：暖白磨砂（alpha 0x46≈70，低才透）
# ⚠ 实测结论（2026-10-06，Win11 build 26200）：SetWindowCompositionAttribute
# 的亚克力/模糊染色（state 3/4）在本机渲染为**发白的实心磨砂**，会把背景
# 完全盖住（黑背景都透不出来），三组对照实验确认。因此默认关闭，
# 毛玻璃质感由绘制层承担（半透明渐变 + 噪点 + 高光 + 柔边）。
# 需要真实模糊时可置 True 试验，或改用 DwmSetWindowAttribute 的
# 系统背景（需非分层窗口，属后续重做项）。
USE_ACRYLIC_ACCENT = False

# 实时背景模糊开关已迁移到设置系统（appcfg.SCHEMA["live_blur"]，默认开）。
# 说明：开=玻璃里背景实时更新，但面板/悬浮球会从**所有截屏/录屏**里消失
# （WDA_EXCLUDEFROMCAPTURE 的代价）；关=打开面板时抓一张背景做静态模糊。
INK_COLOR = QColor("#16213a")
INK_SOFT = QColor("#41527a")
GOLD = QColor("#d9a441")
BLEED_INNER = QColor(170, 120, 120, 110)
TEXT_MAIN = QColor("#2a2a2a")
TEXT_DIM = QColor("#8a8578")
ECHO_OK = QColor("#3a7a3a")
ACCENT = QColor("#3a6ea8")

MOD_ALT, MOD_CONTROL = 0x1, 0x2
WM_HOTKEY, HK_TOGGLE = 0x0312, 1

_user32 = ctypes.windll.user32


def enable_backdrop_blur(hwnd, tint: int, state: int | None = None) -> bool:
    """Win10/11 亚克力毛玻璃（SetWindowCompositionAttribute，非公开 API）。

    state 缺省时：Win11 用 ACCENT_ENABLE_ACRYLICBLURBEHIND(4)、Win10 用
    BLURBEHIND(3)（老版本上 acrylic 拖动会卡）。实测发现本机 Win11 的
    state 4 会渲染出一层偏白的磨砂，可显式传 3 对照。失败静默返回
    False——paint 层的渐变背景本身就是毛玻璃风兜底。
    """
    if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
        return False
    try:
        fn = getattr(_user32, "SetWindowCompositionAttribute", None)
        if fn is None:
            return False

        class ACCENTPOLICY(ctypes.Structure):
            _fields_ = [("AccentState", ctypes.c_uint),
                        ("AccentFlags", ctypes.c_uint),
                        ("GradientColor", ctypes.c_uint),
                        ("AnimationId", ctypes.c_uint)]

        class WINCOMPATTRDATA(ctypes.Structure):
            _fields_ = [("Attribute", ctypes.c_int),
                        ("Data", ctypes.POINTER(ACCENTPOLICY)),
                        ("SizeOfData", ctypes.c_size_t)]

        accent = ACCENTPOLICY()
        if state is not None:
            accent.AccentState = state
        else:
            accent.AccentState = 4 if WIN_BUILD >= 22000 else 3
        accent.AccentFlags = 2
        accent.GradientColor = tint
        data = WINCOMPATTRDATA()
        data.Attribute = 19                      # WCA_ACCENT_POLICY
        data.Data = ctypes.pointer(accent)
        data.SizeOfData = ctypes.sizeof(accent)
        fn(int(hwnd), ctypes.byref(data))
        return True
    except Exception:                            # noqa: BLE001
        return False


def set_round_region(hwnd, w: int, h: int, radius: int) -> bool:
    """把窗口裁剪成圆角矩形（SetWindowRgn）。

    亚克力染色铺满整个窗口矩形，圆角外围会露出方角；区域裁剪后
    染色/模糊都只落在圆角内，视觉干净。
    """
    try:
        gdi32 = ctypes.windll.gdi32
        hwnd = int(hwnd)
        _user32.SetWindowRgn(hwnd, 0, True)      # 先清空旧区域防叠加
        rgn = gdi32.CreateRoundRectRgn(0, 0, w + 1, h + 1, radius * 2,
                                       radius * 2)
        if not rgn:
            return False
        _user32.SetWindowRgn(hwnd, rgn, True)
        return True
    except Exception:                            # noqa: BLE001
        return False


_noise_pixmap: QPixmap | None = None


def _get_noise_pixmap() -> QPixmap:
    """一次性生成的细噪点贴图：给毛玻璃加"磨砂"质感。"""
    global _noise_pixmap
    if _noise_pixmap is None:
        import random
        rng = random.Random(20261006)
        img = QImage(128, 128, QImage.Format_ARGB32)
        img.fill(0)
        for y in range(128):
            for x in range(128):
                a = rng.randint(0, 12)
                if a:
                    img.setPixelColor(x, y, QColor(255, 255, 255, a))
        _noise_pixmap = QPixmap.fromImage(img)
    return _noise_pixmap


class Particles:
    """轻量粒子场：环境漂浮粒子（常驻）+ 特效爆发粒子（书写/上屏触发）。

    尺寸走"细尘"路线：半径 0.5~1.3px 的微粒，数量多而轻。
    """

    def __init__(self, w: int, h: int, n_ambient: int = 26):
        import random
        self.w, self.h = w, h
        self.rng = random.Random(0xC0FFEE)
        self.t = 0.0
        self.ambient = [self._new_ambient() for _ in range(n_ambient)]
        self.effects: list[dict] = []

    def _new_ambient(self, y0=None):
        rng = self.rng
        return [rng.uniform(0, self.w),                       # x
                y0 if y0 is not None else rng.uniform(0, self.h),
                -rng.uniform(4, 13),                          # vy 缓慢上浮
                rng.uniform(0, 6.283),                        # 摆动相位
                rng.uniform(5, 15),                           # 摆幅
                rng.uniform(0.04, 0.12),                      # 摆动频率
                rng.uniform(0.5, 1.3),                        # 半径（细尘）
                rng.uniform(0.05, 0.19)]                      # 透明度

    def spawn_burst(self, x, y, n=8, speed=110.0, gold=0.45):
        rng = self.rng
        for _ in range(n):
            a = rng.uniform(0, 6.283)
            sp = speed * rng.uniform(0.3, 1.0)
            r = rng.random()
            col = (GOLD.red(), GOLD.green(), GOLD.blue()) if r < gold \
                else (78, 96, 138)
            if rng.random() < 0.22:
                col = (255, 255, 255)
            self.effects.append({
                "x": x, "y": y,
                "vx": math.cos(a) * sp, "vy": math.sin(a) * sp * 0.7 - rng.uniform(10, 70),
                "age": 0.0, "life": rng.uniform(0.5, 1.0),
                "r": rng.uniform(0.7, 2.0), "c": col,
            })

    def step(self, dt: float):
        self.t += dt
        for pa in self.ambient:
            pa[1] += pa[2] * dt
            if pa[1] < -8:
                pa[:] = self._new_ambient(y0=self.h + 8)
        for e in self.effects:
            e["age"] += dt
            e["vy"] += 240 * dt                       # 轻微下坠
            drag = max(0.0, 1.0 - 2.0 * dt)
            e["vx"] *= drag
            e["vy"] *= drag
            e["x"] += e["vx"] * dt
            e["y"] += e["vy"] * dt
        self.effects = [e for e in self.effects if e["age"] < e["life"]]

    def paint_ambient(self, p: QPainter):
        p.setPen(Qt.NoPen)
        for x, y, _vy, ph, amp, fr, r, al in self.ambient:
            xx = x + math.sin(self.t * fr * 6.283 + ph) * amp
            p.setBrush(QColor(140, 165, 215, max(4, int(255 * al))))
            p.drawEllipse(QPointF(xx, y), r, r)

    def set_ambient(self, n: int):
        """调整环境粒子数量（设置界面可实时调）。"""
        n = max(0, int(n))
        while len(self.ambient) > n:
            self.ambient.pop()
        while len(self.ambient) < n:
            self.ambient.append(self._new_ambient())

    def paint_effects(self, p: QPainter):
        p.setPen(Qt.NoPen)
        for e in self.effects:
            k = 1.0 - e["age"] / e["life"]
            c = QColor(*e["c"])
            c.setAlpha(max(4, int(235 * (k ** 0.7))))
            rr = e["r"] * (0.45 + k * 0.75)
            p.setBrush(c)
            p.drawEllipse(QPointF(e["x"], e["y"]), rr, rr)


_WDA_EXCLUDEFROMCAPTURE = 0x11


class BackdropSampler:
    """实时背景采样器（Windows Graphics Capture）。

    原理：面板打上 WDA_EXCLUDEFROMCAPTURE 后，**WGC（DWM 捕获通道）不会
    拍到它**（GDI BitBlt 会拍，WGC 不会——实测确认）。于是面板显示期间也
    能持续拿到"纯背后画面"的低频采样，不存在自我反馈；GUI 侧把采样图
    平滑放大当毛玻璃底图 → 背景推移时玻璃里的画面跟着动，不穿帮。

    采样在独立线程（native 回调），只做 numpy 步长降采样 + 存最新值，
    不碰任何 Qt 对象；GUI 线程用 take() 取。
    """

    def __init__(self):
        self.ok = False
        self._cap = None
        self._ctl = None
        self._lock = threading.Lock()
        self._latest = None                 # (ndarray small, w, h)
        self._rect = (0, 0, 100, 100)       # 物理像素裁剪区
        self._sub = 12                      # 降采样倍数
        self._closed = False

    def set_region(self, x, y, w, h, scale: float = 1.0):
        """更新裁剪区（逻辑坐标 → 物理像素）。"""
        self._rect = (int(x * scale), int(y * scale),
                      max(2, int(w * scale)), max(2, int(h * scale)))

    def start(self, monitor_index: int = 1) -> bool:
        try:
            import numpy as np
            from windows_capture import WindowsCapture
        except Exception:                   # noqa: BLE001
            return False
        try:
            cap = WindowsCapture(cursor_capture=False, draw_border=False,
                                 monitor_index=monitor_index,
                                 minimum_update_interval=90)

            @cap.event
            def on_frame_arrived(frame, control):
                try:
                    if self._closed:
                        return
                    buf = frame.frame_buffer
                    x, y, w, h = self._rect
                    sub = self._sub
                    crop = np.ascontiguousarray(
                        buf[y:y + h:sub, x:x + w:sub, :3])
                    with self._lock:
                        self._latest = crop
                except Exception:           # noqa: BLE001
                    pass

            @cap.event
            def on_closed():
                self._closed = True

            self._cap = cap
            self._ctl = cap.start_free_threaded()
            self.ok = True
            return True
        except Exception:                   # noqa: BLE001
            self.ok = False
            return False

    def take(self):
        with self._lock:
            latest = self._latest
            self._latest = None             # 消费式：只转最新一帧
        return latest

    def stop(self):
        self._closed = True
        try:
            if self._ctl is not None:
                self._ctl.stop()
        except Exception:                   # noqa: BLE001
            pass


def load_ui_state() -> dict:
    try:
        with open(UI_STATE_FILE, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_ui_state(state: dict) -> None:
    try:
        os.makedirs(os.path.dirname(UI_STATE_FILE), exist_ok=True)
        with open(UI_STATE_FILE, "w", encoding="utf-8") as fh:
            json.dump(state, fh)
    except OSError:
        pass


def width_of(pressure: float) -> float:
    """落笔线宽（设置可调）。数位板 pressure 实测恒为 ~0.02（未映射），
    不可用；真实粗细由 _chain 里的速度模拟（快细慢粗）逐点决定。"""
    return float(appcfg.SETTINGS["base_width"])


def width_from_speed(dist: float) -> float:
    """速度→线宽：按原始帧间距（Windows Ink ~8-16ms/帧）估算。
    基准/调制幅度均由设置控制（默认 5.0px 慢写 / 降 3.2px 快写）。"""
    base = float(appcfg.SETTINGS["base_width"])
    span = float(appcfg.SETTINGS["speed_span"])
    return base - span * min(1.0, dist / 22.0)


def apply_stroke_taper(stroke: list[tuple]):
    """笔锋包络（落笔成型）：起笔轻入 → 行笔饱满 → 收笔出锋。

    手机手写键盘没有压感却能写出明显笔锋，靠的就是这种按**弧长位置**
    调制线宽的算法包络（外加轨迹平滑）——不是压感，是"贴向默认笔锋"。
    只影响显示；识别用的 (x, y) 不受影响。
    """
    n = len(stroke)
    if n < 2:
        return stroke
    arc = [0.0]
    for (x0, y0, _), (x1, y1, _) in zip(stroke, stroke[1:]):
        arc.append(arc[-1] + math.hypot(x1 - x0, y1 - y0))
    total = arc[-1]
    if total < 6.0:                     # 点/极短笔：不做包络，保持点感
        return stroke

    # 头尾包络长度随笔长自适应（长笔出锋更长，但设上限）
    head_u = min(0.20, (12.0 + 0.03 * total) / total)
    tail_u = min(0.46, max(10.0, min(48.0, 0.10 * total + 10.0)) / total)

    out = []
    head_start = float(appcfg.SETTINGS["taper_head"])
    mid_gain = float(appcfg.SETTINGS["taper_mid"])
    tail_gain = float(appcfg.SETTINGS["taper_tail"])
    for (x, y, w), a in zip(stroke, arc):
        u = a / total
        f = 1.0
        if u < head_u:                  # 起笔：轻入（强度可调）
            f *= head_start + (1.0 - head_start) * ((u / head_u) ** 0.8)
        # 行笔中段鼓肚（模拟饱蘸墨的笔腹，强度可调）
        f *= 1.0 + mid_gain * math.exp(-((u - 0.45) ** 2) / (2 * 0.24 ** 2))
        if u > 1.0 - tail_u:            # 收笔：出锋（强度可调）
            k = (u - (1.0 - tail_u)) / tail_u
            f *= 1.0 - tail_gain * (k ** 1.05)
        out.append((x, y, max(0.35, w * f)))

    # 沿弧长做对称 3 点宽度平滑，消除台阶
    if len(out) > 2:
        sm = [out[0]]
        for i in range(1, len(out) - 1):
            w2 = 0.25 * out[i - 1][2] + 0.5 * out[i][2] + 0.25 * out[i + 1][2]
            sm.append((out[i][0], out[i][1], w2))
        sm.append(out[-1])
        out = sm
    return out


# ---------------------------------------------------------------------------
# SendInput Unicode 注入
#
# 历史 bug（2026-10-05 用户机 panel.log 实测，每次上屏必崩/必失败）：
# 1) INPUT(1, KEYBDINPUT(...)) 给匿名联合传了成员类型，抛
#    TypeError: incompatible types, KEYBDINPUT instead of _I
# 2) 联合体只含 KEYBDINPUT → x64 下 sizeof(INPUT)=32、真实要求 40，
#    SendInput 会因 cbSize 不符直接返回 0（即使不崩也注入失败）
# 3) 按 utf-16-le 的**字节**迭代发键，一个汉字 = 2 个错误码元
# 修复：联合体补全 MOUSEINPUT 对齐；按 16bit 码元构造（代理对拆两码元）。
# ---------------------------------------------------------------------------

_PUL = ctypes.POINTER(ctypes.c_ulong)


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", _PUL)]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD),
                ("dwFlags", wt.DWORD), ("time", wt.DWORD),
                ("dwExtraInfo", _PUL)]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wt.DWORD), ("wParamL", wt.WORD), ("wParamH", wt.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", _KEYBDINPUT), ("mi", _MOUSEINPUT), ("hi", _HARDWAREINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wt.DWORD), ("u", _INPUTUNION)]


_INPUT_KEYBOARD = 1
_KEYEVENTF_UNICODE = 0x0004
_KEYEVENTF_KEYUP = 0x0002


def text_to_utf16_units(text: str) -> list[int]:
    """文本 → UTF-16 码元序列（每码元一个 wScan；BMP 外字符拆成代理对）。"""
    data = text.encode("utf-16-le")
    return [int.from_bytes(data[i:i + 2], "little")
            for i in range(0, len(data), 2)]


def send_text_utf16(text: str) -> int:
    """SendInput 以 Unicode 逐码元把文本打进焦点窗口。

    返回 SendInput 实际插入的输入事件数（每码元 2 个：按下+抬起）；
    0 表示失败——常见于目标窗口以管理员权限运行（UIPI 拦截）。
    """
    units = text_to_utf16_units(text)
    if not units:
        return 0
    n = len(units) * 2
    inputs = (_INPUT * n)()
    k = 0
    for unit in units:
        for flags in (_KEYEVENTF_UNICODE, _KEYEVENTF_UNICODE | _KEYEVENTF_KEYUP):
            inp = inputs[k]
            inp.type = _INPUT_KEYBOARD
            inp.u.ki = _KEYBDINPUT(0, unit, flags, 0, None)
            k += 1
    return int(_user32.SendInput(n, inputs, ctypes.sizeof(_INPUT)))


class RecognizeWorker(QThread):
    done = Signal(int, object)
    ready_sig = Signal(bool)

    def __init__(self):
        super().__init__()
        self._pipe = None
        self._jobs: list[tuple[int, int, InkLine, bool]] = []

    def submit(self, token: int, row: int, ink: InkLine, quick: bool = False):
        # 同行旧任务直接丢弃：预识别排队是识别队列堵塞的元凶
        self._jobs = [j for j in self._jobs if j[1] != row]
        self._jobs.append((token, row, ink, quick))
        if not self.isRunning():
            self.start()

    def run(self):
        if self._pipe is None:
            t0 = time.time()
            self._pipe = Pipeline()
            print(f"[hwime] engine loaded in {time.time() - t0:.1f}s", flush=True)
            self.ready_sig.emit(True)
        while self._jobs:
            token, _row, ink, quick = self._jobs.pop(0)
            try:
                res = self._pipe.recognize_line(ink, quick=quick)
            except Exception as e:      # noqa: BLE001
                import traceback
                traceback.print_exc()
                res = {"text": "", "error": str(e), "cells": [], "a_cells": [],
                       "text_b": "", "score_b": 0.0, "repaired": False}
            self.done.emit(token, res)

    def ready(self) -> bool:
        return self._pipe is not None


class HotkeyFilter(QAbstractNativeEventFilter):
    """全局热键过滤器。

    坑（2026-10-06 用户实测"热键没用"的根因）：RegisterHotKey(None, ...)
    注册的热键消息没有窗口，通过**线程消息队列**投递；Qt 对这类消息
    标注为 windows_dispatcher_MSG 而不是 windows_generic_MSG。
    只监听后者 → 回调永远不触发，必须两者都收。
    """

    def __init__(self, on_toggle):
        super().__init__()
        self.on_toggle = on_toggle

    def nativeEventFilter(self, eventType, message):  # noqa: N802
        if eventType in (b"windows_generic_MSG", b"windows_dispatcher_MSG"):
            msg = wt.MSG.from_address(int(message))
            if msg and msg.message == WM_HOTKEY and msg.wParam == HK_TOGGLE:
                print("[hwime] hotkey fired", flush=True)
                self.on_toggle()
        return False, 0


def smooth_stroke(stroke):
    """3 点移动平均轻平滑（保留端点）。"""
    if len(stroke) < 3:
        return stroke
    out = [stroke[0]]
    for i in range(1, len(stroke) - 1):
        a, b, c = stroke[i - 1], stroke[i], stroke[i + 1]
        out.append(((a[0] + 2 * b[0] + c[0]) / 4,
                    (a[1] + 2 * b[1] + c[1]) / 4,
                    (a[2] + 2 * b[2] + c[2]) / 4))
    out.append(stroke[-1])
    return out


class Panel(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                            | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setWindowFlag(Qt.WindowDoesNotAcceptFocus, True)
        self.setFixedSize(980, BAR_H + ECHO_H + ROW_H * N_ROWS + 40)
        self.setWindowOpacity(float(appcfg.SETTINGS["opacity"]))

        # 面板自持数据：每行 = 笔迹列表，每笔 = [(x, y, w), ...]（w 像素宽）
        self.rows: list[list[list[tuple]]] = [[] for _ in range(N_ROWS)]
        self.row_t: list[list[float]] = [[] for _ in range(N_ROWS)]
        self.echo_cells: list[list] = []
        self.preview = ""
        self.a_cells = None
        self.text_b = ""
        self.active_row = 0
        self._drawing = False
        self._cur: list[tuple] = []
        self._last3d: tuple | None = None
        self._drag_at = None
        self._token = 0
        self._commit_next = False
        self._commit_rows: dict[int, tuple] = {}   # token -> (row, ink快照, 笔画id集)
        self._pending_rows: set[int] = set()     # 已在识别中的行，防重复提交
        self.row_last_ms = [0.0] * N_ROWS       # 每行最后一次落笔时刻
        self._last_res = None
        self._strokes_at_preview = 0
        self._last_input_ms = 0.0
        self._pix_cache: dict[int, QPixmap] = {}
        self._probe_n = 0
        self._w_ema = 4.4
        self._pen_active_until = 0.0        # 数位板活跃期（抑制合成鼠标流）
        self._pred_xy: tuple | None = None  # 笔尖预测终点（显示用）
        self._last_move_t = 0.0
        self.on_moved = None                # 面板被拖动后回调（持久化位置）
        self._bg_blur: QPixmap | None = None  # 毛玻璃底图（静态快照兜底）
        self._live: BackdropSampler | None = None   # 实时背景采样器
        self._live_pix: QPixmap | None = None       # 实时模糊底图
        self._live_started = False

        # 墨迹缓存层：已完成的笔画画进位图，paintEvent 只贴图 + 画当前笔
        self._ink_layer = QPixmap(self.width(), ROW_H * N_ROWS)
        self._ink_layer.fill(Qt.transparent)

        # 粒子场（环境漂浮 + 特效爆发）
        self._parts = Particles(self.width(), self.height(),
                                n_ambient=int(appcfg.SETTINGS["ambient_particles"]))
        self._pt_last = 0.0
        self._part_timer = QTimer(self)
        self._part_timer.setInterval(33)            # ~30fps
        self._part_timer.timeout.connect(self._particle_tick)
        self._part_timer.start()
        # 实时毛玻璃采样节拍（~11fps，够跟手又省电）
        self._bg_timer = QTimer(self)
        self._bg_timer.setInterval(90)
        self._bg_timer.timeout.connect(self._backdrop_tick)
        self._bg_timer.start()
        self._blur_applied = False

        self.worker = RecognizeWorker()
        self.worker.done.connect(self._on_result)
        self.worker.ready_sig.connect(self._on_engine_ready)
        QTimer.singleShot(80, self.worker.start)

        self._predict_timer = QTimer(self)
        self._predict_timer.setSingleShot(True)
        self._predict_timer.setInterval(IDLE_PREDICT_MS)
        self._predict_timer.timeout.connect(self._predict_preview)

        self._wrap_timer = QTimer(self)
        self._wrap_timer.setInterval(WRAP_CHECK_MS)
        self._wrap_timer.timeout.connect(self._tick)
        self._wrap_timer.start()

        self._build_bar()
        self.setWindowTitle("hwime")

    # ------------------------------------------------ UI
    def _build_bar(self):
        bar = QWidget(self)
        bar.setGeometry(0, BAR_H + ECHO_H + ROW_H * N_ROWS, self.width(), 40)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(12, 4, 12, 4)
        for label, fn in (("送出", self.submit_line),
                          ("删一笔", self.undo_stroke),
                          ("清行", self.clear_row),
                          ("清空", self.clear_all)):
            # QPushButton + clicked：实例级覆盖 QLabel.mousePressEvent 在
            # PySide6 下虚分发不可靠，曾导致送出键无反应
            b = QPushButton(label)
            b.setFixedSize(78, 30)
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(
                "QPushButton{color:#39415a;font-size:14px;border-radius:10px;"
                "border:1px solid rgba(255,255,255,150);"
                "background:qlineargradient(x1:0,y1:0,x2:0,y2:1,"
                "stop:0 rgba(255,255,255,196), stop:1 rgba(196,205,224,150));}"
                "QPushButton:hover{border:1px solid rgba(255,255,255,230);"
                "background:qlineargradient(x1:0,y1:0,x2:0,y2:1,"
                "stop:0 rgba(255,255,255,240), stop:1 rgba(178,198,236,205));}"
                "QPushButton:pressed{background:rgba(150,165,200,195);}")
            b.clicked.connect(fn)
            lay.addWidget(b)
        lay.addStretch(1)
        self.status = QLabel("加载识别引擎…")
        self.status.setStyleSheet("QLabel{color:#8a8578;font-size:12px;}")
        lay.addWidget(self.status)

    def _set_status(self, s):
        self.status.setText(s)
        self.status.update()

    def _on_engine_ready(self):
        hk = globals().get("_hotkey_desc")
        self._set_status("引擎就绪" + (f" | 热键 {hk}" if hk else ""))

    def _y_rows(self):
        return BAR_H + ECHO_H

    def prepare_backdrop(self):
        """在面板**隐藏时**调用：抓取面板所在屏幕区域 → 降/升采样模糊，
        作为毛玻璃底图（真·背景模糊快照）。

        为什么自采样：本机 Win11 上 SetWindowCompositionAttribute 的亚克力
        染色渲染为实心白磨砂（见 USE_ACRYLIC_ACCENT 注释），系统级模糊不可
        用；自采样放在显示前做，画面里没有面板自身，无反馈残影。显示期间
        不回采（会把自己叠进去），背景变化后底图略旧属预期。
        抓取失败（离屏渲染/受保护内容）置 None → 回退纯渐变底。
        """
        try:
            scr = self.screen() or QApplication.primaryScreen()
            g = self.geometry()
            pix = scr.grabWindow(0, g.x(), g.y(), g.width(), g.height())
            if pix.isNull():
                self._bg_blur = None
                return
            img = pix.toImage().scaled(self.width(), self.height(),
                                       Qt.IgnoreAspectRatio,
                                       Qt.SmoothTransformation)
            # 双级模糊：1/14 与 1/9 降采样 + 平滑放大，近似磨砂玻璃
            for div in (14, 9):
                img = img.scaled(max(1, self.width() // div),
                                 max(1, self.height() // div),
                                 Qt.IgnoreAspectRatio,
                                 Qt.SmoothTransformation)
                img = img.scaled(self.width(), self.height(),
                                 Qt.IgnoreAspectRatio,
                                 Qt.SmoothTransformation)
            self._bg_blur = QPixmap.fromImage(img)
        except Exception:                            # noqa: BLE001
            self._bg_blur = None

    def _start_live_sampler(self) -> bool:
        """启动实时毛玻璃采样器（幂等）。成功后面板/球从捕获中排除。"""
        if self._live is not None:
            return True
        sampler = BackdropSampler()
        if not sampler.start(monitor_index=1):
            print("[hwime] live sampler 不可用，回退静态模糊", flush=True)
            return False
        self._live = sampler
        try:
            _user32.SetWindowDisplayAffinity(
                int(self.winId()), _WDA_EXCLUDEFROMCAPTURE)
            ball = globals().get("_ball")
            if ball is not None and ball.isVisible():
                _user32.SetWindowDisplayAffinity(
                    int(ball.winId()), _WDA_EXCLUDEFROMCAPTURE)
        except Exception:                # noqa: BLE001
            pass
        print("[hwime] live backdrop sampler ON", flush=True)
        return True

    def showEvent(self, ev):
        super().showEvent(ev)
        # 实时毛玻璃采样器：首次显示时启动一次（带上"从捕获中排除"标记，
        # 之后 WGC 拍不到面板/悬浮球本身，显示中也能拿干净背景）
        if not self._live_started:
            self._live_started = True

            def _boot_live():
                if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
                    return                  # 离屏测试环境跳过
                if not bool(appcfg.SETTINGS["live_blur"]):
                    return                  # 设置里关掉了实时模糊
                self._start_live_sampler()
            QTimer.singleShot(80, _boot_live)
        # 亚克力染色在本机会变"实心白磨砂"（见 USE_ACRYLIC_ACCENT 注释），
        # 默认关闭；开启时才需要圆角区域裁剪防方角。
        if USE_ACRYLIC_ACCENT and not self._blur_applied:
            self._blur_applied = True

            def _apply():
                hwnd = int(self.winId())
                set_round_region(hwnd, self.width(), self.height(), RADIUS)
                enable_backdrop_blur(hwnd, ACRYLIC_TINT_PANEL)
            QTimer.singleShot(60, _apply)

    def apply_settings(self):
        """设置界面「应用」后调用：立即生效（不需重启的项）。"""
        self.setWindowOpacity(float(appcfg.SETTINGS["opacity"]))
        self._parts.set_ambient(int(appcfg.SETTINGS["ambient_particles"]))
        want = bool(appcfg.SETTINGS["live_blur"])
        if want and self._live is None and self._live_started:
            if os.environ.get("QT_QPA_PLATFORM") != "offscreen":
                self._start_live_sampler()
        elif not want and self._live is not None:
            try:
                self._live.stop()
            except Exception:                # noqa: BLE001
                pass
            self._live = None
            self._live_pix = None
            self.prepare_backdrop()          # 回退静态快照
        self.update()
        print("[hwime] settings applied", flush=True)

    def _backdrop_tick(self):
        """实时模式：从采样器取最新小图 → 平滑放大当毛玻璃底图。"""
        if self._live is None or not self.isVisible():
            return
        scr = self.screen() or QApplication.primaryScreen()
        self._live.set_region(self.x(), self.y(),
                              self.width(), self.height(),
                              scale=scr.devicePixelRatio())
        arr = self._live.take()
        if arr is None:
            return
        h, w = arr.shape[:2]
        img = QImage(arr.data, w, h, arr.strides[0],
                     QImage.Format_BGR888).copy()
        self._live_pix = QPixmap.fromImage(img)

    def refresh_backdrop_live(self):
        """面板可见时的重采（拖动后）。

        实时模式下采样器自动跟位置，无需处理；仅静态快照模式做
        "瞬隐一帧规避自采样"的退路。
        """
        if self._live is not None:
            return
        was = self.isVisible()
        if was:
            self.hide()
            QApplication.processEvents()
        self.prepare_backdrop()
        if was:
            self.show()

    def _particle_tick(self):
        now = time.monotonic()
        dt = min(0.08, now - self._pt_last) if self._pt_last else 0.033
        self._pt_last = now
        self._parts.step(dt)
        if self.isVisible():
            self.update()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()

        # ---- 毛玻璃底：实时模糊（WGC）/ 静态快照 / 纯渐变 三级兜底 ----
        path = QPainterPath()
        path.addRoundedRect(QRectF(0.5, 0.5, w - 1.0, h - 1.0), RADIUS, RADIUS)
        p.setClipPath(path)
        fr = int(appcfg.SETTINGS["frost"])               # 霜化浓度（设置可调）
        if self._live_pix is not None:
            p.setRenderHint(QPainter.SmoothPixmapTransform)  # 小图平滑放大=模糊
            p.drawPixmap(0, 0, w, h, self._live_pix)
            bg = QLinearGradient(0, 0, 0, h)
            bg.setColorAt(0.0, QColor(250, 252, 255, fr + 12))
            bg.setColorAt(0.55, QColor(240, 245, 252, fr))
            bg.setColorAt(1.0, QColor(226, 234, 248, fr - 10))
            p.fillPath(path, QBrush(bg))
        elif self._bg_blur is not None:
            p.setRenderHint(QPainter.SmoothPixmapTransform)
            p.drawPixmap(0, 0, w, h, self._bg_blur)
            bg = QLinearGradient(0, 0, 0, h)
            bg.setColorAt(0.0, QColor(250, 252, 255, fr + 12))
            bg.setColorAt(0.55, QColor(240, 245, 252, fr))
            bg.setColorAt(1.0, QColor(226, 234, 248, fr - 10))
            p.fillPath(path, QBrush(bg))
        else:
            bg = QLinearGradient(0, 0, 0, h)             # 无快照时纯半透明
            bg.setColorAt(0.0, QColor(250, 252, 255, max(30, fr - 16)))
            bg.setColorAt(0.55, QColor(240, 245, 252, max(24, fr - 26)))
            bg.setColorAt(1.0, QColor(226, 234, 248, max(20, fr - 34)))
            p.fillPath(path, QBrush(bg))
        p.fillPath(path, QBrush(_get_noise_pixmap()))        # 磨砂噪点
        sheen = QLinearGradient(0, 0, w * 0.7, h * 0.6)
        sheen.setColorAt(0.0, QColor(255, 255, 255, 64))
        sheen.setColorAt(0.4, QColor(255, 255, 255, 20))
        sheen.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.fillPath(path, QBrush(sheen))

        # 环境漂浮粒子（底层装饰）
        self._parts.paint_ambient(p)

        # ---- 候选条 ----
        acc = QLinearGradient(0, 6, 0, BAR_H - 6)            # 左侧强调竖条
        acc.setColorAt(0.0, QColor(ACCENT.red(), ACCENT.green(), ACCENT.blue(), 210))
        acc.setColorAt(1.0, QColor(120, 160, 220, 80))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(acc))
        p.drawRoundedRect(QRectF(6, 6, 3.5, BAR_H - 12), 1.75, 1.75)

        f = p.font()
        f.setPixelSize(PREVIEW_FONT)
        p.setFont(f)
        text = self.preview or "在下方纸上书写…停顿自动识别；写满一行或点「送出」上屏"
        p.setPen(QColor(255, 255, 255, 170))                 # 浅投影提升浮起感
        p.drawText(PREVIEW_X0 + 1, 33, text)
        p.setPen(TEXT_MAIN)
        p.drawText(PREVIEW_X0, 32, text)

        if self.text_b:                                       # 路B 小胶囊
            f.setPixelSize(13)
            p.setFont(f)
            chip = "路B " + self.text_b
            tw = p.fontMetrics().horizontalAdvance(chip) + 18
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(90, 140, 190, 40))
            p.drawRoundedRect(QRectF(PREVIEW_X0 - 4, BAR_H - 26, tw, 19), 6, 6)
            p.setPen(QColor(42, 106, 138))
            p.drawText(PREVIEW_X0 + 5, BAR_H - 12, chip)

        sep = QLinearGradient(0, 0, w, 0)                    # 渐隐分隔线
        sep.setColorAt(0.0, QColor(120, 130, 150, 0))
        sep.setColorAt(0.5, QColor(120, 130, 150, 80))
        sep.setColorAt(1.0, QColor(120, 130, 150, 0))
        p.setPen(QPen(QBrush(sep), 1))
        p.drawLine(0, BAR_H, w, BAR_H)

        # ---- 回显行 ----
        f.setPixelSize(12)
        p.setFont(f)
        label = "已上屏"
        lw = p.fontMetrics().horizontalAdvance(label) + 15
        lg = QLinearGradient(0, BAR_H + 12, 0, BAR_H + 33)
        lg.setColorAt(0.0, QColor(255, 255, 255, 150))
        lg.setColorAt(1.0, QColor(158, 198, 168, 110))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(lg))
        p.drawRoundedRect(QRectF(8, BAR_H + 12, lw, 21), 7, 7)
        p.setPen(ECHO_OK)
        p.drawText(15, BAR_H + 27, label)

        x = ECHO_X0
        for cell in self.echo_cells:
            if x + CELL > w - 8:
                break
            p.drawPixmap(x, BAR_H + (ECHO_H - CELL) // 2, self._cell_pixmap(cell))
            x += CELL + CELL_GAP
        p.setPen(QPen(QBrush(sep), 1))
        p.drawLine(0, BAR_H + ECHO_H, w, BAR_H + ECHO_H)

        # ---- 手写行：判定区 + 出血线 ----
        y_rows = self._y_rows()
        for r in range(N_ROWS):
            row_y = y_rows + r * ROW_H
            wz = QLinearGradient(w * 3 / 4, 0, w, 0)         # 判定区渐隐暖色
            wz.setColorAt(0.0, QColor(226, 120, 50, 0))
            wz.setColorAt(1.0, QColor(226, 120, 50, 36))
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(wz))
            p.drawRoundedRect(QRectF(w * 3 / 4, row_y + 2,
                                     w / 4 - 2, ROW_H - 4), 7, 7)
            p.setPen(QPen(BLEED_INNER, 1, Qt.DashLine))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(QRectF(10, row_y + 7, w - 22, ROW_H - 14), 9, 9)
            bl = QLinearGradient(0, row_y + 3, 0, row_y + ROW_H - 3)
            bl.setColorAt(0.0, QColor(226, 120, 50, 70))     # 出界线渐变橙柱
            bl.setColorAt(0.5, QColor(226, 120, 50, 240))
            bl.setColorAt(1.0, QColor(226, 120, 50, 70))
            p.setPen(QPen(QBrush(bl), 2))
            p.drawLine(w - 5, row_y + 3, w - 5, row_y + ROW_H - 3)
            p.setPen(QPen(QBrush(sep), 1))
            p.drawLine(0, row_y + ROW_H, w, row_y + ROW_H)

        # ---- 墨迹：缓存层 + 当前笔 + 笔尖预测 ----
        p.drawPixmap(0, y_rows, self._ink_layer)
        if self._cur:
            p.save()
            p.translate(0, y_rows + self.active_row * ROW_H)
            self._paint_strokes(p, [self._cur])
            if self._pred_xy:
                x0, y0, w0 = self._cur[-1]
                p.setPen(QPen(INK_COLOR, max(1.0, w0 * 0.72),
                              Qt.SolidLine, Qt.RoundCap))
                p.drawLine(QPointF(x0, y0), QPointF(*self._pred_xy))
            p.restore()

        # 特效粒子（顶层闪光）
        self._parts.paint_effects(p)

        # ---- 边框：渐变描边 + 外沿暗线 ----
        p.setClipping(False)
        border = QLinearGradient(0, 0, 0, h)
        border.setColorAt(0.0, QColor(255, 255, 255, 200))
        border.setColorAt(0.45, QColor(255, 255, 255, 80))
        border.setColorAt(1.0, QColor(96, 108, 140, 120))
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QBrush(border), 1.4))
        p.drawRoundedRect(QRectF(0.7, 0.7, w - 1.4, h - 1.4), RADIUS, RADIUS)
        p.setPen(QPen(QColor(60, 70, 100, 46), 1))
        p.drawRoundedRect(QRectF(0.1, 0.1, w - 0.2, h - 0.2), RADIUS + 2, RADIUS + 2)
        p.end()

    def _paint_strokes(self, p: QPainter, strokes):
        p.setPen(Qt.NoPen)
        p.setBrush(INK_COLOR)
        for st in strokes:
            if len(st) == 1:
                x, y, w = st[0]
                p.setPen(QPen(INK_COLOR, w, Qt.SolidLine, Qt.RoundCap))
                p.drawPoint(QPointF(x, y))
                p.setPen(Qt.NoPen)
                continue
            for a, b in zip(st, st[1:]):
                pen = QPen(INK_COLOR, (a[2] + b[2]) / 2, Qt.SolidLine,
                           Qt.RoundCap, Qt.RoundJoin)
                p.setPen(pen)
                p.drawLine(QPointF(a[0], a[1]), QPointF(b[0], b[1]))

    def _cell_pixmap(self, cell) -> QPixmap:
        key = hash(tuple(tuple(pt) for st in cell for pt in st))
        if key not in self._pix_cache:
            while len(self._pix_cache) >= 64:       # 上限防写久内存膨胀
                self._pix_cache.pop(next(iter(self._pix_cache)))
            img = render_cells_image([cell], char_px=CELL, gap_px=0,
                                     stroke_w=4, pad=4)
            qi = QImage(img.tobytes(), img.width, img.height,
                        img.width, QImage.Format_Grayscale8).copy()
            self._pix_cache[key] = QPixmap.fromImage(qi)
        return self._pix_cache[key]

    # ------------------------------------------------ 输入
    def _row_at(self, pos):
        y0 = self._y_rows()
        if y0 <= pos.y() < y0 + ROW_H * N_ROWS:
            r = int((pos.y() - y0) // ROW_H)
            return r, (pos.x(), pos.y() - y0 - r * ROW_H)
        return None, None

    _TABLET_KIND = {
        QEvent.Type.TabletPress: "Press",
        QEvent.Type.TabletMove: "Move",
        QEvent.Type.TabletRelease: "Release",
    }

    def tabletEvent(self, ev):  # noqa: N802 - 数位板（Windows Ink / wintab）
        kind = self._TABLET_KIND.get(ev.type())
        if kind:
            # 标记数位板活跃：数位板输入时 Windows/Qt 还会合成一份鼠标流，
            # 双流同喂会把每一笔拆成「单点桩笔 + 真实笔」（真实数据实测），
            # 起笔/收笔通道全被污染，路 A 直接报废。近期有板事件 → 弃鼠标流。
            self._pen_active_until = time.monotonic() + 0.7
            pr = ev.pressure()
            if self._probe_n < 6 and kind == "Press":   # 压感值探针
                self._probe_n += 1
                print(f"[hwime] pressure probe #{self._probe_n}: {pr:.3f}",
                      flush=True)
            self._pointer(kind, ev.position(),
                          ev.globalPosition().toPoint(),
                          self._frame_points(ev, pr), pr)

    def _pen_active(self) -> bool:
        return time.monotonic() < self._pen_active_until

    def _frame_points(self, ev, pressure):
        """帧内合并采样点 → [(x, y)]。坐标语义存疑时丢弃（防飘飞）。"""
        try:
            pts = ev.points()
        except AttributeError:
            return None
        if not pts or len(pts) < 2:
            return None
        pos = ev.position()
        out = []
        for ep in pts:
            try:
                out.append((ep.x(), ep.y()))
            except Exception:       # noqa: BLE001
                return None
        first = out[0]
        if max(abs(first[0] - pos.x()), abs(first[1] - pos.y())) > 300:
            return None
        return out

    def mousePressEvent(self, ev):  # noqa: N802
        if self._pen_active():
            return                      # 数位板流优先，忽略合成鼠标
        self._pointer("Press", ev.position(), ev.globalPosition().toPoint())

    def mouseMoveEvent(self, ev):  # noqa: N802
        if self._pen_active():
            return
        self._pointer("Move", ev.position(), ev.globalPosition().toPoint(),
                      self._frame_points(ev, 0.5), 0.5)

    def mouseReleaseEvent(self, ev):  # noqa: N802
        if self._pen_active():
            return
        self._pointer("Release", ev.position(), ev.globalPosition().toPoint())

    def mouseDoubleClickEvent(self, ev):  # noqa: N802
        # 笔的双击也走合成鼠标流；候选项栏（y<=40）放行以保留纠正菜单，
        # 其余区域属于书写区，交给数位板流
        if self._pen_active() and not (0 <= ev.position().y() <= 40):
            return
        self._maybe_cands_menu(ev.position(), ev.globalPosition().toPoint())

    def _clamp_y(self, y):
        return min(ROW_H - 4, max(4, y))

    def _pointer(self, kind, pos, gpos, frame_pts=None, pressure=0.5):
        if kind == "Press" and pos.y() < self._y_rows():
            self._drag_at = gpos - self.frameGeometry().topLeft()
            return
        if kind == "Move" and self._drag_at is not None:
            self.move(gpos - self._drag_at)
            return
        if kind == "Release" and self._drag_at is not None:
            self._drag_at = None
            if self.on_moved:
                self.on_moved()             # 记住面板被拖到的位置
            if pos.y() < self._y_rows():
                return

        row, pt = self._row_at(pos)
        if row is None:
            return

        if kind == "Press":
            if self._drawing:               # 上次 release 丢失（写出界）兜底
                self._finish_stroke(self.active_row)
            if row != self.active_row:
                # 打字机语义：写到另一行时，旧行墨迹自动上屏（打字机换行）
                if (self.rows[self.active_row]
                        and self.active_row not in self._pending_rows
                        and time.time() * 1000 - self.row_last_ms[self.active_row]
                        > 900):
                    self.submit_line(self.active_row)
            self.active_row = row
            self._drawing = True
            self._w_ema = width_of(pressure)
            self._last3d = (pt[0], self._clamp_y(pt[1]), self._w_ema)
            self._cur = [self._last3d]
            self._pred_xy = None
            self._last_move_t = 0.0
            self._predict_timer.stop()
            self._last_input_ms = time.time() * 1000
            self.row_last_ms[row] = self._last_input_ms
            self.grabMouse()
        elif kind == "Move" and self._drawing and self.active_row == row:
            # _chain 自带从上一采样点续接，无需手动补 last3d（重复点会
            # 污染速度→线宽的估算）
            self._chain(frame_pts or [(pt[0], pt[1])])   # 稀疏插值 + 增量上屏
            self._last_input_ms = time.time() * 1000
            self.row_last_ms[row] = self._last_input_ms
        elif kind == "Release" and self._drawing:
            self.releaseMouse()
            self._chain([(pt[0], pt[1])])
            self._finish_stroke(row)
            self._last_input_ms = time.time() * 1000
            self.row_last_ms[row] = self._last_input_ms
            right = max((p[0] for st in self.rows[row] for p in st), default=0)
            if right > self.width() - 24:
                self.submit_line(row)       # 顶到出界线，立即换行
            else:
                self._predict_timer.start()
            self.update()

    def _chain(self, seq2d):
        """把衔接序列接入当前笔画：速度粗细 + 稀疏插值 + 笔尖预测。

        宽度只按**原始采样点**的帧间距计算（插值点距恒 ≤6px，测不出
        速度），插值段内的宽度在两端点间线性过渡，并做 EMA 平滑。
        每帧整行重绘（980×170，代价很小）：笔尖预测的"未来线"在下一
        帧被真实笔迹覆盖重画，不会残留，不需要 bbox 簿记。
        """
        prev = self._cur[-1] if self._cur else self._last3d
        for nxt in seq2d:
            nxt = (nxt[0], self._clamp_y(nxt[1]))
            if prev is None:
                self._cur.append((nxt[0], nxt[1], width_of(0)))
                prev = self._cur[-1]
                continue
            if nxt[0] == prev[0] and nxt[1] == prev[1]:
                continue                    # 与上一点重合：跳过，不污染速度/线宽
            # 原始采样点：帧间距决定该点目标宽度
            d_raw = max(abs(nxt[0] - prev[0]), abs(nxt[1] - prev[1]))
            w_target = width_from_speed(d_raw)
            w = self._w_ema = 0.65 * self._w_ema + 0.35 * w_target
            pts = self._interp((prev[0], prev[1]), nxt)
            for i, p in enumerate(pts):
                f = (i + 1) / len(pts)
                q = (p[0], p[1], prev[2] + (w - prev[2]) * f)
                self._cur.append(q)
                prev = q
        self._last3d = self._cur[-1] if self._cur else self._last3d

        # ---- 笔尖预测：按最近两点瞬时速度外推约 1 帧，遮住笔尖滞后 ----
        now = time.monotonic()
        dt = now - self._last_move_t if self._last_move_t else 0.016
        self._last_move_t = now
        self._pred_xy = None
        if len(self._cur) >= 4:
            (x0, y0, _), (x1, y1, _) = self._cur[-2], self._cur[-1]
            dx, dy = x1 - x0, y1 - y0
            d = math.hypot(dx, dy)
            if d > 0.5:
                speed = d / max(dt, 0.004)          # px/s
                pred_ms = float(appcfg.SETTINGS["predict_ms"])
                plen = min(30.0, speed * (pred_ms / 1000.0))
                if plen > 2.0:
                    self._pred_xy = (x1 + dx / d * plen, y1 + dy / d * plen)
        self.update(0, self._y_rows(), self.width(), ROW_H * N_ROWS)

    @staticmethod
    def _interp(a, b):
        """相邻采样点距 >6px 时线性补点，保证曲线平滑。"""
        d = max(abs(b[0] - a[0]), abs(b[1] - a[1]))
        n = int(d // 6)
        if n <= 0:
            return [b]
        return [(a[0] + (b[0] - a[0]) * i / n,
                 a[1] + (b[1] - a[1]) * i / n) for i in range(1, n + 1)]

    def _finish_stroke(self, row):
        self._drawing = False
        if not self._cur:
            self._last3d = None
            self._pred_xy = None
            return
        # 落笔成型：轻平滑 + 笔锋包络（显示用；识别不受影响）
        smoothed = apply_stroke_taper(smooth_stroke(self._cur))
        self.rows[row].append(smoothed)
        self.row_t[row].append(time.time() * 1000)
        # 收笔闪光粒子（在笔画末端，细尘级轻量）
        ex, ey, _ = smoothed[-1]
        self._parts.spawn_burst(ex, ey + self._y_rows() + row * ROW_H,
                                n=9, speed=95.0, gold=0.4)
        # 已完成笔画固化进缓存层（增量，一次 QPainter）
        lp = QPainter(self._ink_layer)
        lp.setRenderHint(QPainter.Antialiasing)
        lp.save()
        lp.translate(0, row * ROW_H)
        self._paint_strokes(lp, [smoothed])
        lp.restore()
        lp.end()
        self._cur = []
        self._last3d = None
        self._pred_xy = None

    def _rebuild_layer(self):
        """undo/clear/commit 后从数据重建墨迹层。"""
        self._ink_layer.fill(Qt.transparent)
        lp = QPainter(self._ink_layer)
        lp.setRenderHint(QPainter.Antialiasing)
        for r, row in enumerate(self.rows):
            lp.save()
            lp.translate(0, r * ROW_H)
            self._paint_strokes(lp, row)
            lp.restore()
        lp.end()

    # ------------------------------------------------ 行满判定
    def _tick(self):
        self._check_wrap()

    def _check_wrap(self):
        """两行独立判定：某行墨迹进入右 1/4 判定区且停顿 2s → 提交该行。"""
        now = time.time() * 1000
        for r in range(N_ROWS):
            row = self.rows[r]
            if not row or r in self._pending_rows:
                continue
            right = max(max(p[0] for p in st) for st in row)
            idle = now - max(self.row_last_ms[r], self._last_input_ms
                             if r == self.active_row else 0) > WRAP_IDLE_MS
            if right > self.width() - self.width() / 4 and idle:
                print(f"[hwime] wrap: auto-commit row {r} "
                      f"(right={right:.0f}, idle>={WRAP_IDLE_MS}ms)",
                      flush=True)
                self.submit_line(r)

    def wheelEvent(self, ev):  # noqa: N802
        d = 0.05 if ev.angleDelta().y() > 0 else -0.05
        self.setWindowOpacity(max(0.35, min(1.0, self.windowOpacity() + d)))

    # ------------------------------------------------ 识别
    def _ink_of(self, row) -> InkLine:
        """面板三元组 → 引擎 InkLine((x, y))，附每笔完成时刻。"""
        ink = InkLine()
        ts = self.row_t[row]
        for i, st in enumerate(self.rows[row]):
            ink.add([(x, y) for x, y, _ in st],
                    t=ts[i] if i < len(ts) else 0.0)
        return ink

    def _predict_preview(self, row=None):
        row = self.active_row if row is None else row
        if not self.rows[row] or not self.worker.ready():
            return
        self._token += 1
        self._set_status("识别中…")
        self.worker.submit(self._token, row, self._ink_of(row), quick=True)

    def submit_line(self, row=None):
        row = self.active_row if row is None else row
        if not self.rows[row] or row in self._pending_rows:
            return
        # 预识别仍对应该行（期间没再落笔）→ 直接用已纠正的 preview
        if (row == self.active_row and self.preview
                and self._last_res is not None
                and self._strokes_at_preview == len(self.rows[row])
                and len(self.preview) == len(self._last_res["cells"])):
            self._pending_rows.add(row)
            self._commit(dict(self._last_res, text=self.preview), row,
                         ink=self._ink_of(row))
            return
        self._pending_rows.add(row)
        self._token += 1
        # 提交快照：识别期间用户新写的笔画不属于本次提交，结束时不得清掉
        ink = self._ink_of(row)
        stroke_ids = {id(st) for st in self.rows[row]}
        self._commit_rows[self._token] = (row, ink, stroke_ids)
        self._set_status("识别中…")
        self.worker.submit(self._token, row, ink, quick=False)

    def _on_result(self, token, res):
        snap = self._commit_rows.pop(token, None)
        if snap is not None:
            # 行级提交：无论期间是否有新预识别，结果必须落地
            commit_row, ink, stroke_ids = snap
            if "error" not in res:
                self._commit(res, commit_row, ink=ink, stroke_ids=stroke_ids)
            else:
                self._set_status(f"识别出错：{res['error']}")
                self._pending_rows.discard(commit_row)
            return
        if token != self._token:            # 过期预识别（期间又写了字）
            return
        if "error" in res:
            self._set_status(f"识别出错：{res['error']}")
            return
        self.a_cells = res["a_cells"]
        self.text_b = res["text_b"]
        self._last_res = res
        self._strokes_at_preview = len(self.rows[self.active_row])
        self.preview = res["text"]
        self._set_status(f"{len(res['cells'])} 字"
                         + ("（已自动修复分割）" if res["repaired"] else ""))
        self.update()

    def _commit(self, res, row, ink=None, stroke_ids=None):
        text = res["text"]
        self._capture(ink or self._ink_of(row), res)
        if text:
            sent = send_text_utf16(text)
            if not sent:
                # UIPI：目标窗口以管理员权限运行，注入被系统拦截。
                # 墨迹保留，用户可切到普通窗口后重按「送出」。
                self._set_status("上屏失败：目标窗口拒绝注入"
                                 "（管理员窗口？）墨迹已保留")
                self._pending_rows.discard(row)
                return
            self.echo_cells.extend(res["cells"])
            while len(self.echo_cells) > 18:
                self.echo_cells.pop(0)
            # 上屏庆典：从笔画末端炸一簇粒子
            burst_pt = None
            src = ink if ink is not None else None
            if src is not None and src.strokes and src.strokes[-1]:
                bx, by = src.strokes[-1][-1]
                burst_pt = (bx, by + self._y_rows() + row * ROW_H)
            if burst_pt:
                self._parts.spawn_burst(burst_pt[0], burst_pt[1],
                                        n=24, speed=175.0, gold=0.6)
            else:
                self._parts.spawn_burst(self.width() * 0.5,
                                        self._y_rows() + ROW_H * 0.5,
                                        n=18, speed=150.0, gold=0.6)
        # 只清掉本次提交的笔画；识别期间新写的笔画保留继续用
        ids = stroke_ids or {id(st) for st in self.rows[row]}
        keep = [(st, t) for st, t in zip(self.rows[row], self.row_t[row])
                if id(st) not in ids]
        self.rows[row] = [st for st, _ in keep]
        self.row_t[row] = [t for _, t in keep]
        self.row_last_ms[row] = 0.0
        self._pending_rows.discard(row)
        self._rebuild_layer()
        if row == self.active_row:
            self.preview = ""
            self.a_cells = None
            self.text_b = ""
            self._last_res = None
            if self.rows[row]:              # 残留新笔画，继续预识别
                self._predict_timer.start()
        self._set_status(f"已上屏 {len(text)} 字")
        self.update()

    def _capture(self, ink: InkLine, res: dict):
        record = {
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "strokes": [[[round(x, 1), round(y, 1)] for x, y in st]
                        for st in ink.strokes],
            "t_ms": ink.t,
            "text_fused": res.get("text"),
            "text_b": res.get("text_b"),
            "a_top1": [c[0][0] if c else "?" for c in res.get("a_cells") or []],
            "a_top3": [[ch for ch, _ in c[:3]] for c in res.get("a_cells") or []],
            "repaired": res.get("repaired"),
        }
        try:
            data_dir = os.path.dirname(CAPTURE_FILE)
            os.makedirs(data_dir, exist_ok=True)
            # 超 64MB 归档到 data/archive/，防止长期使用无限膨胀
            if (os.path.exists(CAPTURE_FILE)
                    and os.path.getsize(CAPTURE_FILE) > 64 * 1024 * 1024):
                arch = os.path.join(data_dir, "archive")
                os.makedirs(arch, exist_ok=True)
                stamp = time.strftime("%Y%m%d-%H%M%S")
                os.replace(CAPTURE_FILE,
                           os.path.join(arch, f"captures-{stamp}.jsonl"))
            with open(CAPTURE_FILE, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            pass

    # ------------------------------------------------ 候选纠正
    def _preview_char_index(self, x: float) -> int:
        """候选条 x 坐标 → 字符下标。

        必须用与 paintEvent 一致的字体度量（pixelSize=PREVIEW_FONT）；
        此前用 self.font() 的度量做命中，字体大小不一致时点第 n 个字
        常常弹到别的字上。
        """
        if not self.preview:
            return -1
        f = self.font()
        f.setPixelSize(PREVIEW_FONT)
        fm = QFontMetricsF(f)
        acc = 0.0
        for i, ch in enumerate(self.preview):
            w = fm.horizontalAdvance(ch)
            if PREVIEW_X0 + acc <= x < PREVIEW_X0 + acc + w:
                return i
            acc += w
        return -1

    def _maybe_cands_menu(self, pos, gpos):
        if not (self.preview and self._last_res):
            return
        if not (0 <= pos.y() <= 40):
            return
        idx = self._preview_char_index(pos.x())
        if idx < 0:
            return
        cells = self._last_res["cells"]
        if idx >= len(cells):
            return
        # 预识别是 quick 通道（无路 A 矩阵）→ 点开菜单时对单块即时识别
        cands = (self.a_cells[idx]
                 if self.a_cells and idx < len(self.a_cells) else None)
        if not cands and self.worker.ready():
            try:
                cands = self.worker._pipe.a.candidates(cells[idx], n=8)
            except Exception:               # noqa: BLE001
                cands = None
        if not cands:
            return
        menu = QMenu(self)
        for ch, score in cands[:8]:
            act = menu.addAction(f"{ch}  {score:.3f}")
            act.triggered.connect(
                lambda _, c=ch, i=idx: self._replace_char(i, c))
        menu.exec(gpos)

    def _replace_char(self, idx, ch):
        self.preview = self.preview[:idx] + ch + self.preview[idx + 1:]
        self._set_status("已纠正，点「送出」上屏")
        # 纠正处来一小撮金色火花
        self._parts.spawn_burst(PREVIEW_X0 + idx * 30 + 14, 28,
                                n=8, speed=70.0, gold=0.85)
        self.update()

    # ------------------------------------------------ 行操作
    def undo_stroke(self):
        row = self.rows[self.active_row]
        if row:
            row.pop()
            if self.row_t[self.active_row]:
                self.row_t[self.active_row].pop()
            self._rebuild_layer()
            self._last_input_ms = time.time() * 1000
            if row:
                self._predict_timer.start()     # 剩余笔画重新预识别
            else:
                self.preview = ""
                self.a_cells = None
                self.text_b = ""
                self._last_res = None
            self.update()

    def clear_row(self):
        self.rows[self.active_row] = []
        self.row_t[self.active_row] = []
        self._rebuild_layer()
        self.preview = ""
        self.a_cells = None
        self.text_b = ""
        self.update()

    def clear_all(self):
        had_ink = any(self.rows[i] for i in range(N_ROWS))
        for i in range(N_ROWS):
            self.rows[i] = []
            self.row_t[i] = []
        self._rebuild_layer()
        self.preview = ""
        self.a_cells = None
        self.text_b = ""
        if had_ink:                                  # 全清时来一层"清扫"粒子
            self._parts.spawn_burst(self.width() * 0.45,
                                    self._y_rows() + ROW_H * 0.5,
                                    n=14, speed=110.0, gold=0.25)
        self.update()


class Ball(QWidget):
    """常驻悬浮球：点击开/关输入法面板，按住可拖到任意位置。

    视觉（v2.7 加料）：径向渐变球体 + 脉冲光晕（收起时更亮，提示可召
    回）+ 两颗环绕粒子 + 悬停增亮；亚克力磨砂底。球位置与面板位置
    持久化到 data/ui_state.json。
    """

    SIZE = 76                    # 窗口尺寸（约 52px 球体 + 光晕/粒子边距）
    BALL_R = 24                  # 球体半径

    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                            | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setWindowFlag(Qt.WindowDoesNotAcceptFocus, True)
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setCursor(Qt.PointingHandCursor)
        self.on_click = None
        self.on_moved = None
        self._press_gpos = None
        self._press_pos = None
        self._moved = False
        self._hover = False
        self._t0 = time.monotonic()
        self._anim = QTimer(self)
        self._anim.setInterval(40)                  # 25fps 动画
        self._anim.timeout.connect(self._tick)
        self._anim.start()

    def _tick(self):
        if self.isVisible():
            self.update()

    # 注意：悬浮球**不挂亚克力染色**——染色会铺满整个窗口矩形形成
    # 一个"黑方块"包裹球体（用户实测违和）。球用纯 per-pixel 透明绘制。

    def enterEvent(self, ev):  # noqa: N802
        self._hover = True

    def leaveEvent(self, ev):  # noqa: N802
        self._hover = False

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        s = float(self.SIZE)
        cx = cy = s / 2
        r = float(self.BALL_R)
        t = time.monotonic() - self._t0

        # 面板是否可见（可见时球更安静，收起时脉冲更亮提示召回）
        panel = globals().get("_panel")
        panel_open = bool(panel is not None and panel.isVisible())
        pulse = 0.5 + 0.5 * math.sin(t * 2.6)

        # 脉冲光晕（三层渐隐圆）
        halo_base = 46 if panel_open else 92
        if self._hover:
            halo_base += 40
        for i in range(3):
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(118, 150, 224,
                              int(halo_base * pulse / (i + 1.2))))
            rr = r + 2 + i * 3.0
            p.drawEllipse(QPointF(cx, cy), rr, rr)

        # 球体：径向渐变（左上受光）
        rg = QRadialGradient(cx - r * 0.5, cy - r * 0.6, r * 2.0)
        rg.setColorAt(0.0, QColor(84, 104, 156, 246))
        rg.setColorAt(0.55, QColor(38, 52, 92, 242))
        rg.setColorAt(1.0, QColor(13, 19, 38, 246))
        p.setBrush(QBrush(rg))
        p.drawEllipse(QPointF(cx, cy), r, r)

        # 内圈高光描边
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(255, 255, 255, 130 if self._hover else 90), 1.3))
        p.drawEllipse(QPointF(cx, cy), r - 2.2, r - 2.2)
        # 顶部弧光
        p.setPen(QPen(QColor(255, 255, 255, 70), 2.2, Qt.SolidLine, Qt.RoundCap))
        p.drawArc(QRectF(cx - r + 5, cy - r + 5, (r - 5) * 2, (r - 5) * 2),
                  70 * 16, 70 * 16)

        # 环绕粒子（两颗细尘级微粒，异速反向，沿球缘轨道）
        for k, (spd, rad, sz, aa) in enumerate(
                ((2.1, r + 3.0, 1.5, 150), (-1.4, r + 7.5, 1.0, 105))):
            ang = t * spd + k * 2.1
            px = cx + math.cos(ang) * rad
            py = cy + math.sin(ang) * rad * 0.94
            if 1 < px < s - 1 and 1 < py < s - 1:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(206, 222, 255, aa))
                p.drawEllipse(QPointF(px, py), sz, sz)

        # 字形（带投影）
        f = p.font()
        f.setPixelSize(25)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(20, 28, 52, 160))
        p.drawText(self.rect().translated(0, 1), Qt.AlignCenter, "手")
        p.setPen(QColor(246, 248, 252))
        p.drawText(self.rect(), Qt.AlignCenter, "手")
        p.end()

    def mousePressEvent(self, ev):  # noqa: N802
        self._press_gpos = ev.globalPosition().toPoint()
        self._press_pos = self.pos()
        self._moved = False

    def mouseMoveEvent(self, ev):  # noqa: N802
        if self._press_gpos is None:
            return
        delta = ev.globalPosition().toPoint() - self._press_gpos
        if abs(delta.x()) + abs(delta.y()) > 4:
            self._moved = True
        self.move(self._press_pos + delta)

    def mouseReleaseEvent(self, ev):  # noqa: N802
        if self._press_gpos is None:
            return
        was_moved = self._moved
        self._press_gpos = None
        if was_moved:
            if self.on_moved:
                self.on_moved()
        elif self.on_click:
            self.on_click()


def _place_panel_near_ball(panel, ball):
    """面板初始出现在悬浮球上方（放不下则放下方），横向右对齐球。"""
    scr = QApplication.primaryScreen().availableGeometry()
    pw, ph = panel.width(), panel.height()
    x = min(max(scr.left(), ball.x() + ball.width() - pw),
            scr.right() - pw)
    y = ball.y() - ph - 10
    if y < scr.top():
        y = min(ball.y() + ball.height() + 10, scr.bottom() - ph)
    panel.move(x, y)


def _save_ui():
    ball = globals().get("_ball")
    panel = globals().get("_panel")
    state = {}
    if ball is not None:
        state["ball"] = [ball.x(), ball.y()]
    if panel is not None:
        state["panel"] = [panel.x(), panel.y()]
    save_ui_state(state)


def _fallback_tray_icon() -> QIcon:
    """图标文件缺失时的兜底：程序化画一个球。"""
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(22, 33, 58))
    p.drawEllipse(2, 2, 60, 60)
    f = p.font()
    f.setPixelSize(34)
    f.setBold(True)
    p.setFont(f)
    p.setPen(QColor(246, 248, 252))
    p.drawText(pm.rect(), Qt.AlignCenter, "手")
    p.end()
    return QIcon(pm)


def _build_tray(app, panel, ball, hotkey_desc: str) -> QSystemTrayIcon:
    """系统托盘：显示/隐藏、设置、开机自启、关于、退出。"""
    icon_path = os.path.join(_ROOT, "docs", "hwime.ico")
    icon = QIcon(icon_path) if os.path.exists(icon_path) \
        else _fallback_tray_icon()
    tray = QSystemTrayIcon(icon, app)
    tray.setToolTip(f"{appcfg.APP_NAME} v{appcfg.APP_VERSION}")

    tmenu = QMenu()

    def open_settings():
        from settings import SettingsDialog
        dlg = SettingsDialog(None, hotkey_desc=hotkey_desc,
                             on_apply=panel.apply_settings)
        dlg.exec()

    def quit_app():
        try:
            if panel._live is not None:
                panel._live.stop()
        except Exception:                    # noqa: BLE001
            pass
        _save_ui()
        tray.hide()
        app.quit()

    def about():
        QMessageBox.information(
            None, f"关于 {appcfg.APP_NAME}",
            f"{appcfg.APP_NAME} v{appcfg.APP_VERSION}\n\n"
            "悬浮手写输入法：数位板连续书写中文，\n"
            "双路识别（OCR 主导上屏 + 笔迹 CNN 纠正）。\n\n"
            "https://github.com/Elysia-laoda/handwrite-ime")

    act_toggle = tmenu.addAction("显示 / 隐藏面板")
    act_settings = tmenu.addAction("设置…")
    tmenu.addSeparator()
    act_auto = tmenu.addAction("开机自动运行")
    act_auto.setCheckable(True)
    act_auto.setChecked(appcfg.is_autostart())
    tmenu.addSeparator()
    act_about = tmenu.addAction("关于")
    act_quit = tmenu.addAction("退出")

    act_toggle.triggered.connect(lambda: _toggle_panel(app))
    act_settings.triggered.connect(open_settings)
    act_about.triggered.connect(about)
    act_quit.triggered.connect(quit_app)

    def on_auto(checked):
        if not appcfg.set_autostart(checked):
            print("[hwime] 写自启动注册表失败", flush=True)
        act_auto.setChecked(appcfg.is_autostart())
    act_auto.toggled.connect(on_auto)

    tray.activated.connect(
        lambda reason: _toggle_panel(app)
        if reason == QSystemTrayIcon.DoubleClick else None)
    tray.setContextMenu(tmenu)
    tray.show()
    return tray


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)     # 托盘常驻，关窗不退出
    boot_mode = "--boot" in sys.argv         # 开机自启：静默进托盘
    user32 = ctypes.windll.user32
    # 高漫等驱动常占用 Ctrl+Alt+H：按序尝试多个候选热键
    candidates = [("Ctrl+Alt+H", ord("H")), ("Ctrl+Alt+K", ord("K")),
                  ("Ctrl+Alt+G", ord("G")), ("Ctrl+Alt+F9", 0x78),
                  ("Ctrl+Alt+F10", 0x79)]
    hotkey_desc = ""
    for desc, vk in candidates:
        if user32.RegisterHotKey(None, HK_TOGGLE, MOD_CONTROL | MOD_ALT, vk):
            hotkey_desc = desc
            print(f"[hwime] 热键 = {desc}", flush=True)
            break
        print(f"[hwime] {desc} 被占用", flush=True)
    if not hotkey_desc:
        print("[hwime] 所有候选热键注册失败，仅能用悬浮球", flush=True)

    app.installNativeEventFilter(HotkeyFilter(lambda: _toggle_panel(app)))

    scr = app.primaryScreen().availableGeometry()
    state = load_ui_state()

    panel = Panel()
    ball = Ball()
    globals()["_panel"] = panel
    globals()["_ball"] = ball
    globals()["_hotkey_desc"] = hotkey_desc

    ball.on_click = lambda: _toggle_panel(app)
    ball.on_moved = _save_ui
    # 拖动面板后：存位置 + 隔一帧重采毛玻璃底图（新位置的背景）
    panel.on_moved = lambda: (_save_ui(),
                              QTimer.singleShot(30, panel.refresh_backdrop_live))

    # 悬浮球位置：恢复上次 / 默认右下角
    bx, by = state.get("ball", [None, None])
    if not (bx is not None
            and scr.left() <= bx <= scr.right() - ball.width()
            and scr.top() <= by <= scr.bottom() - ball.height()):
        bx = scr.right() - ball.width() - 24
        by = scr.bottom() - ball.height() - 24
    ball.move(bx, by)

    # 面板位置：恢复上次（越界则钳回屏内）/ 默认贴着悬浮球上方
    px, py = state.get("panel", [None, None])
    if px is not None:
        px = min(max(scr.left(), px), scr.right() - panel.width())
        py = min(max(scr.top(), py), scr.bottom() - panel.height())
        panel.move(px, py)
    else:
        _place_panel_near_ball(panel, ball)

    if hotkey_desc:
        panel._set_status(f"引擎加载中… 热键 {hotkey_desc}")
    panel.prepare_backdrop()          # 显示前采样背景（此时画面无自身/无悬浮球）
    ball.show()
    if not boot_mode:
        panel.show()
    tray = _build_tray(app, panel, ball, hotkey_desc)
    globals()["_tray"] = tray
    print(f"[hwime] {appcfg.APP_NAME} v{appcfg.APP_VERSION} 就绪"
          f"{'（自启动托盘模式）' if boot_mode else ''}", flush=True)
    _save_ui()
    sys.exit(app.exec())


def _toggle_panel(app=None):
    """悬浮球/热键共用的开合：显式开，显式关，位置保持不动。

    开启前重采毛玻璃底图（此刻面板还隐藏着，画面里没有自己）。
    """
    panel = globals().get("_panel")
    if panel is None:
        return
    if panel.isVisible():
        panel.hide()
        print("[hwime] panel hidden", flush=True)
    else:
        panel.prepare_backdrop()
        panel.show()
        print("[hwime] panel shown", flush=True)


if __name__ == "__main__":
    main()

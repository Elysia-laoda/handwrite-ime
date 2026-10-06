"""SendInput 上屏链路专项测试。

离线部分（默认）：结构体尺寸、码元拆分、注入参数构造（打桩 SendInput）。
真机部分（--live）：创建真实窗口 + QLineEdit，聚焦后真发 SendInput，
校验文本落入编辑框——验证修复后的上屏全链路。

背景：2026-10-05 真实运行时每次上屏都崩：
  TypeError: incompatible types, KEYBDINPUT instance instead of _I instance
且旧实现按 utf-16-le 字节迭代（一个汉字=2 个错误码元）、INPUT 联合体
缺少 MOUSEINPUT（x64 下 sizeof=32≠40，SendInput 必失败）。
"""
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, "engine"))
sys.path.insert(0, _HERE)

if "--live" not in sys.argv:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import ctypes

import main as pm


def test_structure():
    sizes = {"INPUT": (ctypes.sizeof(pm._INPUT), 40),
             "KEYBDINPUT": (ctypes.sizeof(pm._KEYBDINPUT), 24),
             "MOUSEINPUT": (ctypes.sizeof(pm._MOUSEINPUT), 32)}
    ok = True
    for name, (got, want) in sizes.items():
        flag = "OK" if got == want else "FAIL"
        print(f"[{flag}] sizeof({name}) = {got} (期望 {want}, x64)")
        ok &= got == want
    return ok


def test_units():
    cases = [("你好", [0x4F60, 0x597D]),
             ("A1", [0x41, 0x31]),
             ("𠀀", [0xD840, 0xDC00])]        # U+20000 代理对
    ok = True
    for text, want in cases:
        got = pm.text_to_utf16_units(text)
        flag = "OK" if got == want else "FAIL"
        print(f"[{flag}] units({text!r}) = {[hex(x) for x in got]}")
        ok &= got == want
    return ok


class _Capture:
    def __init__(self):
        self.calls = []

    def __call__(self, n, arr, size):
        self.calls.append((n, arr, size))
        return n


class _Shim:
    """把 _user32 换成可打桩的代理，其余方法透传真实 DLL。"""

    def __init__(self, real, cap):
        self._real = real
        self._cap = cap

    def __getattr__(self, name):
        if name == "SendInput":
            return self._cap
        return getattr(self._real, name)


def test_inject_params():
    cap = _Capture()
    orig = pm._user32
    pm._user32 = _Shim(orig, cap)
    try:
        text = "你好A"
        ret = pm.send_text_utf16(text)
    finally:
        pm._user32 = orig

    n, arr, size = cap.calls[-1]
    units = pm.text_to_utf16_units(text)
    ok = (ret == len(units) * 2          # 每码元 2 个事件（按下+抬起）
          and size == 40
          and n == len(units) * 2
          and [arr[i].u.ki.wScan for i in range(0, n, 2)] == units
          and all(arr[i].type == 1 for i in range(n))
          and [arr[i].u.ki.dwFlags for i in (0, 1)] == [0x4, 0x6])
    print(f"[{'OK' if ok else 'FAIL'}] 注入参数: ret={ret}, cbSize={size}, "
          f"n={n}, 首码元={hex(arr[0].u.ki.wScan)}, "
          f"flags={[hex(arr[i].u.ki.dwFlags) for i in (0, 1)]}")
    return ok


def test_live():
    """真机验证：窗口聚焦→SendInput→文本落入编辑框。"""
    from PySide6.QtCore import QTimer, Qt
    from PySide6.QtWidgets import QApplication, QLineEdit, QWidget, QVBoxLayout

    app = QApplication(sys.argv)
    win = QWidget()
    win.setWindowTitle("hwime send-text live test")
    lay = QVBoxLayout(win)
    edit = QLineEdit()
    lay.addWidget(edit)
    win.resize(360, 80)
    win.setWindowFlag(Qt.WindowStaysOnTopHint, True)
    win.show()
    win.activateWindow()
    win.raise_()
    edit.setFocus()

    user32 = pm._user32
    result = {"ok": False, "why": ""}
    TEXT = "你好A1世界"

    def run():
        app.processEvents()
        fg = user32.GetForegroundWindow()
        mine = int(win.winId())
        if fg != mine:
            result["why"] = f"前台窗口不是测试窗（fg={fg}, mine={mine}），跳过真机注入"
            app.quit()
            return
        n = pm.send_text_utf16(TEXT)
        t0 = time.time()
        while time.time() - t0 < 1.5:
            app.processEvents()
            time.sleep(0.02)
            if edit.text() == TEXT:
                break
        got = edit.text()
        result["ok"] = got == TEXT
        result["why"] = f"sent={n} events, edit.text()={got!r}"
        app.quit()

    QTimer.singleShot(600, run)                    # 等窗口真正拿到焦点
    QTimer.singleShot(8000, app.quit)              # 硬超时
    app.exec()
    win.close()
    print(f"[{'OK' if result['ok'] else 'FAIL'}] 真机注入: {result['why']}")
    return result["ok"]


if __name__ == "__main__":
    print("== 静态结构 ==")
    a = test_structure()
    print("== 码元拆分 ==")
    b = test_units()
    print("== 注入参数（打桩） ==")
    c = test_inject_params()
    d = True
    if "--live" in sys.argv:
        print("== 真机注入 ==")
        d = test_live()
    print("RESULT:", "PASS" if (a and b and c and d) else "FAIL")
    sys.exit(0 if (a and b and c and d) else 1)

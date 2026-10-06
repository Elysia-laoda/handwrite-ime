"""全局热键链路真机测试。

验证三件事的闭环：
1. RegisterHotKey(None, ...) 注册成功
2. Qt 的 NativeEventFilter 能收到线程消息队列投递的 WM_HOTKEY
   （关键怀疑点：这类消息 Qt 标注为 windows_dispatcher_MSG，而不是
   窗口消息的 windows_generic_MSG；历史版本只监听后者 → 热键失灵）
3. 组合键经 SendInput 合成后确实触发了注册的回调

跑法：python panel\\test_hotkey.py
（期间会合成一次 Ctrl+Alt+H；该组合是 hwime 自己的热键，不会外泄）
"""
import ctypes
import ctypes.wintypes as wt
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, "engine"))
sys.path.insert(0, _HERE)

# 注意：不能用 offscreen 平台——那样没有 Windows 消息循环，
# 热键消息根本不会被投递，测试会假阴性。必须在真实平台下跑。

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

import main as pm

VK_CONTROL, VK_MENU, VK_H = 0x11, 0x12, ord("H")


def send_combo(hotkey_id_vk):
    """合成 Ctrl+Alt+<vk>（按下再抬起）。"""
    seq = [(VK_CONTROL, 0), (VK_MENU, 0), (hotkey_id_vk, 0),
           (hotkey_id_vk, pm._KEYEVENTF_KEYUP), (VK_MENU, pm._KEYEVENTF_KEYUP),
           (VK_CONTROL, pm._KEYEVENTF_KEYUP)]
    arr = (pm._INPUT * len(seq))()
    for i, (vk, flags) in enumerate(seq):
        arr[i].type = pm._INPUT_KEYBOARD
        arr[i].u.ki = pm._KEYBDINPUT(vk, 0, flags, 0, None)
    n = pm._user32.SendInput(len(seq), arr, ctypes.sizeof(pm._INPUT))
    return n


def main():
    app = QApplication(sys.argv)
    user32 = pm._user32

    fired = []
    filt = pm.HotkeyFilter(lambda: fired.append(time.time()))
    app.installNativeEventFilter(filt)

    ok_reg = bool(user32.RegisterHotKey(None, pm.HK_TOGGLE,
                                        pm.MOD_CONTROL | pm.MOD_ALT, VK_H))
    print(f"RegisterHotKey(Ctrl+Alt+H) = {ok_reg}")
    if not ok_reg:
        print("RESULT: SKIP (组合键被占用，无法测试)")
        return 2

    result = {"pass": False}

    def press():
        n = send_combo(VK_H)
        print(f"合成按键已发送: {n} events")

    def check():
        if fired:
            result["pass"] = True
            print(f"回调触发 ✓ (延迟 {fired[0] - t0:.2f}s)")
            app.quit()

    t0 = time.time()
    QTimer.singleShot(500, press)
    poll = QTimer()
    poll.timeout.connect(check)
    poll.start(100)
    QTimer.singleShot(4000, app.quit)                # 硬超时

    app.exec()
    user32.UnregisterHotKey(None, pm.HK_TOGGLE)
    print("RESULT:", "PASS" if result["pass"] else "FAIL（WM_HOTKEY 未送达 Qt 回调）")
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())

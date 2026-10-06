"""面板离屏冒烟测试：真实引擎 + 打桩的 SendInput。

流程：等引擎就绪 → 注入合成墨迹 → 预识别 → 送出 → 校验回显与上屏调用。
"""
import os
import sys
import time

os.environ["QT_QPA_PLATFORM"] = "offscreen"
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, "engine"))
sys.path.insert(0, _HERE)

import xml.etree.ElementTree as ET

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

import main as pm
from hwengine.paths import corpus_path

XML = corpus_path("handwriting-zh_CN.xml")

sent = []
# 打桩：返回注入码元数（真实实现返回 SendInput 成功数，0 表示失败）
pm.send_text_utf16 = lambda text: (sent.append(text), len(text))[1]
# 冒烟数据别混进生产采集文件（它是真实笔迹回归集的来源）
pm.CAPTURE_FILE = os.path.join(_ROOT, "_session-temp", "smoke_captures.jsonl")


def load_char(ch):
    for event, elem in ET.iterparse(XML, events=("end",)):
        if elem.tag != "character":
            continue
        if elem.findtext("utf8") == ch:
            pts = [[(float(p.get("x")), float(p.get("y")))
                    for p in st.findall("point")]
                   for st in elem.find("strokes").findall("stroke")]
            elem.clear()
            return pts
        elem.clear()
    return None


def main():
    app = QApplication(sys.argv)

    # 硬自杀：任何挂死都在 90s 内结束进程，绝不允许拖垮系统
    import os

    QTimer.singleShot(90_000, lambda: (print("HARD TIMEOUT", flush=True),
                                       os._exit(3)))

    panel = pm.Panel()
    panel.show()

    state = {"phase": "wait_ready", "ticks": 0}

    def tick():
        state["ticks"] += 1
        if state["phase"] == "wait_ready":
            if panel.worker.ready():
                state["phase"] = "inject"
        elif state["phase"] == "inject":
            # 把「你好」两个字放进手写行（错开摆放模拟两个独立字块）
            row = panel.rows[0]
            xoff = 0
            for ch in "你好":
                strokes = load_char(ch)
                for st in strokes:
                    row.append([(x / 3 + xoff, y / 3, 3.0) for x, y in st])
                panel.row_t[0].append(state["ticks"] * 100)
                xoff += 240
            panel._rebuild_layer()
            state["phase"] = "predict"
            panel._predict_preview()
        elif state["phase"] == "predict":
            if state["ticks"] == 6:      # 注入完成后立刻做一次渲染验证
                img = panel.grab()       # 强制走 paintEvent
                qimg = img.toImage()
                dark = 0
                y0 = pm.BAR_H + pm.ECHO_H
                for yy in range(y0 + 4, y0 + pm.ROW_H - 4, 2):
                    for xx in range(10, panel.width() - 10, 2):
                        g = qimg.pixelColor(xx, yy)
                        if g.lightness() < 100:
                            dark += 1
                print(f"render check: dark ink pixels in row1 = {dark}")
                if dark < 20:
                    print("FAIL: ink not rendered (paintEvent broken)")
                    app.quit()
                    return
            if panel.preview:
                print(f"preview: {panel.preview}")
                state["phase"] = "submit"
                panel.submit_line()
            elif state["ticks"] > 40:
                print("FAIL: preview never arrived")
                print("status:", panel.status.text())
                print("jobs pending:", len(panel.worker._jobs),
                      "worker running:", panel.worker.isRunning())
                app.quit()
        elif state["phase"] == "submit":
            if sent:
                print(f"sent: {sent[0]!r}")
                print(f"echo cells: {len(panel.echo_cells)}")
                ok = len(panel.echo_cells) == 2 and not panel.rows[0]
                print("SMOKE " + ("PASS" if ok else "FAIL"))
                if not ok:
                    app.quit()
                    return
                state["phase"] = "wrap_inject"
            elif state["ticks"] > 60:
                print("FAIL: commit never arrived")
                app.quit()
        elif state["phase"] == "wrap_inject":
            # 换行自动上屏：在判定区注入一笔，停笔超过 1.2s 应自动提交
            row = panel.rows[0]
            row.append([(760, 40, 4.0), (900, 44, 4.0), (940, 46, 4.0)])
            panel.row_t[0].append(state["ticks"] * 100)
            panel.row_last_ms[0] = time.time() * 1000 - 1600
            panel._rebuild_layer()
            state["wrap_sent0"] = len(sent)
            state["phase"] = "wrap_wait"
            state["ticks0"] = state["ticks"]
        elif state["phase"] == "wrap_wait":
            if len(sent) > state["wrap_sent0"]:
                print("WRAP TEST PASS")
                print("SMOKE PASS (with wrap)")
                app.quit()
            elif state["ticks"] - state["ticks0"] > 24:   # ~5s 超时
                print("FAIL: wrap auto-commit never triggered")
                app.quit()

    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(200)
    app.exec()


if __name__ == "__main__":
    main()

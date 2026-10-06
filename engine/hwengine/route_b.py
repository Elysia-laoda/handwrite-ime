"""路 B：整行墨迹渲染成图 → PP-OCR rec 直接识别（跳过检测器）。

我们永远知道墨迹在哪（是自己渲染的），检测器只是浪费且会切碎结果。
"""
from __future__ import annotations

import threading

import numpy as np
from rapidocr import RapidOCR

from .ink import Stroke, render_line_image


class RouteB:
    """整行笔迹 → 识别文本 (+平均分)。线程安全（RapidOCR 内部有锁）。"""

    def __init__(self, line_h_px: int = 64):
        self._ocr = RapidOCR()
        self.line_h_px = line_h_px

    def recognize_line(self, strokes: list[Stroke]) -> tuple[str, float]:
        if not strokes:
            return "", 0.0
        img = render_line_image(strokes, line_h_px=self.line_h_px)
        arr = np.asarray(img.convert("RGB"))
        res = self._ocr.recognize_txt(arr)
        if res is None or not res.txts:
            return "", 0.0
        text = "".join(res.txts)
        score = float(np.mean(res.scores)) if res.scores else 0.0
        return text, score

"""路 A：在线笔迹 CNN（cnn_chinese_hw 预训权重封装）。

上游 recognizer 只在加载/服务路径引用作者私有的 iso_tools 包，
主推理路径 get_candidates_list 不依赖它——这里打桩绕过。
"""
from __future__ import annotations

import sys
import threading
import types

_pkg = types.ModuleType("iso_tools")
_inf = types.ModuleType("iso_tools.inference")
_art = types.ModuleType("iso_tools.inference.artifacts")


class LoadIdentity:  # noqa: D101 - 上游仅用于 ONNX/服务标识，推理路径不触
    def __init__(self, *a, **k):
        pass

    def complete(self, model):
        return None


_art.LoadIdentity = LoadIdentity
_pkg.inference = _inf
_inf.artifacts = _art
sys.modules.setdefault("iso_tools", _pkg)
sys.modules.setdefault("iso_tools.inference", _inf)
sys.modules.setdefault("iso_tools.inference.artifacts", _art)

from cnn_chinese_hw.recognizer.recognizer import HandwritingRecognizer  # noqa: E402

import math  # noqa: E402


def resample_stroke(stroke: list, n: int = 14) -> list:
    """等弧长重采样。训练语料每笔 2~10 点，数位板真实笔迹几百点，
    密度失配会让光栅化特征完全偏移——推理前必须拉回训练分布。"""
    if len(stroke) <= n:
        return stroke
    dists = [0.0]
    for a, b in zip(stroke, stroke[1:]):
        dists.append(dists[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))
    total = dists[-1] or 1.0
    out, k = [], 0
    for i in range(n):
        t = total * i / (n - 1)
        while k < len(dists) - 2 and dists[k + 1] < t:
            k += 1
        seg = (dists[k + 1] - dists[k]) or 1.0
        f = (t - dists[k]) / seg
        (x0, y0), (x1, y1) = stroke[k], stroke[k + 1]
        out.append((x0 + (x1 - x0) * f, y0 + (y1 - y0) * f))
    return out


class RouteA:
    """单字笔迹 → top-k 候选。线程安全（上游自带锁）。

    resample_n：推理前每笔等弧长重采样点数（None/0 = 不重采样）。
    训练语料每笔 2~10 点；数位板真实笔迹几百点，密度失配必须拉回。
    tta：上游的测试时增强次数（0 = 单次渲染，最省时）。
    """

    def __init__(self, resample_n: int | None = 14, tta: int = 0):
        self._rec = HandwritingRecognizer()
        self.resample_n = resample_n
        self.tta = tta

    def candidates(self, strokes, n: int = 8) -> list[tuple[str, float]]:
        """strokes: [(x, y), ...] 列表（一个字的全部笔画）。"""
        if self.resample_n:
            strokes = [resample_stroke([(p[0], p[1]) for p in st],
                                       n=self.resample_n) for st in strokes]
        ranked = self._rec.get_candidates_list(strokes, n_cands=n,
                                               tta=self.tta)
        return [(chr(o), s) for s, o in ranked]

    def candidates_cells(self, cells, n: int = 8):
        return [self.candidates(cell, n) for cell in cells]

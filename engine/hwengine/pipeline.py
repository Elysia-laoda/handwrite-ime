"""端到端管线：一行墨迹 → 文本。

顺序：分割 → 路 B 整行 → 长度修复（合并/二分）→ 路 A 逐块 → 融合。
"""
from __future__ import annotations

from .fuse import beam_decode, decode_aligned
from .ink import InkLine, Stroke
from .route_a import RouteA
from .route_b import RouteB
from .segment import segment_line, split_by_x_gaps


def _bbox(strokes):
    xs = [p[0] for s in strokes for p in s]
    ys = [p[1] for s in strokes for p in s]
    return min(xs), min(ys), max(xs), max(ys)


def _cell_gap(a: list[Stroke], b: list[Stroke]) -> float:
    return _bbox(b)[0] - _bbox(a)[2]


def _cell_width(strokes) -> float:
    x0, _, x1, _ = _bbox(strokes)
    return x1 - x0


def _bisect_cell(strokes: list[Stroke]) -> list[list[Stroke]]:
    """把一个疑似粘连块按笔画中心 x 的中点切成两半。"""
    centers = sorted((min(p[0] for p in s) + max(p[0] for p in s)) / 2 for s in strokes)
    mid = (centers[len(centers) // 2 - 1] + centers[len(centers) // 2]) / 2
    left = [s for s in strokes if (min(p[0] for p in s) + max(p[0] for p in s)) / 2 <= mid]
    right = [s for s in strokes if s not in left]
    if left and right:
        return [left, right]
    return [strokes]


def dp_split_by_count(strokes: list[Stroke], n: int) -> list[list[Stroke]]:
    """把整行笔画按笔画原子切成 n 块，最小化各块字宽对均匀字宽的偏差。

    连续快写时字间水平空隙不可靠，几何阈值分割会整行并成一块；
    路 B 的识别字数是最可靠的监督信号。笔画不可拆分（一笔只属于
    一个字）。O(n²) 预计算区间极值 + O(n·k²) DP。
    """
    m = len(strokes)
    if m <= n:
        return [[s] for s in strokes]
    boxes = []
    for s in strokes:
        xs = [p[0] for p in s]
        boxes.append((min(xs), max(xs)))
    x_lo, x_hi = boxes[0][0], max(b[1] for b in boxes)
    target = (x_hi - x_lo) / n or 1.0

    # 稀疏表风格的区间极值：mm[i][j] = 第 i..j 笔（不含 j）的 (min_x, max_x)
    mm = [[None] * (m + 1) for _ in range(m + 1)]
    for i in range(m):
        lo, hi = boxes[i]
        for j in range(i + 1, m + 1):
            lo = min(lo, boxes[j - 1][0])
            hi = max(hi, boxes[j - 1][1])
            mm[i][j] = (lo, hi)

    INF = float("inf")
    dp = [[INF] * (m + 1) for _ in range(n + 1)]
    cut = [[0] * (m + 1) for _ in range(n + 1)]
    dp[0][0] = 0.0
    for k in range(1, n + 1):
        for j in range(k, m + 1):
            best, arg = INF, k - 1
            for i in range(k - 1, j):
                if dp[k - 1][i] == INF:
                    continue
                lo, hi = mm[i][j]
                c = dp[k - 1][i] + abs((hi - lo) - target) ** 1.5
                if c < best:
                    best, arg = c, i
            dp[k][j] = best
            cut[k][j] = arg

    bounds = [m]
    j = m
    for k in range(n, 0, -1):
        j = cut[k][j]
        bounds.append(j)
    bounds.reverse()
    out = []
    for a, b in zip(bounds, bounds[1:]):
        if b > a:
            out.append(strokes[a:b])
    return out


class Pipeline:
    def __init__(self, route_a: RouteA | None = None, route_b: RouteB | None = None):
        self.a = route_a if route_a is not None else RouteA()
        self.b = route_b if route_b is not None else RouteB()

    def recognize_line(self, ink: InkLine, quick: bool = False) -> dict:
        """quick=True：几何分割（+必要时 DP 对齐）+ 路 B 文本，不算路 A。

        预识别走 quick（每笔都全量会把识别队列塞满）；提交走全量。
        """
        cells = segment_line(ink)
        text_b, score_b = self.b.recognize_line(ink.strokes)
        orig_n = len(cells)
        repaired = False

        if text_b and cells and len(cells) != len(text_b):
            # 路 B 字数做监督：按笔画原子 DP 切分（远优于合并/二分修补）
            cells = dp_split_by_count(ink.strokes, len(text_b))
            repaired = True
        elif text_b and not cells:
            cells = dp_split_by_count(ink.strokes, len(text_b))
            repaired = True

        if quick:
            return {"text": text_b, "cells": cells, "text_b": text_b,
                    "score_b": score_b, "repaired": repaired, "a_cells": []}

        a_cells = self.a.candidates_cells(cells) if cells else []
        # 上屏文本路 B 主导（整行 CTC 识别，实测最稳）；路 A 只作
        # 纠正菜单的备选来源，不再参与选字。
        text = text_b if text_b else (beam_decode(a_cells) if a_cells else "")
        return {"text": text, "cells": cells, "text_b": text_b,
                "score_b": score_b, "repaired": repaired, "a_cells": a_cells}

    # ------------------------------------------------------------------
    def _repair_cells(self, cells, target_n: int):
        """已由 dp_split_by_count 取代（保留接口兼容）。"""
        return cells, False

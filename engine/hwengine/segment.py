"""连续书写笔迹 → 单字块分割。

主信号是水平投影空隙（字间隙），辅信号是书写停顿（有 t 时）。
v1 几何策略：单字内部部件的横向间隙 < 字间隙；以笔画高度的滑动参考
判定“足够大的空隙”。
"""
from __future__ import annotations

from typing import Sequence

from .ink import InkLine, Stroke


def _stroke_xrange(s: Stroke) -> tuple[float, float]:
    return min(p[0] for p in s), max(p[0] for p in s)


def _stroke_height(s: Stroke) -> float:
    ys = [p[1] for p in s]
    return max(ys) - min(ys)


def split_by_x_gaps(strokes: Sequence[Stroke], gap_ratio: float = 0.38) -> list[list[Stroke]]:
    """按水平投影的空带切分。空带阈值 = gap_ratio × 参考字高。

    参考字高取笔画高度的 85 分位（长笔画主导，避免小点拉低）。
    """
    if not strokes:
        return []
    boxes = [(min(p[0] for p in s), max(p[0] for p in s), _stroke_height(s))
             for s in strokes]
    heights = sorted(b[2] for b in boxes if b[2] > 0)
    ref_h = heights[int(0.85 * (len(heights) - 1))] if heights else 1.0
    thresh = gap_ratio * ref_h

    order = sorted(range(len(strokes)), key=lambda i: boxes[i][0])
    groups: list[list[int]] = [[order[0]]]
    cur_max_x = boxes[order[0]][1]
    for i in order[1:]:
        x0, x1, _ = boxes[i]
        if x0 - cur_max_x > thresh:
            groups.append([i])
        else:
            groups[-1].append(i)
        cur_max_x = max(cur_max_x, x1)
    return [[strokes[i] for i in g] for g in groups]


def split_by_pauses(ink: InkLine, pause_ms: float = 450.0) -> list[list[Stroke]]:
    """按时序停顿切分（仅当带时间戳时可用；作为 x 空隙的补充）。"""
    if ink.t is None or len(ink.t) < 2:
        return [list(ink.strokes)] if ink.strokes else []
    groups: list[list[Stroke]] = [[ink.strokes[0]]]
    for i in range(1, len(ink.strokes)):
        if ink.t[i] - ink.t[i - 1] > pause_ms:
            groups.append([ink.strokes[i]])
        else:
            groups[-1].append(ink.strokes[i])
    return groups


def merge_two(segmentations: list[list[list[Stroke]]]) -> list[list[Stroke]]:
    """合并多路分割：取笔画数一致时的并（交集语义的保守并）。

    v1 规则：若 x 空隙分割与停顿分割给出相同块数，信任 x 空隙版；
    否则采纳块数更多的一路（倾向少合并）。
    """
    if not segmentations:
        return []
    best = max(segmentations, key=lambda s: len(s))
    return best


def segment_line(ink: InkLine, gap_ratio: float = 0.38, pause_ms: float = 450.0):
    """对外主入口：一行 InkLine → 字块列表。"""
    if not ink.strokes:
        return []
    cands = [split_by_x_gaps(ink.strokes, gap_ratio=gap_ratio)]
    if ink.t is not None:
        cands.append(split_by_pauses(ink, pause_ms=pause_ms))
    return merge_two(cands)

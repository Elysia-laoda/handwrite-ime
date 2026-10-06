"""笔迹数据结构与渲染工具。

约定：Stroke 是 [(x, y), ...] 的点列（可带时间戳则另存于 InkLine.t）。
坐标保留原始值，渲染时再归一化。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Sequence

import numpy as np
from PIL import Image, ImageDraw

Point = tuple[float, float]
Stroke = list[Point]


@dataclass
class InkLine:
    """一行连续书写的墨迹。t 是与 points 平行的落笔时刻（毫秒，可缺省）。"""
    strokes: list[Stroke] = field(default_factory=list)
    t: list[float] | None = None

    def add(self, stroke: Stroke, t: float | None = None):
        self.strokes.append([tuple(p) for p in stroke])
        if t is not None or self.t is not None:
            if self.t is None:
                self.t = [None] * (len(self.strokes) - 1)  # type: ignore[list-item]
            self.t.append(t)

    def bbox(self) -> tuple[float, float, float, float]:
        xs = [p[0] for s in self.strokes for p in s]
        ys = [p[1] for s in self.strokes for p in s]
        return min(xs), min(ys), max(xs), max(ys)

    def stroke_boxes(self) -> list[tuple[float, float, float, float]]:
        out = []
        for s in self.strokes:
            xs = [p[0] for p in s]
            ys = [p[1] for p in s]
            out.append((min(xs), min(ys), max(xs), max(ys)))
        return out


def render_cells_image(
    cells: Sequence[Sequence[Stroke]],
    char_px: int = 96,
    gap_px: int = 14,
    stroke_w: int = 5,
    pad: int = 16,
) -> Image.Image:
    """把已经分好的字块逐个等比缩放、居中，排成一行白底黑字图。

    与用户在面板里看到的“校准后的规范墨迹”同一渲染逻辑。
    """
    w = pad * 2 + len(cells) * (char_px + gap_px) - gap_px
    img = Image.new("L", (w, char_px + pad * 2), 255)
    draw = ImageDraw.Draw(img)
    for i, strokes in enumerate(cells):
        _draw_cell(draw, strokes, pad + i * (char_px + gap_px), pad, char_px, stroke_w)
    return img


def render_line_image(
    strokes: Sequence[Stroke],
    line_h_px: int = 64,
    stroke_w: int = 4,
) -> Image.Image:
    """把整行未分割墨迹按整体 bbox 等比缩放渲染（路 B 用，保持字距原样）。"""
    xs = [p[0] for s in strokes for p in s]
    ys = [p[1] for s in strokes for p in s]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    bw, bh = max(x1 - x0, 1), max(y1 - y0, 1)
    scale = line_h_px / bh
    w = int(bw * scale) + stroke_w * 2 + 16
    img = Image.new("L", (w, line_h_px + stroke_w * 2 + 16), 255)
    draw = ImageDraw.Draw(img)
    for s in strokes:
        pts = [((x - x0) * scale + stroke_w + 8, (y - y0) * scale + stroke_w + 8)
               for x, y in s]
        draw.line(pts, fill=0, width=stroke_w, joint="curve")
    return img


def _draw_cell(draw, strokes, ox, oy, char_px, stroke_w):
    xs = [p[0] for s in strokes for p in s]
    ys = [p[1] for s in strokes for p in s]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    scale = min(char_px / max(x1 - x0, 1), char_px / max(y1 - y0, 1))
    ox += (char_px - (x1 - x0) * scale) / 2
    oy += (char_px - (y1 - y0) * scale) / 2
    for s in strokes:
        draw.line([(ox + (x - x0) * scale, oy + (y - y0) * scale) for x, y in s],
                  fill=0, width=stroke_w, joint="curve")


def strokes_to_np(img: Image.Image) -> np.ndarray:
    return np.asarray(img.convert("RGB"))

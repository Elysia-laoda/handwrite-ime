"""双路融合解码。

输入：路 A 每字块 top-k（带概率）、路 B 整行文本。
策略：
1. 两路块数一致时逐位融合：log p_a + 路 B 位置命中奖励 + 词频奖励。
2. 块数不一致时只做路 A 的词频 beam 解码（长度修复在 pipeline 层）。
"""
from __future__ import annotations

import math
from typing import Sequence

import jieba

_freq = jieba.dt.FREQ          # 懒初始化后的词典频次表
_MAX_FREQ = 60101967           # jieba 词典总频次（其源码常量）


def word_bonus(prev: str, ch: str) -> float:
    """prev+ch 成词 / ch 自成词的小额奖励，[-inf, 0) 尺度可控。"""
    score = 0.0
    joint = _freq.get(prev + ch)
    if joint:
        score += math.log(joint / _MAX_FREQ)
    single = _freq.get(ch)
    if single:
        score += 0.5 * math.log(single / _MAX_FREQ)
    return score


def decode_aligned(a_cells: Sequence[Sequence[tuple[str, float]]],
                   text_b: str,
                   w_a: float = 1.0,
                   w_b: float = 2.0,
                   w_lm: float = 0.35) -> str:
    """两路对齐（len(text_b) == len(a_cells)）时的逐位融合。

    路 B 命中给固定对数域奖励（不乘 log p_a）——奖励表达的是
    “第二条独立证据链认可这个字”，与路 A 自身置信无关。
    """
    out = []
    for i, cands in enumerate(a_cells):
        b_char = text_b[i] if i < len(text_b) else ""
        best, best_s = cands[0][0], -1e18
        for ch, p in cands:
            s = w_a * math.log(max(p, 1e-9))
            if ch == b_char:
                s += w_b
            prev = out[-1] if out else ""
            s += w_lm * word_bonus(prev, ch)
            if s > best_s:
                best, best_s = ch, s
        out.append(best)
    return "".join(out)


def beam_decode(a_cells: Sequence[Sequence[tuple[str, float]]],
                beam: int = 6,
                w_a: float = 1.0,
                w_lm: float = 0.35) -> str:
    """只有路 A 时：词频 beam 解码。"""
    beams: list[tuple[float, str]] = [(0.0, "")]
    for cands in a_cells:
        nxt = []
        for sc, prefix in beams:
            prev = prefix[-1] if prefix else ""
            for ch, p in cands[:8]:
                nxt.append((sc + w_a * math.log(max(p, 1e-9))
                            + w_lm * word_bonus(prev, ch), prefix + ch))
        nxt.sort(key=lambda x: -x[0])
        beams = nxt[:beam]
    return beams[0][1]

"""M0 验收：合成句子级手写数据，评测整条管线。

数据：handwriting-zh_CN.xml（GB2312 全集，每字一样本）。
合成：随机句 → 每字笔迹等比缩放 + 扰动 → 水平拼接（字距随机、
      字内笔画加时序停顿模拟真实书写节奏）。
指标：整句准确率 / 字准确率；对照 现行(B主导) / 纯路A / 纯路B / 旧融合。

⚠ 重要局限：该语料同时是路 A 的训练数据来源，本评测对路 A 是
训练集内评测——成绩饱和虚高，不能外推。真实书写分布的路 A 表现
以 eval_captures.py（真实面板笔迹）为准。
"""
import os
import random
import sys
import time
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hwengine.fuse import beam_decode, decode_aligned
from hwengine.ink import InkLine
from hwengine.paths import corpus_path
from hwengine.pipeline import Pipeline

XML = corpus_path("handwriting-zh_CN.xml")
N_SENTS = 20
SEED = 42


def load_corpus():
    corpus = {}
    for event, elem in ET.iterparse(XML, events=("end",)):
        if elem.tag != "character":
            continue
        ch = elem.findtext("utf8")
        if ch not in corpus:
            pts = []
            for st in elem.find("strokes").findall("stroke"):
                pts.append([(float(p.get("x")), float(p.get("y")))
                            for p in st.findall("point")])
            corpus[ch] = pts
        elem.clear()
    return corpus


def jitter(strokes, rng, amp=12.0):
    """轻微平移 + 尺寸抖动，模拟同一人每次书写不同。"""
    dx, dy = rng.uniform(-amp, amp), rng.uniform(-amp, amp)
    s = rng.uniform(0.92, 1.08)
    return [[(x * s + dx, y * s + dy) for x, y in st] for st in strokes]


def synth_sentence(corpus, chars, rng):
    """把一句话合成一行连续 InkLine：字高 ~600 单位，字距随机。"""
    ink = InkLine()
    t = 0.0
    xoff = 0.0
    char_h = 600.0
    for ch in chars:
        strokes = jitter(corpus[ch], rng)
        xs = [p[0] for st in strokes for p in st]
        ys = [p[1] for st in strokes for p in st]
        w, h = max(xs) - min(xs), max(ys) - min(ys)
        scale = char_h / h
        xoff += rng.uniform(0.05, 0.30) * char_h     # 字间隙
        placed = [[((x - min(xs)) * scale + xoff,
                    (y - min(ys)) * scale + rng.uniform(0, 40))
                   for x, y in st] for st in strokes]
        for st in placed:
            ink.add(st, t=t)
            t += rng.uniform(60, 160)                # 字内笔画节奏
        xoff += w * scale
        t += rng.uniform(500, 900)                   # 字间停顿
    return ink


def main():
    rng = random.Random(SEED)
    print("loading corpus ...")
    corpus = load_corpus()
    pool = [c for c in corpus if 0x4E00 <= ord(c) <= 0x9FA5]
    print(f"corpus: {len(corpus)} chars, CJK pool {len(pool)}")

    pipe = Pipeline()
    stats = {"cur": 0, "a": 0, "b": 0, "fus": 0}
    char_stats = {"cur": 0, "a": 0, "b": 0, "fus": 0}
    n_char_tot = 0
    t_infer = 0.0

    for si in range(N_SENTS):
        chars = [rng.choice(pool) for _ in range(rng.randint(5, 9))]
        ink = synth_sentence(corpus, chars, rng)
        t0 = time.time()
        out = pipe.recognize_line(ink)
        t_infer += time.time() - t0

        truth = "".join(chars)
        a_cells = out["a_cells"]
        preds = {
            "cur": out["text"],                                   # 现行策略
            "a": beam_decode(a_cells) if a_cells else "",         # 纯路A
            "b": out["text_b"],                                   # 纯路B
            "fus": (decode_aligned(a_cells, out["text_b"])
                    if a_cells and out["text_b"] else ""),        # 旧融合
        }
        n_char_tot += len(chars)
        for k, p in preds.items():
            stats[k] += (p == truth)
            char_stats[k] += sum(1 for x, y in zip(p, truth) if x == y)

        print(f"[{si:02d}] truth={truth} cur={preds['cur']} "
              f"a={preds['a']} fus={preds['fus']} "
              f"n_cells={len(out['cells'])} rep={out['repaired']}")

    print(f"\n（注意：本评测的合成笔迹取自路 A 训练语料本身，"
          f"路 A 成绩饱和虚高；真实分布结论见 eval_captures.py）")
    for k, name in (("cur", "现行(路B主导)"), ("b", "纯路B"),
                    ("a", "纯路A+LM"), ("fus", "旧融合 aligned")):
        print(f"{name:<12}：整句 {stats[k]}/{N_SENTS} = {stats[k] / N_SENTS:.0%}，"
              f"单字 {char_stats[k]}/{n_char_tot} = "
              f"{char_stats[k] / n_char_tot:.1%}")
    print(f"avg latency: {t_infer / N_SENTS * 1000:.0f}ms/line")


if __name__ == "__main__":
    main()

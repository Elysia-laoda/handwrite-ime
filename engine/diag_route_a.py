"""用 captures.jsonl 里的真实笔迹验证路 A 重采样修复。

对比 每笔原始点数 vs 等弧长重采样（模拟训练分布 2-10 点/笔）
两种输入下，路 A top1 与路 B（视作参照真值）的逐字命中率。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hwengine.ink import InkLine
from hwengine.paths import captures_path
from hwengine.pipeline import Pipeline
from hwengine.route_a import RouteA


def resample_stroke(st, n=12):
    import math
    if len(st) <= n:
        return st
    dists = [0.0]
    for (x0, y0), (x1, y1) in zip(st, st[1:]):
        dists.append(dists[-1] + math.hypot(x1 - x0, y1 - y0))
    total = dists[-1] or 1.0
    out, k = [], 0
    for i in range(n):
        t = total * i / (n - 1)
        while k < len(dists) - 2 and dists[k + 1] < t:
            k += 1
        seg = (dists[k + 1] - dists[k]) or 1.0
        f = (t - dists[k]) / seg
        (x0, y0), (x1, y1) = st[k], st[k + 1]
        out.append((x0 + (x1 - x0) * f, y0 + (y1 - y0) * f))
    return out


def main():
    recs = [json.loads(l) for l in
            open(captures_path(), encoding="utf-8")]
    seen = set()
    uniq = []
    for r in recs:
        key = json.dumps(r["strokes"])[:200]
        if key not in seen:
            seen.add(key)
            uniq.append(r)
    print(f"records {len(recs)}, unique {len(uniq)}")
    rec = max(uniq, key=lambda r: sum(len(s) for s in r["strokes"]))
    print("sample:", rec["ts"], "text_b =", rec["text_b"])

    ink = InkLine(strokes=[[(x, y) for x, y in st] for st in rec["strokes"]])
    pipe = Pipeline()
    out = pipe.recognize_line(ink)
    tb = rec["text_b"]
    fused = out["text"]
    hit = sum(1 for a, b in zip(fused, tb) if a == b)
    print(f"cells: {len(out['cells'])} (b len {len(tb)}) repaired={out['repaired']}")
    print(f"b      : {tb}")
    print(f"fused  : {fused}")
    print(f"char hit: {hit}/{min(len(fused), len(tb))}"
          f" = {hit / max(min(len(fused), len(tb)), 1):.0%}")


if __name__ == "__main__":
    main()

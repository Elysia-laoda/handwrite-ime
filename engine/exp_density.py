"""诊断实验：点密度是否破坏路 A。

语料笔迹每笔只有 2~10 个点；数位板真实书写一笔几百点。
把同一笔迹做线性插值加密（模拟数位板采样密度），看路 A 是否失效。
若失效 → 入口做等距重采样即可修复。
"""
import os
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hwengine.paths import corpus_path
from hwengine.route_a import RouteA

XML = corpus_path("handwriting-zh_CN.xml")


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


def densify(strokes, step=5.0):
    """线性插值加密到约每 step 单位一个点（模拟数位板高频采样）。"""
    out = []
    for st in strokes:
        dense = [st[0]]
        for (x0, y0), (x1, y1) in zip(st, st[1:]):
            d = max(abs(x1 - x0), abs(y1 - y0)) ** 2 + (x1 - x0) ** 2 * 0
            import math
            dist = math.hypot(x1 - x0, y1 - y0)
            n = max(1, int(dist / step))
            for i in range(1, n + 1):
                t = i / n
                dense.append((x0 + (x1 - x0) * t, y0 + (y1 - y0) * t))
        out.append(dense)
    return out


def resample(strokes, n_points=24):
    """每笔等弧长重采样到 n_points 个点。"""
    import math

    out = []
    for st in strokes:
        if len(st) < 2:
            out.append(st)
            continue
        dists = [0.0]
        for (x0, y0), (x1, y1) in zip(st, st[1:]):
            dists.append(dists[-1] + math.hypot(x1 - x0, y1 - y0))
        total = dists[-1] or 1.0
        targets = [total * i / (n_points - 1) for i in range(n_points)]
        rs, k = [], 0
        for t in targets:
            while k < len(dists) - 2 and dists[k + 1] < t:
                k += 1
            seg = dists[k + 1] - dists[k] or 1.0
            f = (t - dists[k]) / seg
            (x0, y0), (x1, y1) = st[k], st[k + 1]
            rs.append((x0 + (x1 - x0) * f, y0 + (y1 - y0) * f))
        out.append(rs)
    return out


def main():
    ra = RouteA()
    for ch in "你好我天地":
        strokes = load_char(ch)
        dense = densify(strokes, step=5.0)
        n_orig = sum(len(s) for s in strokes)
        n_dense = sum(len(s) for s in dense)
        base = ra.candidates(strokes, n=3)
        dres = ra.candidates(dense, n=3)
        fixed = ra.candidates(resample(dense, n_points=24), n=3)
        print(f"{ch}: base={base[0][0]}@{base[0][1]:.2f}  "
              f"dense({n_dense}pts)={dres[0][0]}@{dres[0][1]:.2f}  "
              f"resampled={fixed[0][0]}@{fixed[0][1]:.2f}   (orig {n_orig} pts)")


if __name__ == "__main__":
    main()

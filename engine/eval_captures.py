"""真实笔迹离线回归评测（数据源：data/captures.jsonl）。

captures 是面板每次上屏时落盘的原始笔迹 + 当时的识别输出。用它替代
合成评测的登记：

- 合成评测（eval_m0）里"训练语料即测试语料"，路 A 95%+ 是虚高；
  真实书写分布下路 A 崩得厉害，必须用真实数据说话。
- text_fused 是用户提交/纠正后的最终文本，视作弱真值：
  * text_fused == text_b  → 用户接受了路 B 输出（融合安全性样本）
  * text_fused != text_b  → 用户纠正过（融合价值样本，B 大概率有错）

评测：
1. 路 B：与真值的整句/逐字对照（复核 B 的实际水准）
2. 路 A：top1 / top3 逐字命中（多少能进候选菜单救场）
3. 融合策略网格：纯 B / aligned 融合（w_b, w_lm 网格）/ B 主导+A 高
   置信救援（阈值网格）——用真实数据选出不会破坏 B 的融合系数
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hwengine.fuse import decode_aligned
from hwengine.ink import InkLine
from hwengine.paths import captures_path
from hwengine.pipeline import Pipeline
from hwengine.route_b import RouteB

import jieba  # noqa: E402

_freq = jieba.dt.FREQ
_MAX_FREQ = 60101967


def merge_stub_strokes(strokes: list, tol: float = 4.0) -> list:
    """清理「单点桩笔」残留（历史数据专用）。

    2026-10-05 后期会话的面板存在数位板/合成鼠标双流重复：每笔被录成
    「1 点桩笔 + 真实笔」。桩点与后继笔起点重合时可安全并掉；面板侧
    已修复（tablet 优先），此处只服务旧数据回归。
    """
    out = []
    i, n = 0, len(strokes)
    while i < n:
        st = strokes[i]
        if (len(st) == 1 and i + 1 < n and len(strokes[i + 1]) > 1):
            nx = strokes[i + 1][0]
            dx, dy = st[0][0] - nx[0], st[0][1] - nx[1]
            if dx * dx + dy * dy <= tol * tol:
                i += 1
                continue
        out.append(st)
        i += 1
    return out


def load_records(min_strokes: int = 2, dedupe: bool = True) -> list[dict]:
    """载入采集记录。

    - 去重：按完整笔迹 JSON 精确去重（同一行被反复提交的记录只留一份）
    - 桩笔清理：双流重复时代的「单点桩笔」并掉
    - 真值过滤：融合时代（路 B 主导前）的产物 text_fused==a_top1 拼接，
      这类记录不代表用户意图，直接从评测集剔除
    """
    recs = []
    with open(captures_path(), encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            strokes = merge_stub_strokes([[tuple(p) for p in st]
                                          for st in r["strokes"]])
            r["_strokes"] = strokes
            if len(strokes) < min_strokes:
                continue
            recs.append(r)
    if dedupe:
        seen, uniq = set(), []
        for r in recs:
            key = json.dumps(r["_strokes"], sort_keys=True)
            if key not in seen:
                seen.add(key)
                uniq.append(r)
        recs = uniq
    # 融合时代产物剔除
    kept = []
    for r in recs:
        f, b = r.get("text_fused") or "", r.get("text_b") or ""
        a1 = "".join(r.get("a_top1") or [])
        if f and f != b and f == a1:
            continue
        kept.append(r)
    return kept


def char_acc(pred: str, truth: str) -> tuple[int, int]:
    """逐位命中数 / 真值长度（长度不一致按截断比较）。"""
    hit = sum(1 for a, b in zip(pred, truth) if a == b)
    return hit, len(truth)


def decode_b_rescue(a_cells, text_b: str, hi: float,
                    counter: list | None = None) -> str:
    """B 主导：仅当路 A 高置信且与 B 不同、且成词才替换 B。"""
    out = []
    for i, ch_b in enumerate(text_b):
        cands = a_cells[i] if i < len(a_cells) else None
        if cands:
            ch_a, p_a = cands[0]
            if p_a >= hi and ch_a != ch_b:
                prev = out[-1] if out else ""
                joint = _freq.get(prev + ch_a)
                jb = _freq.get(prev + ch_b)
                if joint and (not jb or joint > jb * 3):
                    out.append(ch_a)
                    if counter is not None:
                        counter.append(1)
                    continue
        out.append(ch_b)
    return "".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--detail", action="store_true", help="逐条打印")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 条")
    ap.add_argument("--sweep-a", dest="sweep_a", action="store_true",
                    help="路 A 重采样点数扫描")
    ap.add_argument("--sweep-b", dest="sweep_b", action="store_true",
                    help="路 B 渲染高度扫描（较慢）")
    args = ap.parse_args()

    recs = load_records()
    if args.limit:
        recs = recs[:args.limit]
    print(f"records: {len(recs)}")

    pipe = Pipeline()
    # 只统计有意义的（有文本、有笔画）
    recs = [r for r in recs if r.get("text_fused")]
    print(f"非空文本记录: {len(recs)}\n")

    # ---------------- 逐条计算 ----------------
    b_sent_ok = a1_hit = a3_hit = aligned_tot = 0
    b_hit = b_tot = 0
    safe_rows = []      # text_fused == text_b（B 被接受）
    value_rows = []     # text_fused != text_b（用户纠正过）
    rows = []

    for idx, r in enumerate(recs):
        truth = r["text_fused"]
        tb = r.get("text_b") or ""
        t_ms = r.get("t_ms")
        if t_ms and len(t_ms) != len(r["_strokes"]):
            t_ms = None                 # 时刻表与笔画数不符，弃用
        ink = InkLine(strokes=[[(x, y) for x, y in st] for st in r["_strokes"]],
                      t=t_ms)
        out = pipe.recognize_line(ink)
        cells, a_cells = out["cells"], out["a_cells"]

        h, n = char_acc(tb, truth)
        b_hit += h
        b_tot += n
        b_sent_ok += (tb == truth)

        # 路 A 仅在块数与文本长度一致时评（否则对位无意义）
        a1 = a3 = None
        if a_cells and len(cells) == len(truth):
            aligned_tot += 1
            a1 = sum(1 for c, t in zip(a_cells, truth)
                     if c and c[0][0] == t)
            a3 = sum(1 for c, t in zip(a_cells, truth)
                     if c and any(ch == t for ch, _ in c[:3]))
            a1_hit += a1
            a3_hit += a3

        row = dict(idx=idx, ts=r["ts"], truth=truth, b=tb,
                   cells=len(cells), a1=a1, a3=a3,
                   a_top1="".join(c[0][0] if c else "?" for c in a_cells),
                   a_cells=a_cells, cells_obj=cells,
                   strokes_obj=r["_strokes"])
        rows.append(row)
        (safe_rows if tb == truth else value_rows).append(row)

        if args.detail:
            mark = "OK " if tb == truth else "CBT"
            print(f"[{idx:03d}] {mark} truth={truth}")
            print(f"      b   ={tb}  cells={len(cells)}")
            if a1 is not None:
                print(f"      a1  ={row['a_top1']}  ({a1}/{len(truth)})")
            else:
                print(f"      a1  ={row['a_top1']}  (未对齐)")

    # ---------------- 汇总 ----------------
    print("=" * 72)
    print(f"路 B：逐字命中 {b_hit}/{b_tot} = {b_hit / max(b_tot, 1):.1%}，"
          f"整句完全正确 {b_sent_ok}/{len(rows)} = "
          f"{b_sent_ok / max(len(rows), 1):.1%}")
    if aligned_tot:
        print(f"路 A：对齐记录 {aligned_tot} 条，"
              f"top1 命中 {a1_hit}/{sum(len(r['truth']) for r in rows if r['a1'] is not None)}，"
              f"top3 命中 {a3_hit}")
    print(f"（B被接受 {len(safe_rows)} 条 / B有误需纠正 {len(value_rows)} 条）")
    print("=" * 72)

    # ---------------- 融合策略网格（仅对齐样本） ----------------
    grid_rows = [r for r in rows if r["a_cells"] and len(r["a_cells"]) == len(r["b"])]
    print(f"\n融合网格样本（a_cells 与 b 等长）：{len(grid_rows)} 条")
    if grid_rows:
        def acc_of(fn):
            hit = tot = 0
            sent = 0
            for r in grid_rows:
                pred = fn(r)
                h, n = char_acc(pred, r["truth"])
                hit += h
                tot += n
                sent += (pred == r["truth"])
            return hit / max(tot, 1), sent

        base, base_sent = acc_of(lambda r: r["b"])
        print(f"  基准 纯B: char={base:.1%} sent={base_sent}/{len(grid_rows)}")
        for w_b in (0.5, 1.0, 2.0, 4.0, 8.0):
            for w_lm in (0.0, 0.35, 0.7):
                def fn(r, w_b=w_b, w_lm=w_lm):
                    return decode_aligned(r["a_cells"], r["b"],
                                          w_b=w_b, w_lm=w_lm)
                a, s = acc_of(fn)
                tag = " <== 优于纯B" if a > base + 1e-9 else ""
                print(f"  aligned w_b={w_b:<4} w_lm={w_lm:<4}: "
                      f"char={a:.1%} sent={s}/{len(grid_rows)}{tag}")
        for hi in (0.5, 0.7, 0.85, 0.95):
            cnt: list = []
            a, s = acc_of(lambda r, hi=hi, cnt=cnt:
                          decode_b_rescue(r["a_cells"], r["b"], hi,
                                          counter=cnt))
            tag = " <== 优于纯B" if a > base + 1e-9 else ""
            print(f"  B+救援 hi={hi:<4}: char={a:.1%} "
                  f"sent={s}/{len(grid_rows)} 触发={len(cnt)}{tag}")

    # ---------------- 参数扫描 ----------------
    aligned_rows = [r for r in rows if r["a_cells"] and r["cells_obj"]
                    and len(r["cells_obj"]) == len(r["truth"])]
    if args.sweep_a and aligned_rows:
        print(f"\n路 A 重采样扫描（对齐样本 {len(aligned_rows)} 条）")
        ra = pipe.a                      # 复用已加载模型，只改重采样参数
        for n in (None, 14, 24, 32, 48):
            ra.resample_n = n
            h1 = h3 = 0
            tot = 0
            for r in aligned_rows:
                cands = ra.candidates_cells(r["cells_obj"])
                for c, t in zip(cands, r["truth"]):
                    tot += 1
                    if c and c[0][0] == t:
                        h1 += 1
                    if c and any(ch == t for ch, _ in c[:3]):
                        h3 += 1
            print(f"  resample_n={str(n):<4}: top1={h1}/{tot} = {h1 / max(tot, 1):.1%}"
                  f"  top3={h3}/{tot} = {h3 / max(tot, 1):.1%}")

    if args.sweep_b:
        print(f"\n路 B 渲染高度扫描（全部记录 {len(rows)} 条）")
        for line_h in (48, 64, 96):
            rb = RouteB(line_h_px=line_h)
            hit = tot = sent = 0
            for r in rows:
                tb2, _ = rb.recognize_line(
                    [[(x, y) for x, y in st] for st in r["strokes_obj"]])
                h, n = char_acc(tb2, r["truth"])
                hit += h
                tot += n
                sent += (tb2 == r["truth"])
            print(f"  line_h={line_h:<3}: char={hit / max(tot, 1):.1%} "
                  f"sent={sent}/{len(rows)}")


if __name__ == "__main__":
    main()

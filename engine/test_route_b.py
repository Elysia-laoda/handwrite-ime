"""M0 route B smoke test: render stroke corpora into a handwriting line image,
then recognize it with RapidOCR (PP-OCR family ONNX).

Renders 你好世界 from handwriting-zh_CN.xml, left-to-right, the same way the
panel will render ink before handing it to the OCR lane.
"""
import os
import sys
import time
import xml.etree.ElementTree as ET

from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hwengine.paths import PROJECT_ROOT, corpus_path

XML = corpus_path("handwriting-zh_CN.xml")

SENTENCE = "你好世界"
CHAR_PX = 96          # character cell size in the rendered line
GAP_PX = 18           # gap between character cells
STROKE_W = 5
MARGIN = 16


def load_samples(chars):
    want = set(chars)
    out = {}
    for event, elem in ET.iterparse(XML, events=("end",)):
        if elem.tag != "character":
            continue
        ch = elem.findtext("utf8")
        if ch in want and ch not in out:
            pts = []
            for st in elem.find("strokes").findall("stroke"):
                pts.append([(float(p.get("x")), float(p.get("y")))
                            for p in st.findall("point")])
            out[ch] = pts
        elem.clear()
        if len(out) == len(want):
            break
    return out


def render_line(samples, chars):
    canvas = Image.new("L", (MARGIN * 2 + len(chars) * (CHAR_PX + GAP_PX),
                             CHAR_PX + MARGIN * 2), 255)
    draw = ImageDraw.Draw(canvas)
    for i, ch in enumerate(chars):
        strokes = samples[ch]
        xs = [p[0] for st in strokes for p in st]
        ys = [p[1] for st in strokes for p in st]
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        scale = min(CHAR_PX / (x1 - x0 or 1), CHAR_PX / (y1 - y0 or 1))
        ox = MARGIN + i * (CHAR_PX + GAP_PX) + (CHAR_PX - (x1 - x0) * scale) / 2
        oy = MARGIN + (CHAR_PX - (y1 - y0) * scale) / 2
        for st in strokes:
            draw.line([(ox + (x - x0) * scale, oy + (y - y0) * scale)
                       for x, y in st], fill=0, width=STROKE_W, joint="curve")
    return canvas


def main():
    samples = load_samples(set(SENTENCE))
    missing = [c for c in SENTENCE if c not in samples]
    if missing:
        print("missing chars:", missing)
    img = render_line(samples, [c for c in SENTENCE if c in samples])
    out_png = os.path.join(PROJECT_ROOT, "data", "route_b_test.png")
    os.makedirs(os.path.dirname(out_png), exist_ok=True)
    img.save(out_png)

    from rapidocr import RapidOCR
    t0 = time.time()
    ocr = RapidOCR()
    print(f"ocr init {time.time() - t0:.1f}s")

    t1 = time.time()
    result = ocr(img.convert("RGB"))
    dt = (time.time() - t1) * 1000
    print(f"recognition {dt:.0f}ms")
    print("result:", result.txts if hasattr(result, "txts") else result)


if __name__ == "__main__":
    main()

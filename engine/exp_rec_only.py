"""Route B exp 2: skip the detector, run the rec head alone on the ink line.
We always know where the ink is (we rendered it), so det is pure waste."""
import os
import sys
import time

import numpy as np
from PIL import Image

from rapidocr import RapidOCR

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hwengine.paths import PROJECT_ROOT

IMG = os.path.join(PROJECT_ROOT, "data", "route_b_test.png")

ocr = RapidOCR()
img = np.array(Image.open(IMG).convert("RGB"))

# warm-up call
t0 = time.time()
r = ocr.recognize_txt(img)
print(f"warmup {r.txts} {(time.time() - t0) * 1000:.0f}ms")

for i in range(3):
    t = time.time()
    r = ocr.recognize_txt(img)
    print(f"rec-only run{i}: {r.txts} {(time.time() - t) * 1000:.0f}ms")

# also try 2x upscale, rec models like taller text lines
big = np.array(Image.fromarray(img).resize((img.shape[1] * 2, img.shape[0] * 2), Image.LANCZOS))
t = time.time()
r = ocr.recognize_txt(big)
print(f"rec-only 2x: {r.txts} {(time.time() - t) * 1000:.0f}ms")

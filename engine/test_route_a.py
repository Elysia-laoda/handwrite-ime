"""M0 smoke test: load pretrained cnn_chinese_hw weights and recognize the
README's 我 stroke sample. Expected top candidate: 25105 (我)."""
import sys
import time
import types

# The upstream recognizer imports mcyph's private iso_tools package, which is
# not published; the main inference path (get_candidates_list) never touches
# it, so stub the two import points out.
_pkg = types.ModuleType("iso_tools")
_inf = types.ModuleType("iso_tools.inference")
_art = types.ModuleType("iso_tools.inference.artifacts")


class LoadIdentity:
    def __init__(self, *a, **k):
        pass

    def complete(self, model):
        return None


_art.LoadIdentity = LoadIdentity
_pkg.inference = _inf
_inf.artifacts = _art
sys.modules.setdefault("iso_tools", _pkg)
sys.modules.setdefault("iso_tools.inference", _inf)
sys.modules.setdefault("iso_tools.inference.artifacts", _art)

from cnn_chinese_hw.recognizer.recognizer import HandwritingRecognizer

T0 = time.time()
rec = HandwritingRecognizer()
print(f"model loaded in {time.time() - T0:.1f}s")

strokes_w = [
    [(208, 0), (199, 119), (94, 341)],
    [(0, 461), (781, 520), (915, 520), (999, 479)],
    [(189, 167), (213, 209), (238, 826), (268, 934), (203, 910)],
    [(303, 514), (94, 766)],
    [(462, 17), (497, 586), (522, 688), (646, 886), (796, 1000)],
    [(716, 628), (462, 916)],
    [(696, 101), (771, 155), (835, 251)],
]

T1 = time.time()
cands = rec.get_candidates_list(strokes_w)
dt = (time.time() - T1) * 1000
print(f"inference {dt:.0f}ms, top-5: {[(chr(o), round(s, 4)) for s, o in cands[:5]]}")

# a second run to see warm latency
T2 = time.time()
cands = rec.get_candidates_list(strokes_w)
print(f"warm inference {(time.time() - T2) * 1000:.0f}ms")

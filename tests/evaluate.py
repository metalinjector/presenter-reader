"""Сверка результата с эталоном синтетического вебинара.

python -m tests.evaluate VIDEO.mp4 [VIDEO2.mp4 …] [--out DIR]
Рядом с видео должен лежать VIDEO.json от tests.synth.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile

from presenter_reader import Params, process


def iou(a, b) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = max(0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0, min(ay + ah, by + bh) - max(ay, by))
    inter = ix * iy
    return inter / float(aw * ah + bw * bh - inter)


def evaluate(video: str, out: str, params: Params | None = None, tol: float = 1.5) -> dict:
    with open(os.path.splitext(video)[0] + ".json", encoding="utf-8") as fh:
        gt = json.load(fh)
    res = process(video, out, params or Params())
    det = [(s.t_start, s.t_end, s.repeat_of) for s in res.slides]
    exp = gt["screens"]
    used = set()
    matched = []
    for i, e in enumerate(exp):
        best = None
        for j, (a, b, _) in enumerate(det):
            if j in used:
                continue
            if abs(a - e["start"]) <= tol and (best is None or abs(a - e["start"]) < abs(det[best][0] - e["start"])):
                best = j
        if best is not None:
            used.add(best)
            matched.append((i, best))
    missed = [i for i in range(len(exp)) if i not in {m[0] for m in matched}]
    extra = [j for j in range(len(det)) if j not in used]
    rep_ok = sum(1 for i, j in matched if (exp[i]["repeat_of"] is None) == (det[j][2] is None))
    r = {
        "video": os.path.basename(video),
        "expected": len(exp), "found": len(det), "matched": len(matched),
        "missed": [(round(exp[i]["start"], 1), exp[i]["sid"]) for i in missed],
        "extra": [(round(det[j][0], 1), round(det[j][1] - det[j][0], 1)) for j in extra],
        "repeat_flags_ok": f"{rep_ok}/{len(matched)}",
        "iou": round(iou(res.layout.rect, gt["slide_rect"]), 3),
        "rect": res.layout.rect, "gt_rect": tuple(gt["slide_rect"]),
        "method": res.layout.method, "mode": res.mode, "seconds": round(res.seconds, 1),
    }
    r["ok"] = not r["missed"] and not r["extra"] and r["iou"] >= 0.95 and rep_ok == len(matched)
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("videos", nargs="+")
    ap.add_argument("--out", default=None)
    ap.add_argument("--mode", default="auto")
    args = ap.parse_args()
    base = args.out or tempfile.mkdtemp(prefix="pr_eval_")
    all_ok = True
    for v in args.videos:
        out = os.path.join(base, os.path.splitext(os.path.basename(v))[0])
        r = evaluate(v, out, Params(mode=args.mode))
        all_ok &= r["ok"]
        print(json.dumps(r, ensure_ascii=False))
    print("ALL OK" if all_ok else "FAILURES", file=sys.stderr)
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()

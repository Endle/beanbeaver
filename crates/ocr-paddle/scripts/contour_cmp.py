#!/usr/bin/env python3
"""Mask-vs-contour diagnostic for on-device detection box-position fidelity.

Feeds the SAME DB probability map (dumped by `device_sim --probdump <dir>`)
through PaddleOCR's *actual* reference `DBPostProcess` (cv2 findContours +
minAreaRect + pyclipper unclip) and diffs the resulting boxes against ours
(`ours_boxes.json`, from our imageproc/geo path). Because the prob map is
identical, every box difference is purely the contour/min-rect/unclip algorithm
-- isolated from the upstream mask, model, and preprocessing.

Run inside the paddle venv:
    python contour_cmp.py <probdump-dir> [<probdump-dir> ...]

Verdict:
  - boxes ~match  -> our contour path is faithful; the gap is upstream (mask).
  - boxes differ  -> the imageproc-Suzuki-vs-OpenCV contour path is the lever.
"""
import json
import sys

import numpy as np
from paddlex.inference.models.text_detection.processors import DBPostProcess


def load_probdump(d):
    meta = json.load(open(f"{d}/meta.json"))
    h, w = meta["h"], meta["w"]
    prob = np.fromfile(f"{d}/prob.f32", dtype="<f4").reshape(h, w)
    ours = np.array(json.load(open(f"{d}/ours_boxes.json")), dtype=np.float64)  # (N,4,2)
    return meta, prob, ours


def paddle_boxes(meta, prob):
    dbp = DBPostProcess(
        thresh=meta["thresh"],
        box_thresh=meta["box_thresh"],
        max_candidates=meta["max_candidates"],
        unclip_ratio=meta["unclip_ratio"],
        score_mode="fast",
        box_type="quad",
    )
    pred = prob[None, :, :]  # (1,H,W)
    img_shape = (meta["dest_h"], meta["dest_w"], meta["ratio_h"], meta["ratio_w"])
    boxes, _ = dbp.process(pred, img_shape, meta["thresh"], meta["box_thresh"], meta["unclip_ratio"])
    return np.array(boxes, dtype=np.float64).reshape(-1, 4, 2)


def aabb(quad):  # quad (4,2) -> (x0,y0,x1,y1)
    return quad[:, 0].min(), quad[:, 1].min(), quad[:, 0].max(), quad[:, 1].max()


def iou(a, b):
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    iw, ih = max(0.0, ix1 - ix0), max(0.0, iy1 - iy0)
    inter = iw * ih
    area = lambda x: max(0.0, x[2] - x[0]) * max(0.0, x[3] - x[1])
    uni = area(a) + area(b) - inter
    return inter / uni if uni > 0 else 0.0


def greedy_match(ours, paddle):
    """Greedy IoU matching ours<->paddle. Returns matched pairs + unmatched counts."""
    oa = [aabb(q) for q in ours]
    pa = [aabb(q) for q in paddle]
    pairs = []
    for i in range(len(oa)):
        for j in range(len(pa)):
            v = iou(oa[i], pa[j])
            if v > 0.10:
                pairs.append((v, i, j))
    pairs.sort(reverse=True)
    used_o, used_p, matched = set(), set(), []
    for v, i, j in pairs:
        if i in used_o or j in used_p:
            continue
        used_o.add(i)
        used_p.add(j)
        matched.append((i, j, v, oa[i], pa[j]))
    return matched, len(oa) - len(used_o), len(pa) - len(used_p)


def stats(name, arr):
    arr = np.asarray(arr, dtype=np.float64)
    if arr.size == 0:
        return f"  {name:<10} n=0"
    return (f"  {name:<10} mean={arr.mean():+7.2f}  median={np.median(arr):+7.2f}  "
            f"std={arr.std():6.2f}  p10={np.percentile(arr,10):+7.2f}  p90={np.percentile(arr,90):+7.2f}")


def run(d):
    meta, prob, ours = load_probdump(d)
    paddle = paddle_boxes(meta, prob)
    matched, ours_only, paddle_only = greedy_match(ours, paddle)

    print(f"\n=== {meta['image']}  (prob {meta['w']}x{meta['h']}, dest {meta['dest_w']}x{meta['dest_h']}) ===")
    print(f"  ours boxes: {len(ours)}   paddle boxes: {len(paddle)}   "
          f"matched: {len(matched)}   ours-only: {ours_only}   paddle-only: {paddle_only}")

    if matched:
        ious = [m[2] for m in matched]
        dcx, dcy, dw, dh = [], [], [], []
        for _, _, _, oa, pa in matched:
            ocx, ocy = (oa[0] + oa[2]) / 2, (oa[1] + oa[3]) / 2
            pcx, pcy = (pa[0] + pa[2]) / 2, (pa[1] + pa[3]) / 2
            dcx.append(ocx - pcx)
            dcy.append(ocy - pcy)
            dw.append((oa[2] - oa[0]) - (pa[2] - pa[0]))
            dh.append((oa[3] - oa[1]) - (pa[3] - pa[1]))
        ious = np.array(ious)
        print(f"  IoU mean={ious.mean():.3f} median={np.median(ious):.3f}  "
              f">0.5: {(ious>0.5).mean()*100:.0f}%  >0.7: {(ious>0.7).mean()*100:.0f}%  >0.9: {(ious>0.9).mean()*100:.0f}%")
        print("  matched-box geometric delta (ours - paddle), px:")
        print(stats("dcx", dcx))
        print(stats("dcy", dcy))
        print(stats("dw", dw))
        print(stats("dh", dh))
    return meta, matched, ours_only, paddle_only


def main():
    dirs = sys.argv[1:]
    if not dirs:
        print(__doc__)
        sys.exit(1)
    all_matched_iou, tot_ours_only, tot_paddle_only, tot_matched = [], 0, 0, 0
    agg_dcy, agg_dh = [], []
    for d in dirs:
        meta, matched, oo, po = run(d)
        tot_ours_only += oo
        tot_paddle_only += po
        tot_matched += len(matched)
        for _, _, v, oa, pa in matched:
            all_matched_iou.append(v)
            agg_dcy.append((oa[1] + oa[3]) / 2 - (pa[1] + pa[3]) / 2)
            agg_dh.append((oa[3] - oa[1]) - (pa[3] - pa[1]))
    if len(dirs) > 1:
        print(f"\n=== AGGREGATE ({len(dirs)} fixtures) ===")
        print(f"  matched: {tot_matched}   ours-only: {tot_ours_only}   paddle-only: {tot_paddle_only}")
        ious = np.array(all_matched_iou)
        if ious.size:
            print(f"  IoU mean={ious.mean():.3f} median={np.median(ious):.3f}  "
                  f">0.5: {(ious>0.5).mean()*100:.0f}%  >0.7: {(ious>0.7).mean()*100:.0f}%  >0.9: {(ious>0.9).mean()*100:.0f}%")
            print(stats("dcy(all)", agg_dcy))
            print(stats("dh(all)", agg_dh))


if __name__ == "__main__":
    main()

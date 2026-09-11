#!/usr/bin/env python3
"""
Build every mask asset from the supplied colour-coded mask. Reproducible, so it
travels with the project instead of living in a scratch folder.

The supplied mask has annotation text burned into it - the title on the wall and
"CLOSED DOOR" inside each door, about 1.6 % of the canvas. Used raw it corrupts
the wall and door mattes and inflates six window bounding boxes. This removes
the text by grouping the contaminated pixels into blobs and filling each blob
with the majority region on its boundary ring. Nothing else about the authored
artwork is changed.

Outputs into _pipeline/masks (full canvas + a crop per delivery plate):

    01 WALL          flat facade
    02 WINDOW        glazing / openings
    03 BASE          ground plane and bottom band
    04 DOOR          the three door recesses
    05 COLUMN        the two column bodies
    06 TRIM          the caps and plinths of those columns
    07 NOMAP         the ONLY non-projected area - keep it black
    08 PROJECTABLE   everything except NOMAP            <- multiply your comp by this
    09 OPENINGS      windows + doors                    <- where 3D emerges
    10 FACADE_FLAT   wall + column + trim

and into _pipeline/reference:

    facade_regions.json / .csv          every region rectangle and centre
    PxDL_SW_OPENING_ID_9788x2552.png    R = opening index, G/B = local UV inside it
    PxDL_SW_OPENING_SDF_9788x2552.png   distance from the nearest opening edge

    python build_masks.py
    python build_masks.py --skip-extras      (mattes and regions only, much faster)
"""
import argparse
import json
import os
import subprocess
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import CFG, MASK_ROOT, REF_ROOT, ffmpeg, source  # noqa: E402

W = CFG["canvas"]["w"]
H = CFG["canvas"]["h"]

NAMES = ["WALL", "WINDOW", "BASE", "NOMAP", "DOOR", "COLUMN", "TRIM", "TEXT"]
PAL = np.array([(21, 145, 52), (33, 149, 221), (221, 33, 168), (0, 0, 0),
                (21, 109, 164), (177, 33, 221), (255, 174, 25), (255, 255, 255)], np.int16)
WALL, WINDOW, BASE, NOMAP, DOOR, COLUMN, TRIM, TEXT = range(8)

GROUPS = {
    "01_WALL": [WALL], "02_WINDOW": [WINDOW], "03_BASE": [BASE], "04_DOOR": [DOOR],
    "05_COLUMN": [COLUMN], "06_TRIM": [TRIM], "07_NOMAP": [NOMAP],
    "08_PROJECTABLE": [WALL, WINDOW, BASE, DOOR, COLUMN, TRIM],
    "09_OPENINGS": [WINDOW, DOOR],
    "10_FACADE_FLAT": [WALL, COLUMN, TRIM],
}

FF = ffmpeg()


def write_png(arr, path, pix, tries=4):
    """Write one PNG through ffmpeg.

    Retries: on Windows a just-written file of this size is occasionally still
    locked by a virus scanner when the next write lands on it, and dying
    halfway through 31 masks is not a useful failure mode.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    h, w = arr.shape[:2]
    data = arr.tobytes()
    err = ""
    for attempt in range(tries):
        r = subprocess.run([FF, "-hide_banner", "-loglevel", "error", "-f", "rawvideo",
                            "-pix_fmt", pix, "-s", "%dx%d" % (w, h), "-i", "pipe:0",
                            "-frames:v", "1", "-y", path],
                           input=data, capture_output=True)
        if r.returncode == 0 and os.path.isfile(path) and os.path.getsize(path) > 0:
            return
        err = r.stderr.decode("utf8", "replace")[:500]
        if attempt < tries - 1:
            time.sleep(0.6 * (attempt + 1))
    sys.exit("could not write " + path + "\n" + err)


def dilate(b, r=1):
    o = b.copy()
    for _ in range(r):
        o[1:, :] |= o[:-1, :]
        o[:-1, :] |= o[1:, :]
        o[:, 1:] |= o[:, :-1]
        o[:, :-1] |= o[:, 1:]
    return o


def components(bm):
    """Run-based connected components (4-connected). Returns an int32 label image."""
    h, w = bm.shape
    parent = [0]
    lbl = np.zeros((h, w), np.int32)
    nxt = 1

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    for y in range(h):
        row = bm[y]
        if not row.any():
            continue
        d = np.diff(np.concatenate(([0], row.view(np.int8), [0])))
        for x0, x1 in zip(np.where(d == 1)[0], np.where(d == -1)[0]):
            near = set()
            if y > 0:
                u = np.unique(lbl[y - 1][max(0, x0 - 1):min(w, x1 + 1)])
                near = {int(v) for v in u if v}
            if near:
                c = min(near)
                for n in near:
                    union(c, n)
            else:
                c = nxt
                parent.append(c)
                nxt += 1
            lbl[y, x0:x1] = c
    roots = np.array([find(k) if k > 0 else 0 for k in range(nxt)], np.int32)
    return roots[lbl]


def boxes(lbl, minpx):
    out = []
    ids, cnt = np.unique(lbl[lbl > 0], return_counts=True)
    for i, c in zip(ids, cnt):
        if c < minpx:
            continue
        ys, xs = np.where(lbl == i)
        x0, x1, y0, y1 = int(xs.min()), int(xs.max()), int(ys.min()), int(ys.max())
        out.append(dict(x=x0, y=y0, w=x1 - x0 + 1, h=y1 - y0 + 1,
                        cx=int(round((x0 + x1) / 2)), cy=int(round((y0 + y1) / 2)),
                        px=int(c), _id=int(i)))
    out.sort(key=lambda r: (r["y"] // 400, r["x"]))
    return out


def classify():
    mask_path = source("mask")
    raw = subprocess.run([FF, "-hide_banner", "-loglevel", "error", "-i", mask_path,
                          "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"],
                         capture_output=True).stdout
    if len(raw) < W * H * 3:
        sys.exit("mask decode gave %d bytes, expected %d" % (len(raw), W * H * 3))
    m = np.frombuffer(raw[: W * H * 3], np.uint8).reshape(H, W, 3).astype(np.int16)

    lab = np.empty((H, W), np.uint8)
    dmin = np.empty((H, W), np.int32)
    for y0 in range(0, H, 256):
        y1 = min(H, y0 + 256)
        d = np.abs(m[y0:y1][:, :, None, :].astype(np.int32) - PAL[None, None, :, :]).sum(3)
        lab[y0:y1] = d.argmin(2)
        dmin[y0:y1] = d.min(2)
    return lab, dmin


def remove_text(lab, dmin):
    """Strip the burned-in annotation text and put back what it covered.

    Two stages, because the text sits on two kinds of ground:

      1. Nearest-label fill. Every contaminated pixel takes the label of the
         nearest uncontaminated pixel. On flat ground (the title on the wall,
         "CLOSED DOOR" inside a door) that is exactly right, and across a
         straight boundary it reconstructs the boundary.

      2. Edge reconstruction. Where the title crossed the bottom of a window
         row, stage 1 still leaves a ragged edge - the glyphs bite notches out
         of the blue. The openings are simple shapes with dead-flat bottoms, so
         the bottom edge is rebuilt from the component's own undamaged columns.
         Only pixels the text touched are ever changed.
    """
    text = lab == TEXT
    near = dilate(text, 40)
    edge = (np.arange(W)[None, :] < 900) | (np.arange(W)[None, :] > 9550)
    bad = dilate(text, 2) | (near & ((dmin > 25) | ((lab == NOMAP) & ~edge)
                                     | (lab == WINDOW) & dilate(text, 14)))
    # Antialiasing around the glyphs can land close enough to a palette colour
    # to classify as a real region - a few dozen door-blue pixels in the middle
    # of the wall, which the fill then propagates outward. Every authored region
    # is large, so any small speck near the text is text.
    for target in (WINDOW, DOOR, BASE, COLUMN, TRIM):
        lbl = components(lab == target)
        ids, cnt = np.unique(lbl[lbl > 0], return_counts=True)
        if not ids.size:
            continue
        big = np.isin(lbl, ids[cnt >= 3000])
        small = np.isin(lbl, ids[cnt < 3000])
        # a fragment of a real region sits right next to that region; a speck
        # of misclassified antialiasing is stranded on its own
        speck = small & near & ~dilate(big, 60)
        if speck.any():
            bad |= speck
            print("   %-7s specks stranded near the text: %d px" % (NAMES[target], speck.sum()))

    print("   contaminated: %d px (%.3f %%)" % (bad.sum(), 100 * bad.mean()))

    # ---- stage 1: nearest uncontaminated label -----------------------------
    fill = lab.copy()
    fill[bad] = 255
    unknown = bad.copy()
    for _ in range(400):
        if not unknown.any():
            break
        cand = np.full((H, W), 255, np.uint8)
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            sh = np.roll(fill, (dy, dx), (0, 1))
            if dy == 1:
                sh[0] = 255
            elif dy == -1:
                sh[-1] = 255
            if dx == 1:
                sh[:, 0] = 255
            elif dx == -1:
                sh[:, -1] = 255
            take = unknown & (cand == 255) & (sh != 255)
            cand[take] = sh[take]
        got = unknown & (cand != 255)
        fill[got] = cand[got]
        unknown &= ~got
    if unknown.any():
        sys.exit("nearest-label fill did not converge (%d px left)" % unknown.sum())
    lab = fill

    # ---- stage 2: rebuild the flat bottom edge of every damaged opening ----
    repaired = 0
    for target in (WINDOW, DOOR):
        lbl = components(lab == target)
        ids, cnt = np.unique(lbl[lbl > 0], return_counts=True)
        for cid, n in zip(ids, cnt):
            if n < 3000:
                continue
            sel = lbl == cid
            ys, xs = np.where(sel)
            x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()
            sub = sel[y0:y1 + 1, x0:x1 + 1]
            subbad = bad[y0:y1 + 1, x0:x1 + 1]
            h, w = sub.shape

            has = sub.any(axis=0)
            bot = np.where(has, (h - 1) - np.argmax(sub[::-1], axis=0), -1)
            clean = has & ~subbad.any(axis=0)          # columns the text never touched
            src = bot[clean] if clean.sum() >= 8 else bot[has]
            if src.size == 0:
                continue
            B = int(np.bincount(src - src.min()).argmax() + src.min())

            # any column the text touched is rebuilt from its top down to B
            cols = np.where(has & subbad.any(axis=0))[0]
            if cols.size == 0:
                continue
            top = np.argmax(sub, axis=0)
            yy = np.arange(h)[:, None]
            want = np.zeros_like(sub)
            want[:, cols] = (yy >= top[None, cols]) & (yy <= B)
            change = (want != sub) & subbad             # never touch clean pixels
            if change.any():
                region = lab[y0:y1 + 1, x0:x1 + 1]
                region[change & want] = target
                region[change & ~want] = WALL
                repaired += int(change.sum())
    print("   rebuilt %d px of opening edges the text had eaten" % repaired)
    if (lab == TEXT).any():
        sys.exit("text pixels survived the fill")
    return lab


def approx_sdf(binary, div=2, iters=140):
    """Distance (px) from the nearest True pixel, approximated at reduced scale.

    Good enough for feathering and glow falloff; not a metrically exact EDT.
    """
    small = binary[::div, ::div].copy()
    dist = np.where(small, 0.0, np.inf).astype(np.float32)
    for _ in range(iters):
        prev = dist
        cand = dist.copy()
        cand[1:, :] = np.minimum(cand[1:, :], dist[:-1, :] + 1.0)
        cand[:-1, :] = np.minimum(cand[:-1, :], dist[1:, :] + 1.0)
        cand[:, 1:] = np.minimum(cand[:, 1:], dist[:, :-1] + 1.0)
        cand[:, :-1] = np.minimum(cand[:, :-1], dist[:, 1:] + 1.0)
        cand[1:, 1:] = np.minimum(cand[1:, 1:], dist[:-1, :-1] + 1.4142)
        cand[:-1, :-1] = np.minimum(cand[:-1, :-1], dist[1:, 1:] + 1.4142)
        cand[1:, :-1] = np.minimum(cand[1:, :-1], dist[:-1, 1:] + 1.4142)
        cand[:-1, 1:] = np.minimum(cand[:-1, 1:], dist[1:, :-1] + 1.4142)
        dist = cand
        if np.array_equal(prev, dist):
            break
    dist = np.where(np.isinf(dist), iters, dist) * div
    big = np.repeat(np.repeat(dist, div, axis=0), div, axis=1)[:H, :W]
    if big.shape != (H, W):                      # pad if the canvas is not divisible
        pad = np.zeros((H, W), np.float32)
        pad[:big.shape[0], :big.shape[1]] = big
        big = pad
    return big


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-extras", action="store_true",
                    help="skip the opening ID map and SDF (much faster)")
    a = ap.parse_args()

    print("classifying mask ...")
    lab, dmin = classify()
    print("removing burned-in annotation text ...")
    lab = remove_text(lab, dmin)

    print("\nmattes -> %s" % MASK_ROOT)
    for name, ids in GROUPS.items():
        bm = (np.isin(lab, ids).astype(np.uint8)) * 255
        write_png(bm, os.path.join(MASK_ROOT, "PxDL_SW_MASK_%s_%dx%d.png" % (name, W, H)), "gray")
        print("   %-16s %6.2f %%" % (name, 100 * (bm > 0).mean()))
    write_png(PAL[lab].astype(np.uint8),
              os.path.join(MASK_ROOT, "PxDL_SW_MASK_CLEAN_RGB_%dx%d.png" % (W, H)), "rgb24")

    for p in CFG["plates"]:
        x0, pw = p["x"], p["w"]
        for name, ids in GROUPS.items():
            bm = (np.isin(lab[:, x0:x0 + pw], ids).astype(np.uint8)) * 255
            write_png(bm, os.path.join(
                MASK_ROOT, "PxDL_SW_%s_MASK_%s_%dx%d.png" % (p["name"], name, pw, H)), "gray")
    print("   per-plate crops written for %s" % ", ".join(p["name"] for p in CFG["plates"]))

    print("\nregion tables ...")
    data = {"canvas": {"w": W, "h": H},
            "plates": {p["name"]: {"x": p["x"], "w": p["w"], "h": p["h"]} for p in CFG["plates"]},
            "overlap": CFG["overlap"], "regions": {}}
    comp_cache = {}
    for idx in (WINDOW, DOOR, COLUMN, TRIM, BASE):
        lbl = components(lab == idx)
        comp_cache[idx] = lbl
        bx = boxes(lbl, 3000)
        for b in bx:
            b.pop("_id", None)
        data["regions"][NAMES[idx]] = bx
        print("   %-7s %d regions" % (NAMES[idx], len(bx)))
    os.makedirs(REF_ROOT, exist_ok=True)
    with open(os.path.join(REF_ROOT, "facade_regions.json"), "w") as f:
        json.dump(data, f, indent=1)
    with open(os.path.join(REF_ROOT, "facade_regions.csv"), "w") as f:
        f.write("region,index,x,y,w,h,centre_x,centre_y,pixels\n")
        for k, v in data["regions"].items():
            for i, c in enumerate(v, 1):
                f.write("%s,%d,%d,%d,%d,%d,%d,%d,%d\n"
                        % (k, i, c["x"], c["y"], c["w"], c["h"], c["cx"], c["cy"], c["px"]))
    print("   wrote facade_regions.json / .csv")

    if a.skip_extras:
        return

    # ---- opening ID map ----------------------------------------------------
    # R = opening index (1..N), G/B = normalised UV inside that opening.
    # Lets a shader give every window its own timing and its own local space.
    print("\nopening ID map ...")
    openings = (lab == WINDOW) | (lab == DOOR)
    lbl = components(openings)
    bx = boxes(lbl, 3000)
    idmap = np.zeros((H, W, 3), np.uint8)
    listing = []
    for i, b in enumerate(bx, 1):
        if i > 255:
            print("   ! more than 255 openings, the rest are not indexed")
            break
        sel = lbl == b["_id"]
        ys, xs = np.where(sel)
        u = (xs - b["x"]) / max(b["w"] - 1, 1)
        v = (ys - b["y"]) / max(b["h"] - 1, 1)
        idmap[ys, xs, 0] = i
        idmap[ys, xs, 1] = np.clip(u * 255, 0, 255).astype(np.uint8)
        idmap[ys, xs, 2] = np.clip(v * 255, 0, 255).astype(np.uint8)
        kind = "DOOR" if lab[b["cy"], b["cx"]] == DOOR else "WINDOW"
        row = "upper" if b["cy"] < 800 else ("lower" if b["cy"] < 1800 else "ground")
        listing.append(dict(index=i, kind=kind, row=row, x=b["x"], y=b["y"],
                            w=b["w"], h=b["h"], cx=b["cx"], cy=b["cy"], px=b["px"]))
    write_png(idmap, os.path.join(REF_ROOT, "PxDL_SW_OPENING_ID_%dx%d.png" % (W, H)), "rgb24")
    with open(os.path.join(REF_ROOT, "openings.json"), "w") as f:
        json.dump({"count": len(listing), "openings": listing}, f, indent=1)
    print("   %d openings indexed -> PxDL_SW_OPENING_ID / openings.json" % len(listing))
    for r in listing:
        print("      %3d  %-6s %-6s  x=%5d y=%5d  %4dx%-4d  centre (%5d,%5d)"
              % (r["index"], r["kind"], r["row"], r["x"], r["y"], r["w"], r["h"], r["cx"], r["cy"]))

    # ---- distance from opening edges ---------------------------------------
    print("\nopening distance field ...")
    sdf = approx_sdf(openings, div=2, iters=140)
    MAXD = 512.0
    write_png((np.clip(sdf / MAXD, 0, 1) * 255).astype(np.uint8),
              os.path.join(REF_ROOT, "PxDL_SW_OPENING_SDF_%dx%d.png" % (W, H)), "gray")
    print("   wrote PxDL_SW_OPENING_SDF  (0 = inside an opening, 255 = %d px away or more)" % MAXD)


if __name__ == "__main__":
    main()

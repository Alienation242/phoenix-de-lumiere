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
from _common import (CFG, MASK_ROOT, REF_ROOT, ffmpeg, mask_dirs,  # noqa: E402
                     source)

W = CFG["canvas"]["w"]
H = CFG["canvas"]["h"]
SCALE = 1.0          # canvas px per working px; the tracer measures in canvas px

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


# =========================================================================
# TRACING THE FACADE OUT OF THE NOISE PLATE
#
# The shared noise plate is not a flat field: the windows, doors and columns are
# drawn into it, each with a thin border. The authored colour mask describes the
# same architecture - but the two do not agree everywhere. Measured against the
# plate, the authored openings sit an average of 4.5 px off and the worst is 24.
#
# That matters now that the plate DRIVES the shaders rather than sitting behind
# them: an oil field 12 px wider than the window it belongs to spills a rim of
# colour onto the masonry, and an object clipped to its opening crosses a wall
# that is not where the plate says the wall is.
#
# So this builds a second mask set out of the plate itself. It does NOT try to
# guess what each shape means - the labels still come from the authored mask,
# which is the only thing that knows a door from a window. What it does is move
# every boundary onto the border the plate actually draws.
# =========================================================================

def box_blur(a, r):
    """Separable box blur via a summed-area table."""
    p = np.pad(a, ((r, r), (r, r)), mode="edge")
    c = np.cumsum(np.cumsum(p, 0), 1)
    c = np.pad(c, ((1, 0), (1, 0)))
    k = 2 * r + 1
    return (c[k:, k:] - c[:-k, k:] - c[k:, :-k] + c[:-k, :-k]) / float(k * k)


def grad_mag(a):
    gx = np.zeros_like(a)
    gy = np.zeros_like(a)
    gx[:, 1:-1] = a[:, 2:] - a[:, :-2]
    gy[1:-1, :] = a[2:, :] - a[:-2, :]
    return np.sqrt(gx * gx + gy * gy)


def dist_from(mask, cap):
    """Distance in pixels from the nearest True pixel, capped. Chamfer, iterated."""
    d = np.where(mask, np.float32(0.0), np.float32(cap + 2))
    for _ in range(int(cap) + 2):
        c = d.copy()
        c[1:, :] = np.minimum(c[1:, :], d[:-1, :] + 1.0)
        c[:-1, :] = np.minimum(c[:-1, :], d[1:, :] + 1.0)
        c[:, 1:] = np.minimum(c[:, 1:], d[:, :-1] + 1.0)
        c[:, :-1] = np.minimum(c[:, :-1], d[:, 1:] + 1.0)
        c[1:, 1:] = np.minimum(c[1:, 1:], d[:-1, :-1] + 1.4142)
        c[:-1, :-1] = np.minimum(c[:-1, :-1], d[1:, 1:] + 1.4142)
        c[1:, :-1] = np.minimum(c[1:, :-1], d[:-1, 1:] + 1.4142)
        c[:-1, 1:] = np.minimum(c[:-1, 1:], d[1:, :-1] + 1.4142)
        if np.array_equal(c, d):
            break
        d = c
    return d


def label_edges(lab):
    """Pixels sitting on a boundary between two different labels."""
    e = np.zeros(lab.shape, bool)
    e[:, 1:] |= lab[:, 1:] != lab[:, :-1]
    e[:, :-1] |= lab[:, 1:] != lab[:, :-1]
    e[1:, :] |= lab[1:, :] != lab[:-1, :]
    e[:-1, :] |= lab[1:, :] != lab[:-1, :]
    return e


def plate_borders(static, hi_pct=99.0, lo_pct=90.0, min_len=120.0, close=2.0):
    """The border lines the plate draws around its own architecture.

    Two things make this harder than "threshold the gradient".

    The plate's own cloud texture is just as strong as the architecture, but
    BLOBBY. The architecture is drawn as long thin lines, so anything that does
    not run for at least min_len px is thrown away. That one filter is the
    difference between snapping to a window edge and snapping to a smudge 20 px
    inside it.

    And the edges are not all equally strong. The top of a window is a 13/255
    step against the sky; the BOTTOM of the same window is 8/255 against the
    masonry below it. One threshold either catches the bottom edge and half the
    clouds with it, or catches neither. So it is hysteresis, as in Canny: a
    strong threshold decides where an edge definitely is, a much weaker one
    decides how far it runs, and only pieces containing a strong pixel survive.
    Without it the bottom edges break into a dozen fragments, each too short to
    keep - and a window with no bottom edge leaks out of its own corners.
    """
    g = grad_mag(box_blur(static, max(1, int(round(6.0 / SCALE)))))
    strong = g > np.percentile(g, hi_pct)
    lbl = components(g > np.percentile(g, lo_pct))
    if not lbl.any():
        return np.zeros(static.shape, bool), 0, 0
    ids = np.unique(lbl[lbl > 0])
    ys, xs = np.where(lbl > 0)
    v = lbl[ys, xs]
    o = np.argsort(v, kind="stable")
    ys, xs, v = ys[o], xs[o], v[o]
    lo = np.searchsorted(v, ids)
    hi = np.searchsorted(v, ids, side="right")
    keep = np.zeros(int(lbl.max()) + 1, bool)
    want = int(round(min_len / SCALE))
    for i, a0, a1 in zip(ids, lo, hi):
        yy, xx = ys[a0:a1], xs[a0:a1]
        if (a1 - a0) < want:
            continue
        if max(xx.max() - xx.min(), yy.max() - yy.min()) < want:
            continue
        if not strong[yy, xx].any():
            continue
        keep[i] = True
    border = keep[lbl]
    if close:
        border = dilate(border, max(1, int(round(close / SCALE))))
    return border, len(ids), int(keep.sum())


def grow_labels(field, blocked, rounds):
    """Flood every known label outward a pixel at a time, never entering
    `blocked`. Two fronts meeting at a border both stop on it, which is the
    whole point: that is where the new boundary ends up.
    """
    unknown = field == 255
    for _ in range(rounds):
        if not unknown.any():
            break
        cand = np.full(field.shape, 255, np.uint8)
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            sh = np.roll(field, (dy, dx), (0, 1))
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
        got = unknown & (cand != 255) & ~blocked
        if not got.any():
            break
        field[got] = cand[got]
        unknown &= ~got
    return field


def trace_from_noise(lab, static, reach=44.0, smooth=4.0):
    """Move every boundary in `lab` onto the border the plate draws.

    Each region keeps a core of itself as a seed: everything further than
    `reach` from its own boundary, PLUS its medial axis - so a 30 px mullion
    between two windows still seeds and the two windows cannot merge into one.
    Everything between the cores is then re-grown, blocked by the plate's border
    lines, so a boundary with a border near it lands on that border, and a
    boundary with none (wall against ground, say) meets in the middle, exactly
    where it already was.
    """
    h, w = lab.shape
    border, n_all, n_kept = plate_borders(static)
    print("   border lines   %d ridge pieces, %d long enough to be architecture"
          % (n_all, n_kept))
    print("                  %.2f %% of the canvas" % (100.0 * border.mean()))

    reach_px = max(2, int(round(reach / SCALE)))
    d = dist_from(label_edges(lab), reach_px)
    nb = d.copy()
    nb[1:, :] = np.maximum(nb[1:, :], d[:-1, :])
    nb[:-1, :] = np.maximum(nb[:-1, :], d[1:, :])
    nb[:, 1:] = np.maximum(nb[:, 1:], d[:, :-1])
    nb[:, :-1] = np.maximum(nb[:, :-1], d[:, 1:])
    core = (d > reach_px) | ((d >= 1.0) & (d >= nb))

    field = np.full((h, w), 255, np.uint8)
    field[core] = lab[core]
    print("   seeds          %.1f %% of the canvas kept as known ground"
          % (100.0 * core.mean()))

    field = grow_labels(field, border, reach_px + 8)
    stuck = int((field == 255).sum())
    field = grow_labels(field, np.zeros((h, w), bool), 4 * reach_px + 64)
    if (field == 255).any():
        sys.exit("the trace did not close: %d px never got a label"
                 % (field == 255).sum())
    print("   grown          %d px sat on a border line and were split down it"
          % stuck)

    if smooth:
        # Growing a boundary one pixel at a time leaves a staircase on it. A
        # majority vote in a small window takes the staircase off without
        # moving the boundary anywhere.
        r = max(1, int(round(smooth / SCALE)))
        best = np.zeros((h, w), np.float32)
        out = field.copy()
        for idx in range(len(NAMES)):
            if not (field == idx).any():
                continue
            c = box_blur((field == idx).astype(np.float32), r).astype(np.float32)
            t = c > best
            out[t] = idx
            best[t] = c[t]
        field = out

    print("   result         %.2f %% of the canvas changed label"
          % (float((field != lab).mean()) * 100.0))
    print()
    print("   %-8s %10s %10s %9s" % ("region", "authored", "traced", "change"))
    for idx in (WALL, WINDOW, BASE, NOMAP, DOOR, COLUMN, TRIM):
        a = int((lab == idx).sum())
        b = int((field == idx).sum())
        if not a:
            continue
        print("   %-8s %10d %10d %+8.1f %%"
              % (NAMES[idx], a, b, (b / float(a) - 1) * 100))
    return field


def static_plate(frames, use_hq, cache):
    """Whatever is STANDING STILL in the noise plate.

    Averaging a few hundred frames spread across the whole loop cancels the
    dither and leaves the architecture, which is the thing being traced. Frames
    where the plate has already cut to black contribute nothing but darkness,
    so they are skipped.
    """
    if os.path.isfile(cache):
        raw = subprocess.run([FF, "-hide_banner", "-loglevel", "error", "-i", cache,
                              "-pix_fmt", "gray", "-f", "rawvideo", "pipe:1"],
                             capture_output=True).stdout
        if len(raw) >= W * H:
            print("   reusing %s  (delete it to measure again)"
                  % os.path.basename(cache))
            return np.frombuffer(raw[:W * H], np.uint8).reshape(H, W).astype(np.float32)
        print("   %s is unreadable, measuring again" % os.path.basename(cache))

    hq = CFG.get("source_hq", {}) if use_hq else {}
    stitched = hq.get("STITCHED") or None
    if use_hq and not stitched and not (hq.get("SPSW1") and hq.get("SPSW2")):
        sys.exit("--hq needs source_hq.STITCHED, or both source_hq.SPSW1 and SPSW2")
    p1, p2 = CFG["plates"]
    step = max(1, CFG["loop_frames"] // max(frames, 1))

    if stitched:
        print("   source: one stitched master, %s" % os.path.basename(stitched))
        cmd = [FF, "-hide_banner", "-loglevel", "error", "-i", stitched,
               "-vf", "scale=%d:%d:flags=area,format=gray,select=not(mod(n\\,%d))"
                      % (W, H, step)]
    else:
        v1 = hq["SPSW1"] if use_hq else source("SPSW1")
        v2 = hq["SPSW2"] if use_hq else source("SPSW2")
        print("   source: the two plates, %s + %s"
              % (os.path.basename(v1), os.path.basename(v2)))
        fc = ("[0:v]scale=%d:%d:flags=area,crop=%d:%d:0:0[l];"
              "[1:v]scale=%d:%d:flags=area[r];[l][r]hstack=2,format=gray,"
              "select=not(mod(n\\,%d))"
              % (p1["w"], H, p2["x"], H, p2["w"], H, step))
        cmd = [FF, "-hide_banner", "-loglevel", "error", "-i", v1, "-i", v2,
               "-filter_complex", fc]
    cmd += ["-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"]

    acc = np.zeros((H, W), np.float64)
    got = skipped = 0
    pr = subprocess.Popen(cmd, stdout=subprocess.PIPE, bufsize=W * H * 2)
    t0 = time.time()
    while got < frames:
        buf = pr.stdout.read(W * H)
        if len(buf) < W * H:
            break
        f = np.frombuffer(buf, np.uint8).reshape(H, W)
        if f.mean() < 8.0:                 # the plate has cut to black here
            skipped += 1
            continue
        acc += f
        got += 1
        if got % 10 == 0:
            sys.stdout.write("\r   averaging %d frames ... %d" % (frames, got))
            sys.stdout.flush()
    try:
        pr.kill()
    except Exception:
        pass
    sys.stdout.write("\r" + " " * 44 + "\r")
    if got < 8:
        sys.exit("only %d usable frames came out of the plate - is the source right?"
                 % got)
    avg = acc / got
    print("   averaged %d frames (%d skipped as black) in %.0f s, mean %.1f"
          % (got, skipped, time.time() - t0, avg.mean()))
    write_png(np.clip(avg, 0, 255).astype(np.uint8), cache, "gray")
    print("   wrote %s" % os.path.basename(cache))
    return avg.astype(np.float32)


def main():
    global MASK_ROOT, REF_ROOT
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-extras", action="store_true",
                    help="skip the opening ID map and SDF (much faster)")
    ap.add_argument("--from-noise", action="store_true",
                    help="trace the facade out of the shared noise plate instead "
                         "of taking the authored boundaries at face value. The "
                         "labels still come from the authored mask - only the "
                         "boundaries move. Writes a SECOND, complete mask set "
                         "into masks_noise/ and reference_noise/, so both exist "
                         "side by side and nothing is overwritten")
    ap.add_argument("--frames", type=int, default=180,
                    help="--from-noise: how many frames to average to find what "
                         "stands still in the plate. More is steadier and slower")
    ap.add_argument("--hq", action="store_true",
                    help="--from-noise: trace the high-quality masters in "
                         "source_hq rather than the preview mp4s")
    ap.add_argument("--reach", type=float, default=44.0,
                    help="--from-noise: how far a boundary is allowed to move, "
                         "in canvas px. The worst authored opening is 24 px out")
    a = ap.parse_args()

    if a.from_noise:
        MASK_ROOT, REF_ROOT = mask_dirs("noise")
        print("TRACING THE FACADE OUT OF THE NOISE PLATE")
        print("  masks     -> %s" % MASK_ROOT)
        print("  reference -> %s" % REF_ROOT)
        print("  The authored set in masks/ is not touched.")
        print()

    print("classifying mask ...")
    lab, dmin = classify()
    print("removing burned-in annotation text ...")
    lab = remove_text(lab, dmin)

    if a.from_noise:
        print("\nfinding what stands still in the plate ...")
        os.makedirs(REF_ROOT, exist_ok=True)
        static = static_plate(a.frames, a.hq, os.path.join(
            REF_ROOT, "PxDL_SW_NOISE_STATIC_%dx%d.png" % (W, H)))
        print("\ntracing ...")
        lab = trace_from_noise(lab, static, reach=a.reach)

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

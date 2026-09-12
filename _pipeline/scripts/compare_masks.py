#!/usr/bin/env python3
"""
Which mask is right - the authored one, or the one traced from the noise?

The shared noise plate is not a flat field. The windows, doors and columns are
drawn into it, each with a thin border. So there are two descriptions of the
same facade, and they do not have to agree.

This measures the disagreement on pixels instead of arguing about it. For every
opening and column it finds the offset that best lines that region's outline up
with the border the plate draws. A mask that is already right has its best
offset at (0, 0); one that is out by 12 px says so.

    python compare_masks.py                  both sets, table + overlay PNG
    python compare_masks.py --only layer     just the authored one
    python compare_masks.py --search 30      look further for the best offset

It writes an overlay into reference_noise/ - the averaged plate in grey with the
authored outline in RED and the traced one in GREEN, so the numbers can be
looked at as well as read.

Needs the averaged plate, which build_masks.py --from-noise leaves behind:

    python build_masks.py --from-noise
"""
import argparse
import json
import os
import subprocess
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import CFG, REF_ROOT, ffmpeg, mask_dirs  # noqa: E402

W = CFG["canvas"]["w"]
H = CFG["canvas"]["h"]
FF = ffmpeg()


def decode(path, w, h, channels=1):
    pix = "gray" if channels == 1 else "rgb24"
    raw = subprocess.run([FF, "-hide_banner", "-loglevel", "error", "-i", path,
                          "-vf", "scale=%d:%d:flags=area" % (w, h),
                          "-pix_fmt", pix, "-f", "rawvideo", "pipe:1"],
                         capture_output=True).stdout
    need = w * h * channels
    if len(raw) < need:
        sys.exit("could not read %s" % path)
    a = np.frombuffer(raw[:need], np.uint8)
    return a.reshape(h, w) if channels == 1 else a.reshape(h, w, channels)


def grad_mag(a):
    gx = np.zeros_like(a)
    gy = np.zeros_like(a)
    gx[:, 1:-1] = a[:, 2:] - a[:, :-2]
    gy[1:-1, :] = a[2:, :] - a[:-2, :]
    return np.sqrt(gx * gx + gy * gy)


def outline(m):
    e = np.zeros(m.shape, bool)
    e[:, 1:] |= m[:, 1:] != m[:, :-1]
    e[1:, :] |= m[1:, :] != m[:-1, :]
    return e


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", choices=("", "layer", "noise"))
    ap.add_argument("--search", type=int, default=28,
                    help="how far to look for a better offset, in px")
    ap.add_argument("--div", type=int, default=2, choices=(1, 2, 4),
                    help="work at 1/div. The answer is reported in canvas px "
                         "either way; 2 is four times faster and within a pixel")
    ap.add_argument("--no-overlay", action="store_true")
    a = ap.parse_args()

    w, h = W // a.div, H // a.div
    _, ref_noise = mask_dirs("noise")
    static_path = os.path.join(ref_noise, "PxDL_SW_NOISE_STATIC_%dx%d.png" % (W, H))
    if not os.path.isfile(static_path):
        sys.exit("the averaged plate is not there yet:\n  %s\n"
                 "Build it with:  python build_masks.py --from-noise" % static_path)

    static = decode(static_path, w, h).astype(np.float32)
    # How strongly the PLATE says "there is an edge here". Everything below is
    # scored against this and nothing else.
    edginess = grad_mag(static)
    edginess = np.clip(edginess / max(np.percentile(edginess, 99.5), 1e-6), 0, 1)

    wanted = [a.only] if a.only else ["layer", "noise"]
    sets = []
    for variant in wanted:
        mroot, _ = mask_dirs(variant)
        p = os.path.join(mroot, "PxDL_SW_MASK_09_OPENINGS_%dx%d.png" % (W, H))
        c = os.path.join(mroot, "PxDL_SW_MASK_05_COLUMN_%dx%d.png" % (W, H))
        if not os.path.isfile(p):
            if variant == "noise":
                print("the noise-traced set is not built - skipping it")
                print("   build it with:  python build_masks.py --from-noise\n")
                continue
            sys.exit("missing %s" % p)
        e = outline(decode(p, w, h) > 127) | outline(decode(c, w, h) > 127)
        sets.append((variant, e))
    if not sets:
        sys.exit("nothing to compare")

    openings = json.load(open(os.path.join(REF_ROOT, "openings.json")))["openings"]
    R = max(2, int(round(a.search / float(a.div))))

    print()
    print("How far each opening's outline is from the border the plate draws.")
    print("0 means the mask is already on it. Everything is in canvas pixels.")
    print()
    hdr = "%-4s %-6s %6s %6s" % ("idx", "kind", "x", "y")
    for variant, _ in sets:
        hdr += "   %-22s" % ("--- %s ---" % variant)
    print(hdr)
    print("%-4s %-6s %6s %6s" % ("", "", "", "") +
          "".join("   %5s %5s %7s %7s" % ("dx", "dy", "here", "best") for _ in sets))

    totals = {v: [0.0, 0.0, 0.0, 0] for v, _ in sets}
    worst = {v: (0.0, None) for v, _ in sets}
    for o in openings:
        x0, y0 = o["x"] // a.div, o["y"] // a.div
        x1, y1 = (o["x"] + o["w"]) // a.div, (o["y"] + o["h"]) // a.div
        px0, py0 = max(0, x0 - R - 3), max(0, y0 - R - 3)
        px1, py1 = min(w, x1 + R + 4), min(h, y1 + R + 4)
        gg = edginess[py0:py1, px0:px1]
        row = "%-4d %-6s %6d %6d" % (o["index"], o["kind"], o["x"], o["y"])
        skip = False
        for variant, e in sets:
            em = e[py0:py1, px0:px1].astype(np.float32)
            if em.sum() < 40:
                skip = True
                break
            best = (-1.0, 0, 0)
            at0 = 0.0
            for dy in range(-R, R + 1):
                for dx in range(-R, R + 1):
                    sh = np.roll(np.roll(em, dy, 0), dx, 1)
                    s = float((sh * gg).sum() / sh.sum())
                    if dx == 0 and dy == 0:
                        at0 = s
                    if s > best[0]:
                        best = (s, dx, dy)
            dx, dy = best[1] * a.div, best[2] * a.div
            # A flat optimum is not a misplacement. Openings sit in rows of
            # near-identical shapes with banding between them, so there is
            # almost always SOME offset that scores a hair higher; only count
            # it as "out by" when moving there is a real gain.
            real = best[0] > at0 * 1.08
            if not real:
                dx = dy = 0
            row += "   %+5d %+5d %7.3f %7.3f" % (dx, dy, at0, best[0])
            t = totals[variant]
            t[0] += abs(dx)
            t[1] += abs(dy)
            t[2] += at0
            t[3] += 1
            off = (dx * dx + dy * dy) ** 0.5
            if off > worst[variant][0]:
                worst[variant] = (off, o["index"])
        if not skip:
            print(row)

    print()
    print("%-10s %10s %10s %10s   %s" % ("", "mean |dx|", "mean |dy|", "agreement", ""))
    for variant, _ in sets:
        n, t = totals[variant][3], totals[variant]
        if not n:
            continue
        print("%-10s %10.1f %10.1f %10.3f   worst: opening %s, %.0f px out"
              % (variant, t[0] / n, t[1] / n, t[2] / n,
                 worst[variant][1], worst[variant][0]))
    print()
    print("mean |dx| / |dy|  how far the average region would still like to move,")
    print("                  counted only where moving it would actually help.")
    print("here              how much of the plate's own border the outline sits")
    print("                  on WHERE IT IS, 0..1. This is the one that matters.")
    print("best              the same score at the best offset found. Close to")
    print("                  'here' means the mask is already where it belongs.")

    if a.no_overlay or len(sets) < 1:
        return
    lo, hi = np.percentile(static, 1), np.percentile(static, 99)
    st = np.clip((static - lo) / max(hi - lo, 1e-6) * 210, 0, 210).astype(np.uint8)
    rgb = np.dstack([st, st, st])
    colours = {"layer": (255, 40, 40), "noise": (60, 255, 60)}
    for variant, e in sets:
        rgb[e] = colours.get(variant, (255, 255, 0))
    out = os.path.join(ref_noise, "PxDL_SW_MASK_COMPARE_%dx%d.png" % (w, h))
    os.makedirs(ref_noise, exist_ok=True)
    subprocess.run([FF, "-hide_banner", "-loglevel", "error", "-f", "rawvideo",
                    "-pix_fmt", "rgb24", "-s", "%dx%d" % (w, h), "-i", "pipe:0",
                    "-frames:v", "1", "-y", out],
                   input=np.ascontiguousarray(rgb).tobytes(), check=True)
    print()
    print("overlay -> %s" % out)
    print("           red = authored,  green = traced from the noise")


if __name__ == "__main__":
    main()

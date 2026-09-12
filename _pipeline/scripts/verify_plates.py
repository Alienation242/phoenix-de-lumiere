#!/usr/bin/env python3
"""
Verify a pair of SW-wall delivery plates before you hand them over.

Checks:
  1. resolutions match project.json  (SPSW1 7200x2552, SPSW2 3588x2552)
  2. frame counts match each other
  3. the 1000 px overlap (SPSW1 x 6200..7200 == SPSW2 x 0..1000) is identical

Check 3 is the one that matters. A mismatch there is invisible on your monitor
and glaring on the wall: the blend zone double-exposes or tears. It happens
whenever the two plates are rendered or encoded independently instead of being
sliced from one master.

Usage
    python verify_plates.py --a deliver\\SPSW1\\PxDL_SW_SPSW1.%05d.png ^
                            --b deliver\\SPSW2\\PxDL_SW_SPSW2.%05d.png --start 4770
    python verify_plates.py --a deliver\\SPSW1.mov --b deliver\\SPSW2.mov

Needs numpy. Finds ffmpeg by itself (PATH, PXDL_FFMPEG, TouchDesigner, _pipeline\\bin).
"""
import argparse
import os
import re
import subprocess
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import CFG, ffmpeg, frame_passthrough_args, plate  # noqa: E402

A_NAME = CFG["plates"][0]["name"]
B_NAME = CFG["plates"][1]["name"]
OVERLAP_X = CFG["overlap"]["x0"]
OVERLAP_W = CFG["overlap"]["w"]


def probe_size(ff, src, start=None):
    args = [ff, "-hide_banner"]
    if start is not None and "%" in src:
        args += ["-start_number", str(start)]
    args += ["-i", src]
    err = subprocess.run(args, capture_output=True, text=True).stderr
    m = re.search(r"Stream #\d+:\d+.*?Video:.*?,\s*(\d{2,5})x(\d{2,5})", err, re.S)
    if not m:
        sys.exit("could not read a video stream from %s\n%s" % (src, err[-1500:]))
    return int(m.group(1)), int(m.group(2))


def count_frames(ff, src, start=None):
    """Sequences: count files on disk. Movies: ask ffmpeg."""
    m = re.search(r"%0(\d)d", src)
    if m:
        pad = int(m.group(1))
        d = os.path.dirname(src) or "."
        base = os.path.basename(src)
        pre, suf = base[: base.index("%")], base[base.index("%") + len(m.group(0)):]
        if not os.path.isdir(d):
            return None
        rx = re.compile(r"^%s(\d{%d})%s$" % (re.escape(pre), pad, re.escape(suf)))
        nums = sorted(int(mm.group(1)) for mm in (rx.match(f) for f in os.listdir(d)) if mm)
        if start is not None:
            nums = [n for n in nums if n >= start]
        if not nums:
            return None
        n = 1
        for i in range(1, len(nums)):
            if nums[i] != nums[i - 1] + 1:
                break
            n += 1
        return n
    r = subprocess.run(
        [ff, "-hide_banner", "-nostdin", "-stats", "-loglevel", "error",
         "-i", src, "-map", "0:v:0", "-c", "copy", "-f", "null", "-"],
        capture_output=True, text=True)
    found = re.findall(r"frame=\s*(\d+)", r.stderr)
    return int(found[-1]) if found else None


def read_frames(ff, src, w, h, idxs, start=None):
    sel = "+".join("eq(n\\,%d)" % i for i in idxs)
    args = [ff, "-hide_banner", "-loglevel", "error"]
    if start is not None and "%" in src:
        args += ["-start_number", str(start)]
    args += ["-i", src, "-vf", "select='%s'" % sel] + frame_passthrough_args() + [
             "-pix_fmt", "gray16le", "-f", "rawvideo", "pipe:1"]
    raw = subprocess.run(args, capture_output=True).stdout
    n = len(raw) // (w * h * 2)
    if n == 0:
        sys.exit("decoded 0 frames from %s" % src)
    return np.frombuffer(raw[: n * w * h * 2], np.uint16).reshape(n, h, w)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", required=True, help="%s: movie or printf sequence" % A_NAME)
    ap.add_argument("--b", required=True, help="%s: movie or printf sequence" % B_NAME)
    ap.add_argument("--start", type=int, default=None, help="first frame number of a sequence")
    ap.add_argument("--samples", type=int, default=8)
    ap.add_argument("--div", type=int, default=1, choices=(1, 2, 4),
                    help="the plates were rendered at 1/div. Everything here is "
                         "geometry - expected sizes and where the overlap sits - so "
                         "it all has to scale with it, or a perfectly good half-size "
                         "review render is reported as the wrong resolution and the "
                         "overlap is compared against the wrong columns")
    ap.add_argument("--tol", type=float, default=1.0,
                    help="max mean abs diff in the overlap, on a 0-255 scale (default 1.0)")
    a = ap.parse_args()

    ff = ffmpeg()
    d = max(1, a.div)
    ovx, ovw = OVERLAP_X // d, OVERLAP_W // d
    fails, dims = [], {}

    print()
    for key, src, name in (("a", a.a, A_NAME), ("b", a.b, B_NAME)):
        w, h = probe_size(ff, src, a.start)
        dims[key] = (w, h)
        p = plate(name)
        ew, eh = p["w"] // d, p["h"] // d
        ok = (w, h) == (ew, eh)
        print("%-6s %-46s %5dx%-5d %s" % (name, os.path.basename(src), w, h,
                                          "OK" if ok else "EXPECTED %dx%d" % (ew, eh)))
        if not ok:
            fails.append("%s is %dx%d, expected %dx%d" % (name, w, h, ew, eh))

    na = count_frames(ff, a.a, a.start)
    nb = count_frames(ff, a.b, a.start)
    print("\nframes: %s=%s  %s=%s" % (A_NAME, na, B_NAME, nb))
    if na and nb and na != nb:
        fails.append("frame counts differ: %d vs %d" % (na, nb))

    n = min(na, nb) if (na and nb) else 1
    idxs = sorted(set(np.linspace(0, max(n - 1, 0), a.samples).astype(int).tolist()))

    fa = read_frames(ff, a.a, dims["a"][0], dims["a"][1], idxs, a.start)
    fb = read_frames(ff, a.b, dims["b"][0], dims["b"][1], idxs, a.start)

    print("\noverlap check  (%s x %d..%d  vs  %s x 0..%d)%s"
          % (A_NAME, ovx, ovx + ovw, B_NAME, ovw,
             "" if d == 1 else "   [1/%d scale]" % d))
    print("  %-8s %12s %8s   %s" % ("frame", "mean|diff|", "max", "verdict"))
    worst = 0.0
    for i in range(min(len(fa), len(fb))):
        x = fa[i][:, ovx:ovx + ovw].astype(np.int32)
        y = fb[i][:, :ovw].astype(np.int32)
        d = np.abs(x - y) / 257.0                      # report on a 0-255 scale
        md = float(d.mean())
        worst = max(worst, md)
        print("  %-8d %12.4f %8.1f   %s"
              % (idxs[i], md, d.max(), "OK" if md <= a.tol else "MISMATCH"))
    if worst > a.tol:
        fails.append("overlap differs between plates (worst mean abs diff %.4f > %.4f). "
                     "Slice both plates from ONE master with master_to_plates.ps1." % (worst, a.tol))

    print("\n" + "=" * 62)
    if fails:
        print("FAILED")
        for f in fails:
            print("  - " + f)
        sys.exit(1)
    print("PASSED - plates are consistent and ready to encode")


if __name__ == "__main__":
    main()

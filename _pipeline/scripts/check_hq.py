#!/usr/bin/env python3
"""
Validate the high-quality noise masters before they are used for the delivery.

Run this the moment the ProRes / uncompressed plates arrive, BEFORE rendering
anything from them:

    python check_hq.py

It reads source_hq.SPSW1 / SPSW2 from project.json and answers four questions
that are all cheap to check now and expensive to discover afterwards.

  1. GEOMETRY.  Are they the same size as the mp4s, 7200x2552 and 3588x2552, at
     30.000 fps and 18000 frames? If a master were re-exported at a different
     size the crop and hstack arithmetic in render_shader.py silently produces a
     misaligned canvas.

  2. FRAME ALIGNMENT.  This is the dangerous one. The renderer seeks with -ss
     before -i, which is frame-exact for the intra-only codecs a master is
     likely to use - but "likely" is not a guarantee, and a one-frame offset
     would put this wall a frame out of step with the other eleven surfaces
     while looking completely fine on its own. The check decodes the same frame
     index from the mp4 and from the master and reports which offset in the
     range -3..+3 actually matches best. It must be 0.

  3. LEVELS.  A ProRes master is very often tagged full-range where the mp4 was
     limited, or carries a different matrix. That shifts every luma value, and
     since the plate DRIVES the sky bands and the oil thickness, a shift there
     changes the whole look rather than just the brightness.

  4. THE ARC.  noise_arc.csv holds the mean luma per frame. If it was measured
     from the mp4s and the masters sit at different levels, uPlateMean is wrong
     and rel() mis-centres the dither. The check says whether it needs redoing.

Exit code 0 means safe to render with --hq.
"""
import json
import os
import re
import subprocess
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import CFG, REF_ROOT, ffmpeg, source  # noqa: E402

FPS = CFG["fps"]
PROBE_FRAME = 6000          # mid-loop, busy, well clear of any black passage


def ffprobe_exe(ff):
    """ffprobe sitting next to this ffmpeg, or None.

    Only the FILENAME is rewritten. Replacing "ffmpeg" everywhere in the path
    also rewrites the containing folder - a gyan build lives in
    ffmpeg-9.0.1-full_build\bin, which became ffprobe-9.0.1-full_build and did
    not exist, so every probe silently returned nothing and every file was
    reported as the wrong size.
    """
    d, b = os.path.split(ff)
    if b.lower().startswith("ffmpeg"):
        cand = os.path.join(d, "ffprobe" + b[len("ffmpeg"):])
        if os.path.isfile(cand):
            return cand
    from shutil import which
    return which("ffprobe")


def probe(ff, path):
    exe = ffprobe_exe(ff)
    if exe:
        r = subprocess.run(
            [exe, "-v", "error", "-select_streams", "v:0", "-show_entries",
             "stream=width,height,r_frame_rate,nb_frames,pix_fmt,codec_name,"
             "color_range,color_space,color_primaries,color_transfer",
             "-of", "json", path], capture_output=True, text=True)
        try:
            s = json.loads(r.stdout)["streams"][0]
            if s.get("width"):
                return s
        except Exception:
            pass

    # No ffprobe on this machine: read it out of ffmpeg's own banner instead,
    # so the check still works rather than reporting nonsense.
    r = subprocess.run([ff, "-hide_banner", "-i", path],
                       capture_output=True, text=True)
    txt = (r.stderr or "") + (r.stdout or "")
    out = {}
    m = re.search(r"Stream #\d+:\d+.*?: Video: ([a-zA-Z0-9_]+)[^,]*, *([a-z0-9()]+).*?,"
                  r" *(\d+)x(\d+)", txt)
    if m:
        out["codec_name"] = m.group(1)
        out["pix_fmt"] = m.group(2)
        out["width"] = int(m.group(3))
        out["height"] = int(m.group(4))
    m = re.search(r"([\d.]+) fps", txt)
    if m:
        out["r_frame_rate"] = "%s/1" % int(round(float(m.group(1))))
    return out


def grab(ff, path, frame, w, h):
    """One frame, as the RENDERER fetches it: -ss before -i."""
    ss = "%.6f" % (frame / float(FPS))
    raw = subprocess.run(
        [ff, "-hide_banner", "-loglevel", "error", "-ss", ss, "-i", path,
         "-vf", "scale=%d:%d:flags=area" % (w, h), "-frames:v", "1",
         "-pix_fmt", "gray", "-f", "rawvideo", "pipe:1"],
        capture_output=True).stdout
    if len(raw) < w * h:
        return None
    return np.frombuffer(raw[:w * h], np.uint8).reshape(h, w).astype(np.float32)


def main():
    ff = ffmpeg()
    hq = CFG.get("source_hq", {})
    fails, warns = [], []

    print()
    print("=" * 74)
    print("HIGH-QUALITY NOISE MASTERS  -  pre-flight")
    print("=" * 74)

    for k in ("SPSW1", "SPSW2"):
        if not hq.get(k):
            fails.append("project.json -> source_hq.%s is empty" % k)
        elif not os.path.isfile(hq[k]):
            fails.append("source_hq.%s does not exist: %s" % (k, hq[k]))
    if fails:
        for f in fails:
            print("  FAIL  %s" % f)
        print("\nFill in source_hq in project.json with the full paths, then re-run.")
        return 1

    # ---- 1. geometry -----------------------------------------------------
    print("\n1. GEOMETRY")
    for pl in CFG["plates"]:
        k = pl["name"]
        s = probe(ff, hq[k])
        w, h = s.get("width"), s.get("height")
        rate = s.get("r_frame_rate", "?")
        n = s.get("nb_frames", "?")
        ok = (w == pl["w"] and h == pl["h"])
        print("   %-6s %-22s %sx%s  %s fps  %s frames  %s"
              % (k, s.get("codec_name", "?"), w, h, rate, n, s.get("pix_fmt", "?")))
        if not ok:
            fails.append("%s is %sx%s, the delivery needs %dx%d"
                         % (k, w, h, pl["w"], pl["h"]))
        if rate not in ("30/1", "30000/1000", "?", None):
            warns.append("%s reports %s fps, not 30/1" % (k, rate))
        try:
            if n != "?" and int(n) != CFG["loop_frames"]:
                warns.append("%s has %s frames, the loop is %d"
                             % (k, n, CFG["loop_frames"]))
        except ValueError:
            pass

    # ---- 2. frame alignment ----------------------------------------------
    print("\n2. FRAME ALIGNMENT   (must be offset 0)")
    W, H = 320, 114
    for pl in CFG["plates"]:
        k = pl["name"]
        ref = grab(ff, source(k), PROBE_FRAME, W, H)
        if ref is None:
            warns.append("could not read frame %d from the %s mp4" % (PROBE_FRAME, k))
            continue
        best, scores = None, []
        for off in range(-3, 4):
            g = grab(ff, hq[k], PROBE_FRAME + off, W, H)
            if g is None:
                scores.append((off, float("inf")))
                continue
            # compare SHAPE, not level: the two may sit at different ranges and
            # that is question 3's problem, not this one's
            a = (ref - ref.mean()) / max(ref.std(), 1e-6)
            b = (g - g.mean()) / max(g.std(), 1e-6)
            d = float(np.abs(a - b).mean())
            scores.append((off, d))
            if best is None or d < best[1]:
                best = (off, d)
        line = "  ".join("%+d:%.3f" % (o, d) for o, d in scores)
        print("   %-6s %s" % (k, line))
        if best is None:
            fails.append("could not decode %s master at all" % k)
        elif best[0] != 0:
            fails.append("%s master is offset by %+d frames against the mp4. "
                         "Rendering from it would put this wall out of step with "
                         "the other eleven surfaces." % (k, best[0]))
        else:
            print("          best match at offset 0 - aligned")

    # ---- 3. levels ---------------------------------------------------------
    print("\n3. LEVELS")
    shift = {}
    for pl in CFG["plates"]:
        k = pl["name"]
        ref = grab(ff, source(k), PROBE_FRAME, W, H)
        g = grab(ff, hq[k], PROBE_FRAME, W, H)
        if ref is None or g is None:
            continue
        rm, gm = ref.mean() / 255.0, g.mean() / 255.0
        shift[k] = gm - rm
        s = probe(ff, hq[k])
        print("   %-6s mp4 mean %.4f   master mean %.4f   difference %+.4f   "
              "range=%s space=%s"
              % (k, rm, gm, gm - rm, s.get("color_range", "unset"),
                 s.get("color_space", "unset")))
        if abs(gm - rm) > 0.02:
            warns.append("%s master sits %+.3f away from the mp4 in mean luma - "
                         "very likely a full/limited range tag difference"
                         % (k, gm - rm))

    # ---- 4. the arc --------------------------------------------------------
    print("\n4. THE ARC")
    arc = os.path.join(REF_ROOT, "noise_arc.csv")
    if not os.path.isfile(arc):
        fails.append("noise_arc.csv is missing - run: python analyse_arc.py --hq")
    else:
        big = max((abs(v) for v in shift.values()), default=0.0)
        if big > 0.01:
            fails.append("noise_arc.csv was measured from the mp4s and the masters "
                         "differ by %.3f in mean luma. The arc feeds uPlateMean, "
                         "which centres the dither that drives the sky bands and "
                         "the oil - it MUST be remeasured: python analyse_arc.py --hq"
                         % big)
        else:
            print("   master levels match the mp4s to within %.4f - the existing "
                  "arc is still valid." % big)
            print("   (Remeasuring with --hq is still the tidier thing to do.)")

    # ---- verdict -----------------------------------------------------------
    print()
    print("=" * 74)
    for w_ in warns:
        print("  warn  %s" % w_)
    if fails:
        print("  NOT READY")
        for f in fails:
            print("   - %s" % f)
        print("=" * 74)
        return 1
    print("  READY - safe to render with --hq")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    sys.exit(main())

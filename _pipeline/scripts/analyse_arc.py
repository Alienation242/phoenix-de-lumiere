#!/usr/bin/env python3
"""
Measure the luminance / motion arc of the noise plate and write it as a CSV
you can drive your shader from.

The plate already carries the show's dramaturgy. Baking the arc to a channel and
driving your oil<->cloud morph from it is more predictable than sampling the
live video every frame, and it survives you swapping the video input.

Usage
    python analyse_arc.py                                  # default: 2:40-4:00 with handles
    python analyse_arc.py --start 4800 --count 2400        # exactly 2:40-4:00
    python analyse_arc.py --src ...\\PxDL_SW_SPSW2.mp4 --out arc_spsw2.csv

Output columns
    frame       frame number in the 10-minute loop
    t           seconds into the loop
    t_local     seconds into your segment
    mean        mean luma 0-1        <- the main driver
    mean_norm   mean rescaled so the segment spans 0-1
    std         contrast 0-1
    p05 p95     5th / 95th percentile luma 0-1
    motion      mean abs frame-to-frame difference 0-1  <- spikes = cuts
"""
import argparse, os, subprocess, sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import CFG, REF_ROOT, ffmpeg, source, plate  # noqa: E402

SRC_DEFAULT = source("SPSW1")
OUT_DEFAULT = os.path.join(REF_ROOT, "noise_arc.csv")
FPS = CFG["fps"]
SEG = CFG["segment"]
PLATE_W = CFG["plates"][0]["w"]
PLATE_H = CFG["plates"][0]["h"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=SRC_DEFAULT)
    ap.add_argument("--start", type=int, default=SEG["in"] - SEG["handles"], help="first loop frame")
    ap.add_argument("--count", type=int,
                    default=(SEG["out"] - SEG["in"] + 1) + 2 * SEG["handles"], help="number of frames")
    ap.add_argument("--width", type=int, default=240, help="analysis width (cheap + stable)")
    ap.add_argument("--out", default=OUT_DEFAULT)
    a = ap.parse_args()

    a.ffmpeg = ffmpeg()
    if not os.path.isfile(a.src):
        sys.exit("source not found: %s" % a.src)

    w = a.width
    h = max(1, int(round(w * PLATE_H / float(PLATE_W))))
    ss = "%.6f" % (a.start / float(FPS))
    dur = "%.6f" % ((a.count + 1) / float(FPS))

    print("decoding %d frames from %s at %dx%d ..." % (a.count, os.path.basename(a.src), w, h))
    raw = subprocess.run(
        [a.ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", ss, "-t", dur, "-i", a.src,
         "-vf", "scale=%d:%d:flags=area" % (w, h), "-pix_fmt", "gray",
         "-f", "rawvideo", "pipe:1"], capture_output=True).stdout
    n = len(raw) // (w * h)
    if n < 2:
        sys.exit("decoded %d frames - check --start / --count" % n)
    v = np.frombuffer(raw[: n * w * h], np.uint8).reshape(n, h, w).astype(np.float32) / 255.0
    n = min(n, a.count)
    v = v[:n]
    print("got %d frames" % n)

    flat = v.reshape(n, -1)
    mean = flat.mean(1)
    std = flat.std(1)
    p05 = np.percentile(flat, 5, axis=1)
    p95 = np.percentile(flat, 95, axis=1)
    motion = np.zeros(n, np.float32)
    motion[1:] = np.abs(np.diff(v, axis=0)).mean(axis=(1, 2))

    lo, hi = float(mean.min()), float(mean.max())
    mean_norm = (mean - lo) / max(hi - lo, 1e-6)

    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w") as f:
        f.write("frame,t,t_local,mean,mean_norm,std,p05,p95,motion\n")
        for i in range(n):
            fr = a.start + i
            f.write("%d,%.4f,%.4f,%.6f,%.6f,%.6f,%.6f,%.6f,%.6f\n" %
                    (fr, fr / float(FPS), i / float(FPS),
                     mean[i], mean_norm[i], std[i], p05[i], p95[i], motion[i]))
    print("wrote %s" % a.out)

    med = float(np.median(motion[1:]))
    thr = max(med * 6, 4.0 / 255.0)
    cuts = [i for i in range(1, n) if motion[i] > thr]
    print("\nmean luma  %.3f -> %.3f   (min %.3f @ %.2fs, max %.3f @ %.2fs of segment)" %
          (mean[0], mean[-1], mean.min(), mean.argmin() / FPS, mean.max(), mean.argmax() / FPS))
    print("cuts detected: %d  (threshold %.4f)" % (len(cuts), thr))
    for i in cuts[:40]:
        t = (a.start + i) / float(FPS)
        print("   frame %5d  %d:%06.3f  strength %.4f" % (a.start + i, int(t // 60), t % 60, motion[i]))
    if len(cuts) > 40:
        print("   ... and %d more" % (len(cuts) - 40))


if __name__ == "__main__":
    main()

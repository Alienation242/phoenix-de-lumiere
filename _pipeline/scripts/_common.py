"""
Shared setup for the Python scripts in this folder.

    from _common import CFG, ROOT, ffmpeg, plate, source, decode_gray

Nothing here is machine-specific: paths resolve from this file's own location,
so the project folder can be copied to any PC. Point the big output folders at
an external drive with environment variables:

    PXDL_WORK_ROOT  PXDL_RENDER_ROOT  PXDL_DELIVER_ROOT  PXDL_FFMPEG  PXDL_ROOT
"""
import json
import os
import shutil
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))

ROOT = os.path.abspath(os.environ.get("PXDL_ROOT") or os.path.join(_HERE, "..", ".."))
PIPELINE = os.path.join(ROOT, "_pipeline")

_cfg_path = os.path.join(PIPELINE, "project.json")
if not os.path.isfile(_cfg_path):
    sys.exit("project.json not found at %s" % _cfg_path)
with open(_cfg_path, "r", encoding="utf-8") as fh:
    CFG = json.load(fh)


def _root(env_name, relative):
    return os.environ.get(env_name) or os.path.join(ROOT, relative)


WORK_ROOT = _root("PXDL_WORK_ROOT", CFG["dirs"]["work"])
RENDER_ROOT = _root("PXDL_RENDER_ROOT", CFG["dirs"]["render"])
DELIVER_ROOT = _root("PXDL_DELIVER_ROOT", CFG["dirs"]["deliver"])
MASK_ROOT = os.path.join(ROOT, CFG["dirs"]["masks"])
REF_ROOT = os.path.join(ROOT, CFG["dirs"]["reference"])

# The same facade, described two ways.
#
#   layer     the colour-coded mask supplied with the project, exactly as drawn
#   aligned   the same shapes, each translated as one rigid piece onto the
#             border the shared noise plate draws around its own architecture
#
# Nothing is redrawn in "aligned" - straight edges stay straight, arches stay
# arches, the stepped capital on each pillar is the authored one - only the
# position changes, and only where the plate clearly says so. Both sets live
# side by side so a render can be done either way and the two compared.
MASK_ROOT_ALIGNED = MASK_ROOT + "_aligned"
REF_ROOT_ALIGNED = REF_ROOT + "_aligned"
MASK_VARIANTS = ("layer", "aligned")
MASK_ALIASES = {"noise": "aligned"}      # what "aligned" was called before


def mask_variant(name):
    """Normalise a variant name, accepting the old spelling."""
    v = MASK_ALIASES.get((name or "").lower(), (name or "").lower())
    if v not in MASK_VARIANTS:
        sys.exit("mask variant must be one of %s, not %r"
                 % (" / ".join(MASK_VARIANTS), name))
    return v


def encodable_size(w, h):
    """The nearest size an encoder will actually take.

    Every codec in the delivery path either subsamples chroma 2x2 or aligns its
    macroblocks on even dimensions, so an ODD dimension cannot be encoded at
    all. The canvas divided by 4 is 2447 x 638 - odd width - and without this
    the encoder exits on its first frame and the render dies on a broken pipe
    several seconds later, pointing at the wrong line entirely.

    One function, used by whatever writes a file AND by whatever checks it
    afterwards, so the two can never disagree about what the size should be.
    """
    return (int(w) // 2 * 2, int(h) // 2 * 2)


def mask_dirs(variant="layer"):
    """(mask folder, reference folder) for a mask variant."""
    if mask_variant(variant) == "aligned":
        return MASK_ROOT_ALIGNED, REF_ROOT_ALIGNED
    return MASK_ROOT, REF_ROOT


def ref_file(name, ref_root=None):
    """A reference asset, falling back to the shared reference folder.

    noise_arc.csv is measured off the plate and has nothing to do with which
    mask set is in use, so the noise variant does not keep its own copy of it.
    """
    if ref_root:
        p = os.path.join(ref_root, name)
        if os.path.isfile(p):
            return p
    return os.path.join(REF_ROOT, name)

CANVAS_W = CFG["canvas"]["w"]
CANVAS_H = CFG["canvas"]["h"]
FPS = CFG["fps"]


def ffmpeg():
    """Locate an ffmpeg binary. Explicit override wins, then _pipeline/bin, then PATH.

    bin/ deliberately beats PATH: somebody put that one there on purpose and it
    travels with the project, where an ffmpeg on PATH is whatever the machine
    happens to have - on this one, TouchDesigner's build, which decodes
    everything and encodes nothing we deliver in.
    """
    env = os.environ.get("PXDL_FFMPEG")
    if env:
        if os.path.isfile(env):
            return env
        sys.exit("PXDL_FFMPEG is set but does not exist: %s" % env)

    candidates = [os.path.join(PIPELINE, "bin", "ffmpeg.exe")]
    onpath = shutil.which("ffmpeg")
    if onpath:
        candidates.append(onpath)
    candidates += [
        r"C:\Program Files\Derivative\TouchDesigner\bin\ffmpeg.exe",
        r"C:\ffmpeg\bin\ffmpeg.exe",
    ]
    deriv = r"C:\Program Files\Derivative"
    if os.path.isdir(deriv):
        for d in os.listdir(deriv):
            candidates.append(os.path.join(deriv, d, "bin", "ffmpeg.exe"))
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    sys.exit(
        "ffmpeg not found. On any machine, run:\n"
        "    .\\_pipeline\\scripts\\get_ffmpeg.ps1\n"
        "which downloads a full build, verifies it and puts it in\n"
        "  %s\\bin\\ffmpeg.exe\n"
        "Or put one on PATH, or set PXDL_FFMPEG." % PIPELINE
    )


_PASSTHROUGH = None


def frame_passthrough_args():
    """The args that make ffmpeg emit exactly the frames `select` picked.

    `-vsync 0` did this for years, was deprecated in 5.1 in favour of
    `-fps_mode passthrough`, and was REMOVED in 9.0 - a build this new errors out
    with "Unrecognized option 'vsync'" and decodes nothing. Probe the actual
    binary once rather than guessing from its version string, which may be a
    git hash. Keeps old render nodes working too.
    """
    global _PASSTHROUGH
    if _PASSTHROUGH is None:
        probe = subprocess.run(
            [ffmpeg(), "-hide_banner", "-loglevel", "error",
             "-f", "lavfi", "-i", "color=c=black:s=16x16:d=0.1",
             "-fps_mode", "passthrough", "-frames:v", "1", "-f", "null", "-"],
            capture_output=True)
        _PASSTHROUGH = (["-fps_mode", "passthrough"] if probe.returncode == 0
                        else ["-vsync", "0"])
    return list(_PASSTHROUGH)


def plate(name):
    for p in CFG["plates"]:
        if p["name"] == name:
            return p
    sys.exit("unknown plate %r" % name)


def source(key):
    rel = CFG["source"].get(key)
    if not rel:
        sys.exit("no source %r in project.json" % key)
    p = os.path.join(ROOT, rel.replace("/", os.sep))
    if not os.path.isfile(p):
        sys.exit("source file missing: %s" % p)
    return p


def scale(div):
    for v in CFG["scales"].values():
        if isinstance(v, dict) and v.get("div") == div:
            return v
    sys.exit("div must be 1, 2 or 4 (whole-pixel divisors of %dx%d)" % (CANVAS_W, CANVAS_H))


def decode_gray(src, w, h, start_time=None, frames=1, vf=None, start_number=None):
    """Decode frames as 8-bit grayscale -> numpy array (frames, h, w)."""
    import numpy as np

    args = [ffmpeg(), "-hide_banner", "-loglevel", "error"]
    if start_time is not None:
        args += ["-ss", "%.6f" % start_time]
    if start_number is not None and "%" in src:
        args += ["-start_number", str(start_number)]
    args += ["-i", src]
    if vf:
        args += ["-vf", vf]
    args += ["-frames:v", str(frames), "-pix_fmt", "gray", "-f", "rawvideo", "pipe:1"]
    raw = subprocess.run(args, capture_output=True).stdout
    n = len(raw) // (w * h)
    if n == 0:
        sys.exit("decoded 0 frames from %s" % src)
    return np.frombuffer(raw[: n * w * h], np.uint8).reshape(n, h, w)


def show_context():
    print()
    print("project   %s  [%s]" % (CFG["project"], CFG["surface"]))
    print("root      %s" % ROOT)
    print("ffmpeg    %s" % ffmpeg())
    print("canvas    %d x %d @ %d fps" % (CANVAS_W, CANVAS_H, FPS))
    print("segment   frames %d .. %d  (+/- %d handles)"
          % (CFG["segment"]["in"], CFG["segment"]["out"], CFG["segment"]["handles"]))
    print("work      %s" % WORK_ROOT)
    print("render    %s" % RENDER_ROOT)
    print("deliver   %s" % DELIVER_ROOT)
    print()

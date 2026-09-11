#!/usr/bin/env python3
"""
Render the SW wall at ANY resolution with no licence of any kind.

TouchDesigner Non-Commercial refuses to output above 1280x1280, which rules it
out for a 9788x2552 delivery. This renders the same GLSL on a plain OpenGL 3.3
context instead, at any resolution, with no licensed software in the path.

The scene, in layers, back to front:

    sky + clouds            the noise plate read as a sky inside the venue
    oil                     one thin-film field per opening, not one per wall
    reveals                 fake jambs inside every opening, lit by camera side
    objects (outside)       chrome, clipped to the opening they are passing through
    objects (inside)        chrome, loose in the room
    pillars                 the two column bays, standing in front of everything

A slow left-right camera drives all of it. Each layer moves by a different
amount, which is the whole parallax illusion - on a flat wall, differential
motion IS depth.

    # 20-second preview at quarter size, straight to MP4
    python render_shader.py --div 4 --start 4800 --count 600 --mp4

    # the whole segment at full resolution, PNG sequence ready for slicing
    python render_shader.py --div 1 --png

    # background only, no objects
    python render_shader.py --div 4 --count 300 --mp4 --no-objects

Needs: moderngl, numpy, ffmpeg.   python -m pip install moderngl
"""
import argparse
import csv
import json
import math
import os
import re
import subprocess
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import (CFG, MASK_ROOT, REF_ROOT, RENDER_ROOT, WORK_ROOT,  # noqa: E402
                     ffmpeg, source)

SHADERS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "shaders")
CW, CH = CFG["canvas"]["w"], CFG["canvas"]["h"]
FPS = CFG["fps"]


# --------------------------------------------------------------------------- shader loading

def be_polite(threads):
    """Drop this process below normal priority.

    A full pass is minutes of 100% GPU with every core busy in x264. At normal
    priority that makes the whole machine unusable - the desktop stops
    redrawing and it looks like a hang. Below-normal costs a few percent of
    render speed and hands the interactive session back to its owner.
    """
    try:
        if os.name == "nt":
            import ctypes
            BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
            k = ctypes.windll.kernel32
            k.SetPriorityClass(k.GetCurrentProcess(), BELOW_NORMAL_PRIORITY_CLASS)
        else:
            os.nice(10)
    except Exception:
        pass
    return threads


def popen_polite(args, **kw):
    """Popen, but the child process is below normal priority too."""
    flags = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
    if flags:
        kw["creationflags"] = kw.get("creationflags", 0) | flags
    return subprocess.Popen(args, **kw)


def load_shader(name, _depth=0):
    """Read a shader, resolving #include "file" - core GL 3.3 has no such thing.

    lib_common.glsl carries its own #ifndef guard, so including it from both the
    vertex and the fragment stage of the same program is safe.
    """
    if _depth > 8:
        sys.exit("#include nested too deep at %s" % name)
    with open(os.path.join(SHADERS, name), "r", encoding="utf-8") as fh:
        src = fh.read()
    out = []
    for line in src.splitlines():
        m = re.match(r'\s*#include\s+"([^"]+)"\s*$', line)
        out.append(load_shader(m.group(1), _depth + 1) if m else line)
    return "\n".join(out)


# --------------------------------------------------------------------------- geometry

def _tris(verts, tris, normals=None):
    """Expand indexed geometry to triangles.

    normals=None gives flat shading from the face normal, which is correct for a
    cube or a pyramid - a real polished cube has hard edges. Pass per-vertex
    normals for anything that is meant to be curved, or the chrome reads as a
    faceted disco ball rather than a mirror.
    """
    out = []
    for a, b, c in tris:
        p0, p1, p2 = (np.array(verts[i], "f8") for i in (a, b, c))
        if normals is None:
            n = np.cross(p1 - p0, p2 - p0)
            ln = np.linalg.norm(n)
            n = n / ln if ln > 1e-9 else np.array([0.0, 0.0, 1.0])
            ns = (n, n, n)
        else:
            ns = tuple(np.array(normals[i], "f8") for i in (a, b, c))
        for p, n, uv in zip((p0, p1, p2), ns, ((0, 0), (1, 0), (0.5, 1))):
            out.append([*p, *n, *uv])
    return np.array(out, "f4")


def cube():
    v = [(-1,-1,-1), (1,-1,-1), (1,1,-1), (-1,1,-1), (-1,-1,1), (1,-1,1), (1,1,1), (-1,1,1)]
    t = [(0,2,1),(0,3,2),(4,5,6),(4,6,7),(0,1,5),(0,5,4),
         (2,3,7),(2,7,6),(1,2,6),(1,6,5),(0,4,7),(0,7,3)]
    return _tris(v, t)


def pyramid():
    v = [(-1,-1,-1), (1,-1,-1), (1,-1,1), (-1,-1,1), (0,1.4,0)]
    t = [(0,2,1),(0,3,2),(0,1,4),(1,2,4),(2,3,4),(3,0,4)]
    return _tris(v, t)


def diamond():
    v = [(0,1.5,0), (1,0,0), (0,0,1), (-1,0,0), (0,0,-1), (0,-1.5,0)]
    t = [(0,1,2),(0,2,3),(0,3,4),(0,4,1),(5,2,1),(5,3,2),(5,4,3),(5,1,4)]
    return _tris(v, t)


def sphere(seg=96, ring=48):
    """Smooth, dense. On a unit sphere the normal IS the position."""
    v, n = [], []
    for i in range(ring + 1):
        phi = math.pi * i / ring
        for j in range(seg + 1):
            th = 2 * math.pi * j / seg
            p = (math.sin(phi) * math.cos(th), math.cos(phi), math.sin(phi) * math.sin(th))
            v.append(p)
            n.append(p)
    t = []
    row = seg + 1
    for i in range(ring):
        for j in range(seg):
            a, b = i * row + j, i * row + j + 1
            c, d = (i + 1) * row + j, (i + 1) * row + j + 1
            t += [(a, c, d), (a, d, b)]
    return _tris(v, t, n)


def torus(major=1.0, minor=0.38, seg=96, ring=48):
    v, n = [], []
    for i in range(ring + 1):
        u = 2 * math.pi * i / ring
        cu, su = math.cos(u), math.sin(u)
        for j in range(seg + 1):
            w = 2 * math.pi * j / seg
            cw, sw = math.cos(w), math.sin(w)
            v.append(((major + minor * cw) * cu, minor * sw, (major + minor * cw) * su))
            n.append((cw * cu, sw, cw * su))
    t = []
    row = seg + 1
    for i in range(ring):
        for j in range(seg):
            a, b = i * row + j, i * row + j + 1
            c, d = (i + 1) * row + j, (i + 1) * row + j + 1
            t += [(a, c, d), (a, d, b)]
    return _tris(v, t, n)


SHAPES = {"sphere": sphere, "torus": torus, "cube": cube,
          "diamond": diamond, "pyramid": pyramid}


# --------------------------------------------------------------------------- matrices

def ortho(l, r, b, t, n, f):
    m = np.eye(4, dtype="f4")
    m[0, 0] = 2 / (r - l); m[1, 1] = 2 / (t - b); m[2, 2] = -2 / (f - n)
    m[0, 3] = -(r + l) / (r - l); m[1, 3] = -(t + b) / (t - b); m[2, 3] = -(f + n) / (f - n)
    return m


def model_matrix(pos, scale, rot):
    rx, ry, rz = rot
    cx, sx = math.cos(rx), math.sin(rx)
    cy, sy = math.cos(ry), math.sin(ry)
    cz, sz = math.cos(rz), math.sin(rz)
    Rx = np.array([[1,0,0],[0,cx,-sx],[0,sx,cx]], "f4")
    Ry = np.array([[cy,0,sy],[0,1,0],[-sy,0,cy]], "f4")
    Rz = np.array([[cz,-sz,0],[sz,cz,0],[0,0,1]], "f4")
    R = Rz @ Ry @ Rx * scale
    m = np.eye(4, dtype="f4")
    m[:3, :3] = R
    m[:3, 3] = pos
    return m


def normal_matrix(m):
    """Inverse transpose of the upper 3x3, so normals survive the transform."""
    try:
        return np.linalg.inv(m[:3, :3]).T.astype("f4")
    except np.linalg.LinAlgError:
        return np.eye(3, dtype="f4")


def smooth(x, a, b):
    if b <= a:
        return 1.0 if x >= b else 0.0
    t = min(max((x - a) / (b - a), 0.0), 1.0)
    return t * t * (3 - 2 * t)


# --------------------------------------------------------------------------- camera

def camera_x(t, amp, period):
    """Slow lateral drift. Two incommensurate periods so it never sits still and
    never repeats on a beat the eye can latch onto."""
    a = math.sin(2 * math.pi * t / period)
    b = math.sin(2 * math.pi * t / (period * 0.371) + 1.7)
    return amp * (0.86 * a + 0.14 * b)


# --------------------------------------------------------------------------- scene

def pick_target_door(doors):
    """The big door in the middle. Nearest the canvas centre; it is also the
    largest of the three (757x754 against 556 and 589 wide), so both readings
    agree and there is nothing to guess."""
    return min(doors, key=lambda d: abs(d["cx"] - CW * 0.5))


def build_objects(openings, seed, count, t_lo, t_hi, obj_scale=1.0, drift=1.0):
    """Every object: in through a window, across the room, out through the big
    middle door. Out of the room is out of the piece - they do not come back."""
    rng = np.random.default_rng(seed)
    wins = [o for o in openings if o["kind"] == "WINDOW"]
    doors = [o for o in openings if o["kind"] == "DOOR"]
    if not wins or not doors:
        return []
    door = pick_target_door(doors)
    # Weighted, not round-robin. A mirror finish reads best on curvature, so
    # spheres and tori carry the look and the flat-faced solids punctuate it.
    names = ["sphere", "torus", "sphere", "diamond", "torus", "cube",
             "sphere", "pyramid"]
    objs = []
    for i in range(count):
        w = wins[int(rng.integers(len(wins)))]
        dur = float(rng.uniform(13.0, 22.0))
        # t0 is in SEGMENT time and must span the whole segment, not the chunk
        # being rendered. Spreading it over the chunk instead means a preview of
        # any sub-range lands entirely outside every object's lifetime and you
        # render an empty wall wondering what broke.
        # Bias entries later: the plate is near-black until about 3:10, so the
        # segment should start almost empty and fill up.
        u = float(rng.random()) ** 0.8
        t0 = (t_lo - dur * 0.6) + u * ((t_hi - t_lo) + dur * 0.6)

        # One in four is a hero. Uniform sizing reads as wallpaper however big
        # you make it; the hierarchy is what makes any of them feel like events.
        hero = (i % 4 == 0)
        base = float(rng.uniform(0.95, 1.35)) * (1.55 if hero else 1.0)

        wx, wy = float(w["cx"]), float(w["cy"])
        dx, dy = float(door["cx"]), float(door["cy"])
        side = 1.0 if wx < dx else -1.0
        # leave the window outward into the room, then swing down into the door
        c1 = (wx + side * float(rng.uniform(400, 1400)),
              wy + float(rng.uniform(300, 900)))
        c2 = (dx - side * float(rng.uniform(700, 2100)),
              dy - float(rng.uniform(800, 1500)))

        def wander():
            """Three incommensurate sines. Their sum never repeats inside the
            segment, which is the difference between drifting and orbiting."""
            return [(float(rng.uniform(0.045, 0.21)),      # Hz
                     float(rng.uniform(0, 6.283)),         # phase
                     float(rng.uniform(0.30, 1.0)))        # weight
                    for _ in range(3)]

        objs.append(dict(
            shape=names[i % len(names)],
            p0=(wx, wy), c1=c1, c2=c2, p2=(dx, dy),
            win=(wx, wy, float(w["w"]) * 0.5, float(w["h"]) * 0.5),
            door=(dx, dy, float(door["w"]) * 0.5, float(door["h"]) * 0.5),
            t0=t0, dur=dur, hero=hero,
            size=float(min(w["w"], w["h"])) * base * obj_scale,
            wander_x=wander(), wander_y=wander(),
            drift=drift * (190.0 if hero else 130.0),
            bob=float(rng.uniform(35.0, 95.0)) * drift,
            bob_w=float(rng.uniform(0.09, 0.24)),
            bob_p=float(rng.uniform(0, 6.283)),
            spin=(float(rng.uniform(-0.20, 0.20)), float(rng.uniform(-0.26, 0.26)),
                  float(rng.uniform(-0.14, 0.14))),
            # precession: a slow wobble ON TOP of the spin. Nothing in the real
            # world rotates at a perfectly constant rate about a fixed axis, and
            # the eye reads that immediately as machinery.
            rot_w=(float(rng.uniform(0.09, 0.27)), float(rng.uniform(0.07, 0.23)),
                   float(rng.uniform(0.11, 0.31))),
            rot_p=(float(rng.uniform(0, 6.283)), float(rng.uniform(0, 6.283)),
                   float(rng.uniform(0, 6.283))),
            tint=np.array([rng.uniform(0.86, 1.0), rng.uniform(0.88, 1.0),
                           rng.uniform(0.90, 1.0)], "f4"),
        ))
    return objs


def bez3(p0, c1, c2, p2, t):
    u = 1 - t
    a, b, c, d = u*u*u, 3*u*u*t, 3*u*t*t, t*t*t
    return (a*p0[0] + b*c1[0] + c*c2[0] + d*p2[0],
            a*p0[1] + b*c1[1] + c*c2[1] + d*p2[1])


def _opening_nearness(x, y, rect):
    """1 when the object sits squarely in this opening, 0 once it is clear of it.

    Normalised by the opening's own half-size, so a 325 px window and a 757 px
    door both behave the same way without separate tuning.
    """
    cx, cy, hw, hh = rect
    dx = (x - cx) / max(hw, 1e-3)
    dy = (y - cy) / max(hh, 1e-3)
    return 1.0 - smooth(math.sqrt(dx * dx + dy * dy), 0.45, 1.25)


def object_state(o, t):
    """(pos, scale, alpha, w_outside, depth01) or None if not on screen.

    w_outside is the crossfade that sells the whole idea. At the start the
    object is beyond the wall and is drawn ONLY where its window is, so it
    reads as arriving from outside. It comes through, crosses the room fully
    visible, then goes back outside through the door and is clipped again.

    It is driven by DISTANCE TO THE OPENING, not by path progress. Tying it to
    progress means the crossfade fires at whatever point in the curve you tuned
    it for, and since every object gets a different bezier, objects would start
    being clipped while still hundreds of pixels short of the door - vanishing
    beside the doorway instead of passing through it.
    """
    p = (t - o["t0"]) / o["dur"]
    if p <= 0.0 or p >= 1.0:
        return None

    # Ease, but only partly. Linear progress along a bezier is the single
    # biggest reason CG motion reads as computed - real things accelerate away
    # and settle in. Full smoothstep is the opposite mistake: it stalls the
    # object exactly at the openings, which is where it most needs to be moving.
    pe = p + (smooth(p, 0.0, 1.0) - p) * 0.45

    x, y = bez3(o["p0"], o["c1"], o["c2"], o["p2"], pe)

    # Low-frequency wander, faded out at both ends by `env` so the object still
    # arrives exactly on its window and exactly in the doorway. Without this the
    # path is a curve a computer drew; with it the object is drifting through a
    # room and happens to be going somewhere.
    env = math.sin(math.pi * pe)
    wx = sum(g * math.sin(2 * math.pi * f * t + ph) for f, ph, g in o["wander_x"])
    wy = sum(g * math.sin(2 * math.pi * f * t + ph) for f, ph, g in o["wander_y"])
    x += wx * o["drift"] * env
    y += wy * o["drift"] * 0.55 * env
    y += math.sin(2 * math.pi * o["bob_w"] * t + o["bob_p"]) * o["bob"] * env

    # 0 at both openings, 1 at the near point in the middle of the room
    depth = math.sin(math.pi * min(max(pe, 0.0), 1.0)) ** 0.75

    scale = o["size"] * (0.30 + 0.85 * depth)
    alpha = smooth(pe, 0.0, 0.04) * (1.0 - smooth(pe, 0.96, 1.0))

    # outside -> inside -> outside. The progress terms only gate WHICH opening
    # can claim it, so an object drifting past an unrelated window is not
    # suddenly cut in half by it.
    w_win = (1.0 - smooth(pe, 0.0, 0.40)) * _opening_nearness(x, y, o["win"])
    w_door = smooth(pe, 0.45, 0.98) * _opening_nearness(x, y, o["door"])
    w_out = min(max(max(w_win, w_door), 0.0), 1.0)

    z = -600.0 + 1200.0 * depth
    return (np.array([x, y, z], "f4"), scale, alpha, w_out, depth)


# --------------------------------------------------------------------------- textures

def load_gray(ff, path, w, h):
    raw = subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-i", path,
                          "-vf", "scale=%d:%d:flags=area" % (w, h),
                          "-pix_fmt", "gray", "-f", "rawvideo", "pipe:1"],
                         capture_output=True).stdout
    if len(raw) < w * h:
        sys.exit("could not read %s" % path)
    return np.frombuffer(raw[: w * h], np.uint8).reshape(h, w)


def load_rgb(ff, path, w, h, nearest=True):
    flags = "neighbor" if nearest else "area"
    raw = subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-i", path,
                          "-vf", "scale=%d:%d:flags=%s" % (w, h, flags),
                          "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"],
                         capture_output=True).stdout
    if len(raw) < w * h * 3:
        sys.exit("could not read %s" % path)
    return np.frombuffer(raw[: w * h * 3], np.uint8).reshape(h, w, 3)


# --------------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--div", type=int, default=4, choices=(1, 2, 4))
    ap.add_argument("--start", type=int, default=None)
    ap.add_argument("--count", type=int, default=None)
    ap.add_argument("--png", action="store_true", help="write a PNG sequence (for delivery)")
    ap.add_argument("--mp4", action="store_true", help="write a preview MP4")
    ap.add_argument("--out", default="")
    ap.add_argument("--preview-width", type=int, default=0,
                    help="downscale the MP4 to this width (render stays full size)")
    ap.add_argument("--hq", action="store_true", help="use the ProRes masters from source_hq")
    ap.add_argument("--no-objects", action="store_true")
    ap.add_argument("--no-pillars", action="store_true")
    ap.add_argument("--objects", type=int, default=26)
    ap.add_argument("--obj-scale", type=float, default=1.0,
                    help="overall object size multiplier")
    ap.add_argument("--drift", type=float, default=1.0,
                    help="how far objects wander off their path. 0 = dead-straight "
                         "bezier, which reads as computed")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--threads", type=int, default=2,
                    help="ffmpeg threads for BOTH decode and encode. the default is "
                         "deliberately small so a long preview leaves you a usable "
                         "machine; raise it for the final render")
    ap.add_argument("--full-speed", action="store_true",
                    help="run at normal priority. faster, but the desktop will crawl")
    ap.add_argument("--msaa", type=int, default=8,
                    help="multisampling on the object pass. chrome lives or dies on its edges")

    # look
    ap.add_argument("--levels", type=float, default=32.0,
                    help="colour steps on the WALL. the chrome is never quantised")
    ap.add_argument("--dither-grid", type=float, default=1.0,
                    help="ordered-dither cell in RENDER pixels. Keep it at 1: see the "
                         "note by uGrid below.")
    ap.add_argument("--cloud", type=float, default=0.55)
    ap.add_argument("--plate-mix", type=float, default=0.55)
    ap.add_argument("--horizon", type=float, default=0.34)
    ap.add_argument("--bands", type=float, default=12.0)
    ap.add_argument("--spread", type=float, default=0.95)
    ap.add_argument("--sky-gain", type=float, default=1.7)
    ap.add_argument("--oil-gain", type=float, default=4.2)
    ap.add_argument("--oil-sweep", type=float, default=0.55,
                    help="per-opening view-angle swing. above ~0.8 the film goes white at "
                         "the opening edges, which is the bug this replaced")
    ap.add_argument("--film", default="220,780", help="thin-film thickness range, nm")
    ap.add_argument("--stone", default="0.72,0.66,0.58", help="pillar colour")
    ap.add_argument("--color", default="0.38,0.52,0.85")

    # depth
    ap.add_argument("--parallax", type=float, default=340.0,
                    help="camera sway amplitude in canvas px")
    ap.add_argument("--parallax-period", type=float, default=26.0, help="seconds")
    ap.add_argument("--par-sky", type=float, default=-1.0,
                    help="sky shift per unit camera. negative = behind the wall")
    ap.add_argument("--par-in", type=float, default=-0.55, help="opening interiors")
    ap.add_argument("--par-obj", type=float, default=0.85, help="objects in the room")
    ap.add_argument("--reveal", type=float, default=0.13,
                    help="fake jamb depth, in local opening uv")
    ap.add_argument("--pillar-shift", type=float, default=44.0,
                    help="pillar silhouette travel in canvas px. keep it small")
    ap.add_argument("--pillar-wobble", type=float, default=9.0)
    ap.add_argument("--pillar-gain", type=float, default=0.85)

    # chrome
    ap.add_argument("--gloss", type=float, default=90.0)
    ap.add_argument("--spec-gain", type=float, default=3.4)
    ap.add_argument("--windows", type=float, default=2.4,
                    help="brightness of the room openings reflected in the metal. "
                         "this is what stops a chrome sphere being a grey ball")
    ap.add_argument("--room-mix", type=float, default=0.20)
    ap.add_argument("--exposure", type=float, default=0.95)
    ap.add_argument("--view-fov", type=float, default=0.13,
                    help="fake perspective on the chrome. 0 makes every flat face a "
                         "single flat colour, because the camera is orthographic")
    ap.add_argument("--horizon-hot", type=float, default=0.90,
                    help="the reflected horizon line. this is what reads as metal")
    a = ap.parse_args()

    if not a.png and not a.mp4:
        a.mp4 = True
    seg = CFG["segment"]
    if a.start is None:
        a.start = seg["in"] - seg["handles"]
    if a.count is None:
        a.count = (seg["out"] - seg["in"] + 1) + 2 * seg["handles"]

    W, H = CW // a.div, CH // a.div
    if not a.full_speed:
        be_polite(a.threads)
    ff = ffmpeg()
    try:
        import moderngl
    except ImportError:
        sys.exit("moderngl is not installed:   python -m pip install moderngl")

    print()
    print("render    %d x %d  (1/%d)   frames %d..%d  (%d, %.1f s)"
          % (W, H, a.div, a.start, a.start + a.count - 1, a.count, a.count / float(FPS)))

    ctx = moderngl.create_standalone_context(require=330)
    print("gpu       %s" % ctx.info["GL_RENDERER"])
    print("load      %s, ffmpeg threads %d"
          % ("NORMAL priority" if a.full_speed else "below-normal priority", a.threads))

    quad_vs = """#version 330
    in vec2 aP; out vec2 vUv;
    void main(){ vUv = aP*0.5+0.5; gl_Position = vec4(aP,0,1); }"""

    bg_prog = ctx.program(vertex_shader=quad_vs, fragment_shader=load_shader("sky_oil.frag"))
    pil_prog = ctx.program(vertex_shader=quad_vs, fragment_shader=load_shader("pillars.frag"))
    obj_prog = ctx.program(vertex_shader=load_shader("chrome_object.vert"),
                           fragment_shader=load_shader("chrome_object.frag"))
    comp_prog = ctx.program(vertex_shader=quad_vs, fragment_shader="""#version 330
    in vec2 vUv; out vec4 fragColor;
    uniform sampler2D uBg, uOutside, uInside, uPillars, uMasks, uAux;
    void main(){
        vec3 col = texture(uBg, vUv).rgb;

        // Objects beyond the wall are visible ONLY through an opening. This one
        // multiply is what makes them read as arriving from outside rather than
        // fading up on the wall surface.
        vec4 out_ = texture(uOutside, vUv);
        float slot = texture(uAux, vUv).a;
        out_ *= slot;
        col = col * (1.0 - out_.a) + out_.rgb;

        // then loose in the room, in front of the wall
        vec4 in_ = texture(uInside, vUv);
        col = col * (1.0 - in_.a) + in_.rgb;

        // and the pillars stand in front of all of it
        vec4 pil = texture(uPillars, vUv);
        col = col * (1.0 - pil.a) + pil.rgb;

        col *= texture(uMasks, vUv).a;        // projectable
        fragColor = vec4(col, 1.0);
    }""")

    quad = ctx.buffer(np.array([-1, -1, 3, -1, -1, 3], "f4").tobytes())
    bg_vao = ctx.simple_vertex_array(bg_prog, quad, "aP")
    pil_vao = ctx.simple_vertex_array(pil_prog, quad, "aP")
    comp_vao = ctx.simple_vertex_array(comp_prog, quad, "aP")

    # ---- static textures --------------------------------------------------
    print("textures  masks + opening id + sdf ...")
    mask = np.zeros((H, W, 4), np.uint8)
    for ch, name in ((0, "01_WALL"), (1, "02_WINDOW"), (2, "04_DOOR"), (3, "08_PROJECTABLE")):
        mask[:, :, ch] = load_gray(
            ff, os.path.join(MASK_ROOT, "PxDL_SW_MASK_%s_%dx%d.png" % (name, CW, CH)), W, H)
    t_mask = ctx.texture((W, H), 4, mask.tobytes())
    t_mask.filter = (moderngl.LINEAR, moderngl.LINEAR)

    aux = np.zeros((H, W, 4), np.uint8)
    aux[:, :, 0] = load_gray(
        ff, os.path.join(REF_ROOT, "PxDL_SW_OPENING_SDF_%dx%d.png" % (CW, CH)), W, H)
    for ch, name in ((1, "05_COLUMN"), (2, "06_TRIM"), (3, "09_OPENINGS")):
        aux[:, :, ch] = load_gray(
            ff, os.path.join(MASK_ROOT, "PxDL_SW_MASK_%s_%dx%d.png" % (name, CW, CH)), W, H)
    t_aux = ctx.texture((W, H), 4, aux.tobytes())
    t_aux.filter = (moderngl.LINEAR, moderngl.LINEAR)

    oid = load_rgb(ff, os.path.join(REF_ROOT, "PxDL_SW_OPENING_ID_%dx%d.png" % (CW, CH)), W, H)
    t_oid = ctx.texture((W, H), 3, oid.tobytes())
    t_oid.filter = (moderngl.NEAREST, moderngl.NEAREST)       # it is an index, never filter it

    t_plate = ctx.texture((W, H), 3, dtype="f1")
    t_plate.filter = (moderngl.LINEAR, moderngl.LINEAR)

    # ---- framebuffers -----------------------------------------------------
    def rgba_fbo():
        t = ctx.texture((W, H), 4, dtype="f1")
        t.filter = (moderngl.LINEAR, moderngl.LINEAR)
        return ctx.framebuffer(color_attachments=[t]), t
    fbo_bg, tex_bg = rgba_fbo()
    fbo_pil, tex_pil = rgba_fbo()
    fbo_out, tex_out = rgba_fbo()
    fbo_in, tex_in = rgba_fbo()

    # Chrome lives or dies on its silhouette, so the object pass is multisampled
    # and resolved down. Without this every curved edge staircases and the
    # "perfect render" look goes with it.
    samples = 0
    if a.msaa > 1:
        want = min(a.msaa, int(ctx.info.get("GL_MAX_SAMPLES", 4)))
        for s in (want, 8, 4, 2):
            try:
                ms_c = ctx.renderbuffer((W, H), components=4, samples=s)
                ms_d = ctx.depth_renderbuffer((W, H), samples=s)
                fbo_ms = ctx.framebuffer(color_attachments=[ms_c], depth_attachment=ms_d)
                samples = s
                break
            except Exception:
                continue
    if not samples:
        ms_d = ctx.depth_renderbuffer((W, H))
        fbo_ms = None
    print("msaa      %s" % ("%dx" % samples if samples else "off"))

    fbo_out_d = ctx.framebuffer(color_attachments=[tex_out], depth_attachment=ms_d) \
        if not samples else None
    fbo_in_d = ctx.framebuffer(color_attachments=[tex_in], depth_attachment=ms_d) \
        if not samples else None
    fbo_final = ctx.simple_framebuffer((W, H), components=3)

    # ---- geometry ---------------------------------------------------------
    vaos = {}
    for name, fn in SHAPES.items():
        data = fn()
        vbo = ctx.buffer(data.tobytes())
        vaos[name] = ctx.vertex_array(obj_prog, [(vbo, "3f 3f 2x4", "aPos", "aNrm")])

    openings = json.load(open(os.path.join(REF_ROOT, "openings.json")))["openings"]
    doors = [o for o in openings if o["kind"] == "DOOR"]
    target = pick_target_door(doors)
    seg_lo = (seg["in"] - seg["handles"] - seg["in"]) / float(FPS)
    seg_hi = (seg["out"] + seg["handles"] - seg["in"]) / float(FPS)
    objs = [] if a.no_objects else build_objects(
        openings, a.seed, a.objects, seg_lo, seg_hi, a.obj_scale, a.drift)
    print("objects   %d  ->  door %d at x %d (%dx%d)"
          % (len(objs), target["index"], target["cx"], target["w"], target["h"]))

    regions = json.load(open(os.path.join(REF_ROOT, "facade_regions.json")))["regions"]
    cols = regions.get("COLUMN", [])
    col0 = cols[0] if len(cols) > 0 else {"x": 0, "y": 0, "w": 1, "h": 1}
    col1 = cols[1] if len(cols) > 1 else col0
    print("pillars   %d columns" % len(cols))

    # ---- the plate's own arc ----------------------------------------------
    arc = {}
    arc_path = os.path.join(REF_ROOT, "noise_arc.csv")
    if os.path.isfile(arc_path):
        with open(arc_path) as fh:
            for row in csv.DictReader(fh):
                arc[int(row["frame"])] = (float(row["mean_norm"]), float(row["mean"]))

    # ---- noise stream -----------------------------------------------------
    v1 = source("SPSW1") if not a.hq else None
    v2 = source("SPSW2") if not a.hq else None
    if a.hq:
        hq = CFG.get("source_hq", {})
        if not hq.get("SPSW1") or not hq.get("SPSW2"):
            sys.exit("--hq needs source_hq.SPSW1 / SPSW2 filled in in project.json")
        v1, v2 = hq["SPSW1"], hq["SPSW2"]
    ss = "%.6f" % (a.start / float(FPS))
    p1w, p2w = CFG["plates"][0]["w"], CFG["plates"][1]["w"]
    cut = p1w - CFG["overlap"]["w"]
    # SCALE FIRST, then crop and stack. Cropping and stacking at full size builds
    # a 10788 x 2552 frame for every single frame and then throws 76% of it
    # away - which is most of the CPU cost of a quarter-scale preview, and the
    # reason a long preview could make the machine unusable.
    #
    # Safe because every divisor lands on whole pixels: 7200/4=1800, 6200/4=1550,
    # 3588/4=897, and 1550+897 = 2447 = the quarter canvas exactly.
    for nm, val in (("plate 1", p1w), ("the cut", cut), ("plate 2", p2w)):
        if val % a.div:
            sys.exit("--div %d does not divide %s (%d) evenly" % (a.div, nm, val))
    fc = ("[0:v]scale=%d:%d:flags=area,crop=%d:%d:0:0[l];"
          "[1:v]scale=%d:%d:flags=area[r];[l][r]hstack=2"
          % (p1w // a.div, H, cut // a.div, H, p2w // a.div, H))
    noise = popen_polite(
        [ff, "-hide_banner", "-loglevel", "error", "-threads", str(a.threads),
         "-ss", ss, "-i", v1, "-ss", ss, "-i", v2,
         "-filter_complex", fc, "-frames:v", str(a.count),
         "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"],
        stdout=subprocess.PIPE, bufsize=W * H * 3 * 2)

    # ---- output sink ------------------------------------------------------
    if a.png:
        outdir = a.out or os.path.join(RENDER_ROOT, "master_%dx%d" % (W, H))
        os.makedirs(outdir, exist_ok=True)
        dst = os.path.join(outdir, "PxDL_SW_master.%05d.png")
        sink_args = ["-start_number", str(a.start), dst]
        print("out       %s" % outdir)
    else:
        dst = a.out or os.path.join(WORK_ROOT, "preview",
                                    "render_%dx%d_%d.mp4" % (W, H, a.start))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        has264 = b"libx264" in subprocess.run([ff, "-hide_banner", "-encoders"],
                                              capture_output=True).stdout
        sink_args = []
        if a.preview_width and a.preview_width < W:
            pw = int(a.preview_width) // 2 * 2
            ph = int(round(a.preview_width * H / W / 2)) * 2
        else:
            # yuv420p subsamples chroma 2x2, so it CANNOT take an odd dimension,
            # and the canvas divided by 4 is 2447 x 638 - odd. Without this the
            # encoder exits on the first frame and the render dies on a broken
            # pipe several seconds later, pointing at the wrong line entirely.
            pw, ph = W // 2 * 2, H // 2 * 2
        if (pw, ph) != (W, H):
            sink_args += ["-vf", "scale=%d:%d:flags=area" % (pw, ph)]
            if (pw, ph) != (W, H) and not a.preview_width:
                print("preview   %dx%d -> %dx%d (even dimensions for yuv420p)"
                      % (W, H, pw, ph))
        sink_args += (["-c:v", "libx264", "-crf", "18", "-preset", "veryfast",
                       "-pix_fmt", "yuv420p"] if has264 else ["-c:v", "mpeg4", "-q:v", "3"])
        sink_args += [dst]
        print("out       %s" % dst)
    sink = popen_polite(
        [ff, "-hide_banner", "-loglevel", "error", "-y", "-threads", str(a.threads),
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "%dx%d" % (W, H),
         "-framerate", str(FPS), "-i", "pipe:0", "-r", str(FPS)] + sink_args,
        stdin=subprocess.PIPE)

    col = np.array([float(x) for x in a.color.split(",")], "f4")
    stone = np.array([float(x) for x in a.stone.split(",")], "f4")
    film = [float(x) for x in a.film.split(",")]
    nbytes = W * H * 3
    t_start = time.time()
    print()

    def setu(prog, name, val):
        if name in prog:
            prog[name].value = val

    # Canvas y grows DOWNWARD (y=0 is the top of the wall), and framebuffer row 0
    # holds the top row of the image - because the fullscreen quad maps vUv.y=0
    # to NDC -1, and every texture is uploaded top row first. So canvas y=0 must
    # map to NDC -1, which is bottom=0, top=H.
    #
    # The obvious ortho(0, W, H, 0, ...) is the top-left-origin convention and is
    # exactly wrong here: it mirrors every object vertically about the middle of
    # the wall while leaving the background correct. The objects then travel
    # doors-to-windows on screen no matter what the paths say, which looks
    # plausible enough to miss.
    proj = ortho(0, W, 0, H, -4000, 4000)

    for k in range(a.count):
        buf = noise.stdout.read(nbytes)
        if len(buf) < nbytes:
            print("\nnoise stream ended early at frame %d" % k)
            break
        t_plate.write(buf)

        frame = a.start + k
        # ABSOLUTE segment time, not chunk time. If this were k/FPS the clouds
        # and the camera would restart at every chunk boundary and a chunked
        # final render would visibly jump.
        t = (frame - seg["in"]) / float(FPS)
        mean_norm, _ = arc.get(frame, (0.5, 0.5))
        camx = camera_x(t, a.parallax, a.parallax_period)

        # ---- background ---------------------------------------------------
        t_plate.use(0); t_mask.use(1); t_oid.use(2); t_aux.use(3)
        for nm, val in (("uPlate", 0), ("uMasks", 1), ("uOpenId", 2), ("uAux", 3)):
            setu(bg_prog, nm, val)
        setu(bg_prog, "uRes", (float(W), float(H)))
        setu(bg_prog, "uTime", t)
        setu(bg_prog, "uArc", mean_norm)
        setu(bg_prog, "uCamX", camx)
        setu(bg_prog, "uCamAmp", a.parallax)
        setu(bg_prog, "uSkyToOil", smooth(t, 2.0, 20.0))
        setu(bg_prog, "uCloud", a.cloud)
        setu(bg_prog, "uCloudSpeed", 0.15)
        setu(bg_prog, "uHorizon", a.horizon)
        setu(bg_prog, "uBands", a.bands)
        setu(bg_prog, "uPlateMix", a.plate_mix)
        setu(bg_prog, "uSpread", a.spread)
        setu(bg_prog, "uSkyGain", a.sky_gain)
        setu(bg_prog, "uOilGain", a.oil_gain)
        setu(bg_prog, "uColor", tuple(col))
        setu(bg_prog, "uLevels", a.levels)
        # uGrid is the dither cell in RENDER pixels, so it must NOT track --div.
        # At --div 4 one render pixel is already 4 canvas pixels; passing div
        # made the cell 4x coarser again, a 64-canvas-pixel checkerboard that is
        # not in the full-res render at all. That breaks the one rule the 48h
        # plan rests on, that nothing you learn at quarter scale is wrong.
        setu(bg_prog, "uGrid", a.dither_grid)
        setu(bg_prog, "uReveal", a.reveal)
        setu(bg_prog, "uOilSweep", a.oil_sweep)
        setu(bg_prog, "uFilmMin", film[0])
        setu(bg_prog, "uFilmMax", film[1])
        setu(bg_prog, "uParSky", a.par_sky)
        setu(bg_prog, "uParIn", a.par_in)
        fbo_bg.use(); ctx.disable(moderngl.DEPTH_TEST | moderngl.BLEND)
        fbo_bg.clear(0, 0, 0, 1); bg_vao.render()

        # ---- objects: outside the wall, then inside the room ---------------
        targets = ((fbo_out, tex_out, True), (fbo_in, tex_in, False))
        for fbo_resolve, _tex, want_outside in targets:
            draw = fbo_ms if samples else (fbo_out_d if want_outside else fbo_in_d)
            draw.use()
            draw.clear(0.0, 0.0, 0.0, 0.0)
            if objs:
                ctx.enable(moderngl.DEPTH_TEST)
                ctx.enable(moderngl.BLEND)
                ctx.blend_func = (moderngl.ONE, moderngl.ONE_MINUS_SRC_ALPHA)
                t_plate.use(0)
                setu(obj_prog, "uPlate", 0)
                setu(obj_prog, "uRes", (float(W), float(H)))
                setu(obj_prog, "uTime", t)
                setu(obj_prog, "uBands", a.bands)
                setu(obj_prog, "uCloud", a.cloud)
                setu(obj_prog, "uCloudSpeed", 0.15)
                setu(obj_prog, "uColor", tuple(col))
                setu(obj_prog, "uSkyGain", a.sky_gain)
                setu(obj_prog, "uGloss", a.gloss)
                setu(obj_prog, "uSpecGain", a.spec_gain)
                setu(obj_prog, "uRoomMix", a.room_mix * (0.35 + 0.9 * mean_norm))
                setu(obj_prog, "uExposure", a.exposure)
                setu(obj_prog, "uViewFov", a.view_fov)
                setu(obj_prog, "uHorizonHot", a.horizon_hot)
                setu(obj_prog, "uWindows", a.windows)
                setu(obj_prog, "uLightDir", (0.35, -0.8, 0.5))
                setu(obj_prog, "uLightCol", (1.0, 0.97, 0.92))
                for o in objs:
                    st = object_state(o, t)
                    if st is None:
                        continue
                    pos, scale, alpha, w_out, depth = st
                    w = w_out if want_outside else (1.0 - w_out)
                    aa = alpha * w
                    if aa <= 0.004:
                        continue
                    pos = pos.copy()
                    # objects sit in the room, so they move WITH the camera,
                    # opposite to the sky behind them
                    pos[0] = (pos[0] + camx * a.par_obj * depth) / a.div
                    pos[1] /= a.div
                    sc = scale / a.div
                    rw, rp = o["rot_w"], o["rot_p"]
                    rot = (o["spin"][0] * t + 0.55 * math.sin(rw[0] * t + rp[0]),
                           o["spin"][1] * t + 0.65 * math.sin(rw[1] * t + rp[1]),
                           o["spin"][2] * t + 0.40 * math.sin(rw[2] * t + rp[2]))
                    m = model_matrix(pos, sc, rot)
                    setu(obj_prog, "uMVP", tuple((proj @ m).T.flatten()))
                    setu(obj_prog, "uModel", tuple(m.T.flatten()))
                    setu(obj_prog, "uNormalMat", tuple(normal_matrix(m).T.flatten()))
                    setu(obj_prog, "uTint", tuple(o["tint"]))
                    setu(obj_prog, "uAlpha", float(aa))
                    vaos[o["shape"]].render()
            if samples:
                ctx.copy_framebuffer(fbo_resolve, fbo_ms)     # MSAA resolve

        # ---- pillars ------------------------------------------------------
        ctx.disable(moderngl.DEPTH_TEST | moderngl.BLEND)
        fbo_pil.use(); fbo_pil.clear(0.0, 0.0, 0.0, 0.0)
        if not a.no_pillars:
            t_aux.use(0); t_plate.use(1); t_mask.use(2)
            for nm, val in (("uAux", 0), ("uPlate", 1), ("uMasks", 2)):
                setu(pil_prog, nm, val)
            setu(pil_prog, "uRes", (float(W), float(H)))
            setu(pil_prog, "uTime", t)
            setu(pil_prog, "uArc", mean_norm)
            setu(pil_prog, "uCamX", camx)
            setu(pil_prog, "uCamAmp", a.parallax)
            setu(pil_prog, "uShift", a.pillar_shift)
            setu(pil_prog, "uWobble", a.pillar_wobble)
            setu(pil_prog, "uBands", a.bands)
            setu(pil_prog, "uCloud", a.cloud)
            setu(pil_prog, "uCloudSpeed", 0.15)
            setu(pil_prog, "uColor", tuple(col))
            setu(pil_prog, "uStone", tuple(stone))
            setu(pil_prog, "uSkyGain", a.sky_gain)
            setu(pil_prog, "uGain", a.pillar_gain)
            setu(pil_prog, "uLevels", a.levels)
            setu(pil_prog, "uGrid", a.dither_grid)
            setu(pil_prog, "uCol0", (float(col0["x"]), float(col0["y"]),
                                     float(col0["w"]), float(col0["h"])))
            setu(pil_prog, "uCol1", (float(col1["x"]), float(col1["y"]),
                                     float(col1["w"]), float(col1["h"])))
            setu(pil_prog, "uCanvasW", float(CW))
            pil_vao.render()

        # ---- composite ----------------------------------------------------
        ctx.disable(moderngl.DEPTH_TEST | moderngl.BLEND)
        tex_bg.use(0); tex_out.use(1); tex_in.use(2); tex_pil.use(3)
        t_mask.use(4); t_aux.use(5)
        for nm, val in (("uBg", 0), ("uOutside", 1), ("uInside", 2),
                        ("uPillars", 3), ("uMasks", 4), ("uAux", 5)):
            setu(comp_prog, nm, val)
        fbo_final.use(); fbo_final.clear(0, 0, 0, 1); comp_vao.render()

        sink.stdin.write(fbo_final.read(components=3))

        if k % 30 == 0 or k == a.count - 1:
            el = time.time() - t_start
            fps = (k + 1) / max(el, 1e-6)
            eta = (a.count - k - 1) / max(fps, 1e-6)
            sys.stdout.write("\r  frame %5d / %d   %.1f fps   eta %4.0f s   " %
                             (k + 1, a.count, fps, eta))
            sys.stdout.flush()

    sink.stdin.close(); sink.wait()
    try:
        noise.stdout.close(); noise.wait(timeout=5)
    except Exception:
        noise.kill()
    el = time.time() - t_start
    print("\n\ndone in %.1f s  (%.1f fps)" % (el, a.count / max(el, 1e-6)))
    print(dst if not a.png else os.path.dirname(dst))


if __name__ == "__main__":
    main()

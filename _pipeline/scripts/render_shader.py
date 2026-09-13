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
import collections
import csv
import json
import math
import os
import re
import subprocess
import struct
import zlib
import sys
import threading
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import (CFG, MASK_ROOT, PIPELINE, REF_ROOT, RENDER_ROOT, WORK_ROOT,
                     MASK_VARIANTS, encodable_size, ffmpeg, mask_dirs,
                     mask_variant, ref_file, source)  # noqa: E402

SHADERS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "shaders")

# Delivery codecs for --plates. yuv422p10le and yuv444p10le both subsample or
# align on even widths, which is why --plates refuses --div 4: 3588/4 = 897 and
# an odd-width plate cannot be encoded in any of these.
CODECS = {
    "prores4444":  dict(ext="mov", args=["-c:v", "prores_ks", "-profile:v", "4",
                                         "-vendor", "apl0",
                                         "-pix_fmt", "yuva444p10le"]),
    "prores422hq": dict(ext="mov", args=["-c:v", "prores_ks", "-profile:v", "3",
                                         "-vendor", "apl0", "-pix_fmt", "yuv422p10le"]),
    "dnxhr_hqx":   dict(ext="mov", args=["-c:v", "dnxhd", "-profile:v", "dnxhr_hqx",
                                         "-pix_fmt", "yuv422p10le"]),
    "h264":        dict(ext="mp4", args=["-c:v", "libx264", "-crf", "16",
                                         "-preset", "medium", "-pix_fmt", "yuv420p"]),
}
CW, CH = CFG["canvas"]["w"], CFG["canvas"]["h"]
FPS = CFG["fps"]


# --------------------------------------------------------------------------- shader loading

class Tee(object):
    """stdout that also lands in a file.

    The progress bar rewrites ONE line with a carriage return, which is what
    makes it look like a bar filling rather than four thousand lines scrolling
    past. That only works while stdout is the console itself - put a pipe in
    front of it and every update becomes its own line, in the terminal and in
    the log. So the render is no longer piped anywhere: it writes its own log
    through this, and anything that starts with a carriage return is treated as
    a bar update and kept out of the file.
    """

    def __init__(self, base, fh):
        self._b = base
        self._f = fh

    def write(self, s):
        self._b.write(s)
        if not s.startswith("\r"):
            self._f.write(s)
            self._f.flush()
        return len(s)

    def note(self, s):
        """Into the log only - the console already has the live bar."""
        self._f.write(s + "\n")
        self._f.flush()

    def flush(self):
        self._b.flush()

    def isatty(self):
        return self._b.isatty()


LOG_FH = None


def open_log(path):
    """Point stdout and stderr at `path` as well as at the console."""
    global LOG_FH
    if not path:
        return None
    d = os.path.dirname(os.path.abspath(path))
    if d:
        os.makedirs(d, exist_ok=True)
    fh = open(path, "a", encoding="utf-8")
    fh.write("\n%s\n%s\n" % ("-" * 70, " ".join(sys.argv)))
    sys.stdout = Tee(sys.stdout, fh)
    sys.stderr = Tee(sys.stderr, fh)
    LOG_FH = fh
    return fh


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
    """Popen, but the child process is below normal priority too.

    Its stderr goes into the log when there is one. ffmpeg inherits a real OS
    handle, not our Tee, so without this an encoder's complaint would reach the
    console and never the file.
    """
    flags = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
    if flags:
        kw["creationflags"] = kw.get("creationflags", 0) | flags
    if LOG_FH is not None and "stderr" not in kw:
        kw["stderr"] = LOG_FH
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


def knot(p=2, q=3, major=0.78, minor=0.34, tube=0.24, seg=380, ring=14):
    """A (2,3) torus knot - a trefoil. Round, closed, and all curvature, which
    is what a mirror finish needs.

    The tube frame is taken from the centre circle of the containing torus
    rather than from a Frenet frame. A Frenet frame flips at inflection points
    and does not generally close up after one loop, leaving a visible twist
    seam; this one is periodic in u by construction, so the tube closes exactly.
    """
    def curve(u):
        rr = major + minor * math.cos(q * u)
        return np.array([rr * math.cos(p * u), minor * math.sin(q * u),
                         rr * math.sin(p * u)])

    def centre(u):
        return np.array([major * math.cos(p * u), 0.0, major * math.sin(p * u)])

    v, n = [], []
    for i in range(seg + 1):
        u = 2.0 * math.pi * i / seg
        P = curve(u)
        T = curve(u + 1e-4) - curve(u - 1e-4)
        T = T / max(float(np.linalg.norm(T)), 1e-9)
        N = P - centre(u)
        N = N - T * float(np.dot(N, T))
        ln = float(np.linalg.norm(N))
        N = N / ln if ln > 1e-9 else np.array([0.0, 1.0, 0.0])
        B = np.cross(T, N)
        for j in range(ring + 1):
            ang = 2.0 * math.pi * j / ring
            nrm = math.cos(ang) * N + math.sin(ang) * B
            v.append(tuple(P + tube * nrm))
            n.append(tuple(nrm))
    t = []
    row = ring + 1
    for i in range(seg):
        for j in range(ring):
            aa, bb = i * row + j, i * row + j + 1
            cc, dd = (i + 1) * row + j, (i + 1) * row + j + 1
            t += [(aa, cc, dd), (aa, dd, bb)]
    return _tris(v, t, n)


def moebius(major=1.0, wide=0.33, thick=0.12, seg=300, ring=20):
    """A Moebius TORUS: a solid ring with a half twist, not a paper band.

    The cross-section is a flattened ellipse that rotates through 180 degrees
    over one loop. A round cross-section would make the twist invisible - it is
    the flattening that shows it - and giving it real volume is what separates
    this from a ribbon: a closed surface with an inside and an outside, so the
    chrome has something to be a solid of.

    Closed exactly, because at u = 2*pi the frame has turned by pi, so the ring
    of vertices there coincides with the ring at u = 0 offset by half a turn of
    v. Generating u inclusive means those duplicates sit on top of each other
    and the tube closes with nothing to see.
    """
    def surf(u, vv):
        cu, su = math.cos(u), math.sin(u)
        ch, sh = math.cos(u * 0.5), math.sin(u * 0.5)
        cv, sv = math.cos(vv), math.sin(vv)
        # the ellipse, rotated by half the loop angle
        er = wide * cv * ch - thick * sv * sh      # along the radial direction
        eu = wide * cv * sh + thick * sv * ch      # along the ring axis
        rad = major + er
        return np.array([rad * cu, eu, rad * su])

    h = 1e-4
    v, n = [], []
    for i in range(seg + 1):
        u = 2.0 * math.pi * i / seg
        for j in range(ring + 1):
            vv = 2.0 * math.pi * j / ring
            P = surf(u, vv)
            du = surf(u + h, vv) - surf(u - h, vv)
            dv = surf(u, vv + h) - surf(u, vv - h)
            nn = np.cross(du, dv)
            ln = float(np.linalg.norm(nn))
            nn = nn / ln if ln > 1e-12 else np.array([0.0, 1.0, 0.0])
            v.append(tuple(P))
            n.append(tuple(nn))
    t = []
    row = ring + 1
    for i in range(seg):
        for j in range(ring):
            a, b = i * row + j, i * row + j + 1
            c, d = (i + 1) * row + j, (i + 1) * row + j + 1
            t += [(a, c, d), (a, d, b)]
    return _tris(v, t, n)


# Round shapes only. cube / pyramid / diamond are still defined above and still
# work if you put them back in the mix, but flat facets cannot carry a mirror
# finish: a flat face reflects one direction, so it renders as one flat colour.
SHAPES = {"sphere": sphere, "torus": torus, "moebius": moebius, "knot": knot,
          "cube": cube, "pyramid": pyramid, "diamond": diamond}

# Bounding radius in model units, for keeping objects out of each other.
SHAPE_RADIUS = {"sphere": 1.0, "torus": 1.38, "moebius": 1.36, "knot": 1.40,
                "cube": 1.74, "pyramid": 1.70, "diamond": 1.50}


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


def hms(sec):
    """Seconds as something a person can read at a glance."""
    sec = int(max(sec, 0))
    if sec >= 3600:
        return "%dh %02dm" % (sec // 3600, (sec % 3600) // 60)
    if sec >= 60:
        return "%dm %02ds" % (sec // 60, sec % 60)
    return "%ds" % sec


def smooth(x, a, b):
    if b <= a:
        return 1.0 if x >= b else 0.0
    t = min(max((x - a) / (b - a), 0.0), 1.0)
    return t * t * (3 - 2 * t)


def smoother(x, a, b):
    """Quintic ease. Zero first AND second derivative at both ends.

    smoothstep only zeroes the first derivative, so where one eased stage hands
    over to the next the ACCELERATION jumps even though the position and
    velocity are continuous. The eye reads that as the motion changing gear -
    the object visibly moving between states rather than simply moving. The
    quintic removes it, which is why it is the standard for keyframe
    interpolation, and it is what every stage boundary in object_state uses.
    """
    if b <= a:
        return 1.0 if x >= b else 0.0
    t = min(max((x - a) / (b - a), 0.0), 1.0)
    return t * t * t * (t * (t * 6.0 - 15.0) + 10.0)


# --------------------------------------------------------------------------- camera

def camera_x(t, amp, period):
    """Slow lateral drift. Two incommensurate periods so it never sits still and
    never repeats on a beat the eye can latch onto."""
    a = math.sin(2 * math.pi * t / period)
    b = math.sin(2 * math.pi * t / (period * 0.371) + 1.7)
    return amp * (0.86 * a + 0.14 * b)


# --------------------------------------------------------------------------- scene

# The room, in canvas pixels. The wall is the plane z = 0.
#
# Deep on purpose. The camera is orthographic, so depth costs nothing on screen
# - z only drives the depth buffer and the wall test - but a shallow room with
# objects this big means every object's own radius is comparable to its distance
# from the wall. Two consequences, both measured: an object at its near point
# still had part of itself behind the wall plane and got clipped against the
# openings mid-room, and everything piled into the same slab of depth so the
# separation solver had to shove things hundreds of pixels sideways. Making the
# room three times deeper costs a single float and removes both.
Z_BACK = 1800.0     # how far beyond the wall an object starts and ends
Z_NEAR = 2600.0     # nearest an object ever comes
Z_NEAR_MIN = 700.0  # ... and the shyest one. Every object used to peak at the
                    # same depth, so they all reached the pillars at their
                    # closest point and every single one passed in front. Giving
                    # each its own near point is what puts some of them behind.
Z_LANE = 700.0      # spread of the per-object depth lane
Z_CLIP = 8000.0     # ortho near/far. Wide enough that the separation pushes
                    # below cannot send anything through the far plane.
FAR_FRAC = 0.82     # Size while it is still outside, as a fraction of opening
                    # size. Nearly opening-sized on purpose: at 0.20 the object
                    # began as a distant speck and swelled toward the viewer
                    # head-on, which is the "coming from the front" look. It now
                    # arrives already the size of the hole and travels SIDEWAYS
                    # into it.


def pick_target_door(doors):
    """The big door in the middle. Nearest the canvas centre; it is also the
    largest of the three (757x754 against 556 and 589 wide), so both readings
    agree and there is nothing to guess."""
    return min(doors, key=lambda d: abs(d["cx"] - CW * 0.5))


def build_objects(openings, seed, count, t_lo, t_hi, obj_scale=1.0, drift=1.0,
                  speed=1.0, fit_margin=0.62, door_pad=1.0):
    """Every object: in through a window, across the room, out through the big
    middle door. Out of the room is out of the piece - they do not come back."""
    rng = np.random.default_rng(seed)
    # One of the 27 "windows" is a 51 px slice at x 823, clipped in half by the
    # left black block. It is a real opening but far too narrow to deliver
    # anything through, and sizing an object from it gives a 50 px speck.
    wins = [o for o in openings
            if o["kind"] == "WINDOW" and o["w"] >= 150 and o["h"] >= 200]
    doors = [o for o in openings if o["kind"] == "DOOR"]
    if not wins or not doors:
        return []
    # All three doors now, not just the middle one - but weighted towards it,
    # because it is the biggest and it is the one the piece reads as the exit.
    # Sorted by x so the weighting below is positional, not file order.
    doors = sorted(doors, key=lambda d: d["cx"])
    mid = min(range(len(doors)), key=lambda k: abs(doors[k]["cx"] - CW * 0.5))
    door_bag = []
    for k in range(len(doors)):
        door_bag += [k] * (4 if k == mid else 1)

    # Spread the entry points across the full width. Twelve independent random
    # picks out of twenty-three windows clumps badly - measured, it put seven of
    # the twelve in the right third and one in the left, mean entry x 6814 on a
    # canvas whose centre is 4894, and used three windows twice while most were
    # never used at all. Stratifying by x guarantees the whole wall is used;
    # shuffling which stratum each object gets stops the entries sweeping left
    # to right in step with time.
    wins = sorted(wins, key=lambda o: o["cx"])
    slots = list(range(count))
    rng.shuffle(slots)

    # Weighted, not round-robin, and no flat-faced solids: curvature is what
    # carries a mirror finish.
    names = ["sphere", "torus", "moebius", "knot", "torus", "sphere",
             "knot", "moebius", "torus", "sphere"]
    objs = []
    for i in range(count):
        wi = int((slots[i] + 0.5) / float(max(count, 1)) * len(wins))
        w = wins[min(wi, len(wins) - 1)]
        _door_pref = door_bag[int(rng.integers(len(door_bag)))]
        door = doors[_door_pref]
        dur = float(rng.uniform(19.0, 31.0)) / max(speed, 1e-3)
        # t0 is in SEGMENT time and must span the whole segment, not the chunk
        # being rendered. Spreading it over the chunk instead means a preview of
        # any sub-range lands entirely outside every object's lifetime and you
        # render an empty wall wondering what broke.
        #
        # Stratified, not uniform random: one entry per slot with jitter inside
        # it. Twelve independent draws over two minutes clump badly - measured,
        # it gave stretches of 30+ seconds with nothing on the wall and bursts of
        # seven at once, which is both the empty feeling and the crowding.
        # Bias entries later: the plate is near-black until about 3:10, so the
        # segment should start almost empty and fill up.
        # t_hi is the moment by which everything must be FINISHED, not the
        # last moment something may start. Treating it as a start deadline let
        # an object begin at 3:58 and still be mid-flight at 4:00, which is the
        # opposite of a clean hand-off to the next wall.
        u = (i + float(rng.uniform(0.12, 0.88))) / float(max(count, 1))
        u = u ** 0.85
        # start_lo was t_lo - dur*0.6, which let the first object be well into
        # its flight at the very first frame - so it did not come through a
        # window, it simply materialised in mid-air. Nothing may begin before
        # the segment does; every object now starts at p = 0, which is tiny and
        # out beyond its own window.
        start_lo = max(t_lo, 0.0)
        start_hi = t_hi - dur
        t0 = start_lo + u * max(start_hi - start_lo, 0.0)

        # One in four is a hero. Uniform sizing reads as wallpaper however big
        # you make it; the hierarchy is what makes any of them feel like events.
        hero = (i % 3 == 0)
        base = float(rng.uniform(0.95, 1.30)) * (1.70 if hero else 1.0)

        # How close this one ever comes. Size follows it: something that stays
        # at the back of the room and is still drawn full size does not read as
        # far away, it reads as wrong.
        near_f = float(rng.random())

        # size first, because the near depth has to clear the object's radius
        _size = (max(230.0, min(780.0, float(w["w"]) * base))
                 * obj_scale * (0.62 + 0.38 * near_f))

        # Depth follows SIZE, not an independent roll. Rolling them separately
        # gave objects passing in front of the pillars a mean radius of 509
        # against 465 behind - statistically different, visually identical, so
        # it never read as "the big ones come past in front". Tying them makes
        # the biggest objects reliably the nearest, and the nearest are the ones
        # the depth test lets through in front of a pillar.
        _sn = min(max((_size - 140.0) / 540.0, 0.0), 1.0)
        z_near = Z_NEAR_MIN + _sn * (Z_NEAR - Z_NEAR_MIN)

        wx, wy = float(w["cx"]), float(w["cy"])
        dx, dy = float(door["cx"]), float(door["cy"])
        side = 1.0 if wx < dx else -1.0
        # leave the window outward into the room, then swing down into the door
        # Kept as raw magnitudes, not baked into the points. The door can be
        # reassigned after the fact to stop two objects using it at once, and
        # that has to be doable WITHOUT drawing any more random numbers - one
        # extra draw here would reshuffle every object downstream of it.
        _c1a = float(rng.uniform(400, 1400))
        _c1b = float(rng.uniform(300, 900))
        _c2a = float(rng.uniform(700, 2100))
        _c2b = float(rng.uniform(800, 1500))
        c1 = (wx + side * _c1a, wy + _c1b)
        c2 = (dx - side * _c2a, dy - _c2b)

        def wander():
            """Three incommensurate sines. Their sum never repeats inside the
            segment, which is the difference between drifting and orbiting."""
            return [(float(rng.uniform(0.030, 0.135)),     # Hz
                     float(rng.uniform(0, 6.283)),         # phase
                     float(rng.uniform(0.30, 1.0)))        # weight
                    for _ in range(3)]

        def wsum(w):
            return sum(g for _, _, g in w) or 1.0

        def _sp(mn, mx):
            """A rate with a guaranteed minimum, and a random direction."""
            return float(rng.uniform(mn, mx)) * (1.0 if rng.random() < 0.5 else -1.0)

        _wx, _wy = wander(), wander()
        # Which way it flies in along the wall, and which way it leaves. The
        # departure is the opposite hand, so it crosses rather than doubles back.
        _side = 1.0 if rng.random() < 0.5 else -1.0
        _app_dir = (_side, float(rng.uniform(-0.30, 0.30)))
        _dep_dir = (-_side, float(rng.uniform(-0.30, 0.30)))
        _app_dist = float(rng.uniform(0.60, 1.10)) * float(w["w"])
        _dep_mul = float(rng.uniform(0.60, 1.10))
        _dep_dist = _dep_mul * float(door["w"])

        _ang = float(rng.uniform(0.0, 6.2832))
        _ang2 = _ang + math.pi + float(rng.uniform(-1.1, 1.1))
        _oin = (math.cos(_ang), math.sin(_ang) * 0.75)
        _oout = (math.cos(_ang2), math.sin(_ang2) * 0.75)
        objs.append(dict(
            shape=names[i % len(names)],
            p0=(wx, wy), c1=c1, c2=c2, p2=(dx, dy),
            win=(wx, wy, float(w["w"]) * 0.5, float(w["h"]) * 0.5),
            door=(dx, dy, float(door["w"]) * 0.5, float(door["h"]) * 0.5),
            # the index each opening carries in the opening-ID map, so an object
            # out beyond the wall can be clipped to ITS OWN hole and no other
            win_idx=float(w["index"]), door_idx=float(door["index"]),
            t0=t0, dur=dur, hero=hero,
            # Clamped at both ends. The opening it comes through sets the
            # scale, but an upper window is 325 px and a lower one 600, so
            # unclamped that is a 4x spread between objects doing the same job -
            # and a hero from a lower window ends up 2800 px across, taller than
            # the 2552 px canvas.
            size=_size,
            # Never nearer to the wall than its own radius, or part of the shape
            # is behind the wall plane at its closest point and gets clipped
            # against the openings out in the middle of the room.
            z_near=max(z_near, _size * SHAPE_RADIUS.get(names[i % len(names)], 1.4) * 1.30),
            # The largest this shape may be while it is IN the opening, in the
            # same model units as `size`. SHAPE_RADIUS converts model units to a
            # projected radius, so this is simply the opening's smaller half
            # dimension, with a margin, converted back.
            fit_win=min(float(w["w"]), float(w["h"])) * 0.5 * fit_margin
                    / SHAPE_RADIUS.get(names[i % len(names)], 1.4),
            fit_door=min(float(door["w"]), float(door["h"])) * 0.5 * fit_margin
                     / SHAPE_RADIUS.get(names[i % len(names)], 1.4),
            wander_x=_wx, wander_y=_wy,
            # normalisers, so the wander can be used as a bounded -1..1 signal
            # when it has to stay inside a hole
            wsum_x=wsum(_wx), wsum_y=wsum(_wy),
            drift=drift * (190.0 if hero else 130.0),
            bob=float(rng.uniform(35.0, 95.0)) * drift,
            bob_w=float(rng.uniform(0.055, 0.15)),
            bob_p=float(rng.uniform(0, 6.283)),
            # Magnitude then sign, NOT a symmetric range. uniform(-0.32, 0.32)
            # can hand out 0.01, and an object that barely turns reads as a
            # still image pasted on the wall - which is what made the first
            # torus dull. This guarantees every object is actually rotating.
            spin=(_sp(0.16, 0.36), _sp(0.20, 0.46), _sp(0.11, 0.26)),
            # ... and its own resting pose, so two tori are never presented at
            # the same angle. A torus seen face-on is a flat ring and edge-on is
            # a line; the interesting poses are in between.
            rot_base=(float(rng.uniform(0.0, 6.2832)),
                      float(rng.uniform(0.0, 6.2832)),
                      float(rng.uniform(0.0, 6.2832))),
            # a persistent depth lane, so two objects rarely sit at the same z
            # and the separation solver almost never hits the degenerate case
            lane=float(rng.uniform(-1.0, 1.0)),
            # A CONSTANT depth offset, filled in by the pass at the end of this
            # function. Constant is the whole point: the camera is orthographic,
            # so z costs nothing on screen - the object's path, size and timing
            # are pixel-for-pixel unchanged and only who is in front changes.
            # Nudging positions instead would be a repath, and repathing is
            # visible however gently it is done.
            z_push=0.0,
            # Which SIDE of its window it comes through, and which side of the
            # door it leaves by. Every object used to thread both openings dead
            # centre, which made twelve different journeys look like one. The
            # exit is roughly opposite the entry, so it reads as having crossed
            # rather than doubled back.
            off_in=_oin, off_out=_oout,
            app_dir=_app_dir, dep_dir=_dep_dir,
            app_dist=_app_dist, dep_dist=_dep_dist,
            _door_pref=_door_pref, _c1a=_c1a, _c1b=_c1b,
            _c2a=_c2a, _c2b=_c2b, _dep_mul=_dep_mul,
            # precession: a slow wobble ON TOP of the spin. Nothing in the real
            # world rotates at a perfectly constant rate about a fixed axis, and
            # the eye reads that immediately as machinery.
            rot_w=(float(rng.uniform(0.06, 0.17)), float(rng.uniform(0.05, 0.15)),
                   float(rng.uniform(0.07, 0.20))),
            rot_p=(float(rng.uniform(0, 6.283)), float(rng.uniform(0, 6.283)),
                   float(rng.uniform(0, 6.283))),
            tint=np.array([rng.uniform(0.86, 1.0), rng.uniform(0.88, 1.0),
                           rng.uniform(0.90, 1.0)], "f4"),
        ))

    # ---- no two objects may use the same doorway at the same time ----------
    # Two shapes threading one door together reads as a mistake however well
    # each one behaves on its own.
    #
    # Deliberately a POST-PASS that draws no random numbers. Resolving this
    # inside the loop would need an extra draw, and every object built after it
    # would get different sizes, timings and paths - so a moment that already
    # works would be lost to fixing an unrelated one. This way each object keeps
    # its window, its size, its timing and its shape; only which door it leaves
    # by can change, and only when it has to.
    # Windows first. There are 23 usable windows and more objects than that, so
    # a few are reused - and two objects arriving through one window together
    # is the same mistake as two leaving by one door. Resolved by sliding the
    # later one a second or two further down the segment, which changes its
    # timing and nothing else: not its size, its shape, its path or its window.
    for wdx in set(int(o["win_idx"]) for o in objs):
        share = sorted((o for o in objs if int(o["win_idx"]) == wdx),
                       key=lambda z: z["t0"])
        for k in range(1, len(share)):
            prev, cur = share[k - 1], share[k]
            need = prev["t0"] + 0.22 * prev["dur"] + door_pad
            if cur["t0"] < need:
                latest = t_hi - cur["dur"]
                cur["t0"] = min(need, latest) if latest > cur["t0"] else cur["t0"]

    def door_window(o):
        d = o["dur"]
        return (o["t0"] + 0.78 * d, o["t0"] + d)

    def clashes(iv, booked, pad):
        lo, hi = iv
        return any(lo < b + pad and a < hi + pad for a, b in booked)

    def use_door(o, dk):
        dr = doors[dk]
        dx, dy = float(dr["cx"]), float(dr["cy"])
        side = 1.0 if o["p0"][0] < dx else -1.0
        o["p2"] = (dx, dy)
        o["c1"] = (o["p0"][0] + side * o["_c1a"], o["p0"][1] + o["_c1b"])
        o["c2"] = (dx - side * o["_c2a"], dy - o["_c2b"])
        o["door"] = (dx, dy, float(dr["w"]) * 0.5, float(dr["h"]) * 0.5)
        o["door_idx"] = float(dr["index"])
        o["fit_door"] = (min(float(dr["w"]), float(dr["h"])) * 0.5 * fit_margin
                         / SHAPE_RADIUS.get(o["shape"], 1.4))
        o["dep_dist"] = o["_dep_mul"] * float(dr["w"])

    booked = {k: [] for k in range(len(doors))}
    unresolved = 0
    for o in sorted(objs, key=lambda z: z["t0"] + z["dur"]):
        iv = door_window(o)
        prefs = [o["_door_pref"]] + [k for k in range(len(doors))
                                     if k != o["_door_pref"]]
        pick = next((k for k in prefs if not clashes(iv, booked[k], door_pad)), None)
        if pick is None:
            pick = o["_door_pref"]
            unresolved += 1
        use_door(o, pick)
        booked[pick].append(iv)
    if unresolved:
        print("warning   %d object(s) could not get a clear doorway - every door "
              "was busy. raise --objects less, or shorten --door-pad" % unresolved)

    # ---- overlap on screen, yes. occupy the same space, no. ----------------
    # Two mirrored solids sliding through one another is the one thing that
    # cannot be read as anything but a mistake. But pushing them apart in x or y
    # at render time is a repath, and a repath is visible - that is exactly the
    # bounce the old separation solver produced.
    #
    # So they are separated in DEPTH, by a constant chosen once, here. With an
    # orthographic camera a change in z is invisible: the path, the size and the
    # timing come out pixel for pixel identical. All that changes is which one
    # is in front - and one of them ends up behind the pillar as a result, which
    # is the reading we want anyway.
    SAMPLES = 200
    clashes = []
    for ia in range(len(objs)):
        for ib in range(ia + 1, len(objs)):
            A, B = objs[ia], objs[ib]
            lo = max(A["t0"], B["t0"])
            hi = min(A["t0"] + A["dur"], B["t0"] + B["dur"])
            if hi - lo < 0.2:
                continue
            need = 0.0
            tight = None
            for s in range(SAMPLES + 1):
                t = lo + (hi - lo) * s / SAMPLES
                sa, sb = object_state(A, t), object_state(B, t)
                if sa is None or sb is None or sa[3] < 0.15 or sb[3] < 0.15:
                    continue
                ra = sa[2] * SHAPE_RADIUS.get(A["shape"], 1.4)
                rb = sb[2] * SHAPE_RADIUS.get(B["shape"], 1.4)
                flat = math.hypot(float(sa[0][0] - sb[0][0]),
                                  float(sa[0][1] - sb[0][1]))
                if flat >= ra + rb:
                    continue                       # never overlaps on screen here
                need = max(need, ra + rb)
                # z WITHOUT the pushes, so the relaxation below can solve for them
                dz = ((sa[1] + A["lane"] * Z_LANE) - (sb[1] + B["lane"] * Z_LANE))
                if tight is None or abs(dz) < abs(tight):
                    tight = dz
            if need > 0.0 and tight is not None:
                clashes.append([ia, ib, need * 1.15, tight])

    for _ in range(12):
        for ia, ib, want, base in clashes:
            cur = base + objs[ia]["z_push"] - objs[ib]["z_push"]
            if abs(cur) >= want:
                continue
            sgn = 1.0 if cur >= 0.0 else -1.0
            fix = (want - abs(cur)) * 0.5 * sgn
            objs[ia]["z_push"] += fix
            objs[ib]["z_push"] -= fix
    for o in objs:
        o["z_push"] = max(-2600.0, min(2600.0, o["z_push"]))
    if clashes:
        print("depth     %d pair(s) separated in z so they cannot intersect"
              % len(clashes))
    return objs


def bez3(p0, c1, c2, p2, t):
    u = 1 - t
    a, b, c, d = u*u*u, 3*u*u*t, 3*u*t*t, t*t*t
    return (a*p0[0] + b*c1[0] + c*c2[0] + d*p2[0],
            a*p0[1] + b*c1[1] + c*c2[1] + d*p2[1])


def resolve_overlaps(live, margin, iterations=7, damping=0.55):
    """Push objects apart so their geometry never interpenetrates.

    Two mirrored solids passing through one another is the one artefact that
    reads as fake instantly - real objects occlude, they do not merge.

    JACOBI, NOT GAUSS-SEIDEL. Every pair force is measured against the same
    snapshot and applied together at the end of the sweep. The obvious version
    updates positions inside the pair loop, which makes the outcome depend on
    the order pairs happen to be visited - and once four or more objects crowd
    each other there is more than one valid arrangement, so a hair's difference
    in input flips it to a different one. Measured, that cost up to 771 px of
    movement in a single frame while the underlying paths moved 15.

    Damped and iterated instead: each sweep closes about half the remaining
    overlap, so seven sweeps land within a fraction of a pixel, and the result
    is a continuous function of the input - which is what actually guarantees no
    popping, rather than any amount of clamping.

    Force is scaled by both objects' presence, so one materialising at a window
    ramps its influence up from nothing instead of shoving its neighbour aside
    the instant it appears. Mobility decides who yields: an object still in its
    opening is pinned and takes none of the correction.
    """
    n = len(live)
    if n < 2:
        return
    rad = [l["scale"] * SHAPE_RADIUS.get(l["o"]["shape"], 1.4) for l in live]
    mob = [max(l["alpha"] * l["clear"], 0.0) for l in live]
    pres = [min(max(l["alpha"] * l["clear"], 0.0), 1.0) for l in live]
    for _ in range(iterations):
        delta = [np.zeros(3, "f4") for _ in range(n)]
        for i in range(n):
            for j in range(i + 1, n):
                mi, mj = mob[i], mob[j]
                if mi + mj <= 1e-5:
                    continue
                d = live[j]["pos"] - live[i]["pos"]
                dist = float(np.linalg.norm(d))
                need = (rad[i] + rad[j]) * margin
                if dist >= need:
                    continue
                if dist < 1e-4:
                    d = np.array([0.0, 0.0, 1.0], "f4")
                    dist = 1e-4
                nrm = d / dist
                if abs(float(nrm[2])) < 0.35:
                    sgn = 1.0 if live[i]["o"]["lane"] < live[j]["o"]["lane"] else -1.0
                    blend = 1.0 - abs(float(nrm[2])) / 0.35
                    nrm = nrm + np.array([0.0, 0.0, sgn * blend * 1.2], "f4")
                    nrm = nrm / max(float(np.linalg.norm(nrm)), 1e-6)
                corr = nrm * ((need - dist) * damping * pres[i] * pres[j])
                corr[0] *= 0.9
                corr[1] *= 0.9
                corr[2] *= 1.6
                s = mi + mj
                delta[i] = delta[i] - corr * (mi / s)
                delta[j] = delta[j] + corr * (mj / s)
        for i in range(n):
            live[i]["pos"] = live[i]["pos"] + delta[i]


def object_state(o, t):
    """(pos, wall_z, scale, alpha, depth01) or None if not on screen.

    `wall_z` is the object centre's signed distance from the wall plane:
    negative is out beyond the wall, positive is inside the room. The shader
    clips per fragment against it, so an object halfway through a doorway is
    genuinely half clipped.

    pos[2] carries the same value plus a per-object depth lane, which is what
    the separation solver and the depth buffer use. Keeping the two apart means
    shoving objects around in z to stop them intersecting cannot accidentally
    push one through the wall.
    """
    p = (t - o["t0"]) / o["dur"]
    if p <= 0.0 or p >= 1.0:
        return None

    # Ease, but only partly. Linear progress along a bezier is the single
    # biggest reason CG motion reads as computed - real things accelerate away
    # and settle in. Full smoothstep is the opposite mistake: it stalls the
    # object exactly at the openings, which is where it most needs to be moving.
    pe = p + (smoother(p, 0.0, 1.0) - p) * 0.45

    # ---- where it is, across the wall ----------------------------------
    # `travel` is held at 0 until the object is through the window and pinned
    # at 1 once it reaches the door, so the crossing itself happens with the
    # object dead centre in its opening. Running the bezier on pe directly
    # carried it 260-440 px off the opening by the time it reached the wall
    # plane - through solid masonry, where the mask has nothing to clip it to.
    travel = smoother(pe, 0.10, 0.90)
    x, y = bez3(o["p0"], o["c1"], o["c2"], o["p2"], travel)

    # ---- how far through the wall --------------------------------------
    # `through` is 0 out beyond the wall, 1 in the room: a fast, clean passage.
    # `arc` is the slow swell toward the viewer and back. Separating them lets
    # the object come THROUGH the hole quickly and then approach slowly, instead
    # of doing both on one curve.
    through = smoother(pe, 0.0, 0.18) - smoother(pe, 0.82, 1.0)
    arc = math.sin(math.pi * min(max(pe, 0.0), 1.0)) ** 0.7
    wall_z = -Z_BACK * (1.0 - through) + o["z_near"] * arc * through

    # ---- how big ---------------------------------------------------------
    # Four stages, chained, so the size is ALWAYS moving:
    #
    #   far outside  ->  exactly opening-sized at the crossing
    #                ->  full size out in the room
    #                ->  door-sized at the far crossing
    #                ->  small again, receding away outside
    #
    # It grows the whole way in, not just after it is indoors. Holding it at a
    # flat opening-size until it was through is what made it look like it only
    # started approaching once it had already arrived.
    #
    # An object 1.5-4x wider than its window cannot pass through it, and the
    # renderer can only answer by showing a window-shaped bite out of it, so the
    # fitted sizes are a hard ceiling at both holes.
    # The stages OVERLAP now. Butted end to end there was a dead gap between
    # each one where nothing changed, so the object held still and then set off
    # again - which is most of what "transitioning between states" was.
    far_in = o["fit_win"] * FAR_FRAC
    far_out = o["fit_door"] * FAR_FRAC
    base = far_in + (o["fit_win"] - far_in) * smoother(pe, 0.0, 0.14)
    base = base + (o["size"] - base) * smoother(pe, 0.10, 0.40)
    base = base + (o["fit_door"] - base) * smoother(pe, 0.60, 0.90)
    base = base + (far_out - base) * smoother(pe, 0.86, 1.0)
    # the swell multiplier peaks at 1.0, so it can never lift base over the fit
    scale = base * (0.88 + 0.12 * arc)

    # ---- the side approach, out beyond the wall ---------------------------
    # It is clipped to the openings while it is out there, so off to one side it
    # is simply not drawn - then it slides into the window's footprint and
    # appears ON the window, already the right size, and flies through. That is
    # what makes it arrive from the side instead of fading up head-on out of
    # nothing.
    #
    # Both ramps finish while the object is still COMPLETELY behind the wall
    # (measured: wall_z + radius is about -450 px at the end of the approach),
    # so nothing is sliding sideways while it is threading the opening, and the
    # fit guarantees below are untouched.
    app = 1.0 - smoother(pe, 0.0, 0.095)
    dep = smoother(pe, 0.905, 1.0)
    x += o["app_dir"][0] * o["app_dist"] * app + o["dep_dir"][0] * o["dep_dist"] * dep
    y += (o["app_dir"][1] * o["app_dist"] * app
          + o["dep_dir"][1] * o["dep_dist"] * dep) * 0.5

    # ---- drift, only once it is clear of the wall -------------------------
    # Enveloped by `clear` rather than by the arc. The arc is still 0.79 at the
    # moment the object is in the window, which would let the wander shove it
    # sideways out of its own opening mid-crossing.
    clear = smoother(pe, 0.10, 0.40) * (1.0 - smoother(pe, 0.60, 0.90))
    wx = sum(g * math.sin(2 * math.pi * f * t + ph) for f, ph, g in o["wander_x"])
    wy = sum(g * math.sin(2 * math.pi * f * t + ph) for f, ph, g in o["wander_y"])
    x += wx * o["drift"] * clear
    y += wy * o["drift"] * 0.55 * clear
    y += math.sin(2 * math.pi * o["bob_w"] * t + o["bob_p"]) * o["bob"] * clear

    # ---- a little life in the doorway too ---------------------------------
    # Pinning the object dead centre while it threads the opening is what made
    # the entry and exit read as mechanical. It can move - just not more than
    # the hole actually has room for. The wander is normalised to -1..1 here
    # and scaled by the REAL remaining clearance, so this can never be what
    # pushes a shape into a frame.
    # Both the chosen side and the wander are scaled by the REAL clearance left
    # in this particular opening at this particular size, and together they use
    # at most 90% of it - so neither can ever be what puts a shape into a frame.
    rect = o["win"] if pe < 0.5 else o["door"]
    off = o["off_in"] if pe < 0.5 else o["off_out"]
    half = min(rect[2], rect[3])
    rad = scale * SHAPE_RADIUS.get(o["shape"], 1.4)
    slack = max(half - rad, 0.0)
    in_hole = 1.0 - clear
    x += off[0] * slack * 0.55 * in_hole
    y += off[1] * slack * 0.55 * in_hole
    x += (wx / o["wsum_x"]) * slack * 0.35 * in_hole
    y += (wy / o["wsum_y"]) * slack * 0.35 * 0.7 * in_hole

    alpha = smoother(pe, 0.0, 0.015) * (1.0 - smoother(pe, 0.99, 1.0))

    z = wall_z + o["lane"] * Z_LANE + o["z_push"]
    open_idx = o["win_idx"] if pe < 0.5 else o["door_idx"]
    return (np.array([x, y, z], "f4"), wall_z, scale, alpha, arc, clear, open_idx)


# --------------------------------------------------------------------------- textures

LOOK_PATH = os.path.join(PIPELINE, "look.json")

# ------------------------------------------------------------------- look ----
# look.json is what tune_look.py dialled in. A setting is either a plain value,
# or an object with "keys" - [frame, value] pairs interpolated across the
# segment, so the look can MOVE over the eighty seconds:
#
#   "sky_gain": 2.4,
#   "oil_gain": {"ease": "smooth",
#                "keys": [[4800, 1.2], [6060, 4.0], [6930, 9.5]]},
#   "color":    {"keys": [[3300, "0.38,0.52,0.85"], [7200, "0.90,0.30,0.20"]]}
#
# Plain values become argparse defaults. Keyed ones are evaluated per frame in
# the render loop - which already sets every look uniform once per frame for
# its own reasons, so animating them costs a dictionary lookup and nothing else.
#
# Keys are argparse dests with underscores ("sky_gain", not "--sky-gain"),
# because that is what set_defaults and setattr want.

EASES = ("hold", "linear", "smooth", "ease-in", "ease-out")


def ease_at(kind, x):
    """Where along a segment the value is, given how far along in TIME we are.

    x and the result are both 0..1. 'hold' is what makes a step: it stays on
    the left-hand key until the next one takes over.
    """
    if kind == "hold":
        return 0.0
    if kind == "linear":
        return x
    if kind == "ease-in":
        return x * x
    if kind == "ease-out":
        return 1.0 - (1.0 - x) * (1.0 - x)
    return x * x * (3.0 - 2.0 * x)          # smooth, and the default


class Track(object):
    """One animated setting: [frame, value] keys, and how to get between them.

    A value is either a number or one of the comma strings the command line
    uses for colours and ranges ("0.38,0.52,0.85"). Those interpolate per
    component and come back out in the same form, so nothing downstream can
    tell an animated colour from a fixed one.

    A key may carry its own easing as a third element - [frame, value, "hold"] -
    which applies to the segment STARTING at that key. That is what lets one
    transition snap while the next one glides.
    """

    def __init__(self, name, keys, ease="smooth"):
        if not keys:
            raise ValueError("no keys")
        self.name = name
        self.ease = ease if ease in EASES else "smooth"
        self.text = isinstance(keys[0][1], str)
        self.keys = sorted(
            ((int(k[0]), self._nums(k[1]), (k[2] if len(k) > 2 else None))
             for k in keys),
            key=lambda k: k[0])

    @staticmethod
    def _nums(v):
        if isinstance(v, str):
            return [float(x) for x in v.split(",")]
        if isinstance(v, (list, tuple)):
            return [float(x) for x in v]
        return [float(v)]

    def _out(self, nums):
        return ",".join("%.4f" % n for n in nums) if self.text else nums[0]

    def at(self, frame):
        ks = self.keys
        # Outside the keyed range the first and last values simply hold. The
        # alternative - extrapolating - would run the look off somewhere nobody
        # chose, in the handles, where this surface must match the other eleven.
        if frame <= ks[0][0]:
            return self._out(ks[0][1])
        if frame >= ks[-1][0]:
            return self._out(ks[-1][1])
        # Landing exactly ON a key gives that key's value. Without this the
        # segment loop below matches the span ENDING at the key first - its
        # condition is f0 <= frame <= f1 - and with 'hold' easing that returns
        # the LEFT key, so a hard cut arrived one frame late. Invisible for a
        # smooth track, and exactly wrong for the one thing hold exists to do.
        for kf, kv, _ke in ks:
            if frame == kf:
                return self._out(kv)
        for i in range(len(ks) - 1):
            f0, v0, e0 = ks[i]
            f1, v1, _ = ks[i + 1]
            if f0 <= frame <= f1:
                span = float(f1 - f0)
                u = ease_at(e0 or self.ease,
                            0.0 if span <= 0 else (frame - f0) / span)
                n = min(len(v0), len(v1))
                return self._out([v0[j] + (v1[j] - v0[j]) * u for j in range(n)])
        return self._out(ks[-1][1])

    def describe(self):
        return "%s  %d keys  %d..%d  %s" % (
            self.name, len(self.keys), self.keys[0][0], self.keys[-1][0],
            self.ease)


def look_file_from_argv(argv):
    """--look PATH, read before argparse exists, because the file it names has
    to be loaded BEFORE parse_args in order to become the defaults."""
    if "--look" in argv:
        i = argv.index("--look")
        if i + 1 < len(argv):
            return argv[i + 1]
    for s in argv:
        if s.startswith("--look="):
            return s.split("=", 1)[1]
    return None


def load_look(path=None):
    """(plain values, {name: Track}, problem). Never raises.

    A look file that cannot be read must not be able to stop a delivery render
    at two in the morning - the caller prints the problem and carries on with
    the built-in look.
    """
    path = path or LOOK_PATH
    if not os.path.isfile(path):
        return {}, {}, None
    try:
        with open(path, "r") as fh:
            d = json.load(fh)
    except Exception as e:
        return {}, {}, str(e)
    if not isinstance(d, dict):
        return {}, {}, "the file is not a JSON object"

    static, tracks = {}, {}
    for k, v in d.items():
        if k.startswith("_"):
            continue
        if isinstance(v, dict) and v.get("keys"):
            try:
                tr = Track(k, v["keys"], v.get("ease", "smooth"))
            except (TypeError, ValueError, IndexError, KeyError) as e:
                return {}, {}, "'%s' has unusable keys (%s)" % (k, e)
            tracks[k] = tr
            # Anything read once before the render loop - the colour strings
            # get parsed there - needs a sensible value to start from.
            static[k] = tr.at(tr.keys[0][0])
        else:
            static[k] = v
    return static, tracks, None


def crossing_events(o, samples=200):
    """When and where this object passes THROUGH the wall plane.

    wall_z is negative out beyond the wall and positive in the room, so a sign
    change is the object physically going through a hole - once on the way in
    at its window, once on the way out at its door.

    Solved once, here, rather than watched for per frame: a ripple has to know
    how OLD an impact is, and a per-frame sign test only ever knows "now".
    """
    out = []
    prev = None
    for i in range(samples + 1):
        t = o["t0"] + (i / float(samples)) * o["dur"]
        st = object_state(o, t)
        if st is None:
            prev = None
            continue
        pos, wall_z, scale = st[0], st[1], st[2]
        if prev is not None and (prev[1] < 0.0) != (wall_z < 0.0):
            # linear between two samples is plenty at this density
            f = prev[1] / (prev[1] - wall_z) if (prev[1] != wall_z) else 0.5
            out.append((prev[0] + (t - prev[0]) * f,
                        prev[2][0] + (pos[0] - prev[2][0]) * f,
                        prev[2][1] + (pos[1] - prev[2][1]) * f,
                        max(scale, 1.0)))
        prev = (t, wall_z, pos, scale)
    return out


def load_gray(ff, path, w, h):
    raw = subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-i", path,
                          "-vf", "scale=%d:%d:flags=area" % (w, h),
                          "-pix_fmt", "gray", "-f", "rawvideo", "pipe:1"],
                         capture_output=True).stdout
    if len(raw) < w * h:
        sys.exit("could not read %s" % path)
    return np.frombuffer(raw[: w * h], np.uint8).reshape(h, w)


def load_rgb(ff, path, w, h, nearest=True):
    """An RGB reference image at the render size.

    nearest=True means the values ARE the data - the opening ID map stores the
    opening's index in R and its local UV in G and B - so they must survive the
    downscale untouched.

    ffmpeg's scale=flags=neighbor does NOT do that. Decoding the 27-opening ID
    map to 1/4 with it yields EIGHTY-FOUR distinct indices instead of 28: the
    scaler still goes through a conversion that rounds, and invents openings
    that do not exist along every edge. Objects would then be clipped against a
    window that is not there, and a pane row chosen by index comes out wrong on
    the boundary.

    Only ever visible on previews - at --div 1 nothing is scaled, which is why
    it survived this long in the delivery path. The canvas divides exactly by
    1, 2 and 4, so striding is a true point sample and provably exact.
    """
    if nearest:
        raw = subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-i", path,
                              "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"],
                             capture_output=True).stdout
        if len(raw) < CW * CH * 3:
            sys.exit("could not read %s" % path)
        full = np.frombuffer(raw[: CW * CH * 3], np.uint8).reshape(CH, CW, 3)
        if (w, h) == (CW, CH):
            return full
        if CW % w or CH % h:
            sys.exit("%s cannot be point-sampled to %dx%d" % (path, w, h))
        return np.ascontiguousarray(full[:: CH // h, :: CW // w])

    raw = subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-i", path,
                          "-vf", "scale=%d:%d:flags=area" % (w, h),
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
    ap.add_argument("--plates", action="store_true",
                    help="shorthand for --layout plates")
    ap.add_argument("--layout", default="none",
                    choices=("none", "plates", "canvas", "both"),
                    help="what to write. plates = the TWO PROJECTOR PLATES, sliced "
                         "from the same frame in memory, which is the delivery and "
                         "makes the 1000 px overlap identical in both by construction. "
                         "canvas = ONE stitched 9788x2552 file. both = all three, from "
                         "the same render, for when you do not yet know which the "
                         "producer wants")
    ap.add_argument("--codec", default="prores422hq", choices=sorted(CODECS),
                    help="codec for --plates")
    ap.add_argument("--mp4", action="store_true", help="write a preview MP4")
    ap.add_argument("--out", default="")
    ap.add_argument("--tag", default="KEVIN-CLEVER",
                    help="the suffix on every delivered file name. This used "
                         "to be the mask set (MASK-LAYER / MASK-ALIGNED); it "
                         "is the artist's name now, because that is what the "
                         "producer needs to see on a file that arrives among "
                         "eleven other surfaces. The mask set still names the "
                         "FOLDER, so rendering both one after the other still "
                         "cannot overwrite anything")
    ap.add_argument("--preview-width", type=int, default=0,
                    help="downscale the MP4 to this width (render stays full size)")
    ap.add_argument("--hq", action="store_true", help="use the ProRes masters from source_hq")
    ap.add_argument("--masks", default="layer",
                    choices=tuple(MASK_VARIANTS) + ("noise",),
                    help="which description of the facade to render against. "
                         "'layer' is the authored colour-coded mask exactly as "
                         "drawn. 'aligned' is the same shapes, each translated "
                         "as one rigid piece onto the border the shared plate "
                         "draws - nothing redrawn, nothing deformed. Build it "
                         "first with build_masks.py --align. Output files are "
                         "named after whichever was used, so both can be "
                         "rendered one after the other without overwriting "
                         "each other. 'noise' is the old name for 'aligned'")
    ap.add_argument("--out-width", type=int, default=0,
                    help="scale each output file to this width, height to "
                         "match. The RENDER still happens at --div; this only "
                         "changes what is written. For a file that has to be "
                         "small, render big and encode small: measured at a "
                         "15 MB budget, encoding from the 1/2 render scored "
                         "SSIM 0.931 against 0.896 from the 1/4 render, "
                         "because a bigger downscale averages away the dither "
                         "that would otherwise eat the whole bitrate")
    ap.add_argument("--fit-mb", type=float, default=0.0,
                    help="aim each output file at this many MB. h264 only. It "
                         "swaps constant quality for a bitrate worked out from "
                         "the frame count, held down by VBV so it cannot run "
                         "away on a busy passage. Use it when there is a hard "
                         "ceiling - an upload limit, a message attachment")
    ap.add_argument("--serve", action="store_true",
                    help="stay alive and render frames on demand. Reads "
                         "one JSON command per line on stdin and answers "
                         "with one JSON line. This exists because startup "
                         "is 5.7 s and the frame itself is 0.03 s, so a "
                         "tuner that spawns a renderer per slider move "
                         "pays the 5.7 s every single time")
    ap.add_argument("--log", default="",
                    help="also write everything printed here to this file. The "
                         "live progress bar is left out of it")
    ap.add_argument("--no-objects", action="store_true")
    ap.add_argument("--no-pillars", action="store_true")
    ap.add_argument("--objects", type=int, default=26,
                    help="total across the whole segment, NOT at once")
    ap.add_argument("--obj-scale", type=float, default=1.0,
                    help="overall object size multiplier")
    ap.add_argument("--speed", type=float, default=1.0,
                    help="lower is slower. divides every object's travel time")
    ap.add_argument("--fit-margin", type=float, default=0.62,
                    help="how much of an opening an object may fill as it passes "
                         "through. Lower than it needs to be for clearance alone, "
                         "because the leftover room is what lets an object sit off "
                         "to one side of the hole instead of dead centre")
    ap.add_argument("--intro", type=float, default=10.0,
                    help="seconds to grow out of the bare shared plate, measured from "
                         "the segment IN point (not the render start, so handles show "
                         "untouched noise and a chunked render cannot repeat it). "
                         "With the segment starting at 1:50 this is the whole "
                         "1:50-2:00 approach")
    ap.add_argument("--black-level", type=float, default=0.02,
                    help="mean plate luma below which the plate counts as black. "
                         "the wall is never lit when the shared image is not")
    ap.add_argument("--outro", type=float, default=10.0,
                    help="seconds before the segment OUT point over which the whole "
                         "wall dissolves back into the bare shared plate. Objects are "
                         "required to have landed before it starts")
    ap.add_argument("--emerge", type=float, default=0.85,
                    help="how completely the oil claims an object that is still "
                         "out beyond the wall")
    ap.add_argument("--emerge-tint", type=float, default=0.30,
                    help="how much of the oil's hue a submerged object takes. 0 keeps "
                         "it neutral grey, 1 gives it the film's full colour cast")
    ap.add_argument("--fog", type=float, default=0.48,
                    help="how hard the outside air knocks back an object beyond the "
                         "wall. lower is heavier")
    ap.add_argument("--wall-fade", type=float, default=105.0,
                    help="softness of the wall plane, in canvas px. small is sharp; "
                         "0 would alias along the cut")
    ap.add_argument("--door-pad", type=float, default=1.0,
                    help="seconds of clear air required between two objects using "
                         "the same doorway")
    ap.add_argument("--separation", type=float, default=0.0,
                    help="keep-apart margin as a multiple of the two radii. OFF by "
                         "default: it kept objects from interpenetrating, but when "
                         "two crowded it shoved them apart hard enough to read as a "
                         "bounce. Overlapping on screen and sorting by depth looks "
                         "right; being flicked apart does not. 1.18 turns it back on")
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
    ap.add_argument("--plate-drive", type=float, default=0.75,
                    help="how hard the plate's own dither drives the sky bands. "
                         "1.0 = the wall IS the plate, recoloured; 0 = a plain "
                         "gradient with no texture at all")
    ap.add_argument("--plate-contrast", type=float, default=3.2,
                    help="how hard the plate's dither swings around the frame's own "
                         "mean. this is what keeps the near-black opening from "
                         "collapsing into one flat band")
    ap.add_argument("--plate-mix", type=float, default=0.55)
    ap.add_argument("--horizon", type=float, default=0.34)
    ap.add_argument("--bands", type=float, default=12.0)
    ap.add_argument("--spread", type=float, default=0.95)
    ap.add_argument("--sky-gain", type=float, default=2.15)
    ap.add_argument("--saturation", type=float, default=1.45,
                    help="chroma on the wall and the pillars. 1 = as computed, "
                         "0 = greyscale")
    ap.add_argument("--arc-floor", type=float, default=0.42,
                    help="how far the plate's own darkness is allowed to pull the "
                         "wall down. was 0.25, which crushed the quiet passages")
    ap.add_argument("--oil-gain", type=float, default=6.8)
    # ---- the leaded grid in the windows, and the flutes on the pillars ----
    # The pane counts are the artist's, off a sketch of the real building. The
    # UPPER row count is not: the sketch says 4 and the mattes say 6. With 4
    # columns, the rectangular part of an upper window is 332 x 478 canvas px,
    # which at 4 rows makes a pane 44 % out of square and at 6 rows 4 % out.
    # The lower windows corroborate it - 4 x 8 lands 8 % out of square - so
    # square panes are a measured property of this facade rather than a guess,
    # and 6 is the row count that respects it. Both are flags, so disagreeing
    # is one number.
    ap.add_argument("--panes", type=float, default=1.0,
                    help="master for the window grid. 0 is the old plain glass")
    ap.add_argument("--pane-cols", type=float, default=4.0,
                    help="panes across an opening, both window types")
    ap.add_argument("--pane-rows-upper", type=float, default=6.0,
                    help="rows in an upper window, counted from the springing "
                         "line down. 6, counted off the photograph of the "
                         "venue wall - which is also what square panes "
                         "predicted, and not what the sketch drew")
    ap.add_argument("--pane-rows-lower", type=float, default=8.0,
                    help="rows in a lower window")
    ap.add_argument("--arch-upper", type=float, default=0.232,
                    help="how much of an upper window's height is arch head. "
                         "Measured off the mattes: 144 px of rise on a 620 px "
                         "opening. Above this line the head is a FANLIGHT, not "
                         "more grid - which is what the venue photograph shows")
    ap.add_argument("--arch-lower", type=float, default=0.0,
                    help="the same for a lower window. 0 because their arch is "
                         "segmental - 46 px on 1147 - and the grid simply runs "
                         "under it, top row curved, as on the real wall")
    ap.add_argument("--fan-arc", type=float, default=0.55,
                    help="the fanlight's inner arc, as a fraction of the head "
                         "radius")
    ap.add_argument("--pane-split", type=float, default=18.5,
                    help="opening index above which an opening counts as a "
                         "LOWER window. The mattes are numbered upper first "
                         "(1-18), then lower (19-24), then the doors")
    ap.add_argument("--bevel", type=float, default=0.12,
                    help="chamfer round each pane, as a fraction of the pane")
    ap.add_argument("--bevel-depth", type=float, default=0.55,
                    help="how far the chamfer tips the glass. This is the whole "
                         "effect: each pane then catches the interference "
                         "colours at its own angle")
    ap.add_argument("--mullion", type=float, default=0.055,
                    help="the separation between panes, as a fraction of a pane")
    ap.add_argument("--mullion-dark", type=float, default=0.45,
                    help="how much light the bars take out of the glass")
    # ---- the ripple -------------------------------------------------------
    # Objects come through the windows. Now the glass reacts: a ring leaves the
    # point where one crossed the wall plane and spreads, tipping the same
    # normals the chamfers and the sun already use - so it recolours the
    # interference and swings the sun shading with it rather than being an
    # effect laid on top.
    #
    # Sized against the wall, not the screen. At 9788 px across roughly 38 m,
    # one metre is about 258 px, so the default 220 px wavelength is a ripple
    # every 0.85 m and 900 px/s is about 3.5 m/s - a wave you can follow
    # crossing a window rather than a shimmer.
    ap.add_argument("--ripple", type=float, default=1.35,
                    help="how far a ripple tips the glass, which is what "
                         "recolours the interference. This one SATURATES: the "
                         "angle term is clamped and the tilt feeds a "
                         "normalize(), so past about 1.4 the colour stops "
                         "moving and starts folding. Use --ripple-warp to make "
                         "it bigger, not this")
    ap.add_argument("--ripple-warp", type=float, default=0.075,
                    help="how far a ripple DRAGS the glass, as a fraction of "
                         "the opening. The leaded bars and the chamfers are "
                         "built from the dragged coordinates, so the window "
                         "itself bends - this is the one that reads as the room "
                         "morphing. Nothing saturates; 0.2 is a funhouse mirror")
    ap.add_argument("--ripple-shade", type=float, default=2.4,
                    help="how much harder the ripple swings the sun shading "
                         "than the static chamfers do. 1 treats them alike; "
                         "above that the wave carries its own moving light")
    ap.add_argument("--ripple-grow", type=float, default=1.6,
                    help="how fast the disturbed AREA spreads, in reaches per "
                         "lifetime. A drop starts as a point: 0 switches the "
                         "whole ring field on at once, 1 has the front cross "
                         "the full reach once before the ripple dies. Measured "
                         "in reaches rather than px/s, so a big shape disturbs "
                         "a big area just as quickly. 1.6 is the default "
                         "because it is the value that is BOTH strongest and "
                         "still plainly growing - measured, it reaches 191 px "
                         "at 0.2 s and 481 px at 1.5 s")
    ap.add_argument("--ripple-len", type=float, default=300.0,
                    help="wavelength in canvas px. About 258 px is a metre")
    ap.add_argument("--ripple-speed", type=float, default=320.0,
                    help="how fast the rings travel outward, canvas px/s")
    ap.add_argument("--ripple-spread", type=float, default=7.0,
                    help="how far the disturbance reaches, in object radii. "
                         "7 carries a small object's ripple across its own "
                         "window and a little way onto the masonry")
    ap.add_argument("--ripple-life", type=float, default=2.6,
                    help="seconds before a ripple has gone")

    # ---- the sun ----------------------------------------------------------
    # A light direction, not a light source: everything it touches is a normal
    # that already exists - the pane chamfers and the pillar flutes - so moving
    # it moves their shading and nothing else. Keyframe it and the light
    # crosses the wall with the day.
    #
    # Kept slow on purpose. The wall is around 12 m tall and 38 m wide, and at
    # that size anything you can watch moving is too fast. The default track in
    # look.json takes about a hundred seconds to go from one side to the other,
    # which reads as the light having changed rather than as movement.
    ap.add_argument("--sun-az", type=float, default=0.0,
                    help="where the sun is across the wall. -1 hard left, "
                         "+1 hard right, 0 straight on")
    ap.add_argument("--sun-el", type=float, default=0.55,
                    help="how high the sun is. 0 on the horizon, 1 overhead")
    ap.add_argument("--sun-shade", type=float, default=0.35,
                    help="how hard the sun shades the pane chamfers. This is "
                         "the one number for 'too much'")
    ap.add_argument("--flute-ao", type=float, default=0.5,
                    help="how dark a flute is from its own depth, regardless "
                         "of the sun. Without it the shaft flattens whenever "
                         "the sun crosses its axis")
    ap.add_argument("--flutes", type=float, default=5.0,
                    help="grooves down a pillar shaft. 0 is a plain cylinder")
    ap.add_argument("--flute-depth", type=float, default=0.22,
                    help="how far a groove tips the normal")

    ap.add_argument("--oil-bleed", type=float, default=0.0,
                    help="lift the oil out of the windows and across the whole "
                         "wall. 0 = the openings only, which is the look "
                         "everywhere except the end. 1 = the thin film covers "
                         "everything, and since the plate drives its thickness "
                         "the interference colours follow the plate's own "
                         "patterns - a screen tearing rather than a sky")
    ap.add_argument("--oil-sweep", type=float, default=0.55,
                    help="per-opening view-angle swing. above ~0.8 the film goes white at "
                         "the opening edges, which is the bug this replaced")
    ap.add_argument("--film", default="200,1500",
                    help="thin-film thickness range in nm. Wide on purpose: now that "
                         "the PLATE drives thickness, the range decides how many "
                         "interference orders the dither sweeps through, which is "
                         "what turns the noise itself into the iridescence. "
                         "220,780 is barely one order and reads flat brown; "
                         "180,2400 is more colour again and more chroma noise")
    ap.add_argument("--stone", default="0.72,0.66,0.58", help="pillar colour")
    ap.add_argument("--color", default="0.38,0.52,0.85")
    ap.add_argument("--door-tone", default="0.17,0.10,0.23",
                    help="colour of the doorways. deliberately not the sky's blue - "
                         "the doors are the way out and want to read as depth")
    ap.add_argument("--door-gain", type=float, default=1.0)

    # depth
    ap.add_argument("--parallax", type=float, default=220.0,
                    help="camera sway amplitude in canvas px. Much smaller than it "
                         "was, and none of it moves the plate any more: the depth "
                         "now comes from the objects, the jamb reveals and the "
                         "pillars' shading, all of which this wall owns outright")
    ap.add_argument("--parallax-period", type=float, default=19.0, help="seconds")
    ap.add_argument("--par-in", type=float, default=-0.45,
                    help="tilt of the oil inside each opening. an ANGLE, not an "
                         "offset - it never moves the plate sample")
    ap.add_argument("--par-obj", type=float, default=1.20, help="objects in the room")
    ap.add_argument("--reveal", type=float, default=0.11,
                    help="fake jamb depth, in local opening uv")
    ap.add_argument("--pillar-shift", type=float, default=9.0,
                    help="pillar silhouette travel in canvas px. This moves real "
                         "architecture, so it stays tiny; the pillars' shading "
                         "carries the depth instead")
    ap.add_argument("--pillar-wobble", type=float, default=3.0)
    ap.add_argument("--pillar-gain", type=float, default=1.15)
    ap.add_argument("--trim-lift", type=float, default=0.0,
                    help="brighten the capital and plinth relative to the shaft. "
                         "0 = one continuous stone, which is what it should be. "
                         "This was 0.6, and measured on the wall that made the "
                         "cap 41%% brighter than the shaft and the plinth 15%% - "
                         "so the shaft read as a dark band lying across the "
                         "pillar between them")
    ap.add_argument("--door-frames", default="door", choices=("door", "glass"),
                    help="what to do with the band of WINDOW the authored mask "
                         "wraps around the left and right doors. 'door' treats "
                         "a door's whole opening as door, which is what it is; "
                         "'glass' takes the mask at face value and lets that "
                         "band carry the oil, which puts a stripe of "
                         "interference colour down the side of those two doors")
    ap.add_argument("--pillar-noise", type=float, default=0.25,
                    help="how much of the plate's grain lands on the pillars. "
                         "1 is every bit of it, 0 is none - the pillar still "
                         "breathes with the arc, it just stops being dithered. "
                         "The pillars carry the flutes, the cylinder and the "
                         "sun crossing them, and at full grain the dither sits "
                         "on top of all of it: measured on the shaft, going "
                         "from 1 to 0.25 drops the speckle 61 %% and leaves the "
                         "carving untouched, so the carving-to-speckle ratio "
                         "goes 1.5 -> 3.7")
    ap.add_argument("--pillar-ambient", type=float, default=0.45,
                    help="how much the sky's COLOUR tints the pillar stone - "
                         "its hue only, normalised by its own luma, so dusk and "
                         "midday shift the stone without either one changing "
                         "how bright it is. --color is keyframed across the "
                         "piece, so the pillars follow the day for free. At "
                         "0.45 the stone's red/blue ratio runs 0.72 to 2.28 "
                         "across the piece against 1.24 for the bare stone; at "
                         "1 the pillars simply become the sky")
    ap.add_argument("--plate-hp", type=float, default=1.0,
                    help="how much of the wall's own banding to take off the "
                         "pillars. 1 = all of it, and the plate's grain is kept "
                         "in full. 0 = the old behaviour, where the wall's "
                         "bright bands at the capital and the base printed "
                         "straight through the pillar and the shaft between "
                         "them read as a shadow")
    ap.add_argument("--plate-blur", type=float, default=110.0,
                    help="what counts as 'local' for --plate-hp, in canvas px. "
                         "Bigger than the dither, smaller than the wall's bands")
    ap.add_argument("--pillar-z", type=float, default=1500.0,
                    help="how near the pillars stand, in the same units as the "
                         "objects (-900 out beyond the wall .. +1100 closest). "
                         "objects nearer than this pass in FRONT of them")

    # chrome
    ap.add_argument("--gloss", type=float, default=90.0)
    ap.add_argument("--spec-gain", type=float, default=3.4)
    ap.add_argument("--env-warm", default="1.10,0.84,0.98",
                    help="chrome reflection tint above the horizon")
    ap.add_argument("--env-cool", default="0.70,0.96,1.12",
                    help="chrome reflection tint below the horizon")
    ap.add_argument("--windows", type=float, default=2.0,
                    help="brightness of the room openings reflected in the metal. "
                         "this is what stops a chrome sphere being a grey ball")
    ap.add_argument("--room-mix", type=float, default=0.20)
    ap.add_argument("--exposure", type=float, default=0.85)
    ap.add_argument("--view-fov", type=float, default=0.55,
                    help="fake perspective on the chrome. 0 makes every flat face a "
                         "single flat colour, because the camera is orthographic")
    ap.add_argument("--horizon-hot", type=float, default=0.90,
                    help="the reflected horizon line. this is what reads as metal")

    ap.add_argument("--print-defaults", action="store_true",
                    help="dump every setting and its default as JSON, then stop. "
                         "tune_look.py reads this so the sliders start where the "
                         "renderer actually starts, rather than at a second copy "
                         "of the numbers that would drift out of step")
    ap.add_argument("--no-look", action="store_true",
                    help="ignore look.json and use the built-in defaults")
    ap.add_argument("--look", default="",
                    help="read the look from this file instead of "
                         "_pipeline/look.json. tune_look.py previews through "
                         "this, so a preview goes down exactly the same path "
                         "as the delivery - including animated settings, which "
                         "no command line could express")

    # look.json is where tune_look.py saves what you dialled in. Plain values
    # are applied as argparse DEFAULTS, so anything passed on the command line
    # still wins and the delivery export picks the tuned look up without needing
    # to know about it - sliders whose values the delivery ignored would be
    # worse than no sliders. Animated settings become tracks, evaluated per
    # frame down in the render loop.
    look_file = look_file_from_argv(sys.argv) or LOOK_PATH
    look, tracks, look_err = load_look(look_file)
    look_applied = 0
    if (look or tracks) and "--no-look" not in sys.argv \
            and "--print-defaults" not in sys.argv:
        known = {act.dest for act in ap._actions}
        unknown = sorted(k for k in look if k not in known)
        use = {k: v for k, v in look.items() if k in known}
        ap.set_defaults(**use)
        look_applied = len(use)
        tracks = {k: v for k, v in tracks.items() if k in known}
        if unknown:
            print("look: ignoring unknown setting(s): %s" % ", ".join(unknown))
    else:
        tracks = {}

    a = ap.parse_args()

    if a.print_defaults:
        print(json.dumps(vars(a), indent=1, sort_keys=True))
        return
    if look_err:
        # Never fatal. A broken look file must not be able to stop a delivery
        # render at two in the morning.
        print("look could not be read (%s) - using built-in defaults" % look_err)
    elif look_applied or tracks:
        print("look      %d setting(s) from %s" % (look_applied, look_file))
        for name in sorted(tracks):
            print("          animated  %s" % tracks[name].describe())

    # A setting that is BOTH animated and given on the command line: the flag
    # would be silently overwritten every frame by the track. Say so once, here,
    # rather than leaving somebody to wonder why their flag did nothing.
    if tracks:
        given = set()
        for s in sys.argv[1:]:
            if s.startswith("--"):
                given.add(s[2:].split("=")[0].replace("-", "_"))
        clash = sorted(given & set(tracks))
        if clash:
            print("          NOTE: %s %s animated in the look file, so the "
                  "flag on the command line is ignored"
                  % (", ".join("--" + c.replace("_", "-") for c in clash),
                     "is" if len(clash) == 1 else "are"))

    if not a.png and not a.mp4:
        a.mp4 = True
    open_log(a.log)

    global MASK_ROOT, REF_ROOT
    a.masks = mask_variant(a.masks)
    MASK_ROOT, REF_ROOT = mask_dirs(a.masks)
    if not os.path.isdir(MASK_ROOT):
        sys.exit("the '%s' mask set has not been built yet - there is no %s.\n"
                 "Build it with:   python build_masks.py --align"
                 % (a.masks, MASK_ROOT))
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
    print("masks     %s  (%s)"
          % (a.masks, "the authored colour mask, as drawn" if a.masks == "layer"
             else "the authored shapes, aligned to the plate"))

    ctx = moderngl.create_standalone_context(require=330)
    print("gpu       %s" % ctx.info["GL_RENDERER"])
    print("load      %s, ffmpeg threads %d"
          % ("NORMAL priority" if a.full_speed else "below-normal priority", a.threads))

    quad_vs = """#version 330
    in vec2 aP; out vec2 vUv;
    void main(){ vUv = aP*0.5+0.5; gl_Position = vec4(aP,0,1); }"""

    bg_prog = ctx.program(vertex_shader=quad_vs, fragment_shader=load_shader("sky_oil.frag"))
    # The pillars are drawn into the OBJECTS' depth buffer, at a fixed depth, so
    # the depth test decides per pixel which objects pass in front of them and
    # which go behind. That needs a vertex shader that can place the quad in z;
    # the shared one pins it at 0.
    pillar_vs = """#version 330
    in vec2 aP; out vec2 vUv; uniform float uQuadZ;
    void main(){ vUv = aP*0.5+0.5; gl_Position = vec4(aP, uQuadZ, 1.0); }"""
    pil_prog = ctx.program(vertex_shader=pillar_vs,
                           fragment_shader=load_shader("pillars.frag"))
    obj_prog = ctx.program(vertex_shader=load_shader("chrome_object.vert"),
                           fragment_shader=load_shader("chrome_object.frag"))
    comp_prog = ctx.program(vertex_shader=quad_vs, fragment_shader="""#version 330
    in vec2 vUv; out vec4 fragColor;
    uniform sampler2D uBg, uObjects, uMasks;
    void main(){
        vec3 col = texture(uBg, vUv).rgb;

        // One layer for objects AND pillars: they were depth-sorted against
        // each other when they were drawn, so there is nothing left to order
        // here. The objects also clip themselves against the openings in their
        // own shader, per fragment, so there is nothing to mask either.
        vec4 ob = texture(uObjects, vUv);
        col = col * (1.0 - ob.a) + ob.rgb;

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
    # ... and it must not have been filtered on the way in either - see load_rgb.

    t_plate = ctx.texture((W, H), 3, dtype="f1")
    t_plate.filter = (moderngl.LINEAR, moderngl.LINEAR)

    # ---- framebuffers -----------------------------------------------------
    def rgba_fbo():
        t = ctx.texture((W, H), 4, dtype="f1")
        t.filter = (moderngl.LINEAR, moderngl.LINEAR)
        return ctx.framebuffer(color_attachments=[t]), t
    fbo_bg, tex_bg = rgba_fbo()
    fbo_obj, tex_obj = rgba_fbo()

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

    fbo_obj_d = (None if samples else
                 ctx.framebuffer(color_attachments=[tex_obj], depth_attachment=ms_d))
    fbo_final = ctx.simple_framebuffer((W, H), components=3)

    # ---- geometry ---------------------------------------------------------
    vaos = {}
    for name, fn in SHAPES.items():
        data = fn()
        vbo = ctx.buffer(data.tobytes())
        vaos[name] = ctx.vertex_array(obj_prog, [(vbo, "3f 3f 2x4", "aPos", "aNrm")])

    # ---- the plate's own arc ----------------------------------------------
    # Named plate_arc, not arc: object_state() returns a swell value that was
    # also called arc, and unpacking it in this scope silently replaced this
    # dict with a float. The frame it happened on rendered fine and the NEXT one
    # died in arc.get(), which points nowhere near the actual mistake.
    #
    # Read before the objects are built, because where the plate ENDS decides
    # when they have to have landed.
    plate_arc = {}
    arc_path = ref_file("noise_arc.csv", REF_ROOT)
    if os.path.isfile(arc_path):
        with open(arc_path) as fh:
            for row in csv.DictReader(fh):
                plate_arc[int(row["frame"])] = (float(row["mean_norm"]),
                                                float(row["mean"]))

    # ---- where does the plate stop? ---------------------------------------
    # It does not fade out, it CUTS. Measured on this segment: mean luma 0.506
    # at frame 7348 and 0.000 at 7349, and it strobes between 0.29 and 0.89 for
    # the two seconds before that. Running our own dissolve on a clock meant we
    # were still at half strength when it cut, amplifying the strobe and then
    # painting a treatment onto pure black for five more seconds.
    #
    # So the dissolve ends where the PLATE ends, found from the arc rather than
    # assumed. If the arc is missing we fall back to the segment out point.
    seg_end = (seg["out"] - seg["in"]) / float(FPS)
    cut_frame = seg["out"] + seg["handles"]
    if plate_arc:
        for f in range(seg["out"] + seg["handles"], seg["in"], -1):
            if plate_arc.get(f, (0.0, 0.0))[1] > a.black_level:
                cut_frame = f + 1
                break
    cut_t = min((cut_frame - seg["in"]) / float(FPS), seg_end)
    print("plate ends %s (frame %d) - the dissolve lands there, not at the out point"
          % ("%d:%05.2f" % (int(cut_frame / 30 // 60), (cut_frame / 30.0) % 60),
             cut_frame))

    openings = json.load(open(os.path.join(REF_ROOT, "openings.json")))["openings"]
    doors = [o for o in openings if o["kind"] == "DOOR"]

    # The first opening index that is a door. The shader uses it to treat a
    # door's WHOLE opening as door - see the note in sky_oil.frag - which only
    # works if the doors really are the last openings. They are, because boxes()
    # sorts by row and the doors are the bottom row, but assuming it silently
    # would be the kind of thing that breaks when a mask is rebuilt.
    door_first = min([o["index"] for o in doors]) if doors else 9999
    if a.door_frames == "glass":
        door_first = 9999          # leave the authored WINDOW bands as glass
    stray = [o["index"] for o in openings
             if (o["index"] >= door_first) != (o["kind"] == "DOOR")]
    if stray:
        print("openings  ! windows and doors are interleaved at %s - the door "
              "frames will keep the glass treatment" % stray)
        door_first = 9999
    target = pick_target_door(doors)
    seg_lo = (seg["in"] - seg["handles"] - seg["in"]) / float(FPS)
    # Everything must have LANDED before the dissolve begins.
    seg_hi = cut_t - a.outro
    objs = [] if a.no_objects else build_objects(
        openings, a.seed, a.objects, seg_lo, seg_hi, a.obj_scale, a.drift, a.speed,
        a.fit_margin, a.door_pad)
    print("objects   %d  ->  door %d at x %d (%dx%d)"
          % (len(objs), target["index"], target["cx"], target["w"], target["h"]))

    # Solved whatever --ripple currently says, because it is a keyframeable
    # track: a look that starts the ripple at 0 and brings it in later would
    # otherwise find no crossings to ring, having skipped this at startup. It
    # is two crossings per object and the shader early-outs at zero anyway.
    impacts = []
    if objs:
        for o in objs:
            impacts += crossing_events(o)
        impacts.sort()
        if impacts:
            rr = [i[3] for i in impacts]
            print("ripple    %d crossing(s), radius %.0f-%.0f px, %.0f px "
                  "wavelength, %.1f s life, warp %.3f"
                  % (len(impacts), min(rr), max(rr), a.ripple_len,
                     a.ripple_life, a.ripple_warp))
            # The frames they land on, so a preview can be aimed at one
            # instead of hunting for a ring by scrubbing.
            fr = [seg["in"] + int(round(i[0] * FPS)) for i in impacts]
            for k in range(0, len(fr), 14):
                print("          %s" % " ".join("%5d" % f for f in fr[k:k + 14]))
    # Reused every frame rather than reallocated: this is inside the hot loop.
    ripple_buf = np.zeros((8, 4), "f4")
    impact_u = bg_prog.get("uImpact", None)
    if impact_u is None:
        impact_u = bg_prog.get("uImpact[0]", None)
    if a.ripple > 0.0 and impact_u is None:
        print("ripple    WARNING: the shader has no uImpact array - no ripples")

    regions = json.load(open(os.path.join(REF_ROOT, "facade_regions.json")))["regions"]
    cols = regions.get("COLUMN", [])
    col0 = cols[0] if len(cols) > 0 else {"x": 0, "y": 0, "w": 1, "h": 1}
    col1 = cols[1] if len(cols) > 1 else col0
    print("pillars   %d columns" % len(cols))

    # ---- noise stream -----------------------------------------------------
    # The noise can arrive either way: as the two projector plates, like the
    # preview mp4s, or already stitched into one 9788x2552 canvas. Both are
    # handled - set source_hq.STITCHED for a single file, or SPSW1/SPSW2 for a
    # pair, and STITCHED wins if both are present.
    v1 = v2 = stitched = None
    if a.hq:
        hq = CFG.get("source_hq", {})
        stitched = hq.get("STITCHED") or None
        if stitched:
            if not os.path.isfile(stitched):
                sys.exit("source_hq.STITCHED does not exist: %s" % stitched)
        elif hq.get("SPSW1") and hq.get("SPSW2"):
            v1, v2 = hq["SPSW1"], hq["SPSW2"]
            for p_ in (v1, v2):
                if not os.path.isfile(p_):
                    sys.exit("source_hq file does not exist: %s" % p_)
        else:
            sys.exit("--hq needs either source_hq.STITCHED (one 9788x2552 file) or "
                     "both source_hq.SPSW1 and SPSW2, in project.json")
    else:
        v1, v2 = source("SPSW1"), source("SPSW2")
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
    if stitched:
        # Already one canvas: nothing to crop, nothing to stack. The overlap is
        # inside the file exactly where it belongs.
        print("noise     stitched canvas  %s" % os.path.basename(stitched))
        noise_cmd = ([ff, "-hide_banner", "-loglevel", "error",
                      "-threads", str(a.threads), "-ss", ss, "-i", stitched,
                      "-vf", "scale=%d:%d:flags=area" % (W, H)])
    else:
        fc = ("[0:v]scale=%d:%d:flags=area,crop=%d:%d:0:0[l];"
              "[1:v]scale=%d:%d:flags=area[r];[l][r]hstack=2"
              % (p1w // a.div, H, cut // a.div, H, p2w // a.div, H))
        noise_cmd = ([ff, "-hide_banner", "-loglevel", "error",
                      "-threads", str(a.threads),
                      "-ss", ss, "-i", v1, "-ss", ss, "-i", v2,
                      "-filter_complex", fc])
    def open_noise(start, count, quiet=False):
        """The plate, from `start`, for `count` frames.

        Serve mode re-seeks for every still it is asked for, so the -ss has
        to be rebuilt per call rather than baked in once at startup.
        """
        cmd = list(noise_cmd)
        secs = "%.6f" % (start / float(FPS))
        for i, tok in enumerate(cmd):
            if tok == "-ss":
                cmd[i + 1] = secs
        kw = {"stdout": subprocess.PIPE, "bufsize": W * H * 3 * 2}
        if quiet:
            # The read-ahead thread drops its pipe whenever the page
            # jumps somewhere else, and ffmpeg says so every time. It is
            # not news, and it would land in the middle of the console.
            kw["stderr"] = subprocess.DEVNULL
        return popen_polite(
            cmd + ["-frames:v", str(count),
                   "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"], **kw)

    noise = None if a.serve else open_noise(a.start, a.count)

    # ---- output sink ------------------------------------------------------
    # Serve mode writes each frame where it is told to, one request at a
    # time, so none of the delivery sinks apply.
    layout = "none" if a.serve else a.layout
    if a.plates and layout == "none":
        layout = "plates"

    sinks = []
    if layout != "none":
        spec = CODECS[a.codec]
        outdir = a.out or os.path.join(RENDER_ROOT, "deliver")
        os.makedirs(outdir, exist_ok=True)
        last = a.start + a.count - 1

        targets = []
        if layout in ("plates", "both"):
            for pl in CFG["plates"]:
                targets.append((pl["name"], pl["x"] // a.div, pl["w"] // a.div))
        if layout in ("canvas", "both"):
            targets.append(("CANVAS", 0, CW // a.div))

        # An odd dimension cannot be encoded (see encodable_size). At --div 4
        # the right plate is 3588/4 = 897 and the stitched canvas is 2447, both
        # odd.
        #
        # For a DELIVERY codec that stays a hard stop: nobody should discover a
        # one-pixel-narrow master downstream. For an H.264 REVIEW render it is
        # not worth refusing over - it drops the last column and says so, which
        # is what the --mp4 preview path has always done.
        sized = []
        for nm, x0, pw in targets:
            ew, eh = encodable_size(pw, H)
            if (ew, eh) != (pw, H):
                if a.codec != "h264":
                    sys.exit("--layout %s at --div %d gives %s a %dx%d frame, and an "
                             "odd dimension cannot be encoded in %s. Use --div 1 or "
                             "2, or --codec h264 for a review render."
                             % (layout, a.div, nm, pw, H, a.codec))
                print("output    %-6s %dx%d -> %dx%d  (even dimensions for the encoder; "
                      "review only)" % (nm, pw, H, ew, eh))
            sized.append((nm, x0, ew, eh))
        targets = sized

        # A tag goes straight into a file name, so anything that is not
        # plainly safe there becomes a hyphen rather than a surprise.
        tag = re.sub(r"[^A-Za-z0-9_-]+", "-", a.tag).strip("-").upper()
        if not tag:
            tag = "MASK-" + a.masks.upper()

        if (a.out_width or a.fit_mb) and a.codec != "h264":
            sys.exit("--out-width and --fit-mb rescale and rate-limit the "
                     "output, which is the opposite of what a delivery is for. "
                     "They are review-render options: use --codec h264.")

        for nm, x0, pw, ph in targets:
            fname = "PxDL_SW_%s_%05d-%05d_%s.%s" % (
                nm, a.start, last, tag, spec["ext"])
            path = os.path.join(outdir, fname)

            vf, ow, oh = [], pw, ph
            if a.out_width and a.out_width < pw:
                ow = int(a.out_width) // 2 * 2
                oh = int(round(ph * ow / float(pw))) // 2 * 2
                vf = ["-vf", "scale=%d:%d:flags=lanczos" % (ow, oh)]

            if a.fit_mb > 0.0:
                # Size is bitrate times duration and nothing else, so work back
                # from the duration this render actually has. The 0.93 leaves
                # the container its share. VBV then stops a busy passage
                # borrowing more than the buffer holds, which is what makes a
                # ceiling a ceiling rather than an average.
                secs = max(a.count / float(FPS), 1e-3)
                kbit = max(120, int(a.fit_mb * 8192.0 * 0.93 / secs))
                codec_args = ["-c:v", "libx264", "-preset", "slow",
                              "-pix_fmt", "yuv420p",
                              "-b:v", "%dk" % kbit,
                              "-maxrate", "%dk" % int(kbit * 1.35),
                              "-bufsize", "%dk" % (kbit * 3),
                              "-movflags", "+faststart"]
                print("output    %-6s %.0f MB over %.1f s  ->  %d kbit/s"
                      % (nm, a.fit_mb, secs, kbit))
            else:
                codec_args = list(spec["args"])

            proc = popen_polite(
                [ff, "-hide_banner", "-loglevel", "error", "-y",
                 "-threads", str(a.threads),
                 "-f", "rawvideo", "-pix_fmt", "rgb24",
                 "-s", "%dx%d" % (pw, ph), "-framerate", str(FPS), "-i", "pipe:0",
                 "-r", str(FPS)] + vf + codec_args +
                ["-color_primaries", "bt709", "-color_trc", "bt709",
                 "-colorspace", "bt709", path],
                stdin=subprocess.PIPE)
            sinks.append(dict(proc=proc, x0=x0, x1=x0 + pw, y1=ph,
                              name=nm, path=path))
            print("output    %-6s canvas x %d..%d  ->  %dx%d%s  %s"
                  % (nm, x0 * a.div, (x0 + pw) * a.div, ow, oh,
                     "" if (ow, oh) == (pw, ph) else "  (rendered %dx%d)" % (pw, ph),
                     fname))
        dst = outdir
    elif a.png:
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
    sink = None
    # sinks is what decides this now, not the old --plates flag: with
    # --layout the delivery sinks exist while a.plates is still False, and this
    # went looking for sink_args that were never built.
    if not sinks and not a.serve:
        sink = popen_polite(
            [ff, "-hide_banner", "-loglevel", "error", "-y", "-threads", str(a.threads),
             "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "%dx%d" % (W, H),
             "-framerate", str(FPS), "-i", "pipe:0", "-r", str(FPS)] + sink_args,
            stdin=subprocess.PIPE)

    col = np.array([float(x) for x in a.color.split(",")], "f4")
    stone = np.array([float(x) for x in a.stone.split(",")], "f4")
    door_tone = np.array([float(x) for x in a.door_tone.split(",")], "f4")
    env_warm = np.array([float(x) for x in a.env_warm.split(",")], "f4")
    env_cool = np.array([float(x) for x in a.env_cool.split(",")], "f4")
    film = [float(x) for x in a.film.split(",")]
    nbytes = W * H * 3
    # A bar that rewrites its own line needs a console to rewrite it. If stdout
    # is a file or a pipe, \r does nothing useful and every update lands as
    # another line, so fall back to an occasional milestone instead.
    live_bar = False
    try:
        live_bar = bool(sys.stdout.isatty())
    except Exception:
        live_bar = False
    note_every = max(30, int(a.count / 20.0))
    next_note = 0
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
    proj = ortho(0, W, 0, H, -Z_CLIP, Z_CLIP)

    # ---- where frames come from -------------------------------------------
    # Normally: straight down the plate stream, in order, exactly as before.
    # Serving: whatever the next command on stdin asks for. Either way the body
    # below is the same code rendering the same frame, which is the point - a
    # preview that renders differently from the delivery is worth nothing.
    req = {}

    def say(obj):
        # Straight to the real stdout: the tuner parses this channel, and
        # a Tee installed by --log would put the log header in the middle
        # of it.
        sys.__stdout__.write(json.dumps(obj) + "\n")
        sys.__stdout__.flush()

    def apply_look_file(path):
        """Swap in a different look without restarting. Returns an error or None."""
        if not path:
            return None
        look2, tracks2, err = load_look(path)
        if err:
            return err
        fields = {act.dest for act in ap._actions}
        for key, val in look2.items():
            if key in fields:
                setattr(a, key, val)
        tracks.clear()
        tracks.update({k: v for k, v in tracks2.items() if k in fields})
        return None

    def sequential_feed():
        for k in range(a.count):
            buf = noise.stdout.read(nbytes)
            if len(buf) < nbytes:
                print("\nnoise stream ended early at frame %d" % k)
                return
            yield k, a.start + k, buf

    # ---- the plate, read ahead of the playhead -----------------------------
    # Measured, div 4, off the two 760 MB source mp4s:
    #
    #     spawn ffmpeg                     54 ms
    #     open both files and seek    700-1000 ms     <- this was the whole cost
    #     every frame after the seek       20 ms
    #     the GL draw itself                0-7 ms
    #
    # So a still was never slow because of the picture. It was slow because it
    # threw the decoder away and seeked again for every frame asked for, which
    # is also exactly why a CLIP is quick: one seek, amortised over 120 frames.
    #
    # This keeps ONE decoder open and running forward in a thread, filling a
    # cache ahead of wherever the page is looking. Scrubbing forward then costs
    # nothing at all, and only a jump somewhere else pays for a seek.
    lo_f = seg["in"] - seg["handles"]
    hi_f = seg["out"] + seg["handles"]
    # Reading forward is ~20 ms a frame and a seek is ~800 ms, so anything
    # within about forty frames is cheaper to read than to seek to.
    SEEK_IF_AHEAD = 40
    cache_mb = float(os.environ.get("PXDL_PLATE_CACHE_MB", "400"))
    keep = max(4, int(cache_mb * 1048576 // max(nbytes, 1)))
    # Pinned frames are never trimmed, so they need a bound of their own. A
    # frame is 4.5 MB at div 4 but 75 MB at div 1, and the same handful of
    # bookmarks would be 27 MB in one case and 450 in the other.
    pin_max = max(1, int(200 * 1048576 // max(nbytes, 1)))

    # `cache` is the rolling window around the playhead and gets trimmed.
    # `pinned` is the handful of bookmark frames, and never does: a bookmark is
    # by definition a long way from wherever you are looking now, so trimming by
    # distance threw every one of them away the moment it arrived.
    plate = {"cache": {}, "pinned": {}, "want": None, "focus": lo_f,
             "marks": [], "bad": set(), "pos": None, "err": None,
             "stop": False, "paused": False, "seeks": 0, "decoded": 0,
             "busy_at": time.time(), "asked": False}
    # A bookmark seek takes about a second and cannot be interrupted, so
    # starting one while the page is being worked on makes the NEXT thing
    # asked for wait behind it. Measured: doing them eagerly turned 140 ms
    # of scrubbing into 715. They are worth having, but only in the gaps.
    IDLE_BEFORE_MARKS = 2.0
    plate_cond = threading.Condition(threading.Lock())

    def plate_trim():
        """Drop whatever is furthest from where the page is looking."""
        c, focus = plate["cache"], plate["focus"]
        if len(c) <= keep:
            return
        for f in sorted(c, key=lambda k: -abs(k - focus))[:len(c) - keep]:
            del c[f]

    def plate_have(f):
        return f in plate["cache"] or f in plate["pinned"]

    def plate_buf(f):
        return plate["cache"][f] if f in plate["cache"] else plate["pinned"][f]

    def plate_reader():
        pipe, pos = None, None

        def shut():
            if pipe is None:
                return
            try:
                pipe.stdout.close()
                pipe.wait(timeout=5)
            except Exception:
                pipe.kill()

        try:
            while True:
                with plate_cond:
                    if plate["stop"]:
                        break
                    if plate["paused"]:
                        plate_cond.wait(0.1)
                        continue
                    want, focus = plate["want"], plate["focus"]
                    target, pin = None, False

                    if want is not None and not plate_have(want):
                        target = want              # somebody is waiting on it
                    elif (plate["marks"] and time.time() - plate["busy_at"]
                          > IDLE_BEFORE_MARKS):
                        # The bookmarks are the only jumps this page makes,
                        # and warming them is what turns a jump from 1.2 s
                        # into nothing. Done only while the page is quiet.
                        m = plate["marks"].pop(0)
                        if plate_have(m) or m in plate["bad"]:
                            continue
                        target, pin = m, True
                    elif not plate["asked"]:
                        # Nothing has been asked for yet, so there is no
                        # playhead to read ahead of. Guessing meant starting
                        # at the top of the segment and making the page's
                        # first frame queue behind a seek it never wanted.
                        plate_cond.wait(0.2)
                        continue
                    else:
                        # Fill forward from the playhead. Starting the search at
                        # `focus` rather than at `pos` is what brings the reader
                        # back after a bookmark has taken it somewhere else.
                        nxt, f = None, max(lo_f, focus)
                        limit = min(hi_f, focus + keep - 1)
                        while f <= limit:
                            if not plate_have(f) and f not in plate["bad"]:
                                nxt = f
                                break
                            f += 1
                        if nxt is None or len(plate["cache"]) >= keep:
                            plate_cond.wait(0.2)
                            continue
                        target = nxt

                    # Reading forward is ~20 ms a frame and a seek is ~800, so
                    # anything close ahead is cheaper to walk to than to seek to
                    # - and the frames walked past land in the cache anyway.
                    if (pipe is not None and pos is not None and pos <= target
                            and target - pos <= SEEK_IF_AHEAD):
                        seek, target = False, pos
                    else:
                        seek = True

                # the expensive part, deliberately outside the lock so that a
                # request can still be answered from the cache meanwhile
                if seek:
                    shut()
                    pipe, pos = None, None
                    if not (lo_f <= target <= hi_f):
                        with plate_cond:
                            plate["bad"].add(target)
                            plate_cond.notify_all()
                        continue
                    pipe = open_noise(target, hi_f - target + 1, quiet=True)
                    pos = target
                    with plate_cond:
                        plate["seeks"] += 1
                buf = pipe.stdout.read(nbytes)
                if len(buf) < nbytes:
                    shut()
                    with plate_cond:
                        plate["bad"].add(pos)
                        plate["pos"] = None
                        plate_cond.notify_all()
                    pipe, pos = None, None
                    continue
                with plate_cond:
                    if pin and pos == target and len(plate["pinned"]) < pin_max:
                        plate["pinned"][pos] = buf
                    else:
                        plate["cache"][pos] = buf
                        plate_trim()
                    plate["decoded"] += 1
                    pos += 1
                    plate["pos"] = pos
                    plate_cond.notify_all()
        except Exception as e:                # a reader that dies must say so
            with plate_cond:
                plate["err"] = str(e)
                plate["bad"].add(-1)
                plate_cond.notify_all()
        finally:
            shut()

    def plate_get(frame, timeout=120.0):
        """The frame, waiting for the reader only if it is not already here."""
        end = time.time() + timeout
        with plate_cond:
            plate["focus"] = frame
            plate["busy_at"] = time.time()
            plate["asked"] = True
            if plate_have(frame):
                return plate_buf(frame)
            plate["want"] = frame
            plate["bad"].discard(frame)
            plate_cond.notify_all()
            while not plate_have(frame):
                if frame in plate["bad"] or -1 in plate["bad"]:
                    plate["want"] = None
                    return None
                if time.time() > end:
                    plate["want"] = None
                    return None
                plate_cond.wait(0.2)
            plate["want"] = None
            plate["busy_at"] = time.time()
            return plate_buf(frame)

    def plate_pause(on):
        """A clip reads the plate itself, sequentially. Two decoders racing for
        the same two mp4s is slower than either one alone."""
        with plate_cond:
            plate["paused"] = on
            plate_cond.notify_all()

    reader_thread = threading.Thread(target=plate_reader, daemon=True)

    def serve_feed():
        """One frame per command, and a run of them for a clip."""
        reader_thread.start()
        say({"ready": 1, "w": W, "h": H, "div": a.div,
             "start": lo_f, "end": hi_f, "cache": keep})
        while True:
            line = sys.stdin.readline()
            if not line:
                return
            line = line.strip()
            if not line:
                continue
            try:
                cmd = json.loads(line)
            except ValueError as e:
                say({"error": "bad command: %s" % e})
                continue
            if cmd.get("quit"):
                return

            err = apply_look_file(cmd.get("look"))
            if err:
                say({"error": "look: %s" % err})
                continue

            clip = cmd.get("clip")
            if clip:
                start = int(clip.get("start", a.start))
                count = max(1, int(clip.get("count", 60)))
                req.clear()
                req.update(cmd)
                req["clip_open"] = True
                clip_t[0] = time.time()
                plate_pause(True)
                pipe = open_noise(start, count)
                try:
                    for i in range(count):
                        buf = pipe.stdout.read(nbytes)
                        if len(buf) < nbytes:
                            break
                        req["last"] = (i == count - 1)
                        yield i, start + i, buf
                finally:
                    try:
                        pipe.stdout.close()
                        pipe.wait(timeout=5)
                    except Exception:
                        pipe.kill()
                    plate_pause(False)
                continue

            # The page can ask for the frames it is about to want - the
            # bookmark buttons, say - and the reader picks them up whenever it
            # has nothing more urgent to do.
            marks = cmd.get("warm")
            if marks:
                with plate_cond:
                    plate["marks"] = [int(m) for m in marks
                                      if not plate_have(int(m))][:24]
                    plate_cond.notify_all()
                if not cmd.get("frame"):
                    say({"warming": len(plate["marks"])})
                    continue

            want = int(cmd.get("frame", a.start))
            t_seek = time.time()
            with plate_cond:
                hit = plate_have(want)
            buf = plate_get(want)
            if buf is None:
                say({"error": "the plate has no frame %d" % want})
                continue
            req.clear()
            req.update(cmd)
            req["plate_ms"] = int((time.time() - t_seek) * 1000)
            req["plate_hit"] = hit
            yield 0, want, buf

    # ---- answering a serve request ----------------------------------------
    # The still goes out as a PNG through ffmpeg so the crop and the lanczos
    # scale are byte-for-byte what the old one-shot path produced - the tuner
    # must not start showing a different picture just because it got faster.
    # A clip goes into ONE encoder for the whole run, so the per-frame cost is
    # the render and nothing else.
    clip_enc = {"proc": None, "path": None, "n": 0}

    def _png(arr, level=0):
        """A PNG, written here rather than by ffmpeg.

        Measured on a 1100x287 still: ffmpeg's own encoder plus the process it
        needs costs about 170 ms, and this costs 30.

        Level 0 on purpose, and measured on a REAL frame rather than on noise -
        noise is the one case where it does not matter, which is exactly why it
        was the wrong thing to benchmark on. On a real preview frame:

            level 0     2 ms     925 kB
            level 1    24 ms     623 kB

        The file crosses a loopback socket to a browser and is then thrown
        away. Spending 22 ms to save 300 kB that never touches a network is the
        wrong trade.
        """
        h, w = arr.shape[0], arr.shape[1]
        rows = np.empty((h, w * 3 + 1), np.uint8)
        rows[:, 0] = 0                     # PNG filter type 0, one per row
        rows[:, 1:] = arr.reshape(h, w * 3)

        def chunk(tag, data):
            c = tag + data
            return (struct.pack(">I", len(data)) + c
                    + struct.pack(">I", zlib.crc32(c)))
        return (b"\x89PNG\r\n\x1a\n"
                + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(rows.tobytes(), level))
                + chunk(b"IEND", b""))

    def _still(path, req_):
        """The frame the page asked for, as a PNG on disk.

        A CROP is done here in numpy: there is no resampling in a crop, so
        there is nothing ffmpeg could do differently, and skipping it turns
        170 ms into 30. A FIT still goes through ffmpeg, because its lanczos is
        what the page has always shown and a different kernel would quietly
        change the thing being judged - but ffmpeg hands back raw pixels now
        and the PNG is written here. Verified bit-identical: 0 of 947100
        values differ from what the old path produced.
        """
        crop, fit = req_.get("crop"), req_.get("fit")
        if crop:
            cw = max(1, min(int(crop[0]), W))
            ch = max(1, min(int(crop[1]), H))
            rgb = np.frombuffer(fbo_final.read(components=3),
                                np.uint8).reshape(H, W, 3)
            # ROUND, do not floor. ffmpeg's crop evaluates (iw-cw)/2 as a
            # double and lrints it, so 2447-760 = 1687 halves to 843.5 and
            # lands on 844. Flooring put the 1:1 view one pixel to the left of
            # where it has always been - invisible in a screenshot, and exactly
            # the kind of thing that matters when the dither is what you are
            # judging. Verified: at dx=-1 the two crops were bit-identical.
            x0 = max(0, min(W - cw, int(round((W - cw) / 2.0))))
            y0 = max(0, min(H - ch, int(round((H - ch) / 2.0))))
            blob = _png(np.ascontiguousarray(rgb[y0:y0 + ch, x0:x0 + cw]))
        else:
            raw = fbo_final.read(components=3)
            vf = req_.get("vf") or ""
            if fit:
                vf = "scale=%d:-1:flags=lanczos" % max(16, min(int(fit), W))
            cmd = [ff, "-hide_banner", "-loglevel", "error",
                   "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "%dx%d" % (W, H),
                   "-i", "pipe:0"]
            if vf:
                cmd += ["-vf", vf]
            cmd += ["-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24",
                    "pipe:1"]
            p = subprocess.run(cmd, input=raw, capture_output=True)
            if p.returncode != 0 or not p.stdout:
                return p.stderr.decode("utf8", "replace")[-400:] or "no output"
            ow = max(16, min(int(fit), W)) if fit else W
            # The height comes from what came back, not from repeating
            # ffmpeg's rounding rule here and hoping the two agree.
            oh, rem = divmod(len(p.stdout), ow * 3)
            if rem or oh < 1:
                return "the scaler returned %d bytes, which is not %d px wide" % (
                    len(p.stdout), ow)
            blob = _png(np.frombuffer(p.stdout, np.uint8).reshape(oh, ow, 3))
        try:
            with open(path, "wb") as fh:
                fh.write(blob)
        except OSError as e:
            return str(e)
        return None

    def serve_emit(fr):
        t0 = time.time()
        out = req.get("out")
        gl_ms = int((t0 - req.get("gl_t0", t0)) * 1000)
        if req.get("clip_open"):
            if clip_enc["proc"] is None:
                clip_enc["path"] = out or os.path.join(WORK_ROOT, "tuner_clip.mp4")
                d = os.path.dirname(os.path.abspath(clip_enc["path"]))
                if d:
                    os.makedirs(d, exist_ok=True)
                # An odd dimension cannot be encoded, and "scale=W:-1" will
                # happily produce one: 1200 wide off a 2447x638 canvas is
                # 313 high, and libx264 then refuses to open at all. Round
                # down to even whatever the caller asked for.
                vf = req.get("vf") or ""
                vf = (vf + "," if vf else "") + "crop=trunc(iw/2)*2:trunc(ih/2)*2"
                cmd = [ff, "-hide_banner", "-loglevel", "error", "-y",
                       "-threads", str(a.threads),
                       "-f", "rawvideo", "-pix_fmt", "rgb24",
                       "-s", "%dx%d" % (W, H), "-framerate", str(FPS),
                       "-i", "pipe:0", "-r", str(FPS)]
                if vf:
                    cmd += ["-vf", vf]
                cmd += ["-c:v", "libx264", "-crf", "20", "-preset", "veryfast",
                        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                        clip_enc["path"]]
                clip_enc["proc"] = popen_polite(cmd, stdin=subprocess.PIPE)
                clip_enc["n"] = 0
            try:
                clip_enc["proc"].stdin.write(fbo_final.read(components=3))
            except (BrokenPipeError, OSError) as e:
                # The encoder rejected something. Report it and stay alive -
                # a tuner whose renderer dies on one bad clip is back to
                # paying the 5.7 s startup.
                req["clip_open"] = False
                clip_enc["proc"] = None
                say({"error": "the clip encoder stopped: %s" % e})
                return
            clip_enc["n"] += 1
            if req.get("last"):
                try:
                    clip_enc["proc"].stdin.close()
                    clip_enc["proc"].wait(timeout=120)
                except Exception:
                    clip_enc["proc"].kill()
                say({"clip": clip_enc["path"], "frames": clip_enc["n"],
                     "fps": round(clip_enc["n"] / max(time.time() - clip_t[0], 1e-6), 1)})
                clip_enc["proc"] = None
            return
        if not out:
            say({"error": "no output path given"})
            return
        d = os.path.dirname(os.path.abspath(out))
        if d:
            os.makedirs(d, exist_ok=True)
        err = _still(out, req)
        if err:
            say({"error": "ffmpeg: %s" % err})
        else:
            say({"frame": fr, "out": out,
                 "ms": int((time.time() - t0) * 1000),
                 "plate_ms": req.get("plate_ms", 0),
                 "cached": bool(req.get("plate_hit")),
                 "gl_ms": gl_ms})

    clip_t = [time.time()]
    served = [0]

    for k, frame, buf in (serve_feed() if a.serve else sequential_feed()):
        t_plate.write(buf)
        served[0] += 1
        req["gl_t0"] = time.time()

        # Animated settings, evaluated for THIS frame. The uniform block below
        # already reads a.sky_gain and friends once per frame, so moving them
        # here is all it takes - nothing downstream knows the difference.
        if tracks:
            for _name, _tr in tracks.items():
                setattr(a, _name, _tr.at(frame))
        if tracks or a.serve:
            # These four are parsed out of their comma strings once, before the
            # loop. An animated one has to be re-parsed here or its uniform
            # never moves. Four tiny arrays against a 25 Mpx frame: free.
            col = np.array([float(x) for x in a.color.split(",")], "f4")
            stone = np.array([float(x) for x in a.stone.split(",")], "f4")
            door_tone = np.array([float(x) for x in a.door_tone.split(",")], "f4")
            film = [float(x) for x in a.film.split(",")]

        # ABSOLUTE segment time, not chunk time. If this were k/FPS the clouds
        # and the camera would restart at every chunk boundary and a chunked
        # final render would visibly jump.
        t = (frame - seg["in"]) / float(FPS)
        mean_norm, plate_mean = plate_arc.get(frame, (0.5, 0.25))
        camx = camera_x(t, a.parallax, a.parallax_period)
        # measured from the segment IN point, so the handles are untouched plate
        # THE HAND-OFF, both ends. The ten seconds either side of the delivered
        # 2:00-4:00 are not padding - they are the transition. The wall grows
        # out of the untouched shared plate over 1:50-2:00 and dissolves back
        # into it over 4:00-4:10, so this surface starts and finishes on exactly
        # the image the other eleven are showing, and whatever the piece does
        # next - the black - happens from a clean common frame.
        scene = (smoother(t, 0.0, max(a.intro, 1e-3))
                 * (1.0 - smoother(t, cut_t - max(a.outro, 1e-3), cut_t)))

        # Belt and braces. Once the dissolve has begun, if the plate itself has
        # gone black then so have we - immediately, on the same frame, however
        # the timing was set. The wall is never lit when the shared image is not.
        past_outro = smoother(t, cut_t - a.outro - 1.0, cut_t - a.outro)
        plate_dark = 1.0 - smooth(plate_mean, a.black_level, a.black_level * 5.0)
        scene *= 1.0 - past_outro * plate_dark

        # The camera settles with it. Handing over on the shared plate while the
        # viewpoint is still swung 200 px off centre would put this wall's idea
        # of straight-ahead out of step with the other eleven at the one moment
        # they all have to agree. It starts centred and ends centred.
        camx *= scene

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
        setu(bg_prog, "uHorizon", a.horizon)
        setu(bg_prog, "uBands", a.bands)
        setu(bg_prog, "uPlateMix", a.plate_mix)
        setu(bg_prog, "uPlateDrive", a.plate_drive)
        setu(bg_prog, "uPlateMean", plate_mean)
        setu(bg_prog, "uPlateContrast", a.plate_contrast)
        setu(bg_prog, "uSaturation", a.saturation)
        setu(bg_prog, "uArcFloor", a.arc_floor)
        setu(bg_prog, "uDoorTone", tuple(door_tone))
        setu(bg_prog, "uDoorGain", a.door_gain)
        setu(bg_prog, "uSpread", a.spread)
        setu(bg_prog, "uSkyGain", a.sky_gain)
        setu(bg_prog, "uOilGain", a.oil_gain)
        setu(bg_prog, "uOilBleed", a.oil_bleed)
        setu(bg_prog, "uPanes", a.panes)
        setu(bg_prog, "uPaneCols", a.pane_cols)
        setu(bg_prog, "uPaneRowsUp", a.pane_rows_upper)
        setu(bg_prog, "uPaneRowsLow", a.pane_rows_lower)
        setu(bg_prog, "uPaneSplit", a.pane_split)
        setu(bg_prog, "uDoorFirst", float(door_first))
        setu(bg_prog, "uBevel", a.bevel)
        setu(bg_prog, "uBevelDepth", a.bevel_depth)
        setu(bg_prog, "uMullion", a.mullion)
        setu(bg_prog, "uMullionDark", a.mullion_dark)
        setu(bg_prog, "uArchUp", a.arch_upper)
        setu(bg_prog, "uArchLow", a.arch_lower)
        setu(bg_prog, "uFanArc", a.fan_arc)
        setu(bg_prog, "uSunDir", (a.sun_az, a.sun_el))
        setu(bg_prog, "uSunShade", a.sun_shade)

        # Which crossings are still ringing at THIS frame. The list is short -
        # two per object over the whole segment - so a scan is cheaper than
        # anything cleverer, and only the freshest few ever reach the shader.
        # Past three lifetimes exp(-3) is 5 %, which is nothing on a wall.
        act = []
        for (ti, ix, iy, irad) in impacts:
            age = t - ti
            if 0.0 <= age <= a.ripple_life * 3.0:
                act.append((age, ix, iy, irad))
        act.sort()
        act = act[:8]
        ripple_buf[:] = 0.0
        for i, (age, ix, iy, irad) in enumerate(act):
            ripple_buf[i] = (ix, iy, age, irad)
        setu(bg_prog, "uImpactN", len(act))
        # GL reports an array uniform under its FIRST ELEMENT's name, so this
        # is "uImpact[0]" on some drivers and "uImpact" on others. Looking for
        # only one of them silently skipped the write, left every impact at
        # (0,0,0,0), and made the whole effect do nothing at all while still
        # reporting 52 crossings quite happily.
        if impact_u is not None:
            impact_u.write(ripple_buf.tobytes())
        setu(bg_prog, "uCanvas", (float(CW), float(CH)))
        setu(bg_prog, "uRippleAmp", a.ripple)
        setu(bg_prog, "uRippleFreq", 6.283185307179586 / max(a.ripple_len, 1.0))
        setu(bg_prog, "uRippleOmega",
             a.ripple_speed * 6.283185307179586 / max(a.ripple_len, 1.0))
        setu(bg_prog, "uRippleSpread", a.ripple_spread)
        setu(bg_prog, "uRippleLife", a.ripple_life)
        setu(bg_prog, "uRippleWarp", a.ripple_warp)
        setu(bg_prog, "uRippleShade", a.ripple_shade)
        setu(bg_prog, "uRippleGrow", a.ripple_grow)
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
        setu(bg_prog, "uParIn", a.par_in)
        setu(bg_prog, "uIntro", scene)
        fbo_bg.use(); ctx.disable(moderngl.DEPTH_TEST | moderngl.BLEND)
        fbo_bg.clear(0, 0, 0, 1); bg_vao.render()

        # ---- objects -------------------------------------------------------
        # ONE pass. Each fragment decides for itself whether it is out beyond
        # the wall (and so visible only through an opening) or inside the room.
        # The old two-pass crossfade drew the object twice at partial alpha and
        # dissolved between the clipped and unclipped copies, so the part of it
        # outside the opening ghosted into existence instead of the object
        # coming through the hole.
        live = []
        for o in objs:
            st = object_state(o, t)
            if st is None:
                continue
            pos, wall_z, scale, alpha, swell, clear, open_idx = st
            if alpha <= 0.004:
                continue
            live.append(dict(o=o, pos=pos, wall_z=wall_z, scale=scale,
                             alpha=alpha, swell=swell, clear=clear,
                             open_idx=open_idx))
        if a.separation > 0:
            resolve_overlaps(live, a.separation)

        draw = fbo_ms if samples else fbo_obj_d
        draw.use()
        draw.clear(0.0, 0.0, 0.0, 0.0)
        if live:
            ctx.enable(moderngl.DEPTH_TEST)
            ctx.enable(moderngl.BLEND)
            ctx.blend_func = (moderngl.ONE, moderngl.ONE_MINUS_SRC_ALPHA)
            t_plate.use(0); t_aux.use(1); tex_bg.use(2); t_oid.use(3)
            setu(obj_prog, "uPlate", 0)
            setu(obj_prog, "uAux", 1)
            setu(obj_prog, "uBg", 2)
            setu(obj_prog, "uOpenId", 3)
            setu(obj_prog, "uEmerge", a.emerge)
            setu(obj_prog, "uEmergeTint", a.emerge_tint)
            setu(obj_prog, "uFog", a.fog)
            # A FLAT ambient level, not the plate sampled per pixel. Reading the
            # plate at the fragment's screen position printed the wall's own
            # pattern onto the metal, which is exactly what "the objects look
            # transparent" was - the wall appearing to show through them.
            setu(obj_prog, "uAmbient", tuple(col * (0.20 + 1.30 * plate_mean)))
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
            setu(obj_prog, "uEnvWarm", tuple(env_warm))
            setu(obj_prog, "uEnvCool", tuple(env_cool))
            setu(obj_prog, "uWallFade", a.wall_fade / float(a.div))
            setu(obj_prog, "uLightDir", (0.35, -0.8, 0.5))
            setu(obj_prog, "uLightCol", (1.0, 0.97, 0.92))
            def draw_objects(group):
              for L in group:
                o = L["o"]
                pos, scale, alpha, swell = L["pos"], L["scale"], L["alpha"], L["swell"]
                pos = pos.copy()
                # objects sit in the room, so they move WITH the camera,
                # opposite to the sky behind them. Enveloped by `clear` too: an
                # object sitting in a window must not be slid out of it by the
                # camera either.
                pos[0] = (pos[0] + camx * a.par_obj * swell * L["clear"]) / a.div
                pos[1] /= a.div
                # The solver is free to slide objects in z to keep them apart.
                # Subtracting that offset back out in the shader leaves the wall
                # test reading the object's TRUE distance from the wall, so
                # de-intersecting can never shove something through it.
                z_bias = float(pos[2]) - L["wall_z"]
                pos[2] = pos[2] / float(a.div)
                sc = scale / a.div
                rw, rp, rb = o["rot_w"], o["rot_p"], o["rot_base"]
                rot = (rb[0] + o["spin"][0] * t + 0.55 * math.sin(rw[0] * t + rp[0]),
                       rb[1] + o["spin"][1] * t + 0.65 * math.sin(rw[1] * t + rp[1]),
                       rb[2] + o["spin"][2] * t + 0.40 * math.sin(rw[2] * t + rp[2]))
                m = model_matrix(pos, sc, rot)
                setu(obj_prog, "uMVP", tuple((proj @ m).T.flatten()))
                setu(obj_prog, "uModel", tuple(m.T.flatten()))
                setu(obj_prog, "uNormalMat", tuple(normal_matrix(m).T.flatten()))
                setu(obj_prog, "uTint", tuple(o["tint"]))
                setu(obj_prog, "uAlpha", float(alpha * scene))
                setu(obj_prog, "uZBias", z_bias / float(a.div))
                setu(obj_prog, "uOpenIdx", float(L["open_idx"]))
                vaos[o["shape"]].render()

            # ---- in front of the pillars, or behind them. NEVER BOTH -------
            # An object spans a range of depths and the pillar is a plane at a
            # single depth, so a per-fragment depth test genuinely slices the
            # object in half when its centre sits near that plane - correct
            # geometry, but it reads as the pillar cutting through it.
            #
            # The side is decided ONCE per object, from its near point, and
            # holds for its whole flight. Deciding it per frame from the
            # object's current depth would be just as correct and would pop the
            # moment it crossed the plane, which is worse. Since depth follows
            # size, this is still "the big ones come past in front".
            # z_push is included, so an object shoved to the back to avoid an
            # intersection also ends up behind the pillar - which is exactly the
            # "one in front, one behind" reading, arrived at for free.
            def side(L):
                return L["o"]["z_near"] + L["o"]["z_push"]
            behind = [L for L in live if side(L) <= a.pillar_z]
            front = [L for L in live if side(L) > a.pillar_z]
            draw_objects(behind)
        # ---- pillars, in the SAME depth buffer as the objects -------------
        # Not a layer pasted over the top: a real occluder at a real depth. An
        # object nearer than uQuadZ fails the pillar's depth test and shows in
        # front of it; one further away is covered. Since an object's depth
        # swings from behind the wall to close to the viewer and back, the same
        # object passes behind a pillar on the way in and in front of it at its
        # nearest point.
        if not a.no_pillars:
            # No depth test: everything drawn before this point is the "behind"
            # group, so the pillar simply covers it, and the "front" group is
            # drawn afterwards and covers the pillar. Sorting by draw order
            # instead of by depth is what keeps any single object wholly on one
            # side of the pillar.
            ctx.disable(moderngl.DEPTH_TEST)
            ctx.enable(moderngl.BLEND)
            ctx.blend_func = (moderngl.ONE, moderngl.ONE_MINUS_SRC_ALPHA)
            ctx.depth_mask = False
            t_aux.use(0); t_plate.use(1); t_mask.use(2)
            for nm, val in (("uAux", 0), ("uPlate", 1), ("uMasks", 2)):
                setu(pil_prog, nm, val)
            # /a.div, and that divisor is the whole bug this line used to have.
            # Everything on the object side is converted to render-pixel units -
            # pos[2], uZBias, uWallFade all get divided - so the pillar's depth
            # has to be divided too or the two are being compared in different
            # units. At --div 2 a pillar asked for at 1500 was competing as if it
            # were at 3000, which is in front of very nearly everything. It would
            # have come out right at --div 1 and wrong in every single preview.
            setu(pil_prog, "uQuadZ", -(a.pillar_z / float(a.div)) / Z_CLIP)
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
            setu(pil_prog, "uTrimLift", a.trim_lift)
            setu(pil_prog, "uPlateMean", plate_mean)
            setu(pil_prog, "uPlateHP", a.plate_hp)
            setu(pil_prog, "uPlateBlur", a.plate_blur)
            setu(pil_prog, "uPlateNoise", a.pillar_noise)
            setu(pil_prog, "uAmbient", a.pillar_ambient)
            setu(pil_prog, "uIntro", scene)
            setu(pil_prog, "uFlutes", a.flutes)
            setu(pil_prog, "uFluteDepth", a.flute_depth)
            setu(pil_prog, "uFluteAO", a.flute_ao)
            setu(pil_prog, "uSunDir", (a.sun_az, a.sun_el))
            setu(pil_prog, "uSaturation", a.saturation)
            setu(pil_prog, "uLevels", a.levels)
            setu(pil_prog, "uGrid", a.dither_grid)
            setu(pil_prog, "uCol0", (float(col0["x"]), float(col0["y"]),
                                     float(col0["w"]), float(col0["h"])))
            setu(pil_prog, "uCol1", (float(col1["x"]), float(col1["y"]),
                                     float(col1["w"]), float(col1["h"])))
            setu(pil_prog, "uCanvasW", float(CW))
            pil_vao.render()
            ctx.depth_mask = True

        # ---- and the objects that belong in front of the pillars ----------
        # Depth testing is back on, so these still sort correctly against each
        # other AND against the behind group, and each one still self-occludes.
        # Only their relationship to the pillar is decided by draw order.
        if live and front:
            ctx.enable(moderngl.DEPTH_TEST)
            ctx.enable(moderngl.BLEND)
            ctx.blend_func = (moderngl.ONE, moderngl.ONE_MINUS_SRC_ALPHA)
            draw_objects(front)

        if samples:
            ctx.copy_framebuffer(fbo_obj, fbo_ms)     # MSAA resolve

        # ---- composite ----------------------------------------------------
        ctx.disable(moderngl.DEPTH_TEST | moderngl.BLEND)
        tex_bg.use(0); tex_obj.use(1); t_mask.use(2)
        for nm, val in (("uBg", 0), ("uObjects", 1), ("uMasks", 2)):
            setu(comp_prog, nm, val)
        fbo_final.use(); fbo_final.clear(0, 0, 0, 1); comp_vao.render()

        if a.serve:
            serve_emit(frame)
        elif sinks:
            # ONE frame, sliced however many ways. The plates therefore carry byte-identical
            # content through the 1000 px overlap, and full brightness in both -
            # which is the single requirement the delivery cannot get wrong. There
            # is no master on disk to drift from, and no second render to disagree.
            rgb = np.frombuffer(fbo_final.read(components=3),
                                np.uint8).reshape(H, W, 3)
            for sk in sinks:
                sk["proc"].stdin.write(np.ascontiguousarray(
                    rgb[:sk["y1"], sk["x0"]:sk["x1"]]).tobytes())
        else:
            sink.stdin.write(fbo_final.read(components=3))

        if not a.serve and (k % 15 == 0 or k == a.count - 1):
            el = time.time() - t_start
            done = k + 1
            fps = done / max(el, 1e-6)
            eta = (a.count - done) / max(fps, 1e-6)
            frac = done / float(max(a.count, 1))
            filled = int(round(frac * 36))
            bar = ("  [%s%s] %3d%%  %5d/%-5d  %4.1f fps  %s left    "
                   % ("#" * filled, "." * (36 - filled), int(frac * 100),
                      done, a.count, fps, hms(eta)))
            if live_bar:
                # ONE line, rewritten in place, so it reads as a bar filling up
                sys.stdout.write("\r" + bar)
                sys.stdout.flush()
            if done >= next_note or done == a.count:
                next_note = done + note_every
                if not live_bar:
                    # nothing to rewrite - output is a file or a pipe - so leave
                    # a readable trail rather than 4000 copies of the same line
                    print(bar.rstrip())
                elif hasattr(sys.stdout, "note"):
                    sys.stdout.note(bar.rstrip())        # log only

    # Serving stops when stdin closes or a quit arrives. There is no sink to
    # flush and no plate stream left open - each request opened and closed its
    # own - so this is the whole shutdown.
    if a.serve:
        with plate_cond:
            plate["stop"] = True
            plate_cond.notify_all()
        reader_thread.join(timeout=10)
        print("serve     stopped after %d request(s), %d plate seek(s), "
              "%d frame(s) decoded" % (served[0], plate["seeks"],
                                       plate["decoded"]))
        return

    if sinks:
        for sk in sinks:
            sk["proc"].stdin.close()
        for sk in sinks:
            sk["proc"].wait()
    else:
        sink.stdin.close(); sink.wait()
    try:
        noise.stdout.close(); noise.wait(timeout=5)
    except Exception:
        noise.kill()
    el = time.time() - t_start
    print("\n\ndone in %.1f s  (%.1f fps)" % (el, a.count / max(el, 1e-6)))
    if sinks:
        for sk in sinks:
            sz = os.path.getsize(sk["path"]) / (1024.0 ** 3) if os.path.isfile(sk["path"]) else 0.0
            print("  %-6s %8.2f GB  %s" % (sk["name"], sz, sk["path"]))
    else:
        print(dst if not a.png else os.path.dirname(dst))


if __name__ == "__main__":
    main()

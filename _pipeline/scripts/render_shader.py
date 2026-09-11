#!/usr/bin/env python3
"""
Render the SW wall at ANY resolution with no licence of any kind.

TouchDesigner Non-Commercial refuses to output above 1280x1280, which rules it
out for a 9788x2552 delivery. This renders the same GLSL on a plain OpenGL 3.3
context instead: your sky/oil shader as the background, low-poly PS1 objects
travelling in through the windows and out through the doors, composited over the
noise plate, written straight to a PNG sequence or an MP4.

A full 9788x2552 frame of the background costs about 0.14 s even on the Intel
iGPU, so the GPU is not the bottleneck - decoding the noise and writing frames is.

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
import subprocess
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import (CFG, MASK_ROOT, REF_ROOT, RENDER_ROOT, WORK_ROOT,  # noqa: E402
                     ffmpeg, source)

SHADERS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "shaders")
CW, CH = CFG["canvas"]["w"], CFG["canvas"]["h"]
FPS = CFG["fps"]


# --------------------------------------------------------------------------- geometry

def _faces(verts, tris):
    """Expand indexed geometry to flat-shaded triangles with face normals."""
    out = []
    for a, b, c in tris:
        p0, p1, p2 = np.array(verts[a]), np.array(verts[b]), np.array(verts[c])
        n = np.cross(p1 - p0, p2 - p0)
        ln = np.linalg.norm(n)
        n = n / ln if ln > 1e-9 else np.array([0.0, 0.0, 1.0])
        for p, uv in zip((p0, p1, p2), ((0, 0), (1, 0), (0.5, 1))):
            out.append([*p, *n, *uv])
    return np.array(out, "f4")


def cube():
    v = [(-1,-1,-1), (1,-1,-1), (1,1,-1), (-1,1,-1), (-1,-1,1), (1,-1,1), (1,1,1), (-1,1,1)]
    t = [(0,2,1),(0,3,2),(4,5,6),(4,6,7),(0,1,5),(0,5,4),
         (2,3,7),(2,7,6),(1,2,6),(1,6,5),(0,4,7),(0,7,3)]
    return _faces(v, t)


def pyramid():
    v = [(-1,-1,-1), (1,-1,-1), (1,-1,1), (-1,-1,1), (0,1.4,0)]
    t = [(0,2,1),(0,3,2),(0,1,4),(1,2,4),(2,3,4),(3,0,4)]
    return _faces(v, t)


def diamond():
    v = [(0,1.5,0), (1,0,0), (0,0,1), (-1,0,0), (0,0,-1), (0,-1.5,0)]
    t = [(0,1,2),(0,2,3),(0,3,4),(0,4,1),(5,2,1),(5,3,2),(5,4,3),(5,1,4)]
    return _faces(v, t)


def sphere(seg=8, ring=6):
    """Deliberately coarse - a PS1 sphere is a faceted ball, not a smooth one."""
    v, t = [], []
    for i in range(ring + 1):
        phi = math.pi * i / ring
        for j in range(seg):
            th = 2 * math.pi * j / seg
            v.append((math.sin(phi) * math.cos(th), math.cos(phi), math.sin(phi) * math.sin(th)))
    for i in range(ring):
        for j in range(seg):
            a = i * seg + j
            b = i * seg + (j + 1) % seg
            c = (i + 1) * seg + j
            d = (i + 1) * seg + (j + 1) % seg
            t += [(a, c, d), (a, d, b)]
    return _faces(v, t)


SHAPES = {"cube": cube, "pyramid": pyramid, "diamond": diamond, "sphere": sphere}


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


# --------------------------------------------------------------------------- scene

def build_objects(openings, seed, count, seg_frames):
    """Each object: in through a window, across the wall, out through a door."""
    rng = np.random.default_rng(seed)
    wins = [o for o in openings if o["kind"] == "WINDOW"]
    doors = [o for o in openings if o["kind"] == "DOOR"]
    if not wins or not doors:
        return []
    names = list(SHAPES)
    objs = []
    for i in range(count):
        w = wins[int(rng.integers(len(wins)))]
        d = doors[int(rng.integers(len(doors)))]
        dur = float(rng.uniform(7.0, 13.0))
        t0 = float(rng.uniform(-dur * 0.5, seg_frames / FPS))
        # a control point out on the wall so the path arcs rather than cutting straight
        mx = (w["cx"] + d["cx"]) * 0.5 + float(rng.uniform(-900, 900))
        my = (w["cy"] + d["cy"]) * 0.5 + float(rng.uniform(-260, 120))
        objs.append(dict(
            shape=names[i % len(names)],
            p0=(float(w["cx"]), float(w["cy"])),
            p1=(mx, my),
            p2=(float(d["cx"]), float(d["cy"])),
            t0=t0, dur=dur,
            size=float(min(w["w"], w["h"])) * float(rng.uniform(0.22, 0.36)),
            spin=(float(rng.uniform(-0.7, 0.7)), float(rng.uniform(-0.9, 0.9)),
                  float(rng.uniform(-0.5, 0.5))),
            phase=float(rng.uniform(0, 6.283)),
            tint=np.array([rng.uniform(0.55, 1.0), rng.uniform(0.6, 1.0),
                           rng.uniform(0.7, 1.0)], "f4"),
        ))
    return objs


def bez(p0, p1, p2, t):
    u = 1 - t
    return (u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0],
            u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1])


def object_state(o, t):
    """Returns (pos, scale, alpha, underness) or None if not on screen yet."""
    p = (t - o["t0"]) / o["dur"]
    if p <= 0.0 or p >= 1.0:
        return None
    x, y = bez(o["p0"], o["p1"], o["p2"], p)
    # emerging from the window, then shrinking away as it reaches the door
    grow = smooth(p, 0.0, 0.10)
    shrink = 1.0 - 0.75 * smooth(p, 0.62, 1.0)
    scale = o["size"] * (0.25 + 0.75 * grow) * shrink
    alpha = smooth(p, 0.0, 0.08) * (1.0 - smooth(p, 0.80, 1.0))
    under = smooth(p, 0.55, 0.95)          # sinks behind the noise on the way out
    z = -400.0 + 800.0 * math.sin(p * math.pi)
    return (np.array([x, y, z], "f4"), scale, alpha, under)


def smooth(x, a, b):
    if b <= a:
        return 1.0 if x >= b else 0.0
    t = min(max((x - a) / (b - a), 0.0), 1.0)
    return t * t * (3 - 2 * t)


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
    ap.add_argument("--objects", type=int, default=14)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--snap", type=float, default=140.0, help="PS1 vertex snap; 0 = off")
    ap.add_argument("--levels", type=float, default=32.0)
    ap.add_argument("--cloud", type=float, default=0.55)
    ap.add_argument("--plate-mix", type=float, default=0.55)
    ap.add_argument("--horizon", type=float, default=0.34)
    ap.add_argument("--bands", type=float, default=12.0)
    ap.add_argument("--spread", type=float, default=0.95)
    ap.add_argument("--sky-gain", type=float, default=1.7)
    ap.add_argument("--oil-gain", type=float, default=2.6)
    ap.add_argument("--color", default="0.38,0.52,0.85")
    a = ap.parse_args()

    if not a.png and not a.mp4:
        a.mp4 = True
    seg = CFG["segment"]
    if a.start is None:
        a.start = seg["in"] - seg["handles"]
    if a.count is None:
        a.count = (seg["out"] - seg["in"] + 1) + 2 * seg["handles"]

    W, H = CW // a.div, CH // a.div
    ff = ffmpeg()
    try:
        import moderngl
    except ImportError:
        sys.exit("moderngl is not installed:   python -m pip install moderngl")

    print()
    print("render    %d x %d  (1/%d)   frames %d..%d  (%d)"
          % (W, H, a.div, a.start, a.start + a.count - 1, a.count))

    ctx = moderngl.create_standalone_context(require=330)
    print("gpu       %s" % ctx.info["GL_RENDERER"])

    def load(name):
        with open(os.path.join(SHADERS, name), "r", encoding="utf-8") as fh:
            return fh.read()

    quad_vs = """#version 330
    in vec2 aP; out vec2 vUv;
    void main(){ vUv = aP*0.5+0.5; gl_Position = vec4(aP,0,1); }"""

    bg_prog = ctx.program(vertex_shader=quad_vs, fragment_shader=load("sky_oil.frag"))
    obj_prog = ctx.program(vertex_shader=load("psx_object.vert"),
                           fragment_shader=load("psx_object.frag"))
    comp_prog = ctx.program(vertex_shader=quad_vs, fragment_shader="""#version 330
    in vec2 vUv; out vec4 fragColor;
    uniform sampler2D uBg, uOver, uUnder, uPlate, uMasks;
    void main(){
        vec3 col   = texture(uBg, vUv).rgb;
        vec4 over  = texture(uOver, vUv);
        vec4 under = texture(uUnder, vUv);
        float pl   = dot(texture(uPlate, vUv).rgb, vec3(0.2126,0.7152,0.0722));

        // under the noise: visible where the plate is dark, hidden where it is bright
        vec3 uc = under.rgb / max(under.a, 1e-4);
        col = mix(col, uc, under.a * (1.0 - clamp(pl*1.5, 0.0, 1.0)) * 0.9);

        // over the noise: screen-blend, so it stays readable while travelling
        col = 1.0 - (1.0 - col) * (1.0 - over.rgb);

        col *= texture(uMasks, vUv).a;        // projectable
        fragColor = vec4(col, 1.0);
    }""")

    quad = ctx.buffer(np.array([-1, -1, 3, -1, -1, 3], "f4").tobytes())
    bg_vao = ctx.simple_vertex_array(bg_prog, quad, "aP")
    comp_vao = ctx.simple_vertex_array(comp_prog, quad, "aP")

    # ---- static textures --------------------------------------------------
    print("textures  masks + opening id ...")
    mask = np.zeros((H, W, 4), np.uint8)
    for ch, name in ((0, "01_WALL"), (1, "02_WINDOW"), (2, "04_DOOR"), (3, "08_PROJECTABLE")):
        mask[:, :, ch] = load_gray(
            ff, os.path.join(MASK_ROOT, "PxDL_SW_MASK_%s_%dx%d.png" % (name, CW, CH)), W, H)
    t_mask = ctx.texture((W, H), 4, mask.tobytes())
    t_mask.filter = (moderngl.LINEAR, moderngl.LINEAR)

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
    fbo_over, tex_over = rgba_fbo()
    fbo_under, tex_under = rgba_fbo()
    depth = ctx.depth_renderbuffer((W, H))
    fbo_over_d = ctx.framebuffer(color_attachments=[tex_over], depth_attachment=depth)
    fbo_under_d = ctx.framebuffer(color_attachments=[tex_under], depth_attachment=depth)
    fbo_out = ctx.simple_framebuffer((W, H), components=3)

    # ---- geometry ---------------------------------------------------------
    vaos = {}
    for name, fn in SHAPES.items():
        data = fn()
        vbo = ctx.buffer(data.tobytes())
        vaos[name] = ctx.vertex_array(obj_prog, [(vbo, "3f 3f 2f", "aPos", "aNrm", "aUv")])

    openings = json.load(open(os.path.join(REF_ROOT, "openings.json")))["openings"]
    objs = [] if a.no_objects else build_objects(openings, a.seed, a.objects, a.count)
    print("objects   %d" % len(objs))

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
    cut = CFG["plates"][0]["w"] - CFG["overlap"]["w"]
    fc = ("[0:v]crop=%d:%d:0:0[l];[l][1:v]hstack=2[s];[s]scale=%d:%d:flags=area"
          % (cut, CH, W, H))
    noise = subprocess.Popen(
        [ff, "-hide_banner", "-loglevel", "error", "-ss", ss, "-i", v1, "-ss", ss, "-i", v2,
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
            ph = int(round(a.preview_width * H / W / 2)) * 2
            sink_args += ["-vf", "scale=%d:%d:flags=area" % (a.preview_width, ph)]
        sink_args += (["-c:v", "libx264", "-crf", "20", "-preset", "veryfast",
                       "-pix_fmt", "yuv420p"] if has264 else ["-c:v", "mpeg4", "-q:v", "4"])
        sink_args += [dst]
        print("out       %s" % dst)
    sink = subprocess.Popen(
        [ff, "-hide_banner", "-loglevel", "error", "-y",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "%dx%d" % (W, H),
         "-framerate", str(FPS), "-i", "pipe:0", "-r", str(FPS)] + sink_args,
        stdin=subprocess.PIPE)

    col = np.array([float(x) for x in a.color.split(",")], "f4")
    nbytes = W * H * 3
    import time
    t_start = time.time()
    print()

    for k in range(a.count):
        buf = noise.stdout.read(nbytes)
        if len(buf) < nbytes:
            print("\nnoise stream ended early at frame %d" % k)
            break
        t_plate.write(buf)

        frame = a.start + k
        # ABSOLUTE segment time, not chunk time. If this were k/FPS the clouds
        # would restart at every chunk boundary and a chunked final render would
        # visibly jump.
        t = (frame - seg["in"]) / float(FPS)
        mean_norm, _ = arc.get(frame, (0.5, 0.5))

        # ---- background ---------------------------------------------------
        t_plate.use(0); t_mask.use(1); t_oid.use(2)
        for nm, val in (("uPlate", 0), ("uMasks", 1), ("uOpenId", 2)):
            if nm in bg_prog:
                bg_prog[nm].value = val
        def setu(prog, name, val):
            if name in prog:
                prog[name].value = val
        setu(bg_prog, "uRes", (float(W), float(H)))
        setu(bg_prog, "uTime", t)
        setu(bg_prog, "uArc", mean_norm)
        setu(bg_prog, "uSkyToOil", smooth(t, 8.0, 40.0))
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
        setu(bg_prog, "uGrid", float(max(1, a.div)))
        fbo_bg.use(); ctx.disable(moderngl.DEPTH_TEST | moderngl.BLEND)
        fbo_bg.clear(0, 0, 0, 1); bg_vao.render()

        # ---- objects, in two passes: over the noise and under it ----------
        proj = ortho(0, W, H, 0, -4000, 4000)      # y down, matching image space
        for fbo, want_under in ((fbo_over_d, False), (fbo_under_d, True)):
            fbo.use()
            fbo.clear(0, 0, 0, 0)
            ctx.enable(moderngl.DEPTH_TEST)
            ctx.enable(moderngl.BLEND)
            ctx.blend_func = (moderngl.ONE, moderngl.ONE_MINUS_SRC_ALPHA)  # premultiplied
            for o in objs:
                st = object_state(o, t)
                if st is None:
                    continue
                pos, scale, alpha, under = st
                w = under if want_under else (1.0 - under)
                aa = alpha * w
                if aa <= 0.004:
                    continue
                pos = pos.copy()
                pos[0] /= a.div; pos[1] /= a.div
                sc = scale / a.div
                rot = (o["spin"][0] * t + o["phase"],
                       o["spin"][1] * t, o["spin"][2] * t)
                m = model_matrix(pos, sc, rot)
                setu(obj_prog, "uMVP", tuple((proj @ m).T.flatten()))
                setu(obj_prog, "uModel", tuple(m.T.flatten()))
                setu(obj_prog, "uSnapRes", a.snap)
                setu(obj_prog, "uLightDir", (0.35, -0.8, 0.5))
                setu(obj_prog, "uLightCol", (1.0, 0.95, 0.85))
                setu(obj_prog, "uAmbient", (0.28, 0.31, 0.4))
                setu(obj_prog, "uTint", tuple(o["tint"]))
                setu(obj_prog, "uEmissive", 0.25 + 0.35 * mean_norm)
                setu(obj_prog, "uAlpha", float(aa))
                setu(obj_prog, "uLevels", a.levels)
                setu(obj_prog, "uGrid", float(max(1, a.div)))
                vaos[o["shape"]].render()

        # ---- composite ----------------------------------------------------
        ctx.disable(moderngl.DEPTH_TEST | moderngl.BLEND)
        tex_bg.use(0); tex_over.use(1); tex_under.use(2); t_plate.use(3); t_mask.use(4)
        for nm, val in (("uBg", 0), ("uOver", 1), ("uUnder", 2), ("uPlate", 3), ("uMasks", 4)):
            setu(comp_prog, nm, val)
        fbo_out.use(); fbo_out.clear(0, 0, 0, 1); comp_vao.render()

        sink.stdin.write(fbo_out.read(components=3))

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

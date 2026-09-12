"""Phoenix de Lumiere - sliders for the look.

    python tune_look.py

Opens a page in your browser with sliders for colour, contrast, the plate, the
oil and the dither. Move one, and the wall re-renders - one real frame through
the real renderer, about three seconds at quarter size. What you see is what
the delivery makes, because it IS the delivery renderer.

Save writes _pipeline/look.json, which render_shader.py loads as its defaults.
So EXPORT.cmd picks up the tuned look with nothing else to remember. Anything
passed on the command line still wins over the file, and --no-look ignores it.

WHY A BROWSER

This python has no tkinter (no tcl/tk was installed with it), and the render PC
cannot pip-install anything - its DNS is broken. A browser is already on every
Windows machine and needs no packages at all. The server binds to 127.0.0.1,
so nothing outside this machine can reach it, and it needs no internet.

    python tune_look.py --div 2        sharper previews, slower
    python tune_look.py --frame 6930   start on the strobe at 3:51
    python tune_look.py --no-browser   just print the URL
"""
import argparse
import json
import os
import re
import socket
import subprocess
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import CFG, PIPELINE, ffmpeg  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
RENDERER = os.path.join(HERE, "render_shader.py")
PAGE = os.path.join(HERE, "tune_look.html")
LOOK = os.path.join(PIPELINE, "look.json")
FPS = CFG["fps"]

# ---------------------------------------------------------------- the sliders --
# Only the look. Geometry, timing and the object choreography are not in here:
# they are decisions, not dials, and they live in the docs.
#
#   kind  float / int   one number
#         rgb           three, written to the CLI as "r,g,b"
#         pair          two, written as "lo,hi"
#
# Ranges are wider than you should need in both directions, because finding out
# what too far looks like is how you know the middle is right. Defaults are NOT
# repeated here - they come from `render_shader.py --print-defaults`, so there is
# one copy of every number and it is the renderer's.
GROUPS = [
    ("Colour", [
        ("color", "rgb", None, None, None,
         "the theme colour the sky is built from"),
        ("saturation", "float", 0.0, 3.0, 0.05,
         "chroma on the wall and pillars. 0 is greyscale"),
        ("stone", "rgb", None, None, None, "pillar colour"),
        ("door_tone", "rgb", None, None, None,
         "the doorways. deliberately not the sky's blue - they read as depth"),
        ("door_gain", "float", 0.0, 3.0, 0.05, "how lit the doorways are"),
    ]),
    ("Sky and contrast", [
        ("sky_gain", "float", 0.0, 5.0, 0.05,
         "base sky brightness. lifts the gradient, not the clouds - "
         "cloud colour is already near 1.0 and only clips"),
        ("horizon", "float", 0.0, 1.0, 0.01, "where the horizon sits on the wall"),
        ("bands", "int", 2, 32, 1, "sky quantisation steps"),
        ("spread", "float", 0.0, 2.0, 0.05, "how far the oil bands across the width"),
        ("cloud", "float", 0.0, 1.0, 0.01, "cloud coverage"),
        ("arc_floor", "float", 0.0, 1.0, 0.01,
         "how far the plate's darkness may pull the wall down. "
         "low values crush the quiet passages"),
        ("exposure", "float", 0.0, 2.0, 0.05, "overall exposure"),
    ]),
    ("The noise plate", [
        ("plate_mix", "float", 0.0, 1.0, 0.01,
         "how hard the plate's dither shades the sky. you must still SEE the "
         "dither through it - the plate is the floor of this whole piece"),
        ("plate_drive", "float", 0.0, 1.5, 0.01,
         "how hard the dither drives the sky bands. 1.0 = the wall IS the "
         "plate, recoloured. 0 = a plain gradient, no texture"),
        ("plate_contrast", "float", 0.0, 8.0, 0.1,
         "how hard the dither swings around the frame's mean. this is what "
         "keeps the near-black opening from collapsing to one flat band"),
    ]),
    ("Oil in the windows", [
        ("oil_gain", "float", 0.0, 15.0, 0.1, "oil intensity before tonemapping"),
        ("oil_sweep", "float", 0.0, 1.0, 0.01,
         "per-opening view-angle swing. above ~0.8 the film goes white at the "
         "opening edges"),
        ("film", "pair", 100.0, 3000.0, 10.0,
         "thin-film thickness in nm. the range decides how many interference "
         "orders the dither sweeps through - narrow reads flat brown, wide is "
         "more colour and more chroma noise"),
    ]),
    ("Dither and pillars", [
        ("levels", "int", 4, 128, 1,
         "colour steps on the wall. 32 is the PSX look. chrome is never quantised"),
        ("dither_grid", "int", 1, 4, 1,
         "dither cell in render pixels. KEEP AT 1 - anything else crawls"),
        ("pillar_gain", "float", 0.0, 3.0, 0.05, "how lit the pillars are"),
        ("trim_lift", "float", 0.0, 1.0, 0.05,
         "capital and plinth against the shaft. 0 = one continuous stone, "
         "which is what it should be"),
    ]),
]

# Moments worth judging the look at, from the plate's own structure - see
# docs/03_48H_PLAN.md. A look that only works at one of these is not finished.
MARKS = [
    ("2:40  near-black", 4800),
    ("3:10  building", 5700),
    ("3:22  on the beat", 6060),
    ("3:44  busy", 6720),
    ("3:51  strobe", 6930),
    ("4:00  hand-off", 7200),
]


def run(args, **kw):
    """subprocess with output captured and no exception on failure."""
    p = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       **kw)
    return p.returncode, (p.stdout or b"").decode("utf-8", "replace")


def defaults():
    rc, out = run([sys.executable, RENDERER, "--print-defaults"])
    if rc != 0:
        sys.exit("could not read the renderer's defaults:\n" + out)
    # --print-defaults prints JSON last; anything before it is noise from the
    # import side of the script.
    i = out.find("{")
    if i < 0:
        sys.exit("the renderer printed no defaults:\n" + out)
    return json.loads(out[i:])


def load_look():
    if not os.path.isfile(LOOK):
        return {}
    try:
        with open(LOOK) as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def split_look(d):
    """look.json as the page wants it: plain values apart from animated ones.

    An animated setting is stored as {"ease": ..., "keys": [[frame, value]]} -
    the same shape render_shader.py reads - so this is only a sorting job.
    """
    static, tracks = {}, {}
    for k, v in (d or {}).items():
        if k.startswith("_"):
            continue
        if isinstance(v, dict) and v.get("keys"):
            tracks[k] = {"ease": v.get("ease", "smooth"), "keys": v["keys"]}
        else:
            static[k] = v
    return static, tracks


def spec(dflt):
    """The slider list, with each default filled in from the renderer."""
    groups = []
    for title, items in GROUPS:
        out = []
        for name, kind, lo, hi, step, blurb in items:
            if name not in dflt:
                # A setting was renamed in render_shader.py. Say so rather than
                # quietly showing a slider that controls nothing.
                print("  warn  no setting called --%s in the renderer, skipping"
                      % name.replace("_", "-"))
                continue
            out.append({"name": name, "kind": kind, "min": lo, "max": hi,
                        "step": step, "blurb": blurb,
                        "flag": "--" + name.replace("_", "-"),
                        "default": dflt[name]})
        if out:
            groups.append({"title": title, "items": out})
    return groups


def to_flag(kind, value):
    """A slider's value as the renderer's command line wants it.

    Takes either form, because both turn up: the page sends [r, g, b] and the
    renderer's own default for the same setting is the string "0.38,0.52,0.85".
    Putting both through here is what lets flags() and /save compare a slider
    against its default at all - iterating the string instead gave each
    character to float() and fell over on the decimal point.
    """
    if kind in ("rgb", "pair"):
        if isinstance(value, str):
            value = [p for p in value.replace(" ", "").split(",") if p]
        pat = "%.3f" if kind == "rgb" else "%g"
        return ",".join(pat % float(v) for v in value)
    if kind == "int":
        return "%d" % int(round(float(value)))
    return "%g" % float(value)


class Tuner(object):
    def __init__(self, div, work):
        self.div = div
        self.work = work
        self.lock = threading.Lock()
        self.ff = ffmpeg()
        self.defaults = defaults()
        self.spec = spec(self.defaults)
        # Every slider name, so /render can reject anything else rather than
        # passing an arbitrary string through to a subprocess.
        self.allowed = {i["name"]: i["kind"]
                        for g in self.spec for i in g["items"]}

    def look_dict(self, params, tracks, changed_only=False):
        """The page's state as a look file.

        changed_only=False for a preview - write everything, so what renders is
        exactly what the sliders say. changed_only=True for Save, so look.json
        stays a short list of decisions somebody can read in a diff rather than
        a dump of every default.
        """
        out = {}
        for g in self.spec:
            for i in g["items"]:
                name, kind = i["name"], i["kind"]
                if name in (tracks or {}):
                    tr = tracks[name]
                    keys = []
                    for k in tr.get("keys") or []:
                        row = [int(k[0]), to_flag(kind, k[1])
                               if kind in ("rgb", "pair") else float(k[1])]
                        if len(k) > 2 and k[2]:
                            row.append(str(k[2]))
                        keys.append(row)
                    if not keys:
                        continue
                    out[name] = {"ease": tr.get("ease", "smooth"), "keys": keys}
                    continue
                if name not in params:
                    continue
                got = to_flag(kind, params[name])
                if changed_only and got == to_flag(kind, i["default"]):
                    continue
                if kind in ("rgb", "pair"):
                    out[name] = got
                elif kind == "int":
                    out[name] = int(round(float(params[name])))
                else:
                    out[name] = float(params[name])
        return out

    def write_look(self, path, look):
        look = dict(look)
        look["_note"] = (
            "Written by tune_look.py. render_shader.py loads this as its "
            "DEFAULTS, so EXPORT.cmd uses it too. A setting with 'keys' is "
            "animated: [frame, value] pairs interpolated with 'ease' "
            "(hold / linear / smooth / ease-in / ease-out), and a third "
            "element on a key overrides the ease for the segment starting "
            "there. Command-line flags still win over plain values; --no-look "
            "ignores this file entirely. Delete it for the built-in look.")
        text = json.dumps(look, indent=1, sort_keys=True)
        # One keyframe per line. Indented, each [frame, value] becomes three or
        # four lines of its own, and a track with ten keys turns look.json into
        # forty lines nobody can read in a diff - and this file is meant to be
        # read in a diff, it is the artistic decision under version control.
        # Only bracket-free arrays collapse, which is exactly the key rows.
        text = re.sub(r"\[\s+((?:[^][{}])*?)\s+\]",
                      lambda m: "[" + " ".join(m.group(1).split()) + "]", text)
        with open(path, "w") as fh:
            print(text, file=fh)

    def argv(self, look_file, frame, div):
        # The preview goes through --look rather than a string of flags,
        # because no command line can express an animated setting - and because
        # it then takes exactly the path the delivery takes.
        return [sys.executable, RENDERER,
                "--div", str(div), "--start", str(frame), "--count", "1",
                "--png", "--out", self.work, "--look", look_file]

    def flags(self, params, tracks):
        """The constant settings as a line you can paste into a terminal.

        Animated ones have no flag to give - they only exist in the look file -
        so they are named rather than silently left out.
        """
        parts, moving = [], []
        for g in self.spec:
            for i in g["items"]:
                name = i["name"]
                if name in (tracks or {}):
                    moving.append(i["flag"])
                    continue
                if name not in params:
                    continue
                got = to_flag(i["kind"], params[name])
                if got != to_flag(i["kind"], i["default"]):
                    parts.append("%s %s" % (i["flag"], got))
        line = " ".join(parts)
        if moving:
            note = ("# animated, so they live in look.json and not on a "
                    "command line: " + ", ".join(sorted(moving)))
            line = (line + "\n" + note) if line else note
        return line

    def render(self, params, tracks, frame, div, width, detail):
        """One frame, as a PNG ready for the page. (bytes, note) or (None, why)."""
        with self.lock:
            for f in os.listdir(self.work):
                if f.lower().endswith(".png"):
                    try:
                        os.remove(os.path.join(self.work, f))
                    except OSError:
                        pass
            # A preview look file of its own, next to the frame. It never
            # touches _pipeline/look.json - moving a slider must not change
            # what the delivery would render until you press Save.
            look_file = os.path.join(self.work, "preview_look.json")
            try:
                self.write_look(look_file, self.look_dict(params, tracks))
            except OSError as e:
                return None, "could not write the preview look file: %s" % e
            rc, out = run(self.argv(look_file, frame, div))
            if rc != 0:
                return None, self._why(out)
            pngs = [os.path.join(self.work, f) for f in os.listdir(self.work)
                    if f.lower().endswith(".png")]
            if not pngs:
                return None, "the renderer wrote no frame:\n" + out[-1500:]
            src = max(pngs, key=os.path.getmtime)

            # ffmpeg does the scaling and cropping: it is here, it is fast, and
            # it saves this tool needing an image library that this python does
            # not have.
            dst = os.path.join(self.work, "preview_out.png")
            if detail:
                # A 1:1 slice. Fitting 2447 px into a browser hides exactly the
                # dither and banding these sliders exist to judge.
                vf = "crop=%d:%d:(iw-%d)/2:(ih-%d)/2" % (width, 420, width, 420)
            else:
                vf = "scale=%d:-1:flags=lanczos" % width
            rc, ffout = run([self.ff, "-y", "-hide_banner", "-loglevel", "error",
                             "-i", src, "-vf", vf, dst])
            if rc != 0 or not os.path.isfile(dst):
                return None, "ffmpeg could not prepare the preview:\n" + ffout[-800:]
            with open(dst, "rb") as fh:
                return fh.read(), None

    @staticmethod
    def _why(out):
        """The useful line out of a failed render, not the whole log."""
        lines = [l.rstrip() for l in out.splitlines() if l.strip()]
        for l in reversed(lines):
            if ("Error" in l or "error" in l or "Traceback" in l
                    or l.startswith("usage:") or "unrecognized" in l):
                return l
        return lines[-1] if lines else "the renderer failed and said nothing"


class Handler(BaseHTTPRequestHandler):
    tuner = None
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass                      # the console is for render errors, not GETs

    def _send(self, code, body, ctype):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError):
            pass                  # the page navigated away mid-render

    def _json(self, code, obj):
        self._send(code, json.dumps(obj), "application/json")

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return {}

    @property
    def route(self):
        """The path with any ?query and #fragment taken off.

        Matching self.path exactly meant "/?r=123" was a 404 - which is what a
        cache-busting reload or anything that appends a parameter looks like.
        """
        return self.path.split("?", 1)[0].split("#", 1)[0] or "/"

    def do_GET(self):
        t = self.tuner
        if self.route in ("/", "/index.html"):
            try:
                with open(PAGE, "rb") as fh:
                    self._send(200, fh.read(), "text/html; charset=utf-8")
            except OSError as e:
                self._send(500, "tune_look.html is missing: %s" % e, "text/plain")
            return
        if self.route == "/state":
            seg = CFG["segment"]
            self._json(200, {
                "groups": t.spec,
                "saved": split_look(load_look())[0],
                "tracks": split_look(load_look())[1],
                "eases": ["hold", "linear", "smooth", "ease-in", "ease-out"],
                "div": t.div,
                "frames": {"lo": seg["in"] - seg["handles"],
                           "hi": seg["out"] + seg["handles"],
                           "fps": FPS},
                "marks": [{"label": l, "frame": f} for l, f in MARKS],
                "lookPath": LOOK,
                "hasLook": os.path.isfile(LOOK),
            })
            return
        self._send(404, "no", "text/plain")

    def do_POST(self):
        t = self.tuner
        b = self._body()
        if self.route == "/render":
            params = b.get("params") or {}
            frame = int(b.get("frame") or 0)
            div = int(b.get("div") or t.div)
            width = max(320, min(2400, int(b.get("width") or 1100)))
            if div not in (1, 2, 4):
                div = t.div
            tracks = b.get("tracks") or {}
            png, why = t.render(params, tracks, frame, div, width,
                                bool(b.get("detail")))
            if png is None:
                self._json(200, {"ok": False, "error": why})
                return
            import base64
            self._json(200, {"ok": True,
                             "png": base64.b64encode(png).decode("ascii"),
                             "flags": t.flags(params, tracks)})
            return

        if self.route == "/save":
            params = b.get("params") or {}
            tracks = b.get("tracks") or {}
            keep = t.look_dict(params, tracks, changed_only=True)
            try:
                t.write_look(LOOK, keep)
            except OSError as e:
                self._json(200, {"ok": False, "error": str(e)})
                return
            n = len([k for k in keep if not k.startswith("_")])
            moving = len([k for k in keep
                          if isinstance(keep[k], dict) and keep[k].get("keys")])
            print("  saved %d setting(s) to %s%s"
                  % (n, LOOK, (" (%d animated)" % moving) if moving else ""))
            self._json(200, {"ok": True, "count": n, "animated": moving,
                             "path": LOOK})
            return

        if self.route == "/revert":
            try:
                if os.path.isfile(LOOK):
                    os.remove(LOOK)
                    print("  removed %s" % LOOK)
            except OSError as e:
                self._json(200, {"ok": False, "error": str(e)})
                return
            self._json(200, {"ok": True})
            return

        self._send(404, "no", "text/plain")


def main():
    ap = argparse.ArgumentParser(description="sliders for the look")
    ap.add_argument("--div", type=int, default=4, choices=(1, 2, 4),
                    help="preview scale. 4 is about 3 s a frame, 2 is sharper "
                         "and slower")
    ap.add_argument("--frame", type=int, default=None,
                    help="which frame to open on (default: 3:10, where there is "
                         "something to look at)")
    ap.add_argument("--port", type=int, default=0, help="0 = pick a free one")
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()

    work = os.path.join(PIPELINE, "..", "work", "tune")
    work = os.path.abspath(os.environ.get("PXDL_TUNE_WORK") or work)
    os.makedirs(work, exist_ok=True)

    print()
    print("=" * 70)
    print(" PHOENIX DE LUMIERE  -  look")
    print("=" * 70)
    tuner = Tuner(a.div, work)
    Handler.tuner = tuner

    # 127.0.0.1, never 0.0.0.0: this serves a tool that runs a renderer, and
    # nothing outside this machine has any business reaching it.
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    port = srv.socket.getsockname()[1]
    frame = a.frame if a.frame is not None else 5700
    url = "http://127.0.0.1:%d/#frame=%d" % (port, frame)

    print()
    print("  sliders   %s" % url)
    print("  previews  1/%d scale, one real frame through render_shader.py" % a.div)
    print("  saves to  %s" % LOOK)
    print("            render_shader.py reads that as its defaults, so")
    print("            EXPORT.cmd picks up whatever you save here.")
    print()
    print("  Ctrl+C to stop.")
    print()
    sys.stdout.flush()
    if not a.no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print()
        print("  stopped.")
    finally:
        srv.server_close()


if __name__ == "__main__":
    try:
        main()
    except socket.error as e:
        sys.exit("could not start the local server: %s" % e)

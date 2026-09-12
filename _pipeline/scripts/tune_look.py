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

    def argv(self, params, frame, div):
        a = [sys.executable, RENDERER,
             "--div", str(div), "--start", str(frame), "--count", "1",
             "--png", "--out", self.work]
        for name, value in params.items():
            kind = self.allowed.get(name)
            if kind is None:
                continue
            a += ["--" + name.replace("_", "-"), to_flag(kind, value)]
        return a

    def flags(self, params):
        """The same settings as a line you can paste into a terminal."""
        parts = []
        for g in self.spec:
            for i in g["items"]:
                name = i["name"]
                if name not in params:
                    continue
                got = to_flag(i["kind"], params[name])
                if got != to_flag(i["kind"], i["default"]):
                    parts.append("%s %s" % (i["flag"], got))
        return " ".join(parts)

    def render(self, params, frame, div, width, detail):
        """One frame, as a PNG ready for the page. (bytes, note) or (None, why)."""
        with self.lock:
            for f in os.listdir(self.work):
                if f.lower().endswith(".png"):
                    try:
                        os.remove(os.path.join(self.work, f))
                    except OSError:
                        pass
            rc, out = run(self.argv(params, frame, div))
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
                "saved": load_look(),
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
            png, why = t.render(params, frame, div, width, bool(b.get("detail")))
            if png is None:
                self._json(200, {"ok": False, "error": why})
                return
            import base64
            self._json(200, {"ok": True,
                             "png": base64.b64encode(png).decode("ascii"),
                             "flags": t.flags(params)})
            return

        if self.route == "/save":
            params = b.get("params") or {}
            keep = {}
            for g in t.spec:
                for i in g["items"]:
                    name = i["name"]
                    if name not in params:
                        continue
                    got = to_flag(i["kind"], params[name])
                    if got == to_flag(i["kind"], i["default"]):
                        continue          # only what you actually changed
                    if i["kind"] in ("rgb", "pair"):
                        keep[name] = got
                    elif i["kind"] == "int":
                        keep[name] = int(round(float(params[name])))
                    else:
                        keep[name] = float(params[name])
            keep["_note"] = ("Written by tune_look.py. render_shader.py loads "
                             "this as its DEFAULTS, so EXPORT.cmd uses it too. "
                             "Command-line flags still win; --no-look ignores "
                             "this file. Delete it to go back to the built-in "
                             "look.")
            try:
                with open(LOOK, "w") as fh:
                    json.dump(keep, fh, indent=1, sort_keys=True)
            except OSError as e:
                self._json(200, {"ok": False, "error": str(e)})
                return
            n = len([k for k in keep if not k.startswith("_")])
            print("  saved %d setting(s) to %s" % (n, LOOK))
            self._json(200, {"ok": True, "count": n, "path": LOOK})
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

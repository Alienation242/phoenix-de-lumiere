# Moving the project to another machine

Nothing in `_pipeline/` is machine-specific. Every script resolves the project
root from its own location and reads constants from `_pipeline/project.json`.
Copy the folder anywhere, on any PC, and it works.

---

## What to copy

```
PhoenixDeLumiere/
  RawNoise/          the two plates + mask + venue screenshot   (1.6 GB)
  _pipeline/         docs, masks, reference, scripts, shaders    (7 MB)
  work/              your TD project and 3D assets
```

`render/` and `deliver/` do **not** travel — they are regenerated output. Leave
them behind.

---

## Point the big folders at fast storage

The three output roots are environment variables. Set them before running
anything, and the project writes to the external drive instead of `C:`.

```powershell
$env:PXDL_WORK_ROOT    = "E:\PxDL\work"
$env:PXDL_RENDER_ROOT  = "E:\PxDL\render"
$env:PXDL_DELIVER_ROOT = "E:\PxDL\deliver"
```

To make them stick for your user account:

```powershell
[Environment]::SetEnvironmentVariable('PXDL_RENDER_ROOT', 'E:\PxDL\render', 'User')
[Environment]::SetEnvironmentVariable('PXDL_DELIVER_ROOT','E:\PxDL\deliver','User')
[Environment]::SetEnvironmentVariable('PXDL_WORK_ROOT',   'E:\PxDL\work',   'User')
```

Open a new terminal afterwards. `check_environment.ps1` prints where everything
currently points, so you can confirm it took.

All overrides:

| variable | effect |
|---|---|
| `PXDL_ROOT` | override the project root (normally derived from the script location) |
| `PXDL_FFMPEG` | full path to an ffmpeg.exe |
| `PXDL_WORK_ROOT` | where proxies, previews and your TD project live |
| `PXDL_RENDER_ROOT` | where the master sequence is written |
| `PXDL_DELIVER_ROOT` | where the plates are written |

---

## Space you need

For 2460 frames at 9788 × 2552, measured on this material:

| | per frame | total |
|---|---|---|
| master, 16-bit PNG | ~30 MB | **~72 GB** |
| ProRes 4444, both plates | ~18 MB | **~43 GB** |
| 16-bit PNG plates, both | ~37 MB | ~89 GB |
| DPX 10-bit uncompressed | 100 MB | 240 GB — don't |

Budget **~150 GB** with headroom. A 1 TB NVMe is the right purchase; 2 TB if you
want to keep versions.

---

## First run on a new machine

```powershell
cd <project>\_pipeline\scripts

# 1. does this machine have what it needs?
.\check_environment.ps1

# 2. rebuild the mask assets (optional - they are already in the repo)
python build_masks.py

# 3. get your working plate
.\extract_segment.ps1 -Div 4
```

`check_environment.ps1` reports:

- VRAM against the 25 Mpx canvas, and which render scale that implies
- free space on whatever the output roots point at, against the real estimates
- which ffmpeg encoders are available
- the TouchDesigner and Resolve licence caps
- Python + numpy
- that all source files and 31 mask files are present

It exits non-zero if something is actually missing, and prints warnings for
things that merely need a decision.

---

## ffmpeg

The scripts look for ffmpeg in this order:

1. `$env:PXDL_FFMPEG`
2. `ffmpeg` on `PATH`
3. `_pipeline\bin\ffmpeg.exe`
4. any TouchDesigner install's `bin\ffmpeg.exe`
5. `C:\ffmpeg\bin\ffmpeg.exe`

**Get a full build.** The one bundled with TouchDesigner decodes everything but
can only encode png / dpx / ffv1 / mjpeg / mpeg4 — no ProRes, no DNxHR, no
H.264. Download the `full` build from gyan.dev or `win64-gpl` from BtbN, unzip
it, and either put it on `PATH` or drop `ffmpeg.exe` into `_pipeline\bin\`.
Dropping it in `_pipeline\bin\` means it travels with the project, which is the
least surprising option for a render node.

---

## A render node with no TouchDesigner licence

If you render on a second machine, remember TD needs its own licence there. Two
ways around it:

- **Render the frames on the licensed machine**, copy the master sequence to the
  render node, and use it only for slicing and encoding. `make_delivery.ps1`,
  `master_to_plates.ps1` and `verify_plates.py` need nothing but ffmpeg, Python
  and numpy.
- Or move the TD licence — Derivative lets you deactivate and reactivate on
  another machine through your account.

Slicing and encoding is the slow, unattended part anyway, and it is exactly the
part that needs no licence.

---

## Reproducibility

Every generated asset can be rebuilt from the three source files:

| asset | rebuilt by |
|---|---|
| all 31 masks, region tables, opening ID map, opening SDF | `build_masks.py` |
| `noise_arc.csv` | `analyse_arc.py` |
| working proxies | `extract_segment.ps1` |

So if anything in `_pipeline/masks` or `_pipeline/reference` is ever lost or
looks wrong, delete it and run the script. Nothing in there is hand-made.

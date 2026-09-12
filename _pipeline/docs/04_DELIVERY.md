# Delivery — the short version

**Double-click `EXPORT.cmd` in the project root and pick a number.** That is the
whole thing. Everything below is only for when something is unusual.

```
  [1] Deliver      full 9788x2552, ProRes 422 HQ      51 GB    ~2 h
  [2] DeliverMax   full 9788x2552, ProRes 4444       250 GB    ~2.7 h
  [3] Draft        half size, H.264, full length       2 GB    ~25 min
  [4] Proof        half size, H.264, 25 seconds      170 MB    ~2 min
```

Run **Proof** first. If it looks right, run **Deliver**.

The script checks the machine, renders, verifies the result and writes
`DELIVERY_NOTES.txt` next to the files. It refuses to start if anything is
missing and refuses to report success if the plates do not verify.

---

## Two plates, or one stitched file?

`-Layout` decides. Default is `Plates`.

| | what you get | when |
|---|---|---|
| `Plates` | `SPSW1` 7200×2552 and `SPSW2` 3588×2552 | **the default.** Matches the noise plates that were supplied, so it drops straight into the same slots |
| `Stitched` | one `CANVAS` 9788×2552 file | if the producer would rather cut the whole wall and slice it themselves |
| `Both` | all three, from one render | **if you are not sure what they want.** One render, so it costs encode time and disk, not render time |

```powershell
.\_pipeline\scripts\export_delivery.ps1 -Preset Deliver -Layout Both
```

The two plates **overlap by 1000 px** and both carry **full brightness** through
it. That is deliberate — the soft edge is applied by the projector blend
downstream. Do not ramp the files.

---

## Which mask?

There are two descriptions of this facade, and they do not agree everywhere.
`-Masks` picks one; the menu asks if you do not.

| | what it is |
|---|---|
| `Layer` | **the default.** The authored colour-coded mask supplied with the project |
| `Noise` | the same facade traced out of the shared noise plate, which draws its own windows, doors and columns |

```powershell
.\_pipeline\scripts\export_delivery.ps1 -Preset Proof -Masks Noise
```

Each goes to **its own folder** (`Deliver_MASK-LAYER` / `Deliver_MASK-NOISE`)
and every file carries the tag in its name, so both can be rendered one after
the other and nothing is overwritten or mixed up. Which one to send is an
artistic call — **`docs/05_MASKS.md` has the measurements and the pictures.**

---

## When the high-quality noise arrives

The supplied mp4s are 0.019 bits/pixel. The wall now uses the plate as an
*input* — the dither drives the sky bands and the oil thickness — so its
compression shows more than it used to. Switching to the masters is worth it.

It can arrive either way, and both are handled:

**1. Put the paths into `project.json` → `source_hq`.**

Either one stitched file:
```json
"source_hq": { "STITCHED": "E:/PxDL/masters/PxDL_SW_noise_9788x2552.mov" }
```
or the two plates:
```json
"source_hq": {
  "SPSW1": "E:/PxDL/masters/PxDL_SW_SPSW1.mov",
  "SPSW2": "E:/PxDL/masters/PxDL_SW_SPSW2.mov"
}
```
`STITCHED` wins if both are filled in.

> Edit that file in a plain text editor. PowerShell's `Set-Content -Encoding utf8`
> writes a byte-order mark, which the scripts cannot read.

**2. Validate them.** About thirty seconds.
```powershell
python _pipeline\scripts\check_hq.py
```
It answers four questions, and the second is the one that matters:

- **Geometry** — 7200×2552 / 3588×2552 (or 9788×2552 stitched), 30 fps, 18000 frames.
- **Frame alignment** — decodes the same frame from the mp4 and the master at
  offsets −3…+3 and reports which actually matches. It must be 0. A one-frame
  offset would put this wall out of step with the other eleven surfaces while
  looking completely fine on its own.
- **Levels** — a master is very often tagged full-range where the mp4 was
  limited. That shifts every luma value, and since the plate *drives* the image,
  it changes the look, not just the brightness.
- **The arc** — whether `noise_arc.csv` needs remeasuring.

**3. Remeasure the arc if it says to.** About four minutes.
```powershell
python _pipeline\scripts\analyse_arc.py --hq
```
`noise_arc.csv` supplies `uPlateMean`, which centres the dither. Measured on one
source and rendered from another, the wall's contrast is wrong everywhere.

**4. Export with `-HQ`.**
```powershell
.\_pipeline\scripts\export_delivery.ps1 -Preset Deliver -HQ
```
`-HQ` runs `check_hq.py` as part of preflight and will not start if it fails.

### Ask for ProRes, not uncompressed

Genuinely uncompressed at 7200×2552 is about **1.1 GB/s** to read both plates at
30 fps — beyond most drives — and roughly **660 GB per plate** for the ten-minute
loop. ProRes 4444 or 422 HQ is almost certainly what is actually wanted, and is
what this pipeline is happiest with. Worth saying before they export something
unusable.

---

## The overlap check, and what its numbers mean

The two plates are cut from **the same frame in memory**, so their content is
identical by construction. What the check measures afterwards is the two files
having been *encoded separately at different widths* — different macroblock
grids, different bit allocation — so every lossy codec leaves a little noise in
the overlap.

Measured on this content:

| codec | overlap difference | tolerance used |
|---|---|---|
| ProRes 4444 | **0.0000** (4:4:4, exact) | 0.5 |
| ProRes 422 HQ | 1.04 | 2.0 |
| DNxHR HQX | 1.62 | 3.0 |
| H.264 | 2.55 | 5.0 |

For scale: **the supplied noise plates differ from each other by 1.54** in their
own overlap. A real fault — a wrong crop, a frame offset — reads in the tens,
not the ones.

---

## If something goes wrong

The script writes a log next to the output. Nothing is ever sent anywhere, so a
failed run is safe to fix and repeat.

| it says | it means |
|---|---|
| `not enough disk` | it tells you the number it needs. Point `PXDL_DELIVER_ROOT` at a bigger drive |
| `noise_arc.csv only covers …` | run `python analyse_arc.py` (add `--hq` if using masters) |
| `encoder … missing` | this ffmpeg is a partial build. Get a full one from gyan.dev |
| `moderngl missing` | `python -m pip install moderngl` |
| overlap `MISMATCH` | see the table above before assuming the worst — but do not send the files until it passes |

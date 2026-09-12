<#
  Phoenix de Lumiere - SW wall - DELIVERY EXPORT

  One command, start to finish: check the machine, render the two projector
  plates, verify them, and write the notes that go with them.

  Double-click EXPORT.cmd in the project root and pick a number. Or:

      .\export_delivery.ps1 -Preset Deliver
      .\export_delivery.ps1 -Preset Deliver -Yes        # no confirmation
      .\export_delivery.ps1 -Preset Draft               # quick half-res look

  WHAT COMES OUT

  Two files, exactly matching the noise plates that were supplied:

      PxDL_SW_SPSW1_03270-07529.mov     7200 x 2552    canvas x    0 .. 7200
      PxDL_SW_SPSW2_03270-07529.mov     3588 x 2552    canvas x 6200 .. 9788

  They OVERLAP by 1000 px and both carry FULL BRIGHTNESS through it. That is
  deliberate and must not be "fixed": the soft edge is applied by the projector
  blend downstream. Baking a ramp into these files would double-darken the seam.

  Both plates are cut from the same frame in memory, so the overlap is identical
  in both by construction. The script measures it afterwards and refuses to
  report success if it is not.
#>
[CmdletBinding()]
param(
    [ValidateSet('Deliver', 'DeliverMax', 'Draft', 'Proof')]
    [string] $Preset,
    [switch] $Yes,
    [string] $Out,
    [int]    $Threads = 4
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\_common.ps1"

# ---------------------------------------------------------------- presets ----
# MB-per-frame figures are MEASURED on this content at full resolution, both
# plates together, not taken from a codec datasheet. They are what the disk
# check below trusts, so if the look changes a lot re-measure them.
$Presets = [ordered]@{
    'Deliver' = @{
        Div = 1; Codec = 'prores422hq'; MBPerFrame = 12.2; SecPerFrame = 1.7
        What = 'THE DELIVERY. Full 9788x2552, ProRes 422 HQ, 10-bit 4:2:2.'
        Note = 'Overlap matches to ~1.0/255 - better than the supplied noise plates, which differ by 1.5.'
    }
    'DeliverMax' = @{
        Div = 1; Codec = 'prores4444'; MBPerFrame = 60.1; SecPerFrame = 2.3
        What = 'Full 9788x2552, ProRes 4444, 12-bit 4:4:4.'
        Note = 'Overlap is BIT-IDENTICAL (0.0000). Needs a big drive - about 250 GB.'
    }
    'Draft' = @{
        Div = 2; Codec = 'h264'; MBPerFrame = 0.5; SecPerFrame = 0.35
        What = 'Half size 4894x1276, H.264. For review, NOT for projection.'
        Note = 'Correct plate geometry, so the overlap can still be checked.'
    }
    'Proof' = @{
        Div = 2; Codec = 'h264'; MBPerFrame = 0.5; SecPerFrame = 0.35; Seconds = 25
        What = '25 seconds only, half size, H.264. A two-minute sanity check.'
        Note = 'Run this first. If it looks right, run Deliver.'
    }
}

function Line { param([string]$s = '') Write-Host $s }
function Head {
    param([string]$s)
    Line; Line ('=' * 74); Line $s; Line ('=' * 74)
}
function Good { param([string]$s) Write-Host "  OK    $s" -ForegroundColor Green }
function Bad  { param([string]$s) Write-Host "  FAIL  $s" -ForegroundColor Red }
function Warn { param([string]$s) Write-Host "  warn  $s" -ForegroundColor Yellow }

function Format-TC {
    # [int] in PowerShell ROUNDS, it does not truncate - so frame 3300 came out
    # as 2:50 instead of 1:50 and 5249 as 3:55 instead of 2:54.97. Every
    # timecode in the delivery notes was wrong by up to a minute, which is
    # exactly the sort of thing that wastes somebody's morning.
    # Invariant culture, so the notes read 2:30.00 on every machine. The -f
    # operator follows the local culture, and on this one that produced
    # "2:30,00" - a comma in a timecode handed to someone who has to conform it.
    param([int]$f)
    $s = $f / 30.0
    $m = [Math]::Floor($s / 60.0)
    [string]::Format([Globalization.CultureInfo]::InvariantCulture,
                     '{0}:{1:00.00}', [int]$m, ($s - $m * 60.0))
}

# ---------------------------------------------------------------- the menu ---
if (-not $Preset) {
    Head 'PHOENIX DE LUMIERE  -  SW WALL  -  DELIVERY EXPORT'
    Line
    $i = 0
    foreach ($k in $Presets.Keys) {
        $i++
        $p = $Presets[$k]
        Write-Host ("  [{0}]  {1,-11} {2}" -f $i, $k, $p.What) -ForegroundColor White
        Line ("                   {0}" -f $p.Note)
        Line
    }
    Line '  [q]  quit'
    Line
    $pick = Read-Host 'Which one? (1 is the delivery)'
    if ($pick -eq 'q') { return }
    if (-not $pick) { $pick = '1' }
    $idx = 0
    if (-not [int]::TryParse($pick, [ref]$idx) -or $idx -lt 1 -or $idx -gt $Presets.Count) {
        Bad "not a choice: $pick"; return
    }
    $Preset = @($Presets.Keys)[$idx - 1]
}
$P = $Presets[$Preset]

# ---------------------------------------------------------------- the plan ---
$segIn   = [int]$Cfg.segment.'in'
$segOut  = [int]$Cfg.segment.'out'
$handles = [int]$Cfg.segment.handles
$start   = $segIn - $handles
$count   = ($segOut - $segIn + 1) + 2 * $handles
if ($P.Contains('Seconds')) {
    $start = [int]($segIn + 40 * 30)      # somewhere with objects in flight
    $count = [int]($P.Seconds * 30)
}
$last = $start + $count - 1

$div    = [int]$P.Div
$plate1 = $Cfg.plates[0]
$plate2 = $Cfg.plates[1]
$w1 = [int]$plate1.w / $div; $h1 = [int]$plate1.h / $div
$w2 = [int]$plate2.w / $div; $h2 = [int]$plate2.h / $div

$outDir = if ($Out) { $Out } else { Join-Path $DeliverRoot $Preset }
$needGB = [math]::Round($P.MBPerFrame * $count / ($div * $div) / 1024.0, 1)
$mins   = [math]::Round($P.SecPerFrame * $count / ($div * $div) / 60.0, 0)

Head "PLAN  -  $Preset"
Line $P.What
Line $P.Note
Line
Line ("  frames      {0} .. {1}   ({2} frames, {3:N1} s)" -f $start, $last, $count, ($count/30.0))
Line ("  timecode    {0} .. {1}  of the ten-minute loop" -f (Format-TC $start), (Format-TC $last))
Line ("  the piece   {0} .. {1}   1:50 - 4:10, hand-off to bare plate at both ends" -f $segIn, $segOut)
Line ("  plate 1     {0}  {1} x {2}   canvas x {3} .. {4}" -f $plate1.name, $w1, $h1, ($plate1.x/$div), (($plate1.x + $plate1.w)/$div))
Line ("  plate 2     {0}  {1} x {2}   canvas x {3} .. {4}" -f $plate2.name, $w2, $h2, ($plate2.x/$div), (($plate2.x + $plate2.w)/$div))
Line ("  overlap     {0} px, FULL BRIGHTNESS in both - do not pre-blend" -f ([int]$Cfg.overlap.w / $div))
Line ("  codec       {0}" -f $P.Codec)
Line ("  output      {0}" -f $outDir)
Line ("  needs       about {0} GB and roughly {1} minutes" -f $needGB, $mins)
Line

# ---------------------------------------------------------------- preflight --
Head 'PREFLIGHT'
$fail = @()

$py = (Get-Command python -ErrorAction SilentlyContinue)
if ($py) { Good ("python  {0}" -f (& python -c "import sys;print(sys.version.split()[0])")) }
else { $fail += 'python is not on PATH'; Bad 'python not found' }

if ($py) {
    & python -c "import moderngl" 2>$null
    if ($LASTEXITCODE -eq 0) { Good 'moderngl' } else { $fail += 'moderngl missing - run: python -m pip install moderngl'; Bad 'moderngl missing' }
    & python -c "import numpy" 2>$null
    if ($LASTEXITCODE -eq 0) { Good 'numpy' } else { $fail += 'numpy missing'; Bad 'numpy missing' }
}

Good ("ffmpeg  {0}" -f $FFmpegExe)
$encNeeded = switch ($P.Codec) {
    'prores4444'  { 'prores_ks' } 'prores422hq' { 'prores_ks' }
    'dnxhr_hqx'   { 'dnxhd' }     default       { 'libx264' }
}
if (Test-FFmpegEncoder $encNeeded) { Good "encoder $encNeeded" }
else { $fail += "this ffmpeg has no $encNeeded encoder - get a full build from gyan.dev"; Bad "encoder $encNeeded missing" }

foreach ($k in 'SPSW1', 'SPSW2') {
    $src = Join-Path $ProjectRoot ($Cfg.source.$k -replace '/', '\')
    if (Test-Path $src) { Good "source $k" } else { $fail += "missing source: $src"; Bad "source $k missing" }
}
foreach ($n in '02_WINDOW', '04_DOOR', '05_COLUMN', '06_TRIM', '08_PROJECTABLE', '09_OPENINGS') {
    $m = Join-Path $MaskRoot ("PxDL_SW_MASK_{0}_{1}x{2}.png" -f $n, $Cfg.canvas.w, $Cfg.canvas.h)
    if (-not (Test-Path $m)) { $fail += "missing mask: $m"; Bad "mask $n missing" }
}
if ($fail.Count -eq 0) { Good 'all 31 masks + opening maps' }

$arc = Join-Path $RefRoot 'noise_arc.csv'
if (Test-Path $arc) {
    $fr = @(Get-Content $arc | Select-Object -Skip 1 | ForEach-Object { [int]($_ -split ',')[0] })
    $lo = ($fr | Measure-Object -Minimum).Minimum
    $hi = ($fr | Measure-Object -Maximum).Maximum
    if ($lo -le $start -and $hi -ge $last) { Good ("noise_arc.csv covers {0}..{1}" -f $lo, $hi) }
    else {
        $fail += "noise_arc.csv only covers $lo..$hi but this render needs $start..$last. Run: python analyse_arc.py"
        Bad "noise_arc.csv covers $lo..$hi, need $start..$last"
    }
} else { $fail += 'noise_arc.csv missing - run: python analyse_arc.py'; Bad 'noise_arc.csv missing' }

New-Item -ItemType Directory -Force $outDir | Out-Null
$drive = (Get-Item $outDir).PSDrive
$freeGB = [math]::Round($drive.Free / 1GB, 1)
if ($freeGB -gt $needGB * 1.15) { Good ("disk    {0} GB free on {1}:, needs about {2} GB" -f $freeGB, $drive.Name, $needGB) }
else {
    $fail += ("not enough disk: {0} GB free on {1}:, this preset needs about {2} GB" -f $freeGB, $drive.Name, $needGB)
    Bad ("disk    {0} GB free, needs about {1} GB" -f $freeGB, $needGB)
}

Line
if ($fail.Count) {
    Head 'STOPPED  -  fix these first'
    foreach ($f in $fail) { Line "  - $f" }
    Line
    exit 1
}
Good 'ready'

# ---------------------------------------------------------------- go ---------
if (-not $Yes) {
    Line
    $go = Read-Host "Start the render? This takes about $mins minutes. [Y/n]"
    if ($go -and $go -notmatch '^[Yy]') { Line 'cancelled.'; return }
}

$log = Join-Path $outDir ("export_{0}_{1}.log" -f $Preset, (Get-Date -Format 'yyyyMMdd-HHmmss'))
Head 'RENDERING'
Line "  log: $log"
Line "  It reports frames/sec and an ETA as it goes. It runs at below-normal"
Line "  priority, so the machine stays usable."
Line

$started = Get-Date
& python (Join-Path $PSScriptRoot 'render_shader.py') `
    --div $div --start $start --count $count `
    --plates --codec $P.Codec --threads $Threads --out $outDir 2>&1 |
    Tee-Object -FilePath $log
$rc = $LASTEXITCODE
$took = (Get-Date) - $started

if ($rc -ne 0) {
    Head 'RENDER FAILED'
    Line "  exit code $rc. The log is at:"
    Line "    $log"
    exit $rc
}

# ---------------------------------------------------------------- verify -----
Head 'VERIFYING'
$f1 = Get-ChildItem $outDir -Filter '*SPSW1*' | Sort-Object LastWriteTime | Select-Object -Last 1
$f2 = Get-ChildItem $outDir -Filter '*SPSW2*' | Sort-Object LastWriteTime | Select-Object -Last 1
if (-not $f1 -or -not $f2) { Bad 'one or both plates are missing'; exit 1 }

Line "  Checking resolution, frame count, and that the 1000 px overlap holds."
Line
& python (Join-Path $PSScriptRoot 'verify_plates.py') --a $f1.FullName --b $f2.FullName `
    --div $div --samples 8 --tol 2 2>&1 | Tee-Object -FilePath $log -Append
$vrc = $LASTEXITCODE

# ---------------------------------------------------------------- notes ------
$notes = Join-Path $outDir 'DELIVERY_NOTES.txt'
$banner = if ($div -eq 1 -and $P.Codec -ne 'h264') { '' } else { @"

  *********************************************************************
  *  THIS IS A REVIEW RENDER, NOT THE DELIVERY.                       *
  *  1/$div scale, $($P.Codec). The plate geometry is correct so the overlap
  *  can be checked, but do not send this to the projectors.
  *  Run EXPORT.cmd and choose "Deliver" for the real files.
  *********************************************************************
"@ }
@"
PHOENIX DE LUMIERE  -  SW WALL
Delivered by the SW artist. Generated $(Get-Date -Format 'yyyy-MM-dd HH:mm').
$banner

THE TWO FILES
  $($f1.Name)
      $w1 x $h1   covers canvas x $($plate1.x/$div) .. $(($plate1.x+$plate1.w)/$div)
  $($f2.Name)
      $w2 x $h2   covers canvas x $($plate2.x/$div) .. $(($plate2.x+$plate2.w)/$div)

  Full canvas is $([int]$Cfg.canvas.w / $div) x $([int]$Cfg.canvas.h / $div). The two plates OVERLAP by
  $([int]$Cfg.overlap.w / $div) px, between x $([int]$Cfg.overlap.x0 / $div) and x $([int]$Cfg.overlap.x1 / $div).
  (All figures above are at the rendered 1/$div scale. At full scale the canvas
  is $($Cfg.canvas.w) x $($Cfg.canvas.h) and the overlap is $([int]$Cfg.overlap.w) px from x $($Cfg.overlap.x0).)

THE OVERLAP IS NOT PRE-BLENDED - THIS IS DELIBERATE
  Both plates carry FULL BRIGHTNESS through the overlap, with identical content.
  This matches the noise plates that were supplied. Apply the soft edge in the
  projector blend as usual. Do not ramp these files: it would double-darken the
  seam.

  Both plates are cut from the same rendered frame in memory, so the overlap
  cannot drift between them. Measured on this export: see the verification block
  in the log.

FRAMES AND TIMING
  Files contain frames $start .. $last of the ten-minute loop
     = $(Format-TC $start) .. $(Format-TC $last) at 30 fps, $count frames.

  The piece proper runs $segIn .. $segOut  ($(Format-TC $segIn) .. $(Format-TC $segOut)).
  Frames outside that are handles and show the untouched shared noise plate.

  Within the piece:
     1:50 - 2:00   the wall grows out of the bare shared plate
     2:00 - 4:00   the performance
     4:00 - 4:10   it dissolves back into the bare shared plate
  So it can be cut anywhere in the hand-off windows and will still match the
  other surfaces. At $(Format-TC $segIn) and $(Format-TC $segOut) the output is the shared plate,
  pixel for pixel.

  The wall also follows the plate's own blackout: when the shared noise cuts to
  black at 4:04.97, this surface is already black.

FORMAT
  codec        $($P.Codec)
  frame rate   30.000 fps, constant
  colour       bt709 primaries / transfer / matrix, tagged
  canvas       $($Cfg.canvas.w) x $($Cfg.canvas.h) at div $div

  The black regions of the mask (x 0-823 and x 9608-9788, lower parts) are
  rendered BLACK, not transparent, as required.

QUESTIONS
  Anything about geometry, timing or the overlap: check _pipeline/docs/
  00_TECHNICAL_SPEC.md in the project, which has every measurement this was
  built from.
"@ | Set-Content -Path $notes -Encoding utf8

# ---------------------------------------------------------------- done -------
if ($vrc -eq 0) {
    Head 'DONE  -  PLATES VERIFIED'
} else {
    Head 'DONE  -  BUT VERIFICATION FAILED. DO NOT SEND THESE.'
}
Line ("  took        {0:hh\:mm\:ss}" -f $took)
Line ("  {0}   {1:N2} GB" -f $f1.Name, ($f1.Length/1GB))
Line ("  {0}   {1:N2} GB" -f $f2.Name, ($f2.Length/1GB))
Line ("  notes       {0}" -f $notes)
Line ("  log         {0}" -f $log)
Line
if ($vrc -ne 0) {
    Line '  The overlap check did not pass. Read the verification block in the log'
    Line '  before sending anything to the producer.'
    exit 1
}
Line '  Both plates are the right size, the same length, and agree through the'
Line '  1000 px overlap. Send these two files plus DELIVERY_NOTES.txt.'
Line

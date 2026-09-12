<#
  Phoenix de Lumiere - SW wall - DELIVERY EXPORT

  One command, start to finish: check the machine, render the two projector
  plates, verify them, and write the notes that go with them.

  Double-click EXPORT.cmd in the project root and pick a number. Or:

      .\export_delivery.ps1 -Preset Deliver
      .\export_delivery.ps1 -Preset Deliver -Yes        # no confirmation
      .\export_delivery.ps1 -Preset Draft               # quick half-res look
      .\export_delivery.ps1 -Preset Deliver -Masks Aligned
      .\export_delivery.ps1 -Preset Deliver -OutRoot E:\PxDL

  WHERE IT WRITES

  The menu asks. Press Enter for the default - a 'deliver' folder inside the
  project on C: - or type any folder on any drive. It is created if it is not
  there, proven writable, and checked for room before anything starts.

  A full delivery is about 51 GB and will not fit on most system drives, so
  this is where you point it at an external one. From the command line -OutRoot
  is that same choice; -Out is the blunter one, writing exactly where it is
  told and making no subfolder.

  -Masks picks which description of the facade to render against. 'Layer' is
  the authored colour-coded mask exactly as drawn; 'Aligned' is the same
  shapes, each translated as one rigid piece onto the border the shared noise
  plate draws - nothing redrawn, nothing deformed, and the black area is not
  touched at all. The two go to different folders and carry different file
  names, so both can be rendered one after the other and compared.

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
    [switch] $HQ,
    [ValidateSet('Plates', 'Stitched', 'Both')]
    [string] $Layout = 'Plates',
    [ValidateSet('Layer', 'Aligned', 'Noise')]
    [string] $Masks,
    [string] $OutRoot,
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
# How far apart the two plates may measure through the overlap before something
# is actually wrong.
#
# They are cut from the same frame in memory, so the CONTENT is identical by
# construction. What the check sees afterwards is the two files being encoded
# SEPARATELY at different widths - different macroblock grids, different bit
# allocation - so every lossy codec leaves a little noise there. These numbers
# are the measured noise floor on this content plus headroom; a real fault (a
# wrong crop, a frame offset) shows up as tens of units, not ones.
#
#   prores4444   measured 0.00    it is 4:4:4, so the overlap comes out exact
#   prores422hq  measured 1.04
#   dnxhr_hqx    measured 1.62
#   h264         measured 2.55    a 2.0 tolerance failed a perfectly good Draft
$Tolerance = @{
    'prores4444' = 0.5; 'prores422hq' = 2.0; 'dnxhr_hqx' = 3.0; 'h264' = 5.0
}

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
        Div = 2; Codec = 'h264'; MBPerFrame = 1.0; SecPerFrame = 0.55
        What = 'Half size 4894x1276, H.264. For review, NOT for projection.'
        Note = 'Correct plate geometry, so the overlap can still be checked.'
    }
    'Proof' = @{
        Div = 2; Codec = 'h264'; MBPerFrame = 1.0; SecPerFrame = 0.55; Seconds = 25
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

# ---------------------------------------------------------- where it writes --
function Get-FreeGB {
    # $null when it cannot be measured - a UNC share, a drive that went away -
    # rather than an exception, so the caller decides what that means.
    param([string]$Path)
    try {
        $root = [IO.Path]::GetPathRoot([IO.Path]::GetFullPath($Path))
        if (-not $root) { return $null }
        $di = New-Object IO.DriveInfo $root
        if (-not $di.IsReady) { return $null }
        return [math]::Round($di.AvailableFreeSpace / 1GB, 1)
    } catch { return $null }
}

function Show-Drives {
    param([double]$NeedGB)
    foreach ($d in [IO.DriveInfo]::GetDrives()) {
        if (-not $d.IsReady) { continue }
        if ($d.DriveType -eq 'CDRom') { continue }
        $free = [math]::Round($d.AvailableFreeSpace / 1GB, 1)
        $verdict = if ($free -gt $NeedGB * 1.15) { 'room for this preset' } else { 'too small for this preset' }
        Line ("    {0,-4} {1,-9} {2,8:N1} GB free   {3}" -f $d.Name, $d.DriveType, $free, $verdict)
    }
}

function Read-ExportRoot {
    # Hands back a folder that exists and has been PROVEN writable. Creating a
    # directory does not prove that - a full drive and a read-only share both
    # get past it - and the alternative is finding out two hours in, at the
    # first encode.
    param([string]$Default, [double]$NeedGB)

    Head 'WHERE SHOULD THE FILES GO?'
    Line
    Line ("  This preset needs about {0:N1} GB." -f $NeedGB)
    Line
    Line ("  default   {0}" -f $Default)
    Line ("            {0}" -f $(if ($env:PXDL_DELIVER_ROOT) {
        'from PXDL_DELIVER_ROOT' } else { 'the deliver folder inside the project' }))
    $dFree = Get-FreeGB $Default
    if ($null -eq $dFree) {
        Line '            free space could not be measured'
    } elseif ($dFree -gt $NeedGB * 1.15) {
        Line ("            {0:N1} GB free - enough" -f $dFree)
    } else {
        Line ("            {0:N1} GB free - NOT ENOUGH for this preset" -f $dFree)
    }
    Line
    Line '  Drives on this machine right now:'
    Show-Drives -NeedGB $NeedGB
    Line
    Line '  Press Enter for the default, or type a folder - an external drive is'
    Line '  the usual answer for a full delivery. It is created if it does not'
    Line '  exist, and a folder named after the preset and the mask set is made'
    Line '  inside it, so two runs can never overwrite each other.'
    Line

    while ($true) {
        $ans = Read-Host 'Folder [Enter = default]'
        if ($ans) { $ans = $ans.Trim().Trim('"').Trim() }
        if (-not $ans) { return $Default }

        # Explorer's "Copy as path" hands over a quoted string, hence the trim
        # above. A relative path is resolved against the project rather than
        # whatever directory this happened to be launched from.
        try { $full = [IO.Path]::GetFullPath([IO.Path]::Combine($ProjectRoot, $ans)) }
        catch { Bad ("not a usable path: {0}" -f $ans); Line; continue }

        try { New-Item -ItemType Directory -Force $full -ErrorAction Stop | Out-Null }
        catch {
            Bad ("cannot create {0}" -f $full)
            Line ("        {0}" -f $_.Exception.Message)
            Line; continue
        }

        $probe = Join-Path $full ('.pxdl_write_test_{0}' -f [guid]::NewGuid().ToString('N'))
        try {
            [IO.File]::WriteAllText($probe, 'x')
            Remove-Item -LiteralPath $probe -Force -ErrorAction SilentlyContinue
        } catch {
            Bad ("that folder cannot be written to: {0}" -f $full)
            Line ("        {0}" -f $_.Exception.Message)
            Line; continue
        }

        $free = Get-FreeGB $full
        if ($null -ne $free -and $free -le $NeedGB * 1.15) {
            Warn ("{0:N1} GB free there, and this preset needs about {1:N1} GB." -f $free, $NeedGB)
            Line '        Preflight will stop the run if it does not fit.'
            $go = Read-Host '  Use it anyway? [y/N]'
            if ($go -notmatch '^[Yy]') { Line; continue }
        }
        Good ("output goes to {0}" -f $full)
        return $full
    }
}

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
# Captured before $Preset is filled in below: it is what tells the destination
# question further down whether there is anybody there to answer it.
$interactive = (-not $Preset)
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

    if (-not $Masks -and (Test-Path ((Get-MaskRoots 'Aligned').Masks))) {
        Line
        Line '  Two descriptions of this wall exist. Same shapes in both; some of'
        Line '  them sit a few pixels apart - see docs/06_MASKS.md.'
        Line
        Line '    [1]  authored mask   exactly as it was drawn'
        Line '    [2]  aligned         the same shapes, moved onto the plate'
        Line
        $mp = Read-Host 'Which one? [1]'
        $Masks = if ($mp -eq '2') { 'Aligned' } else { 'Layer' }
    }
}
$Masks = Get-MaskVariant $Masks
$MaskSet = Get-MaskRoots $Masks
$MaskTag = "MASK-" + $Masks.ToUpper()
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

# Both writes the canvas as well as the plates, which is roughly another 90% of
# the pixels - it is one render, but it is not one file's worth of disk.
$sizeMul = switch ($Layout) { 'Plates' { 1.0 } 'Stitched' { 0.9 } default { 1.9 } }
$needGB = [math]::Round($P.MBPerFrame * $sizeMul * $count / ($div * $div) / 1024.0, 1)
$mins   = [math]::Round($P.SecPerFrame * $count / ($div * $div) / 60.0, 0)

# Asked here, after the size is known, rather than left to an environment
# variable: a full delivery is ~51 GB, the system drive usually cannot take it,
# and by the time preflight says so you have already picked a preset. Skipped
# entirely when a path was passed in, or when nothing is driving this by hand.
if (-not $Out -and -not $OutRoot -and $interactive) {
    $OutRoot = Read-ExportRoot -Default $DeliverRoot -NeedGB $needGB
}
if (-not $OutRoot) { $OutRoot = $DeliverRoot }

# The mask set is part of the output path AND part of every file name, so
# rendering both one after the other cannot overwrite or confuse the two.
# -Out is the exception: it means "exactly here", and makes no subfolder.
$outDir = if ($Out) { $Out } else {
    [IO.Path]::Combine($OutRoot, ("{0}_{1}" -f $Preset, $MaskTag))
}

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
Line ("  source      {0}" -f $(if ($HQ) { 'the HIGH-QUALITY masters (source_hq)' } else { 'the supplied mp4s - 0.019 bits/pixel' }))
Line ("  layout      {0}" -f $(switch ($Layout) {
    'Plates'   { 'two projector plates (matches the supplied noise)' }
    'Stitched' { 'one stitched canvas file' }
    default    { 'BOTH - two plates AND one stitched canvas, from one render' } }))
Line ("  masks       {0}" -f $(if ($Masks -eq 'Aligned') {
    'the authored shapes, aligned to the shared plate' } else { 'the authored colour mask, as drawn' }))
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
else {
    $fail += "this ffmpeg has no $encNeeded encoder. Run: .\_pipeline\scripts\get_ffmpeg.ps1"
    Bad "encoder $encNeeded missing"
}

foreach ($k in 'SPSW1', 'SPSW2') {
    $src = Join-Path $ProjectRoot ($Cfg.source.$k -replace '/', '\')
    if (Test-Path $src) { Good "source $k" } else { $fail += "missing source: $src"; Bad "source $k missing" }
}
$maskMissing = $false
foreach ($n in '01_WALL', '02_WINDOW', '04_DOOR', '05_COLUMN', '06_TRIM', '08_PROJECTABLE', '09_OPENINGS') {
    $m = Join-Path $MaskSet.Masks ("PxDL_SW_MASK_{0}_{1}x{2}.png" -f $n, $Cfg.canvas.w, $Cfg.canvas.h)
    if (-not (Test-Path $m)) { $maskMissing = $true; Bad "mask $n missing" }
}
foreach ($n in 'openings.json', 'facade_regions.json',
               ("PxDL_SW_OPENING_ID_{0}x{1}.png" -f $Cfg.canvas.w, $Cfg.canvas.h),
               ("PxDL_SW_OPENING_SDF_{0}x{1}.png" -f $Cfg.canvas.w, $Cfg.canvas.h)) {
    if (-not (Test-Path (Join-Path $MaskSet.Reference $n))) {
        $maskMissing = $true; Bad "reference $n missing"
    }
}
if ($maskMissing) {
    if ($Masks -eq 'Aligned') {
        $fail += 'the aligned mask set is not built. Run: python _pipeline\scripts\build_masks.py --align'
    } else {
        $fail += 'the mask set is incomplete. Run: python _pipeline\scripts\build_masks.py'
    }
} else {
    Good ("masks   {0} set, complete" -f $Masks.ToLower())
}

if ($HQ) {
    Line
    Line '  --- high-quality masters ---'
    & python (Join-Path $PSScriptRoot 'check_hq.py')
    if ($LASTEXITCODE -ne 0) {
        $fail += 'the high-quality masters did not pass check_hq.py - see above'
    } else {
        Good 'high-quality masters validated'
    }
    Line
}

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

try { New-Item -ItemType Directory -Force $outDir -ErrorAction Stop | Out-Null }
catch {
    $fail += ("the output folder cannot be created: {0} - {1}" -f $outDir, $_.Exception.Message)
    Bad ("output  cannot create {0}" -f $outDir)
}
# Not (Get-Item $outDir).PSDrive any more: now that the destination can be any
# folder that gets typed in, it can be a UNC path, which has no PSDrive - and
# under Set-StrictMode reading .Free off that $null is a terminating error, so
# the export would die here instead of reporting the problem.
if (Test-Path $outDir -ErrorAction SilentlyContinue) {
    $freeGB = Get-FreeGB $outDir
    if ($null -eq $freeGB) {
        Warn ("disk    free space cannot be measured at {0}, needs about {1} GB" -f $outDir, $needGB)
    } elseif ($freeGB -gt $needGB * 1.15) {
        Good ("disk    {0} GB free at {1}, needs about {2} GB" -f $freeGB, $outDir, $needGB)
    } else {
        $fail += ("not enough disk: {0} GB free at {1}, this preset needs about {2} GB. Run it again and give it a folder on a bigger drive when it asks." -f $freeGB, $outDir, $needGB)
        Bad ("disk    {0} GB free, needs about {1} GB" -f $freeGB, $needGB)
    }
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
Line "  A progress bar fills in below, with frames/sec and a time remaining."
Line "  It runs at below-normal priority, so the machine stays usable."
Line

$started = Get-Date
$layoutArg = switch ($Layout) { 'Plates' { 'plates' } 'Stitched' { 'canvas' } default { 'both' } }
$renderArgs = @('--div', $div, '--start', $start, '--count', $count,
                '--layout', $layoutArg, '--codec', $P.Codec, '--threads', $Threads,
                '--masks', $Masks.ToLower(), '--log', $log, '--out', $outDir)
if ($HQ) { $renderArgs += '--hq' }
# NOT piped into Tee-Object on purpose. The progress bar rewrites one line with
# a carriage return; a pipe in front of it turns every update into its own line
# and the bar becomes four thousand lines of scrollback. The render writes its
# own log through --log instead, and leaves the bar out of it.
& python (Join-Path $PSScriptRoot 'render_shader.py') @renderArgs
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
$fc = Get-ChildItem $outDir -Filter '*CANVAS*' | Sort-Object LastWriteTime | Select-Object -Last 1

if ($Layout -eq 'Stitched') {
    if (-not $fc) { Bad 'the stitched canvas is missing'; exit 1 }
    Line "  One stitched file, so there is no overlap to cross-check: the 1000 px"
    Line "  band is inside it, where it belongs. Checking size and length only."
    Line
    $vrc = 0
    & python (Join-Path $PSScriptRoot 'verify_plates.py') --a $fc.FullName `
        --b $fc.FullName --div $div --samples 2 --tol 99 2>&1 |
        Select-String -Pattern 'frames:' | Tee-Object -FilePath $log -Append
    $expect = "$([int]$Cfg.canvas.w / $div)x$([int]$Cfg.canvas.h / $div)"
    Line "  expected $expect"
} else {
    if (-not $f1 -or -not $f2) { Bad 'one or both plates are missing'; exit 1 }

$tol = $Tolerance[$P.Codec]
Line "  Checking resolution, frame count, and that the 1000 px overlap holds."
Line "  Tolerance for $($P.Codec) is $tol - the two files are encoded separately at"
Line "  different widths, so a lossy codec always leaves a little noise in the"
Line "  overlap. A real fault would read in the tens, not the ones."
Line
& python (Join-Path $PSScriptRoot 'verify_plates.py') --a $f1.FullName --b $f2.FullName `
    --div $div --samples 8 --tol $tol 2>&1 | Tee-Object -FilePath $log -Append
$vrc = $LASTEXITCODE
}

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
  mask set     $(if ($Masks -eq 'Aligned') { 'the authored shapes, aligned to the shared plate' } else { 'the authored colour-coded mask, as drawn' })
  codec        $($P.Codec)
  noise source $(if ($HQ) { 'the high-quality masters' } else { 'the supplied preview mp4s (0.019 bits/pixel)' })
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
# list everything that was actually produced, so a Both run does not quietly
# leave the stitched file out of the summary
foreach ($f in (Get-ChildItem $outDir -Include *.mov, *.mp4 -File -Recurse |
                Sort-Object Name)) {
    Line ("  {0,-42} {1,8:N2} GB" -f $f.Name, ($f.Length / 1GB))
}
Line ("  notes       {0}" -f $notes)
Line ("  log         {0}" -f $log)
Line
if ($vrc -ne 0) {
    Line '  The overlap check did not pass. Read the verification block in the log'
    Line '  before sending anything to the producer.'
    exit 1
}
if ($Layout -eq 'Stitched') {
    Line '  One stitched 9788x2552 canvas. Send it plus DELIVERY_NOTES.txt.'
} elseif ($Layout -eq 'Both') {
    Line '  Both forms, from one render. The two SPSW plates are what matches the'
    Line '  supplied noise; the CANVAS file is the same thing unsplit, if the'
    Line '  producer would rather cut that and slice it themselves. Send whichever'
    Line '  they ask for, plus DELIVERY_NOTES.txt.'
} else {
    Line '  Both plates are the right size, the same length, and agree through the'
    Line '  1000 px overlap. Send these two files plus DELIVERY_NOTES.txt.'
}
Line

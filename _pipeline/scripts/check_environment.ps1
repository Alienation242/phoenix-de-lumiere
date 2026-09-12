<#
  Run this FIRST on any machine you intend to render on.

  Checks the things that actually stop this project: VRAM against a 25 Mpx
  canvas, free space against the sequence size, ffmpeg encoder support, and the
  two licence caps that silently refuse to render at this resolution.

      .\check_environment.ps1
      .\check_environment.ps1 -Codec prores4444
#>
[CmdletBinding()]
param(
    [string] $Codec = 'prores4444'
)

. "$PSScriptRoot\_common.ps1"

$warn = New-Object System.Collections.ArrayList
$fail = New-Object System.Collections.ArrayList
function Line([string]$s) { Write-Host ("   " + $s) }
function Caution([string]$s) { [void]$warn.Add($s); Write-Host ("   ! " + $s) -ForegroundColor Yellow }
function Bad([string]$s) { [void]$fail.Add($s); Write-Host ("   X " + $s) -ForegroundColor Red }
function Good([string]$s) { Write-Host ("   + " + $s) -ForegroundColor Green }

Show-Context

$cw = [int]$Cfg.canvas.w; $chh = [int]$Cfg.canvas.h
$mpx = ($cw * $chh) / 1e6
$frames = ([int]$Cfg.segment.out - [int]$Cfg.segment.in + 1) + 2 * [int]$Cfg.segment.handles

Write-Host "--- project ---"
Line ("canvas {0} x {1} = {2:N1} Mpx  ({3:N2}x a 4K UHD frame)" -f $cw, $chh, $mpx, (($cw*$chh)/(3840.0*2160.0)))
Line ("render {0} frames incl. handles" -f $frames)

Write-Host ""
Write-Host "--- GPU ---"
$gpus = Get-CimInstance Win32_VideoController -ErrorAction SilentlyContinue
$bestVram = 0
foreach ($g in $gpus) {
    # AdapterRAM is a signed 32-bit field and saturates at 4 GB. Try the registry
    # for the real figure before trusting it.
    $vram = 0
    try {
        $key = Get-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}\*" -ErrorAction SilentlyContinue |
               Where-Object { $_.DriverDesc -eq $g.Name } | Select-Object -First 1
        if ($key -and $key.'HardwareInformation.qwMemorySize') {
            $vram = [double]$key.'HardwareInformation.qwMemorySize' / 1GB
        }
    } catch { }
    if ($vram -le 0 -and $g.AdapterRAM) { $vram = [double]$g.AdapterRAM / 1GB }
    Line ("{0}  ~{1:N1} GB VRAM" -f $g.Name, $vram)
    if ($vram -gt $bestVram) { $bestVram = $vram }
}
$bufMB = ($cw * $chh * 8) / 1MB      # one RGBA16F buffer
Line ("one RGBA16F buffer at full canvas = {0:N0} MB" -f $bufMB)
if ($bestVram -ge 12)     { Good ("{0:N1} GB - comfortable, you can render 3D at full canvas" -f $bestVram) }
elseif ($bestVram -ge 8)  { Good ("{0:N1} GB - fine if the 3D layer renders at 1/2" -f $bestVram) }
elseif ($bestVram -ge 6)  { Caution ("{0:N1} GB - render the 3D layer at 1/4 and keep the comp chain short" -f $bestVram) }
else                      { Caution ("{0:N1} GB - tight. 3D at 1/4 only, 8-bit comp where possible, no MSAA at full canvas" -f $bestVram) }

Write-Host ""
Write-Host "--- RAM / CPU ---"
$cs = Get-CimInstance Win32_ComputerSystem
$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
Line ("{0}  {1}c/{2}t" -f $cpu.Name.Trim(), $cpu.NumberOfCores, $cpu.NumberOfLogicalProcessors)
$ram = [double]$cs.TotalPhysicalMemory / 1GB
if ($ram -ge 31) { Good ("{0:N0} GB RAM" -f $ram) } else { Caution ("{0:N0} GB RAM - 32 GB+ recommended at this frame size" -f $ram) }

Write-Host ""
Write-Host "--- disk ---"
# rough per-frame sizes measured on this material at 9788x2552
$est = @{
    'master png16'  = 30MB
    'plate png16'   = 37MB      # 7200 + 3588 wide together
    'prores4444'    = 18MB
    'prores422hq'   = 9MB
    'ffv1'          = 32MB
}
foreach ($root in @(@{n='work';p=$WorkRoot}, @{n='render';p=$RenderRoot}, @{n='deliver';p=$DeliverRoot})) {
    $drive = $null
    try { $drive = (Get-Item -LiteralPath (Split-Path $root.p -Qualifier) -ErrorAction SilentlyContinue) } catch {}
    $q = Split-Path $root.p -Qualifier
    $free = 0
    if ($q) {
        $d = Get-PSDrive -Name $q.TrimEnd(':') -ErrorAction SilentlyContinue
        if ($d) { $free = [double]$d.Free / 1GB }
    }
    Line ("{0,-8} {1}   free {2:N1} GB" -f $root.n, $root.p, $free)
}
$needRender  = ($frames * $est['master png16']) / 1GB
$needDeliver = ($frames * $est['prores4444']) / 1GB
Line ("estimated: master sequence ~{0:N0} GB, ProRes 4444 plates ~{1:N0} GB" -f $needRender, $needDeliver)
$q = Split-Path $RenderRoot -Qualifier
$freeR = 0
if ($q) { $d = Get-PSDrive -Name $q.TrimEnd(':') -ErrorAction SilentlyContinue; if ($d) { $freeR = [double]$d.Free / 1GB } }
if ($freeR -ge ($needRender + $needDeliver) * 1.3) { Good ("{0:N0} GB free on the render drive - enough" -f $freeR) }
else {
    Caution ("only {0:N0} GB free where render/ points; you need roughly {1:N0} GB." -f $freeR, (($needRender + $needDeliver) * 1.3))
    Line ("   point the big folders at an external drive:")
    Line ("     `$env:PXDL_RENDER_ROOT  = 'E:\PxDL\render'")
    Line ("     `$env:PXDL_DELIVER_ROOT = 'E:\PxDL\deliver'")
    Line ("     `$env:PXDL_WORK_ROOT    = 'E:\PxDL\work'")
}

Write-Host ""
Write-Host "--- ffmpeg ---"
Line $FFmpegExe
foreach ($e in @('png', 'ffv1', 'prores_ks', 'dnxhd', 'libx264')) {
    if (Test-FFmpegEncoder $e) { Good ("encoder $e") } else { Line ("   encoder $e  - not available") }
}
$want = @{ prores4444='prores_ks'; prores422hq='prores_ks'; dnxhr444='dnxhd'; h264='libx264'; ffv1='ffv1'; png16='png' }[$Codec]
if ($want -and -not (Test-FFmpegEncoder $want)) {
    Caution ("-Codec $Codec needs '$want', which this build lacks. Run: .\get_ffmpeg.ps1")
}

Write-Host ""
Write-Host "--- tools ---"
$td = Get-ChildItem 'C:\Program Files\Derivative' -Directory -ErrorAction SilentlyContinue
if ($td) {
    foreach ($t in $td) {
        $exe = Join-Path $t.FullName 'bin\TouchDesigner.exe'
        if (Test-Path $exe) { Line ("TouchDesigner  {0}" -f (Get-Item $exe).VersionInfo.ProductVersion) }
    }
    Caution "TouchDesigner: confirm the licence is Commercial or Pro. Non-Commercial caps output at 1280x1280 and cannot render this canvas."
} else { Line "TouchDesigner not installed" }

$rv = 'C:\Program Files\Blackmagic Design\DaVinci Resolve\Resolve.exe'
if (Test-Path $rv) {
    $pn = (Get-Item $rv).VersionInfo.ProductName
    Line ("DaVinci Resolve  {0}  ({1})" -f (Get-Item $rv).VersionInfo.ProductVersion, $pn)
    if ($pn -notmatch 'Studio') { Caution "Resolve looks like the free version - it caps output at 3840x2160. You need Studio for 9788 px. Check Help > About." }
} else { Line "DaVinci Resolve not installed" }

foreach ($n in @('Epic Games\UE_5.6', 'Maxon Cinema 4D 2026', 'Blender Foundation')) {
    if (Test-Path (Join-Path 'C:\Program Files' $n)) { Line ("found  $n") }
}

Write-Host ""
Write-Host "--- python ---"
$py = Get-Command python -ErrorAction SilentlyContinue
if ($py) {
    Line ("{0}  {1}" -f $py.Source, (Invoke-Native 'python' @('--version')).LastLine)
    # moderngl is the renderer now, not an optional extra - check it first.
    foreach ($m in 'moderngl', 'numpy') {
        $r = Test-PyModule $m
        if ($r.Ok) { Good $m }
        else {
            Bad ("$m missing - run:  python -m pip install $m")
            if ($r.Reason) { Line ("     {0}" -f $r.Reason) }
        }
    }
} else { Bad "python not found on PATH - render_shader.py and verify_plates.py need it" }

Write-Host ""
Write-Host "--- project files ---"
foreach ($k in $Cfg.source.PSObject.Properties.Name) {
    $p = Join-Path $ProjectRoot $Cfg.source.$k
    if (Test-Path $p) { Good ("source/$k") } else { Bad ("missing source/$k -> $p") }
}
$nm = (Get-ChildItem $MaskRoot -Filter '*.png' -ErrorAction SilentlyContinue).Count
if ($nm -ge 31) { Good ("$nm mask files") } else { Caution ("only $nm mask files in $MaskRoot (expected 31)") }

Write-Host ""
Write-Host ("=" * 62)
if ($fail.Count) {
    Write-Host "NOT READY" -ForegroundColor Red
    $fail | ForEach-Object { Write-Host "  X $_" }
} elseif ($warn.Count) {
    Write-Host "READY, with warnings" -ForegroundColor Yellow
    $warn | ForEach-Object { Write-Host "  ! $_" }
} else {
    Write-Host "READY" -ForegroundColor Green
}

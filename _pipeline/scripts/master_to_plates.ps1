<#
  Slice a finished 9788x2552 master sequence into the two delivery plates.

  This is the ONLY safe way to make the plates. The 1000 px overlap
  (x 6200..7200) must be pixel-identical in both, and cropping one master gives
  you that for free. Rendering the two plates separately does not.

      SPSW1   7200 x 2552   crop at x = 0
      SPSW2   3588 x 2552   crop at x = 6200

  Output is a lossless PNG sequence per plate. Encode it afterwards with
  make_delivery.ps1 - keep the sequence until the producer has signed off.

  Examples
      .\master_to_plates.ps1 -MasterPattern "E:\PxDL\render\master\master.%05d.png" -StartFrame 4770 -Count 2460
      .\master_to_plates.ps1 -MasterPattern ... -StartFrame 4770 -Count 2460 -ApplyProjectableMask
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $MasterPattern,
    [int]    $StartFrame = -1,
    [int]    $Count      = -1,
    [string] $OutRoot    = '',
    [switch] $ApplyProjectableMask,
    [ValidateSet(8, 16)]
    [int]    $BitDepth   = 16
)

. "$PSScriptRoot\_common.ps1"
Show-Context

if ($StartFrame -lt 0) { $StartFrame = [int]$Cfg.segment.in - [int]$Cfg.segment.handles }
if ($Count -lt 0) {
    $Count = ([int]$Cfg.segment.out - [int]$Cfg.segment.in + 1) + 2 * [int]$Cfg.segment.handles
}
if (-not $OutRoot) { $OutRoot = $DeliverRoot }

$first = Expand-FramePattern $MasterPattern $StartFrame
if (-not (Test-Path $first)) { throw "first master frame not found: $first" }
Write-Host "master    $MasterPattern"
Write-Host "frames    $StartFrame .. $($StartFrame + $Count - 1)  ($Count frames)"
Write-Host ""

$pixFmt = if ($BitDepth -eq 16) { 'rgb48be' } else { 'rgb24' }
$cw = [int]$Cfg.canvas.w; $chh = [int]$Cfg.canvas.h

foreach ($p in $Cfg.plates) {
    $dir = Join-Path $OutRoot $p.name
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    $out = Join-Path $dir ("PxDL_SW_{0}.%05d.png" -f $p.name)
    Write-Host ("{0}  {1} x {2}  crop x={3}  ->  {4}" -f $p.name, $p.w, $p.h, $p.x, $dir)

    $crop = "crop=$($p.w):$($p.h):$($p.x):0"

    if ($ApplyProjectableMask) {
        $mask = Join-Path $MaskRoot 'PxDL_SW_MASK_08_PROJECTABLE_9788x2552.png'
        if (-not (Test-Path $mask)) { throw "mask not found: $mask" }
        # multiply the master by the projectable matte, then crop.
        # black stays black in the areas that are not projected.
        $fc = "[1:v]format=gray,scale=${cw}:${chh}[m];" +
              "[0:v]format=gbrp16le[v];" +
              "[v][m]blend=all_mode=multiply:shortest=1,format=$pixFmt,$crop[o]"
        Invoke-FFmpeg @(
            '-hide_banner', '-loglevel', 'warning', '-stats',
            '-start_number', "$StartFrame", '-i', $MasterPattern,
            '-loop', '1', '-i', $mask,
            '-filter_complex', $fc, '-map', '[o]',
            '-frames:v', "$Count", '-start_number', "$StartFrame", '-y', $out
        ) "slice $($p.name)"
    }
    else {
        Invoke-FFmpeg @(
            '-hide_banner', '-loglevel', 'warning', '-stats',
            '-start_number', "$StartFrame", '-i', $MasterPattern,
            '-vf', $crop, '-pix_fmt', $pixFmt,
            '-frames:v', "$Count", '-start_number', "$StartFrame", '-y', $out
        ) "slice $($p.name)"
    }
}

$a = $Cfg.plates[0].name; $b = $Cfg.plates[1].name
Write-Host ""
Write-Host "now verify, then encode:"
Write-Host "  python `"$PSScriptRoot\verify_plates.py`" --a `"$OutRoot\$a\PxDL_SW_$a.%05d.png`" --b `"$OutRoot\$b\PxDL_SW_$b.%05d.png`" --start $StartFrame"
Write-Host "  .\make_delivery.ps1 -SeqRoot `"$OutRoot`" -StartFrame $StartFrame -Count $Count"

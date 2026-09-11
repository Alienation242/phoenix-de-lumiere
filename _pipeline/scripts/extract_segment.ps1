<#
  Extract your segment from the noise plates as a PNG sequence.

  The noise plate is the permanent base of the whole installation - your render
  sits ON it, it never replaces it. So you need it as a frame-accurate,
  re-decodable sequence rather than scrubbing an H.264 file in your comp.

  Frame numbers follow the 10-minute loop, not 0. Defaults come from
  project.json (4800..7199 plus handles).

  Examples
      .\extract_segment.ps1 -Div 4                 # quarter-res proxy, to design against
      .\extract_segment.ps1 -Div 1                 # full res, for the final render
      .\extract_segment.ps1 -Div 4 -Stitched       # one 2447x638 canvas instead of two plates
#>
[CmdletBinding()]
param(
    [int]    $StartFrame = -1,                 # -1 = project.json segment.in  minus handles
    [int]    $EndFrame   = -1,                 # -1 = project.json segment.out plus handles
    [ValidateSet(1, 2, 4)]
    [int]    $Div        = 4,
    [switch] $Stitched,
    [switch] $NoHandles,
    [switch] $HQ,                              # use the ProRes masters from project.json source_hq
    [string] $OutRoot    = ''                  # default: <work>/segment
)

. "$PSScriptRoot\_common.ps1"
Show-Context

$h = if ($NoHandles) { 0 } else { [int]$Cfg.segment.handles }
if ($StartFrame -lt 0) { $StartFrame = [int]$Cfg.segment.in  - $h }
if ($EndFrame   -lt 0) { $EndFrame   = [int]$Cfg.segment.out + $h }
if ($EndFrame -lt $StartFrame) { throw "EndFrame ($EndFrame) is before StartFrame ($StartFrame)" }
if (-not $OutRoot) { $OutRoot = Join-Path $WorkRoot 'segment' }

$fps   = [int]$Cfg.fps
$count = $EndFrame - $StartFrame + 1
$ss    = ([double]$StartFrame / $fps).ToString('0.000000', [Globalization.CultureInfo]::InvariantCulture)
$sc    = Get-Scale $Div
$ch    = [int]$sc.h

Write-Host ("frames  {0} .. {1}   ({2} frames, {3:N3} s, handles {4})" -f $StartFrame, $EndFrame, $count, ($count / $fps), $h)
Write-Host ("start   {0} s" -f $ss)
Write-Host ("scale   1/{0}  ->  {1} x {2}" -f $Div, $sc.w, $ch)
Write-Host ""

$v1 = Get-SourcePath 'SPSW1' $HQ.IsPresent
$v2 = Get-SourcePath 'SPSW2' $HQ.IsPresent
if ($HQ) { Write-Host "source  ProRes masters" } else { Write-Host "source  preview mp4 (pass -HQ for the ProRes masters)" }

if ($Stitched) {
    $cut = [int]$Cfg.plates[0].w - ([int]$Cfg.overlap.x1 - [int]$Cfg.overlap.x0)   # 7200-1000 = 6200
    $dir = Join-Path $OutRoot ("stitched_{0}x{1}" -f $sc.w, $ch)
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    $pattern = Join-Path $dir 'PxDL_SW_stitched.%05d.png'
    Write-Host "-> $dir"
    Invoke-FFmpeg @(
        '-hide_banner', '-loglevel', 'warning', '-stats',
        '-ss', $ss, '-i', $v1, '-ss', $ss, '-i', $v2,
        '-filter_complex',
        "[0:v]crop=${cut}:$($Cfg.canvas.h):0:0[a];[a][1:v]hstack=2[s];[s]scale=$($sc.w):${ch}:flags=area[o]",
        '-map', '[o]', '-frames:v', "$count", '-start_number', "$StartFrame", '-y', $pattern
    ) 'stitched extract'
}
else {
    foreach ($p in $Cfg.plates) {
        $pw  = [int]($p.w / $Div)
        $src = Get-SourcePath $p.name $HQ.IsPresent
        $dir = Join-Path $OutRoot ("{0}_{1}x{2}" -f $p.name, $pw, $ch)
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
        $pattern = Join-Path $dir ("PxDL_SW_{0}.%05d.png" -f $p.name)
        Write-Host "-> $dir"
        $a = @('-hide_banner', '-loglevel', 'warning', '-stats', '-ss', $ss, '-i', $src)
        if ($Div -ne 1) { $a += @('-vf', "scale=${pw}:${ch}:flags=area") }
        $a += @('-frames:v', "$count", '-start_number', "$StartFrame", '-y', $pattern)
        Invoke-FFmpeg $a "extract $($p.name)"
    }
}

Write-Host ""
Write-Host "done. frames carry the loop's own numbering ($StartFrame..$EndFrame)."

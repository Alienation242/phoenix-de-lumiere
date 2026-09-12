<#
  Stitch the two plates back into one 9788x2552 canvas for review.

  The overlap is identical in both plates, so the stitch is a hard cut at the
  middle of the blend zone (x = 6700) - no ramp needed, and the result is exact:

      SPSW1  crop 6700 wide at x = 0
      SPSW2  crop 3088 wide at x = 500      ->  6700 + 3088 = 9788

  A visible seam means your plates disagree in the overlap, which is a real bug.
  verify_plates.py says the same thing numerically.

  Examples
      .\preview_stitch.ps1 -A ..\..\RawNoise\PxDL_SW_SPSW1.mp4 `
                           -B ..\..\RawNoise\PxDL_SW_SPSW2.mp4 -Stills 4800,5400,6300,7100
      .\preview_stitch.ps1 -A "E:\PxDL\deliver\SPSW1\PxDL_SW_SPSW1.%05d.png" `
                           -B "E:\PxDL\deliver\SPSW2\PxDL_SW_SPSW2.%05d.png" -Start 4770 -Movie

  -SourceDiv is for plates that have ALREADY been scaled down - which is what
  the Draft and Proof presets produce. The crops below are in canvas
  coordinates (cut at 6700 of 9788), so handing them a 4894-wide Draft plate
  without saying so asks ffmpeg to crop 6700 px out of 3600 and it fails.

      .\preview_stitch.ps1 -A "...\Draft_MASK-LAYER\PxDL_SW_SPSW1_03270-07529.mp4" `
                           -B "...\Draft_MASK-LAYER\PxDL_SW_SPSW2_03270-07529.mp4" `
                           -SourceDiv 2 -Div 4 -Movie
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $A,
    [Parameter(Mandatory)] [string] $B,
    [int]    $Start  = 0,
    [int]    $Count  = 0,
    [int[]]  $Stills = @(),
    [switch] $Movie,
    [ValidateSet(1, 2, 4)]
    [int]    $Div    = 4,
    [ValidateSet(1, 2, 4)]
    [int]    $SourceDiv = 1,
    [string] $OutRoot = ''
)

. "$PSScriptRoot\_common.ps1"
Show-Context

if (-not $OutRoot) { $OutRoot = Join-Path $WorkRoot 'preview' }
New-Item -ItemType Directory -Force -Path $OutRoot | Out-Null

$fps  = [int]$Cfg.fps
$cut  = [int]$Cfg.stitch_cut_x                       # 6700
$pA   = $Cfg.plates[0]; $pB = $Cfg.plates[1]
$bOff = $cut - [int]$pB.x                            # 6700 - 6200 = 500
$bW   = [int]$pB.w - $bOff                           # 3588 - 500  = 3088
$cH   = [int]$Cfg.canvas.h

# Plates that were already rendered at 1/SourceDiv need the crops in THEIR
# pixels, not the canvas's. 6700, 2552, 500 and 3088 all divide by 4, so this
# stays on whole pixels for every divisor the project allows.
if ($SourceDiv -gt 1) {
    $cut = [int]($cut / $SourceDiv)
    $cH  = [int]($cH / $SourceDiv)
    $bOff = [int]($bOff / $SourceDiv)
    $bW  = [int]($bW / $SourceDiv)
}
if ($Div -lt $SourceDiv) {
    Write-Host ("  -Div {0} would upscale a 1/{1} source. Using -Div {1}." -f $Div, $SourceDiv)
    $Div = $SourceDiv
}
$sc   = Get-Scale $Div
$pw   = [int]$sc.w; $ph = [int]$sc.h

$stitch = "[0:v]crop=${cut}:${cH}:0:0[l];" +
          "[1:v]crop=${bW}:${cH}:${bOff}:0[r];" +
          "[l][r]hstack=2[s];[s]scale=${pw}:${ph}:flags=area[o]"

function Is-Sequence([string]$p) { return ($p -match '%0\dd') }

if ($Stills.Count -gt 0) {
    foreach ($f in $Stills) {
        $out = Join-Path $OutRoot ("stitch_{0:D5}.png" -f $f)
        Write-Host ("still frame {0}  ->  {1}" -f $f, $out)
        if (Is-Sequence $A) {
            $rel = $f - $Start
            $fc = "[0:v]select=eq(n\,$rel)[a];[1:v]select=eq(n\,$rel)[b];" +
                  "[a]crop=${cut}:${cH}:0:0[l];" +
                  "[b]crop=${bW}:${cH}:${bOff}:0[r];" +
                  "[l][r]hstack=2[s];[s]scale=${pw}:${ph}:flags=area[o]"
            Invoke-FFmpeg (@('-hide_banner','-loglevel','error',
                '-start_number',"$Start",'-i',$A,'-start_number',"$Start",'-i',$B,
                '-filter_complex',$fc,'-map','[o]') + (Get-FramePassthroughArgs) +
                @('-frames:v','1','-y',$out)) "still $f"
        } else {
            $t = ([double]$f / $fps).ToString('0.000000', [Globalization.CultureInfo]::InvariantCulture)
            Invoke-FFmpeg @('-hide_banner','-loglevel','error','-ss',$t,'-i',$A,'-ss',$t,'-i',$B,
                '-filter_complex',$stitch,'-map','[o]','-frames:v','1','-y',$out) "still $f"
        }
    }
}

if ($Movie -or $Stills.Count -eq 0) {
    # H.264 cannot encode an odd dimension, and the quarter-scale canvas is
    # 2447 wide - gcd(9788,2552)=4 makes 9788/4 odd. The stills keep the exact
    # size; the movie loses at most one pixel off each axis, and says so.
    $mw = $pw - ($pw % 2); $mh = $ph - ($ph % 2)
    if ($mw -ne $pw -or $mh -ne $ph) {
        Write-Host ("  {0}x{1} is odd and H.264 needs even dimensions - the movie is {2}x{3}." -f $pw, $ph, $mw, $mh)
    }
    $movieStitch = $stitch -replace "scale=${pw}:${ph}", "scale=${mw}:${mh}"
    $out = Join-Path $OutRoot ("stitch_preview_{0}x{1}.mp4" -f $mw, $mh)
    Write-Host "preview movie -> $out"
    # NOT $a. PowerShell variable names are case-insensitive, so $a IS $A -
    # the plate path this function was handed. Building the argument list in
    # $a overwrote it, and every later $A then stringified the argument array
    # into the command line instead of naming a file. The tell was '-stats-i'
    # fused together in ffmpeg's complaint: that is a string += an array, not
    # an array += an array. The stills branch never used $a, which is why only
    # the movie was broken.
    $ffArgs = @('-hide_banner','-loglevel','warning','-stats')
    if (Is-Sequence $A) { $ffArgs += @('-framerate',"$fps",'-start_number',"$Start",'-i',$A) } else { $ffArgs += @('-i',$A) }
    if (Is-Sequence $B) { $ffArgs += @('-framerate',"$fps",'-start_number',"$Start",'-i',$B) } else { $ffArgs += @('-i',$B) }
    $ffArgs += @('-filter_complex',$movieStitch,'-map','[o]')
    if ($Count -gt 0) { $ffArgs += @('-frames:v',"$Count") }
    # prefer H.264 when the ffmpeg build has it; the TouchDesigner build does not
    if (Test-FFmpegEncoder 'libx264') { $ffArgs += @('-c:v','libx264','-crf','20','-preset','fast','-pix_fmt','yuv420p') }
    else                              { $ffArgs += @('-c:v','mpeg4','-q:v','3') }
    $ffArgs += @('-r',"$fps",'-y',$out)
    Invoke-FFmpeg $ffArgs 'preview movie'
}

Write-Host ""
Write-Host "done -> $OutRoot"

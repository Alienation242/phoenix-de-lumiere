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
$sc   = Get-Scale $Div
$pw   = [int]$sc.w; $ph = [int]$sc.h

$stitch = "[0:v]crop=${cut}:$($Cfg.canvas.h):0:0[l];" +
          "[1:v]crop=${bW}:$($Cfg.canvas.h):${bOff}:0[r];" +
          "[l][r]hstack=2[s];[s]scale=${pw}:${ph}:flags=area[o]"

function Is-Sequence([string]$p) { return ($p -match '%0\dd') }

if ($Stills.Count -gt 0) {
    foreach ($f in $Stills) {
        $out = Join-Path $OutRoot ("stitch_{0:D5}.png" -f $f)
        Write-Host ("still frame {0}  ->  {1}" -f $f, $out)
        if (Is-Sequence $A) {
            $rel = $f - $Start
            $fc = "[0:v]select=eq(n\,$rel)[a];[1:v]select=eq(n\,$rel)[b];" +
                  "[a]crop=${cut}:$($Cfg.canvas.h):0:0[l];" +
                  "[b]crop=${bW}:$($Cfg.canvas.h):${bOff}:0[r];" +
                  "[l][r]hstack=2[s];[s]scale=${pw}:${ph}:flags=area[o]"
            Invoke-FFmpeg @('-hide_banner','-loglevel','error',
                '-start_number',"$Start",'-i',$A,'-start_number',"$Start",'-i',$B,
                '-filter_complex',$fc,'-map','[o]','-vsync','0','-frames:v','1','-y',$out) "still $f"
        } else {
            $t = ([double]$f / $fps).ToString('0.000000', [Globalization.CultureInfo]::InvariantCulture)
            Invoke-FFmpeg @('-hide_banner','-loglevel','error','-ss',$t,'-i',$A,'-ss',$t,'-i',$B,
                '-filter_complex',$stitch,'-map','[o]','-frames:v','1','-y',$out) "still $f"
        }
    }
}

if ($Movie -or $Stills.Count -eq 0) {
    $out = Join-Path $OutRoot ("stitch_preview_{0}x{1}.mp4" -f $pw, $ph)
    Write-Host "preview movie -> $out"
    $a = @('-hide_banner','-loglevel','warning','-stats')
    if (Is-Sequence $A) { $a += @('-framerate',"$fps",'-start_number',"$Start",'-i',$A) } else { $a += @('-i',$A) }
    if (Is-Sequence $B) { $a += @('-framerate',"$fps",'-start_number',"$Start",'-i',$B) } else { $a += @('-i',$B) }
    $a += @('-filter_complex',$stitch,'-map','[o]')
    if ($Count -gt 0) { $a += @('-frames:v',"$Count") }
    # prefer H.264 when the ffmpeg build has it; the TouchDesigner build does not
    if (Test-FFmpegEncoder 'libx264') { $a += @('-c:v','libx264','-crf','20','-preset','fast','-pix_fmt','yuv420p') }
    else                              { $a += @('-c:v','mpeg4','-q:v','3') }
    $a += @('-r',"$fps",'-y',$out)
    Invoke-FFmpeg $a 'preview movie'
}

Write-Host ""
Write-Host "done -> $OutRoot"

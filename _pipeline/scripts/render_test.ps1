<#
  Make a small preview video out of whatever you have rendered, fast.

  Takes a master sequence, a pair of plates, or a deliver folder, and gives you
  one small MP4 with the frame number and timecode burnt in. Use it after every
  reduced-resolution render pass so you can actually watch your 80 seconds
  instead of scrubbing frames.

  Examples
      # a 9788-wide master sequence
      .\render_test.ps1 -In "E:\PxDL\render\master\PxDL_SW_master.%05d.png"

      # a ÷4 test render straight out of TouchDesigner
      .\render_test.ps1 -In "E:\PxDL\render\test\out.%05d.png" -Width 1632

      # a folder holding SPSW1\ and SPSW2\ - stitched automatically
      .\render_test.ps1 -In "E:\PxDL\deliver"

      # your render above, the raw noise below, to check you are sitting on it
      .\render_test.ps1 -In "E:\PxDL\render\master\m.%05d.png" -Compare

      # the source noise on its own
      .\render_test.ps1 -Source

  Sizes: -Width 1224 (÷8, fastest) · 1632 · 2447 (÷4) · 4894 (÷2)
#>
[CmdletBinding()]
param(
    [string] $In       = '',      # sequence pattern, movie, or a folder with SPSW1\ SPSW2\
    [string] $B        = '',      # second plate, if you pass the two separately
    [switch] $Source,             # preview the supplied noise instead
    [int]    $Width    = 2447,
    [int]    $Start    = -1,
    [int]    $Count    = -1,
    [int]    $Fps      = 0,       # 0 = project fps
    [switch] $Compare,            # stack your render over the source noise
    [switch] $NoBurnin,
    [string] $Out      = '',
    [int]    $Crf      = 20
)

. "$PSScriptRoot\_common.ps1"

$fps = if ($Fps -gt 0) { $Fps } else { [int]$Cfg.fps }
if ($Start -lt 0) { $Start = [int]$Cfg.segment.in - [int]$Cfg.segment.handles }
if ($Count -lt 0) { $Count = ([int]$Cfg.segment.out - [int]$Cfg.segment.in + 1) + 2 * [int]$Cfg.segment.handles }
if (-not $In -and -not $Source) { throw "give -In <sequence|movie|folder> or -Source" }

$cw = [int]$Cfg.canvas.w; $chh = [int]$Cfg.canvas.h
$ph = [int][Math]::Round($Width * $chh / $cw / 2) * 2
$pw = [int][Math]::Round($Width / 2) * 2

$cut  = [int]$Cfg.stitch_cut_x
$pA   = $Cfg.plates[0]; $pB = $Cfg.plates[1]
$bOff = $cut - [int]$pB.x
$bW   = [int]$pB.w - $bOff
function Stitch([string]$a, [string]$b) {
    return "[$a]crop=${cut}:${chh}:0:0[sl];[$b]crop=${bW}:${chh}:${bOff}:0[sr];[sl][sr]hstack=2"
}
function IsSeq([string]$p) { return ($p -match '%0\dd') }

$ff = @('-hide_banner', '-loglevel', 'warning', '-stats')
$fc   = ''
$srcSs = ([double]$Start / $fps).ToString('0.000000', [Globalization.CultureInfo]::InvariantCulture)
$v1 = Get-SourcePath 'SPSW1'
$v2 = Get-SourcePath 'SPSW2'
$label = 'preview'

if ($Source) {
    $ff += @('-ss', $srcSs, '-i', $v1, '-ss', $srcSs, '-i', $v2)
    $fc = (Stitch '0:v' '1:v') + ",scale=${pw}:${ph}:flags=area[main]"
    $label = 'source'
}
else {
    # a folder holding SPSW1\ and SPSW2\ ?
    if ((Test-Path $In -PathType Container) -and -not $B) {
        $a1 = Join-Path (Join-Path $In $pA.name) ("PxDL_SW_{0}.%05d.png" -f $pA.name)
        $a2 = Join-Path (Join-Path $In $pB.name) ("PxDL_SW_{0}.%05d.png" -f $pB.name)
        if (-not (Test-Path (Expand-FramePattern $a1 $Start))) { throw "no plate sequence under $In" }
        $In = $a1; $B = $a2
    }

    if ($B) {
        foreach ($p in @($In, $B)) {
            if (IsSeq $p) { $ff += @('-framerate', "$fps", '-start_number', "$Start", '-i', $p) }
            else          { $ff += @('-i', $p) }
        }
        $fc = (Stitch '0:v' '1:v') + ",scale=${pw}:${ph}:flags=area[main]"
        $label = 'plates'
    }
    else {
        if (IsSeq $In) {
            if (-not (Test-Path (Expand-FramePattern $In $Start))) { throw "first frame not found: $(Expand-FramePattern $In $Start)" }
            $ff += @('-framerate', "$fps", '-start_number', "$Start", '-i', $In)
        } else {
            if (-not (Test-Path $In)) { throw "not found: $In" }
            $ff += @('-i', $In)
        }
        $fc = "[0:v]scale=${pw}:${ph}:flags=area[main]"
        $label = [IO.Path]::GetFileNameWithoutExtension($In) -replace '\.%0\dd$', ''
    }
}

# stack the source noise underneath, so you can see you are sitting ON it
if ($Compare -and -not $Source) {
    $n = @($ff | Where-Object { $_ -eq '-i' }).Count
    $ff += @('-ss', $srcSs, '-i', $v1, '-ss', $srcSs, '-i', $v2)
    $fc += ";" + (Stitch "${n}:v" "$($n+1):v") + ",scale=${pw}:${ph}:flags=area[ref]"
    $fc += ";[main][ref]vstack=2[stk]"
    $last = 'stk'
} else {
    $last = 'main'
}

if (-not $NoBurnin) {
    $font = "C\:/Windows/Fonts/consola.ttf"
    if (-not (Test-Path 'C:\Windows\Fonts\consola.ttf')) { $font = "C\:/Windows/Fonts/arial.ttf" }
    $tc = "%{eif\:floor(($Start+n)/$fps/60)\:d\:2}\\\:%{eif\:mod(floor(($Start+n)/$fps)\,60)\:d\:2}.%{eif\:mod($Start+n\,$fps)\:d\:2}"
    $txt = "f %{eif\:$Start+n\:d\:5}   $tc"
    $fc += ";[$last]drawtext=fontfile='$font':text='$txt':x=12:y=h-30:fontsize=20:fontcolor=yellow:box=1:boxcolor=black@0.65:boxborderw=5[out]"
    $last = 'out'
}

if (-not $Out) {
    $Out = Join-Path (Join-Path $WorkRoot 'preview') ("test_{0}_{1}x{2}.mp4" -f $label, $pw, $ph)
}
New-Item -ItemType Directory -Force -Path (Split-Path $Out -Parent) | Out-Null

$ff += @('-filter_complex', $fc, '-map', "[$last]")
$ff += @('-frames:v', "$Count")
if (Test-FFmpegEncoder 'libx264') {
    $ff += @('-c:v', 'libx264', '-crf', "$Crf", '-preset', 'veryfast', '-pix_fmt', 'yuv420p',
               '-movflags', '+faststart')
} else {
    Write-Host "note: this ffmpeg has no libx264, falling back to mpeg4. A full build gives" -ForegroundColor Yellow
    Write-Host "      much smaller previews - see docs/02_PORTABLE_RENDER.md" -ForegroundColor Yellow
    $ff += @('-c:v', 'mpeg4', '-q:v', '4')
}
$ff += @('-r', "$fps", '-y', $Out)

Write-Host ""
Write-Host ("preview   {0} x {1}   {2} frames from {3}" -f $pw, ($(if ($Compare -and -not $Source) { $ph * 2 } else { $ph })), $Count, $Start)
Write-Host ("out       {0}" -f $Out)
Write-Host ""
Invoke-FFmpeg $ff 'preview render'

$sz = (Get-Item $Out).Length / 1MB
Write-Host ""
Write-Host ("done  {0:N1} MB" -f $sz)
Write-Host $Out

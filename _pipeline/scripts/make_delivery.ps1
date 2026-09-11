<#
  Encode the two delivery plates.

  Two ways in:

    -MasterPattern   encode straight from the 9788x2552 master, cropping each
                     plate on the fly. One pass, no intermediate sequence, and
                     the overlap is still guaranteed identical because both
                     plates come from the same source frames. Use this.

    -SeqRoot         encode from plate sequences already made by
                     master_to_plates.ps1 (when you want a lossless plate
                     archive as well).

  Codecs (-Codec):
    prores4444   ProRes 4444, 12-bit 4:4:4   <- default, the safe "best quality"
    prores422hq  ProRes 422 HQ, 10-bit 4:2:2   about half the size
    dnxhr444     DNxHR 444, 12-bit
    ffv1         FFV1 in MKV, mathematically lossless
    h264         H.264 CRF 12, only if the producer insists on mp4
    png16        16-bit PNG sequence, lossless, no codec ambiguity at all

  Examples
      .\make_delivery.ps1 -MasterPattern "E:\PxDL\render\master\master.%05d.png"
      .\make_delivery.ps1 -MasterPattern ... -Codec prores422hq
      .\make_delivery.ps1 -SeqRoot "E:\PxDL\deliver" -Codec ffv1
#>
[CmdletBinding()]
param(
    [string] $MasterPattern = '',
    [string] $SeqRoot       = '',
    [int]    $StartFrame    = -1,
    [int]    $Count         = -1,
    [ValidateSet('prores4444', 'prores422hq', 'dnxhr444', 'ffv1', 'h264', 'png16')]
    [string] $Codec         = 'prores4444',
    [string] $OutRoot       = '',
    [switch] $ApplyProjectableMask,
    [switch] $DryRun
)

. "$PSScriptRoot\_common.ps1"
Show-Context

if (-not $MasterPattern -and -not $SeqRoot) { throw "give either -MasterPattern or -SeqRoot" }
if ($MasterPattern -and $SeqRoot)           { throw "give -MasterPattern or -SeqRoot, not both" }
if ($StartFrame -lt 0) { $StartFrame = [int]$Cfg.segment.in - [int]$Cfg.segment.handles }
if ($Count -lt 0) {
    $Count = ([int]$Cfg.segment.out - [int]$Cfg.segment.in + 1) + 2 * [int]$Cfg.segment.handles
}
if (-not $OutRoot) { $OutRoot = $DeliverRoot }
$fps = [int]$Cfg.fps

# ---- codec table ------------------------------------------------------------
$PRESETS = @{
    'prores4444'  = @{ enc = 'prores_ks'; ext = 'mov'; pix = 'yuva444p10le'
                       args = @('-profile:v', '4', '-vendor', 'apl0', '-bits_per_mb', '8000') }
    'prores422hq' = @{ enc = 'prores_ks'; ext = 'mov'; pix = 'yuv422p10le'
                       args = @('-profile:v', '3', '-vendor', 'apl0') }
    'dnxhr444'    = @{ enc = 'dnxhd';     ext = 'mov'; pix = 'yuv444p10le'
                       args = @('-profile:v', 'dnxhr_444') }
    'ffv1'        = @{ enc = 'ffv1';      ext = 'mkv'; pix = 'gbrp16le'
                       args = @('-level', '3', '-g', '1', '-slices', '16', '-slicecrc', '1') }
    'h264'        = @{ enc = 'libx264';   ext = 'mp4'; pix = 'yuv420p'
                       args = @('-crf', '12', '-preset', 'slow', '-x264-params', 'keyint=30:min-keyint=30') }
    'png16'       = @{ enc = 'png';       ext = 'png'; pix = 'rgb48be'; args = @('-compression_level', '3') }
}
$Pre = $PRESETS[$Codec]

if (-not (Test-FFmpegEncoder $Pre.enc)) {
    throw @"
This ffmpeg build has no '$($Pre.enc)' encoder.
  using: $FFmpegExe
The build bundled with TouchDesigner decodes everything but can only encode
png / dpx / ffv1 / mjpeg / mpeg4 - no ProRes, DNxHR or H.264.

Fix: download a full build (gyan.dev 'full' or BtbN win64-gpl), then either put
it on PATH or point at it:
    `$env:PXDL_FFMPEG = "E:\tools\ffmpeg\bin\ffmpeg.exe"
Or choose a codec this build supports:  -Codec ffv1   /   -Codec png16
"@
}

Write-Host ("codec     {0}  ({1}, {2}, .{3})" -f $Codec, $Pre.enc, $Pre.pix, $Pre.ext)
Write-Host ("frames    {0} .. {1}  ({2} frames, {3:N2} s)" -f $StartFrame, ($StartFrame + $Count - 1), $Count, ($Count / $fps))
Write-Host ("out       {0}" -f $OutRoot)
Write-Host ""

New-Item -ItemType Directory -Force -Path $OutRoot | Out-Null
$maskPath = Join-Path $MaskRoot 'PxDL_SW_MASK_08_PROJECTABLE_9788x2552.png'
if ($ApplyProjectableMask -and -not (Test-Path $maskPath)) { throw "mask not found: $maskPath" }

foreach ($p in $Cfg.plates) {
    $a = @('-hide_banner', '-loglevel', 'warning', '-stats', '-framerate', "$fps")

    if ($MasterPattern) {
        $first = Expand-FramePattern $MasterPattern $StartFrame
        if (-not (Test-Path $first)) { throw "first master frame not found: $first" }
        $a += @('-start_number', "$StartFrame", '-i', $MasterPattern)
        $crop = "crop=$($p.w):$($p.h):$($p.x):0"
        if ($ApplyProjectableMask) {
            $a += @('-loop', '1', '-i', $maskPath)
            $vf = "[1:v]format=gray,scale=$($Cfg.canvas.w):$($Cfg.canvas.h)[m];" +
                  "[0:v]format=gbrp16le[v];[v][m]blend=all_mode=multiply:shortest=1,$crop[o]"
            $a += @('-filter_complex', $vf, '-map', '[o]')
        } else {
            $a += @('-vf', $crop)
        }
    }
    else {
        $seq = Join-Path (Join-Path $SeqRoot $p.name) ("PxDL_SW_{0}.%05d.png" -f $p.name)
        $first = Expand-FramePattern $seq $StartFrame
        if (-not (Test-Path $first)) { throw "first plate frame not found: $first" }
        $a += @('-start_number', "$StartFrame", '-i', $seq)
    }

    if ($Codec -eq 'png16') {
        $dir = Join-Path $OutRoot ("{0}_png16" -f $p.name)
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
        $out = Join-Path $dir ("PxDL_SW_{0}.%05d.png" -f $p.name)
        $a += @('-start_number', "$StartFrame")
    } else {
        $out = Join-Path $OutRoot ("PxDL_SW_{0}_{1}-{2}.{3}" -f $p.name, $StartFrame, ($StartFrame + $Count - 1), $Pre.ext)
    }

    $a += @('-c:v', $Pre.enc) + $Pre.args
    $a += @('-pix_fmt', $Pre.pix)
    # tag the colour explicitly - the two source plates disagree about this and
    # an untagged delivery is how a brightness step at the seam gets in
    $a += @('-color_primaries', 'bt709', '-color_trc', 'bt709', '-colorspace', 'bt709')
    $a += @('-frames:v', "$Count", '-y', $out)

    Write-Host ("{0}  {1} x {2}  ->  {3}" -f $p.name, $p.w, $p.h, $out)
    if ($DryRun) { Write-Host ("    " + ($a -join ' ')) ; continue }
    Invoke-FFmpeg $a "encode $($p.name)"
}

Write-Host ""
if (-not $DryRun) {
    Write-Host "verify the encoded plates before you send them:"
    $e = $Pre.ext
    if ($Codec -eq 'png16') {
        Write-Host "  python `"$PSScriptRoot\verify_plates.py`" --a `"$OutRoot\$($Cfg.plates[0].name)_png16\PxDL_SW_$($Cfg.plates[0].name).%05d.png`" --b `"$OutRoot\$($Cfg.plates[1].name)_png16\PxDL_SW_$($Cfg.plates[1].name).%05d.png`" --start $StartFrame"
    } else {
        $n1 = "PxDL_SW_$($Cfg.plates[0].name)_$StartFrame-$($StartFrame + $Count - 1).$e"
        $n2 = "PxDL_SW_$($Cfg.plates[1].name)_$StartFrame-$($StartFrame + $Count - 1).$e"
        Write-Host "  python `"$PSScriptRoot\verify_plates.py`" --a `"$OutRoot\$n1`" --b `"$OutRoot\$n2`" --tol 2"
        Write-Host "  (lossy codecs will not hit 0.000 in the overlap - allow a small tolerance)"
    }
}

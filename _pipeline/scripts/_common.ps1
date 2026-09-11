<#
  Shared setup for every script in this folder.  Dot-source it:

      . "$PSScriptRoot\_common.ps1"

  Nothing here is machine-specific. Copy the whole project folder to any PC and
  the scripts resolve themselves from their own location. Point the big output
  folders at an external drive with environment variables:

      $env:PXDL_RENDER_ROOT  = "E:\PxDL\render"
      $env:PXDL_DELIVER_ROOT = "E:\PxDL\deliver"
      $env:PXDL_WORK_ROOT    = "E:\PxDL\work"
      $env:PXDL_FFMPEG       = "E:\tools\ffmpeg\bin\ffmpeg.exe"
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# ---- project root -----------------------------------------------------------
if ($env:PXDL_ROOT) {
    $script:ProjectRoot = (Resolve-Path $env:PXDL_ROOT).Path
} else {
    # scripts/ -> _pipeline/ -> project root
    $script:ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
}
$script:PipelineRoot = Join-Path $ProjectRoot '_pipeline'

$cfgPath = Join-Path $PipelineRoot 'project.json'
if (-not (Test-Path $cfgPath)) { throw "project.json not found at $cfgPath" }
$script:Cfg = Get-Content $cfgPath -Raw | ConvertFrom-Json

# ---- output roots (env-overridable so they can live on an external drive) ----
function Resolve-Root([string]$envName, [string]$relative) {
    $v = [Environment]::GetEnvironmentVariable($envName)
    if ($v) { return $v }
    return (Join-Path $ProjectRoot $relative)
}
$script:WorkRoot    = Resolve-Root 'PXDL_WORK_ROOT'    $Cfg.dirs.work
$script:RenderRoot  = Resolve-Root 'PXDL_RENDER_ROOT'  $Cfg.dirs.render
$script:DeliverRoot = Resolve-Root 'PXDL_DELIVER_ROOT' $Cfg.dirs.deliver
$script:MaskRoot    = Join-Path $ProjectRoot $Cfg.dirs.masks
$script:RefRoot     = Join-Path $ProjectRoot $Cfg.dirs.reference

# ---- ffmpeg -----------------------------------------------------------------
function Find-FFmpeg {
    if ($env:PXDL_FFMPEG) {
        if (Test-Path $env:PXDL_FFMPEG) { return $env:PXDL_FFMPEG }
        throw "PXDL_FFMPEG is set but does not exist: $env:PXDL_FFMPEG"
    }
    $candidates = @(
        (Join-Path $PipelineRoot 'bin\ffmpeg.exe'),
        'C:\Program Files\Derivative\TouchDesigner\bin\ffmpeg.exe',
        'C:\ffmpeg\bin\ffmpeg.exe'
    )
    # any TouchDesigner version
    Get-ChildItem 'C:\Program Files\Derivative' -Directory -ErrorAction SilentlyContinue |
        ForEach-Object { $candidates += (Join-Path $_.FullName 'bin\ffmpeg.exe') }
    $onPath = Get-Command ffmpeg -ErrorAction SilentlyContinue
    if ($onPath) { $candidates = @($onPath.Source) + $candidates }

    foreach ($c in $candidates) { if ($c -and (Test-Path $c)) { return $c } }
    throw @"
ffmpeg not found. Do one of:
  - put ffmpeg.exe on PATH
  - set `$env:PXDL_FFMPEG to its full path
  - drop it in $PipelineRoot\bin\ffmpeg.exe
A full build (with x264 / ProRes) from gyan.dev or BtbN is worth having; the one
bundled with TouchDesigner decodes everything but cannot encode H.264 or ProRes.
"@
}
$script:FFmpegExe = Find-FFmpeg

function Test-FFmpegEncoder([string]$name) {
    $out = & $FFmpegExe -hide_banner -encoders 2>&1 | Out-String
    return ($out -match "\s$([regex]::Escape($name))\s")
}

# ---- helpers ----------------------------------------------------------------
function Get-Plate([string]$name) {
    $p = $Cfg.plates | Where-Object { $_.name -eq $name }
    if (-not $p) { throw "unknown plate '$name' (have: $($Cfg.plates.name -join ', '))" }
    return $p
}

function Get-SourcePath([string]$key, [bool]$UseHQ = $false) {
    if ($UseHQ) {
        $hq = $null
        if ($Cfg.PSObject.Properties.Name -contains 'source_hq') { $hq = $Cfg.source_hq.$key }
        if (-not $hq) {
            throw @"
-HQ was asked for but source_hq.$key is empty in project.json.
Put the path to the ProRes master there, e.g.
    "source_hq": { "$key": "E:/PxDL/master/PxDL_SW_$key.mov" }
"@
        }
        $p = if ([IO.Path]::IsPathRooted($hq)) { $hq } else { Join-Path $ProjectRoot $hq }
        if (-not (Test-Path $p)) { throw "HQ source missing: $p" }
        return $p
    }
    $rel = $Cfg.source.$key
    if (-not $rel) { throw "no source '$key' in project.json" }
    $p = Join-Path $ProjectRoot $rel
    if (-not (Test-Path $p)) { throw "source file missing: $p" }
    return $p
}

function Get-Scale([int]$div) {
    $s = $Cfg.scales.PSObject.Properties |
         Where-Object { $_.Value -is [PSCustomObject] -and $_.Value.div -eq $div }
    if (-not $s) { throw "div must be 1, 2 or 4 (whole-pixel divisors of 9788x2552)" }
    return $s.Value
}

function Expand-FramePattern([string]$pattern, [int]$frame) {
    $m = [regex]::Match($pattern, '%0(\d)d')
    if (-not $m.Success) { throw "pattern must contain a printf token such as %05d: $pattern" }
    return $pattern.Substring(0, $m.Index) +
           $frame.ToString('D' + [int]$m.Groups[1].Value) +
           $pattern.Substring($m.Index + $m.Length)
}

function Invoke-FFmpeg([string[]]$Arguments, [string]$What = 'ffmpeg') {
    # ffmpeg writes its progress to stderr. With $ErrorActionPreference='Stop'
    # PowerShell turns those lines into terminating NativeCommandErrors as soon
    # as the output is piped anywhere, so relax it here and trust the exit code.
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $FFmpegExe @Arguments 2>&1 | ForEach-Object {
            if ($_ -is [System.Management.Automation.ErrorRecord]) { Write-Host $_.Exception.Message }
            else { Write-Host $_ }
        }
        $code = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $prev
    }
    if ($code -ne 0) { throw "$What failed (exit $code)" }
}

function Show-Context {
    Write-Host ""
    Write-Host "project   $($Cfg.project)  [$($Cfg.surface)]"
    Write-Host "root      $ProjectRoot"
    Write-Host "ffmpeg    $FFmpegExe"
    Write-Host "canvas    $($Cfg.canvas.w) x $($Cfg.canvas.h) @ $($Cfg.fps) fps"
    Write-Host "segment   frames $($Cfg.segment.in) .. $($Cfg.segment.out)  (+/- $($Cfg.segment.handles) handles)"
    Write-Host "work      $WorkRoot"
    Write-Host "render    $RenderRoot"
    Write-Host "deliver   $DeliverRoot"
    Write-Host ""
}

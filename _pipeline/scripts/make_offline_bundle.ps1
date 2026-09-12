<#
  Phoenix de Lumiere - build an OFFLINE INSTALL BUNDLE

  Run this on a machine that HAS working internet:

      .\make_offline_bundle.ps1

  It collects everything the pipeline needs but cannot get by itself - the
  python wheels and a full ffmpeg - into one folder with an installer in it.
  Copy that folder to the machine with no internet, double-click INSTALL.cmd,
  and it is done. The installer never touches the network.

  WHY THIS EXISTS

  A render machine with broken DNS cannot reach pypi.org or gyan.dev, so
  neither `pip install moderngl` nor get_ffmpeg.ps1 can work there - both fail
  with "getaddrinfo failed" or "Could not resolve host". The project itself
  travels fine in git; these two do not, because one is ~13 MB per python
  version and the other is ~100 MB.

      .\make_offline_bundle.ps1 -Dest E:\                    onto a USB stick
      .\make_offline_bundle.ps1 -PythonVersions 310          only that python
      .\make_offline_bundle.ps1 -Force                       re-fetch everything

  Wheels are downloaded for several python versions because the target machine
  usually has a different one - this one is 3.11, the render PC is 3.10, and a
  wheel is version-specific. They are small next to ffmpeg.
#>
[CmdletBinding()]
param(
    [string]   $Dest,
    [string[]] $PythonVersions = @('310', '311', '312', '313'),
    [switch]   $Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
if (-not $Dest) { $Dest = Join-Path $ProjectRoot '_offline_bundle' }

# What the pipeline imports, and nothing else: render_shader.py needs moderngl
# (which pulls glcontext) and every script needs numpy. There is no Pillow -
# every image goes through ffmpeg.
$Packages = 'moderngl', 'numpy'

$ZipUrl = 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip'
$ShaUrl = 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip.sha256'

function Line { param([string]$s = '') Write-Host $s }
function Head { param([string]$s) Line; Line ('=' * 74); Line $s; Line ('=' * 74) }
function Good { param([string]$s) Write-Host "  OK    $s" -ForegroundColor Green }
function Bad  { param([string]$s) Write-Host "  FAIL  $s" -ForegroundColor Red }
function Warn { param([string]$s) Write-Host "  warn  $s" -ForegroundColor Yellow }

# Same stderr guard as _common.ps1's Invoke-Native, repeated because this
# script deliberately does not dot-source it: it has to run on a machine where
# there is no ffmpeg yet, and _common.ps1 throws while looking for one.
function Invoke-Quiet {
    param([string]$Exe, [string[]]$Arguments = @())
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $out = & $Exe @Arguments 2>&1 | ForEach-Object {
            if ($_ -is [Management.Automation.ErrorRecord]) { $_.Exception.Message }
            else { [string]$_ }
        }
        $code = $LASTEXITCODE
    }
    finally { $ErrorActionPreference = $prev }
    return [pscustomobject]@{ Ok = ($code -eq 0); Lines = @($out); Text = (@($out) -join "`n") }
}

Head 'BUILD AN OFFLINE INSTALL BUNDLE'
Line
Line "  into   $Dest"
Line "  for    python $($PythonVersions -join ', ')  on 64-bit Windows"
Line

$WheelDir = Join-Path $Dest 'wheels'
New-Item -ItemType Directory -Force $WheelDir | Out-Null
$fail = @()

# ------------------------------------------------------------------ wheels ---
Head 'PYTHON WHEELS'
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Bad 'python is not on PATH here, so the wheels cannot be downloaded'
    exit 1
}
foreach ($v in $PythonVersions) {
    # --only-binary is required with --python-version: pip will not build a
    # source distribution for an interpreter it is not running under, and a
    # wheel is the only thing the offline machine can install anyway.
    # The argument array is built FIRST and passed as one thing. Writing
    # `Invoke-Quiet 'python' @(...) + $Packages` instead looks like
    # concatenation and is not: PowerShell reads the @(...) as one positional
    # argument and then `+` and $Packages as two more, so pip ran with no
    # package names at all.
    $dlArgs = @(
        '-m', 'pip', 'download',
        '--only-binary=:all:',
        '--platform', 'win_amd64',
        '--python-version', $v,
        '-d', $WheelDir) + $Packages
    $r = Invoke-Quiet 'python' $dlArgs
    if ($r.Ok) {
        Good ("python $v")
    } else {
        Warn ("python $v - no wheels for this version, skipping")
        $last = ($r.Lines | Where-Object { $_ -match 'ERROR|error:' } | Select-Object -Last 1)
        if ($last) { Line ("        {0}" -f $last) }
    }
}
$wheels = @(Get-ChildItem $WheelDir -Filter '*.whl' -ErrorAction SilentlyContinue)
if (-not $wheels.Count) {
    Bad 'no wheels were downloaded at all'
    exit 1
}
Line
Good ("{0} wheels, {1:N1} MB" -f $wheels.Count,
      (($wheels | Measure-Object -Property Length -Sum).Sum / 1MB))
# Which python versions are actually covered - the installer prints this back
# when it cannot find a wheel, which is the one confusing failure left.
$tags = @($wheels | ForEach-Object {
    if ($_.Name -match '-cp(\d\d\d)-') { $Matches[1] }
} | Sort-Object -Unique)
if ($tags.Count) { Line ("        covers python {0}" -f (($tags | ForEach-Object {
    $_.Substring(0,1) + '.' + $_.Substring(1) }) -join ', ')) }

# ------------------------------------------------------------------ ffmpeg ---
Head 'FFMPEG'
$FFDir = Join-Path $Dest 'ffmpeg'
New-Item -ItemType Directory -Force $FFDir | Out-Null
$zip = Join-Path $FFDir 'ffmpeg-release-essentials.zip'
$shaFile = Join-Path $FFDir 'ffmpeg-release-essentials.zip.sha256'

try {
    [Net.ServicePointManager]::SecurityProtocol =
        [Net.SecurityProtocolType]::Tls12 -bor [Net.ServicePointManager]::SecurityProtocol
} catch { }
$prevProgress = $ProgressPreference
$ProgressPreference = 'SilentlyContinue'
try {
    Line "  asking the publisher for the current checksum"
    $published = ((Invoke-WebRequest -Uri $ShaUrl -UseBasicParsing).Content |
                  Out-String).Trim().Split()[0]
    Good ("published  {0}" -f $published.ToLower())

    # Re-use a zip that is already here and already correct. Re-downloading
    # 106 MB to rebuild a bundle is the sort of thing that stops people
    # rebuilding it.
    $haveGood = $false
    if ((Test-Path $zip) -and -not $Force) {
        if ((Get-FileHash $zip -Algorithm SHA256).Hash -ieq $published) {
            Good 'the zip already here matches - not downloading again'
            $haveGood = $true
        } else {
            Warn 'the zip already here is a different version - replacing it'
        }
    }

    if (-not $haveGood) {
        Line "  downloading $ZipUrl"
        Line '  about 106 MB - this is the slow part'
        Invoke-WebRequest -Uri $ZipUrl -OutFile $zip -UseBasicParsing
        $actual = (Get-FileHash $zip -Algorithm SHA256).Hash
        if ($actual -ine $published) {
            Bad 'checksum does NOT match what the publisher lists'
            Line "        published $published"
            Line "        got       $actual"
            Remove-Item $zip -Force -ErrorAction SilentlyContinue
            exit 1
        }
        Good ("downloaded and verified  {0:N1} MB" -f ((Get-Item $zip).Length / 1MB))
    }
    # Written next to the zip so the OFFLINE machine can check it too, without
    # needing to ask anybody.
    Set-Content -LiteralPath $shaFile -Value $published.ToLower() -Encoding ascii
}
finally { $ProgressPreference = $prevProgress }

# The zip travels rather than the two unpacked exes: 106 MB against 196 MB, and
# it is the form the checksum belongs to.

# --------------------------------------------------------------- installer ---
Head 'INSTALLER'
Copy-Item (Join-Path $PSScriptRoot 'offline_install.ps1') (Join-Path $Dest 'install.ps1') -Force
Copy-Item (Join-Path $PSScriptRoot 'offline_INSTALL.cmd')  (Join-Path $Dest 'INSTALL.cmd') -Force
Good 'INSTALL.cmd + install.ps1'

@"
PHOENIX DE LUMIERE - offline install bundle
built $(Get-Date -Format 'yyyy-MM-dd HH:mm') on $env:COMPUTERNAME

WHAT THIS IS

  Everything the render pipeline needs that it cannot download on a machine
  with no working internet: the python packages and a full ffmpeg.

HOW TO USE IT

  1. Copy this whole folder to the other machine. Anywhere will do.
  2. Double-click INSTALL.cmd.
  3. It asks where the project folder is, unless it can work that out itself.

  Nothing is downloaded, nothing is installed system-wide, no administrator
  rights are needed. It puts the python packages into that machine's python
  and ffmpeg.exe into the project's _pipeline\bin\.

WHAT IS IN HERE

  wheels\      python packages: $($Packages -join ', ') (+ glcontext)
               $($wheels.Count) files, for python $(($tags | ForEach-Object { $_.Substring(0,1) + '.' + $_.Substring(1) }) -join ', ')
  ffmpeg\      the official Windows build as published, plus its SHA-256
               so the installer can verify it without the internet
  INSTALL.cmd  double-click this
  install.ps1  what INSTALL.cmd runs, if you would rather read it first

AFTERWARDS

  Check it took:

      cd <project>\_pipeline\scripts
      .\check_environment.ps1

  Then run EXPORT.cmd in the project root as usual.
"@ | Set-Content -LiteralPath (Join-Path $Dest 'README.txt') -Encoding utf8
Good 'README.txt'

# -------------------------------------------------------------------- done ---
$totalMB = ((Get-ChildItem $Dest -Recurse -File | Measure-Object -Property Length -Sum).Sum / 1MB)
Head 'DONE'
Line ("  {0}" -f $Dest)
Line ("  {0:N1} MB in total" -f $totalMB)
Line
Line '  Copy that folder to the machine with no internet and double-click'
Line '  INSTALL.cmd inside it.'
Line
if ($fail.Count) { foreach ($f in $fail) { Bad $f }; exit 1 }

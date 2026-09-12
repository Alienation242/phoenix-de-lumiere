<#
  Phoenix de Lumiere - put a USABLE ffmpeg inside the project

  Run this once on any machine the project is copied to:

      .\get_ffmpeg.ps1

  It downloads the official Windows build, checks it against the checksum the
  publisher lists, and puts ffmpeg.exe and ffprobe.exe in _pipeline\bin\, which
  every script in this project looks at first. Nothing is installed, nothing is
  put on PATH, no administrator rights are needed, and nothing outside the
  project folder is touched.

  WHY THIS EXISTS

  The ffmpeg that comes with TouchDesigner decodes everything and encodes
  almost nothing - no libx264, no ProRes, no DNxHR. If that is the one that
  gets found, every delivery preset stops at preflight, and the previews fall
  back to mpeg4 (a 49 MB file where x264 makes about 5 MB). The binary is
  ~100 MB, which is too big to keep in git, so it is fetched instead of
  committed: _pipeline\bin\ is in .gitignore.

      .\get_ffmpeg.ps1            do nothing if a good one is already there
      .\get_ffmpeg.ps1 -Force     replace whatever is there
      .\get_ffmpeg.ps1 -Check     only report what the current one can do
#>
[CmdletBinding()]
param(
    [switch] $Force,
    [switch] $Check
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$PipelineRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$BinDir       = Join-Path $PipelineRoot 'bin'
$Exe          = Join-Path $BinDir 'ffmpeg.exe'

# The encoders this project cannot deliver without: h264 for every preview and
# the Draft/Proof presets, prores_ks for the delivery itself, dnxhd for the
# alternative the producer may ask for.
$Needed = 'libx264', 'prores_ks', 'dnxhd'

$ZipUrl = 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip'
$ShaUrl = 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip.sha256'

function Line { param([string]$s = '') Write-Host $s }
function Good { param([string]$s) Write-Host "  OK    $s" -ForegroundColor Green }
function Bad  { param([string]$s) Write-Host "  FAIL  $s" -ForegroundColor Red }
function Warn { param([string]$s) Write-Host "  warn  $s" -ForegroundColor Yellow }

function Test-Build {
    # What can this binary actually encode? Returns $null if it will not run at
    # all, otherwise the list of wanted encoders it is missing.
    param([string]$Path)
    if (-not (Test-Path $Path)) { return $null }
    try { $enc = & $Path -hide_banner -encoders 2>&1 | Out-String }
    catch { return $null }
    if (-not $enc) { return $null }
    # The leading comma matters. PowerShell unrolls a returned array, and an
    # EMPTY one unrolls to nothing at all - so "no encoders missing", the good
    # case, came back as $null and read as "there is no ffmpeg here".
    return ,@($Needed | Where-Object { $enc -notmatch "\s$([regex]::Escape($_))\s" })
}

function Show-Build {
    param([string]$Path)
    $ver = (& $Path -hide_banner -version 2>&1 | Select-Object -First 1)
    Line ("  {0}" -f $ver)
    Line ("  {0}" -f $Path)
}

Line
Line '=========================================================================='
Line ' ffmpeg for this project'
Line '=========================================================================='
Line

# ------------------------------------------------------------ what is here ---
$missing = Test-Build $Exe
if ($null -ne $missing -and $missing.Count -eq 0) {
    Show-Build $Exe
    Good ('has ' + ($Needed -join ', '))
    if ($Check) { Line; exit 0 }
    if (-not $Force) {
        Line
        Line '  Nothing to do. Use -Force to replace it anyway.'
        Line
        exit 0
    }
} elseif ($Check) {
    if ($null -eq $missing) { Bad "no working ffmpeg at $Exe" }
    else { Show-Build $Exe; Bad ('missing encoders: ' + ($missing -join ', ')) }
    Line
    Line '  Run this script without -Check to fetch a build that has them.'
    Line
    exit 1
} elseif ($null -ne $missing) {
    Warn ('the ffmpeg in bin\ is missing: ' + ($missing -join ', ') + ' - replacing it')
}

# ------------------------------------------------------------------ fetch ----
# Windows PowerShell 5.1 still negotiates SSL3/TLS1.0 by default on some
# machines, which this host refuses outright.
try {
    [Net.ServicePointManager]::SecurityProtocol =
        [Net.SecurityProtocolType]::Tls12 -bor [Net.ServicePointManager]::SecurityProtocol
} catch { }

# Invoke-WebRequest renders a progress bar by redrawing the console on every
# chunk, which on a 100 MB download costs far more time than the download.
$prevProgress = $ProgressPreference
$ProgressPreference = 'SilentlyContinue'

$tmp = Join-Path ([IO.Path]::GetTempPath()) ('pxdl_ffmpeg_' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Force $tmp | Out-Null
$zip = Join-Path $tmp 'ffmpeg.zip'

try {
    Line "  downloading $ZipUrl"
    Line '  about 106 MB - this is the slow part'
    Invoke-WebRequest -Uri $ZipUrl -OutFile $zip -UseBasicParsing
    $sizeMB = [math]::Round((Get-Item $zip).Length / 1MB, 1)
    Good ("downloaded {0} MB" -f $sizeMB)

    # The publisher lists a SHA-256 next to the file. It proves the download
    # arrived intact and is the file that was published, which is the whole
    # reason to prefer this over "grab an exe from somewhere".
    $published = ((Invoke-WebRequest -Uri $ShaUrl -UseBasicParsing).Content |
                  Out-String).Trim().Split()[0]
    $actual = (Get-FileHash $zip -Algorithm SHA256).Hash
    if ($actual -ine $published) {
        Bad 'checksum does NOT match - the download is not what was published'
        Line "        published $published"
        Line "        got       $actual"
        Line '        Nothing was installed. Try again; if it fails twice, do not use the file.'
        exit 1
    }
    Good ("checksum matches  {0}" -f $actual.ToLower())

    Line '  unpacking'
    Expand-Archive -Path $zip -DestinationPath (Join-Path $tmp 'x') -Force

    $src = Get-ChildItem (Join-Path $tmp 'x') -Recurse -Filter 'ffmpeg.exe' |
           Select-Object -First 1
    if (-not $src) { Bad 'no ffmpeg.exe inside the archive'; exit 1 }
    $srcBin = $src.Directory.FullName

    New-Item -ItemType Directory -Force $BinDir | Out-Null
    foreach ($n in 'ffmpeg.exe', 'ffprobe.exe') {
        $from = Join-Path $srcBin $n
        if (Test-Path $from) { Copy-Item $from (Join-Path $BinDir $n) -Force }
    }
    # ffmpeg is GPL/LGPL; keep its licence next to the binary rather than
    # shipping a stripped executable with no notice attached.
    $lic = Get-ChildItem (Join-Path $tmp 'x') -Recurse -Filter 'LICENSE*' |
           Select-Object -First 1
    if ($lic) { Copy-Item $lic.FullName (Join-Path $BinDir 'FFMPEG_LICENSE.txt') -Force }
}
finally {
    $ProgressPreference = $prevProgress
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
}

# ----------------------------------------------------------------- verify ----
Line
$missing = Test-Build $Exe
if ($null -eq $missing) { Bad "the new ffmpeg will not run: $Exe"; exit 1 }
Show-Build $Exe
if ($missing.Count) {
    Bad ('this build is missing: ' + ($missing -join ', '))
    Line '        That should not happen with this build. Check the URL above.'
    exit 1
}
Good ('has ' + ($Needed -join ', '))
Line
Line '  Every script in the project finds this automatically - it is checked'
Line '  before PATH, so a cut-down ffmpeg installed elsewhere cannot win.'
Line '  It is NOT in git (too big); run this again on the next machine.'
Line

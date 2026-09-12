<#
  Phoenix de Lumiere - install from the offline bundle

  This is the script that gets copied into an offline bundle as install.ps1 and
  run by INSTALL.cmd next to it. It is also kept here in scripts\ so it travels
  in git and there is one copy to maintain.

  It installs, from files sitting beside it and WITHOUT touching the network:

    - the python packages the pipeline imports (moderngl, numpy, glcontext)
    - a full ffmpeg, into the project's _pipeline\bin\

  Nothing goes system-wide and no administrator rights are needed.

      .\install.ps1
      .\install.ps1 -ProjectRoot D:\KevinClever_PxDL\phoenix-de-lumiere-main
#>
[CmdletBinding()]
param(
    [string] $ProjectRoot,
    [switch] $SkipPython,
    [switch] $SkipFFmpeg
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$BundleDir = $PSScriptRoot
$WheelDir  = Join-Path $BundleDir 'wheels'
$FFDir     = Join-Path $BundleDir 'ffmpeg'
$Packages  = 'moderngl', 'numpy'
$Needed    = 'libx264', 'prores_ks', 'dnxhd'

function Line { param([string]$s = '') Write-Host $s }
function Head { param([string]$s) Line; Line ('=' * 74); Line $s; Line ('=' * 74) }
function Good { param([string]$s) Write-Host "  OK    $s" -ForegroundColor Green }
function Bad  { param([string]$s) Write-Host "  FAIL  $s" -ForegroundColor Red }
function Warn { param([string]$s) Write-Host "  warn  $s" -ForegroundColor Yellow }

# Redirecting a native program's stderr in Windows PowerShell turns each line
# into a NativeCommandError, and $ErrorActionPreference='Stop' makes the first
# one terminate the script - so a failing pip would kill this installer instead
# of reporting why. Everything external goes through here.
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
    return [pscustomobject]@{
        Ok = ($code -eq 0); Lines = @($out); Text = (@($out) -join "`n")
    }
}

function Find-ProjectRoot {
    # The project is recognised by _pipeline\project.json - look up through the
    # bundle's own ancestors first, then one level sideways from each of them,
    # which covers the usual "bundle dropped next to the project folder".
    $d = Get-Item $BundleDir
    while ($d) {
        if (Test-Path (Join-Path $d.FullName '_pipeline\project.json')) { return $d.FullName }
        $d = $d.Parent
    }
    $d = (Get-Item $BundleDir).Parent
    while ($d) {
        foreach ($c in (Get-ChildItem $d.FullName -Directory -ErrorAction SilentlyContinue)) {
            if (Test-Path (Join-Path $c.FullName '_pipeline\project.json')) { return $c.FullName }
        }
        $d = $d.Parent
    }
    return $null
}

function Read-ProjectRoot {
    Line
    Line '  Where is the project folder? It is the one with EXPORT.cmd in it.'
    Line '  A path copied from Explorer works with its quotes still on.'
    Line
    while ($true) {
        $ans = Read-Host 'Project folder'
        if ($ans) { $ans = $ans.Trim().Trim('"').Trim() }
        if (-not $ans) { Line '  (nothing typed - press Ctrl+C to give up)'; continue }
        try { $full = [IO.Path]::GetFullPath([IO.Path]::Combine($BundleDir, $ans)) }
        catch { Bad ("not a usable path: {0}" -f $ans); continue }
        if (Test-Path (Join-Path $full '_pipeline\project.json')) { return $full }
        Bad ("no _pipeline\project.json under {0}" -f $full)
        Line '        That is not the project folder, or only part of it was copied.'
    }
}

Head 'PHOENIX DE LUMIERE  -  OFFLINE INSTALL'
Line
Line '  Nothing here is downloaded. Everything comes from this folder.'

if (-not $ProjectRoot) { $ProjectRoot = Find-ProjectRoot }
if ($ProjectRoot) {
    if (-not (Test-Path (Join-Path $ProjectRoot '_pipeline\project.json'))) {
        Bad ("no _pipeline\project.json under {0}" -f $ProjectRoot)
        $ProjectRoot = Read-ProjectRoot
    }
} else {
    Warn 'could not work out where the project is'
    $ProjectRoot = Read-ProjectRoot
}
Line
Good ("project  {0}" -f $ProjectRoot)

$fail = @()

# ------------------------------------------------------------------ python ---
if (-not $SkipPython) {
    Head 'PYTHON PACKAGES'
    $py = Get-Command python -ErrorAction SilentlyContinue
    if (-not $py) {
        Bad 'python is not on PATH on this machine'
        Line '        Install python 3.10 or newer first, ticking "Add to PATH".'
        Line '        That installer is the one thing this bundle cannot carry.'
        $fail += 'python is not installed, or not on PATH'
    } elseif (-not (Test-Path $WheelDir)) {
        Bad "no wheels\ folder in this bundle"
        $fail += 'the bundle is incomplete - wheels\ is missing'
    } else {
        $pv = (Invoke-Quiet 'python' @('-c', "import sys;print('%d%d' % sys.version_info[:2])")).Text.Trim()
        Line ("  {0}  (python {1}.{2})" -f $py.Source, $pv.Substring(0, 1), $pv.Substring(1))

        # --no-index is what guarantees this cannot reach for the network: pip
        # uses only the wheels in this folder, so a machine with broken DNS
        # never waits on a name lookup that will fail four times over.
        $pipArgs = @('-m', 'pip', 'install', '--no-index', '--find-links', $WheelDir) + $Packages
        $r = Invoke-Quiet 'python' $pipArgs
        if (-not $r.Ok -and $r.Text -match 'Permission denied|Could not install packages|access is denied') {
            # A python under Program Files needs --user for a non-admin install.
            Warn 'no permission for a normal install - retrying into your own user folder'
            $r = Invoke-Quiet 'python' ($pipArgs + '--user')
        }
        if ($r.Ok) {
            foreach ($l in ($r.Lines | Where-Object { $_ -match 'Successfully installed|already satisfied' })) {
                Line ("  {0}" -f $l)
            }
            Good 'pip finished'
        } else {
            Bad 'pip could not install the packages'
            foreach ($l in ($r.Lines | Where-Object { $_ -match 'ERROR|error:' } | Select-Object -Last 3)) {
                Line ("        {0}" -f $l)
            }
            if ($r.Text -match 'No matching distribution|Could not find a version') {
                $tags = @(Get-ChildItem $WheelDir -Filter '*.whl' | ForEach-Object {
                    if ($_.Name -match '-cp(\d\d\d)-') { $Matches[1] }
                } | Sort-Object -Unique)
                Line ('        This bundle has wheels for python ' + (($tags | ForEach-Object {
                    $_.Substring(0,1) + '.' + $_.Substring(1) }) -join ', ') +
                      ", and this machine runs $($pv.Substring(0,1)).$($pv.Substring(1)).")
                Line ('        Rebuild the bundle with:  make_offline_bundle.ps1 -PythonVersions ' + $pv)
            }
            $fail += 'the python packages did not install - see above'
        }

        # pip saying "Successfully installed" is not the same as the module
        # loading: a wheel for the wrong architecture installs happily and then
        # fails to import. Ask python directly.
        foreach ($m in $Packages) {
            $t = Invoke-Quiet 'python' @('-c', "import $m")
            if ($t.Ok) { Good "import $m" }
            else {
                Bad "python still cannot import $m"
                $last = ($t.Lines | Where-Object { $_ -and $_.Trim() } | Select-Object -Last 1)
                if ($last) { Line ("        {0}" -f $last) }
                $fail += "python cannot import $m"
            }
        }
    }
}

# ------------------------------------------------------------------ ffmpeg ---
if (-not $SkipFFmpeg) {
    Head 'FFMPEG'
    $zip = Get-ChildItem $FFDir -Filter '*.zip' -ErrorAction SilentlyContinue |
           Select-Object -First 1
    $BinDir = Join-Path $ProjectRoot '_pipeline\bin'
    $Exe    = Join-Path $BinDir 'ffmpeg.exe'

    if (-not $zip) {
        Bad "no ffmpeg zip in this bundle"
        $fail += 'the bundle is incomplete - ffmpeg\ is missing'
    } else {
        # The checksum was written into the bundle by make_offline_bundle.ps1
        # precisely so this machine can verify the file without asking anyone.
        $shaFile = Join-Path $FFDir ($zip.Name + '.sha256')
        if (Test-Path $shaFile) {
            $published = (Get-Content $shaFile -Raw).Trim().Split()[0]
            $actual = (Get-FileHash $zip.FullName -Algorithm SHA256).Hash
            if ($actual -ine $published) {
                Bad 'the ffmpeg zip does not match its checksum - the copy is damaged'
                Line "        expected $published"
                Line "        got      $actual"
                Line '        Copy the bundle across again.'
                $fail += 'the ffmpeg zip is damaged'
                $zip = $null
            } else {
                Good ("checksum matches  {0}" -f $actual.ToLower())
            }
        } else {
            Warn 'no checksum file next to the zip - installing it unverified'
        }
    }

    if ($zip) {
        $tmp = Join-Path ([IO.Path]::GetTempPath()) ('pxdl_off_' + [guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Force $tmp | Out-Null
        try {
            Line '  unpacking'
            Expand-Archive -LiteralPath $zip.FullName -DestinationPath $tmp -Force
            $src = Get-ChildItem $tmp -Recurse -Filter 'ffmpeg.exe' | Select-Object -First 1
            if (-not $src) {
                Bad 'no ffmpeg.exe inside the zip'
                $fail += 'the ffmpeg zip has no ffmpeg.exe in it'
            } else {
                New-Item -ItemType Directory -Force $BinDir | Out-Null
                foreach ($n in 'ffmpeg.exe', 'ffprobe.exe') {
                    $from = Join-Path $src.Directory.FullName $n
                    if (Test-Path $from) { Copy-Item $from (Join-Path $BinDir $n) -Force }
                }
                $lic = Get-ChildItem $tmp -Recurse -Filter 'LICENSE*' | Select-Object -First 1
                if ($lic) { Copy-Item $lic.FullName (Join-Path $BinDir 'FFMPEG_LICENSE.txt') -Force }
                Good ("installed into {0}" -f $BinDir)
            }
        }
        finally { Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue }
    }

    if (Test-Path $Exe) {
        $enc = Invoke-Quiet $Exe @('-hide_banner', '-encoders')
        if (-not $enc.Ok) {
            Bad 'the installed ffmpeg will not run'
            $fail += 'the installed ffmpeg will not run'
        } else {
            $ver = (Invoke-Quiet $Exe @('-hide_banner', '-version')).Lines | Select-Object -First 1
            Line ("  {0}" -f $ver)
            $miss = @($Needed | Where-Object { $enc.Text -notmatch "\s$([regex]::Escape($_))\s" })
            if ($miss.Count) {
                Bad ('missing encoders: ' + ($miss -join ', '))
                $fail += 'the installed ffmpeg is missing: ' + ($miss -join ', ')
            } else {
                Good ('has ' + ($Needed -join ', '))
            }
        }
    }
}

# -------------------------------------------------------------------- done ---
if ($fail.Count) {
    Head 'NOT FINISHED'
    foreach ($f in $fail) { Line "  - $f" }
    Line
    Line '  Nothing was broken by this: run it again once the above is sorted.'
    Line
    exit 1
}
Head 'READY'
Line '  This machine now has everything the pipeline needs.'
Line
Line '  Check it over:'
Line ("      cd {0}\_pipeline\scripts" -f $ProjectRoot)
Line '      .\check_environment.ps1'
Line
Line '  Then run EXPORT.cmd in the project folder. Choose Proof first - it is'
Line '  two minutes and proves the whole chain before you commit to a delivery.'
Line

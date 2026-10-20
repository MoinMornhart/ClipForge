# Baut dist\ClipForge-Setup-<Version>.exe
#   powershell -ExecutionPolicy Bypass -File installer\build.ps1
# Voraussetzungen: Inno Setup 6 (winget install JRSoftware.InnoSetup), PyInstaller in der Entwicklungsumgebung.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$build = Join-Path $env:LOCALAPPDATA "ClipForge\build"   # viele Zwischendateien: nicht in iCloud/OneDrive
$stage = Join-Path $build "stage"
$cache = Join-Path $build "cache"
$dist = Join-Path $root "dist"
# Python mit PyInstaller: die Entwicklungsumgebung, auf GitHub (Release-Workflow) per CLIPFORGE_BUILD_PY
$devPy = if ($env:CLIPFORGE_BUILD_PY) { $env:CLIPFORGE_BUILD_PY } else { Join-Path $env:LOCALAPPDATA "ClipForge\venv\Scripts\python.exe" }
$pyVer = "3.11.9"   # letzte 3.11-Version mit offiziellem Windows-Embed-Paket

$version = (Select-String -Path (Join-Path $root "clipforge\__init__.py") -Pattern '__version__ = "(.+)"').Matches[0].Groups[1].Value
$sha = (git -C $root rev-parse --short=12 HEAD).Trim()
Write-Host "ClipForge $version ($sha)"

if (Test-Path $stage) { Remove-Item -Recurse -Force $stage }
New-Item -ItemType Directory -Force $stage, $cache, $dist | Out-Null

# 1) Programmcode
Copy-Item -Recurse (Join-Path $root "clipforge") (Join-Path $stage "clipforge")
Get-ChildItem $stage -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force
foreach ($f in "requirements.txt", "README.md", "LICENSE") { Copy-Item (Join-Path $root $f) $stage }
Copy-Item (Join-Path $PSScriptRoot "setup_runtime.bat") $stage
Set-Content -Path (Join-Path $stage "BUILD") -Value $sha -NoNewline -Encoding ascii

# 2) Eingebettete Python-Laufzeit
$embedZip = Join-Path $cache "python-$pyVer-embed-amd64.zip"
if (-not (Test-Path $embedZip)) {
    Write-Host "Lade Python $pyVer (embed) ..."
    Invoke-WebRequest "https://www.python.org/ftp/python/$pyVer/python-$pyVer-embed-amd64.zip" -OutFile $embedZip
}
$getPip = Join-Path $cache "get-pip.py"
if (-not (Test-Path $getPip)) { Invoke-WebRequest "https://bootstrap.pypa.io/get-pip.py" -OutFile $getPip }
$rt = Join-Path $stage "runtime"
Expand-Archive $embedZip -DestinationPath $rt
Copy-Item $getPip $rt
# Suchpfade: Standardbibliothek, Laufzeit, Programmordner (..), site-packages; "import site" aktiviert pip-Pakete
$pth = Get-ChildItem $rt -Filter "python*._pth" | Select-Object -First 1
Set-Content -Path $pth.FullName -Encoding ascii -Value @"
python311.zip
.
..
Lib\site-packages
import site
"@

# 3) ClipForge.exe (Starter)
& $devPy -m PyInstaller --noconfirm --clean --onefile --noconsole --name ClipForge `
    --icon (Join-Path $root "clipforge\ui\assets\icon.ico") `
    --distpath (Join-Path $build "launcher") --workpath (Join-Path $build "pyi") --specpath (Join-Path $build "pyi") `
    (Join-Path $PSScriptRoot "launcher.py")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller fehlgeschlagen" }
Copy-Item (Join-Path $build "launcher\ClipForge.exe") $stage

# 4) Installer
$iscc = @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe", "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe") |
    Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw "Inno Setup fehlt: winget install JRSoftware.InnoSetup" }
& $iscc "/DMyVersion=$version" "/DStage=$stage" "/DOutDir=$dist" (Join-Path $PSScriptRoot "ClipForge.iss")
if ($LASTEXITCODE -ne 0) { throw "Inno Setup fehlgeschlagen" }

$out = Join-Path $dist "ClipForge-Setup-$version.exe"
Write-Host ("Fertig: {0} ({1:N1} MB)" -f $out, ((Get-Item $out).Length / 1MB))

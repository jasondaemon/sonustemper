$ErrorActionPreference = "Stop"

$root = Resolve-Path "$PSScriptRoot\\..\\.."
$vendorDir = Join-Path $root "vendor\\ffmpeg\\windows"
$htmxFile = Join-Path $root "sonustemper-ui\\app\\static\\vendor\\htmx.min.js"

if (!(Test-Path (Join-Path $vendorDir "ffmpeg.exe")) -or !(Test-Path (Join-Path $vendorDir "ffprobe.exe"))) {
  Write-Error "Missing ffmpeg/ffprobe in $vendorDir. Place binaries there before building."
  exit 1
}
if (!(Test-Path $htmxFile)) {
  Write-Error "Missing HTMX at $htmxFile. Add the official htmx.min.js before building."
  exit 1
}
$htmxContent = Get-Content -Raw $htmxFile
if ($htmxContent -match "HTMX_PLACEHOLDER" -or ($htmxContent.Length -lt 10000)) {
  Write-Error "HTMX file appears to be a placeholder. Replace it with the official minified build before packaging."
  exit 1
}

python -m venv "$root\\.venv-native"
& "$root\\.venv-native\\Scripts\\Activate.ps1"
pip install --upgrade pip
pip install -r "$root\\requirements.txt"
pip install pyinstaller

pyinstaller "$root\\build\\native\\sonustemper.spec"

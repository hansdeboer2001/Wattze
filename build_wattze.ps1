# Bouw de Windows-zip voor vrienden.
#   .\build_wattze.ps1
$root = $PSScriptRoot
$spec = Join-Path $root 'wattze.spec'
$distDir = Join-Path $root 'dist\Wattze'
$zipPath = Join-Path $root 'dist\Wattze.zip'

function Find-Python {
  $cmd = Get-Command python.exe -ErrorAction SilentlyContinue
  if ($cmd) { return $cmd.Source }
  $py = Get-Command py.exe -ErrorAction SilentlyContinue
  if ($py) {
    $exe = & $py.Source -3 -c "import sys;print(sys.executable)" 2>$null
    if ($exe -and (Test-Path $exe)) { return $exe }
  }
  foreach ($p in @(
    (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\python.exe'),
    (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'),
    (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python311\python.exe')
  )) { if ($p -and (Test-Path $p)) { return $p } }
  $w = Get-Command pythonw.exe -ErrorAction SilentlyContinue
  if ($w) {
    $exe = Join-Path (Split-Path -Parent $w.Source) 'python.exe'
    if (Test-Path $exe) { return $exe }
  }
  return $null
}

if (-not (Test-Path -LiteralPath $spec)) {
  Write-Host "wattze.spec ontbreekt in $root"
  exit 1
}

$py = Find-Python
if (-not $py) {
  Write-Host 'Python 3 niet gevonden. Installeer Python 3.12 via https://www.python.org/downloads/ en probeer opnieuw.'
  exit 1
}

Write-Host "Python: $py"
& $py -c "import PyInstaller" 2>$null
if ($LASTEXITCODE -ne 0) {
  Write-Host 'PyInstaller ontbreekt; eenmalig installeren...'
  & $py -m pip install pyinstaller
  if ($LASTEXITCODE -ne 0) { exit 1 }
}

Write-Host 'Bouwen...'
Set-Location -LiteralPath $root
& $py -m PyInstaller --noconfirm --clean $spec
if ($LASTEXITCODE -ne 0) { exit 1 }

$exe = Join-Path $distDir 'Wattze.exe'
if (-not (Test-Path -LiteralPath $exe)) {
  Write-Host "Build klaar maar Wattze.exe ontbreekt: $exe"
  exit 1
}

if (Test-Path -LiteralPath $zipPath) { Remove-Item -LiteralPath $zipPath -Force }
Compress-Archive -Path $distDir -DestinationPath $zipPath -CompressionLevel Optimal
$mb = [math]::Round((Get-Item -LiteralPath $zipPath).Length / 1MB, 1)
Write-Host "Klaar: $zipPath ($mb MB)"

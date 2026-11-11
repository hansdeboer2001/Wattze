# Start of stop de Wattze-tool.
#   .\start_wattze.ps1        herstart de server en opent de browser
#   .\start_wattze.ps1 stop   stopt de server
$ErrorActionPreference = 'SilentlyContinue'
$root = $PSScriptRoot
$entry = Join-Path $root 'wattze_game.py'
$port = 8080
if ($env:KICKR_PORT) { $port = [int]$env:KICKR_PORT }
$stopOnly = $args -contains 'stop'

function Find-Python {
  $cmd = Get-Command pythonw.exe -ErrorAction SilentlyContinue
  if ($cmd) { return $cmd.Source }
  $cmd = Get-Command python.exe -ErrorAction SilentlyContinue
  if ($cmd) { return $cmd.Source }
  $py = Get-Command py.exe -ErrorAction SilentlyContinue
  if ($py) {
    $exe = & $py.Source -3 -c "import sys,os;print(os.path.join(os.path.dirname(sys.executable),'pythonw.exe'))" 2>$null
    if ($exe -and (Test-Path $exe)) { return $exe }
    $exe = & $py.Source -3 -c "import sys;print(sys.executable)" 2>$null
    if ($exe -and (Test-Path $exe)) { return $exe }
  }
  foreach ($p in @(
    (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\pythonw.exe'),
    (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\pythonw.exe'),
    (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python311\pythonw.exe')
  )) { if ($p -and (Test-Path $p)) { return $p } }
  return $null
}

function Stop-Wattze {
  Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -and ($_.CommandLine -match 'wattze_game\.py')
  } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
  Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
    ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
  Start-Sleep -Milliseconds 400
}

Stop-Wattze
if ($stopOnly) {
  Write-Host 'Gestopt.'
  exit 0
}

$pyw = Find-Python
if (-not $pyw) {
  Write-Host 'Python 3 niet gevonden. Installeer Python 3.12 via https://www.python.org/downloads/ en probeer opnieuw.'
  Read-Host 'Druk op Enter om te sluiten'
  exit 1
}

$py = if (Test-Path (Join-Path (Split-Path -Parent $pyw) 'python.exe')) {
  Join-Path (Split-Path -Parent $pyw) 'python.exe'
} else { $pyw }

$requirements = Join-Path $root 'requirements.txt'
& $py -c 'import bleak, segno' 2>$null
if ($LASTEXITCODE -ne 0) {
  Write-Host 'Benodigde Python-pakketten ontbreken; eenmalig installeren...'
  & $py -m pip install --user -r $requirements 2>$null
  if ($LASTEXITCODE -ne 0) { & $py -m pip install -r $requirements }
  & $py -c 'import bleak, segno' 2>$null
  if ($LASTEXITCODE -ne 0) {
    Write-Host 'Kon de pakketten niet installeren. Voer handmatig uit:'
    Write-Host "  `"$py`" -m pip install -r `"$requirements`""
    Read-Host 'Druk op Enter om te sluiten'
    exit 1
  }
}

Start-Process -FilePath $pyw -ArgumentList "`"$entry`"" -WorkingDirectory $root -WindowStyle Hidden
$up = $false
for ($i = 0; $i -lt 25 -and -not $up; $i++) {
  Start-Sleep -Seconds 1
  try { Invoke-RestMethod -Uri "http://127.0.0.1:$port/api" -TimeoutSec 2 | Out-Null; $up = $true } catch {}
}
if (-not $up) {
  Write-Host 'De server startte niet. Start handmatig voor foutmeldingen:'
  Write-Host "  `"$py`" `"$entry`""
  Read-Host 'Druk op Enter om te sluiten'
  exit 1
}
function Start-WattzeBrowser([string]$url) {
  $candidates = @(
    (Join-Path $env:ProgramFiles 'Microsoft\Edge\Application\msedge.exe'),
    (Join-Path ${env:ProgramFiles(x86)} 'Microsoft\Edge\Application\msedge.exe'),
    (Join-Path $env:ProgramFiles 'Google\Chrome\Application\chrome.exe'),
    (Join-Path ${env:ProgramFiles(x86)} 'Google\Chrome\Application\chrome.exe')
  )
  $browser = $candidates | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1
  if ($browser) {
    Start-Process -FilePath $browser -ArgumentList '--new-window','--start-fullscreen',$url
  } else {
    Start-Process $url
  }
}
Start-WattzeBrowser "http://localhost:$port/start?boot=1"

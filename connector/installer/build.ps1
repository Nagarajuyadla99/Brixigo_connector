# Build Windows binaries with PyInstaller (onedir) then compile Inno installer (optional).
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

Write-Host "Installing build dependencies..."
python -m pip install -r requirements.txt

Write-Host "Running PyInstaller (onedir — single process, no bootloader child)..."
python -m PyInstaller --noconfirm --clean --distpath dist --workpath build\tray installer\brixigo_connector.spec
python -m PyInstaller --noconfirm --clean --distpath dist --workpath build\svc installer\brixigo_connector_service.spec

$trayDir = "dist\BrixigoConnector"
$svcDir = "dist\BrixigoConnectorService"
if (-not (Test-Path (Join-Path $trayDir "BrixigoConnector.exe"))) {
    throw "Tray onedir build missing: $trayDir\BrixigoConnector.exe"
}
if (-not (Test-Path (Join-Path $svcDir "BrixigoConnectorService.exe"))) {
    throw "Service onedir build missing: $svcDir\BrixigoConnectorService.exe"
}
Write-Host "Tray binary OK (onedir): $trayDir"
Write-Host "Service binary OK (onedir): $svcDir"

# Stage a flat client pack: UI onedir + service exe/deps + installer helpers
$pack = "dist\packaging"
New-Item -ItemType Directory -Force -Path $pack | Out-Null
Copy-Item -Force -Recurse "$trayDir\*" $pack
# Service: merge into same folder (service exe name does not collide with UI)
Copy-Item -Force -Recurse "$svcDir\*" $pack
Copy-Item -Force "installer\setup_service.ps1" $pack
Copy-Item -Force "installer\uninstall_service.ps1" $pack
Write-Host "Client pack staged at $pack"

$iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
if (Test-Path $iscc) {
    Write-Host "Compiling Inno Setup installer..."
    & $iscc "installer\BrixigoConnector.iss"
} else {
    Write-Host "Inno Setup not found — use dist\packaging\ for client install."
}

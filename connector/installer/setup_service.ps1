#Requires -RunAsAdministrator
param(
    [Parameter(Mandatory = $false)]
    [string]$InstallDir = $PSScriptRoot,

    [Parameter(Mandatory = $false)]
    [string]$MasterKey = $env:BRIXIGO_CONNECTOR_MASTER_KEY
)

$ErrorActionPreference = "Stop"
$ServiceName = "BrixigoConnector"
$DisplayName = "Brixigo Connector Agent"
$ExePath = Join-Path $InstallDir "BrixigoConnectorService.exe"

Write-Host "Installing $DisplayName from $ExePath"

$dataDir = Join-Path $InstallDir "data"
$logsDir = Join-Path $InstallDir "logs"
New-Item -ItemType Directory -Force -Path $dataDir | Out-Null
New-Item -ItemType Directory -Force -Path $logsDir | Out-Null

# Allow non-elevated tray UI to read/write SQLite + config next to Program Files installs.
foreach ($dir in @($dataDir, $logsDir)) {
    try {
        & icacls.exe $dir /grant "*S-1-5-32-545:(OI)(CI)M" /T /C | Out-Null
    } catch {
        Write-Warning "Could not set modify ACL on $dir — tray UI may need elevation to save settings."
    }
}

if (-not (Test-Path $ExePath)) {
    throw "Service executable not found: $ExePath"
}

# Production environment profile (machine-wide) when a master key is supplied.
# Without a master key we still install the service but leave ENV unset so
# first-run configuration from the tray UI is not blocked.
if (-not [string]::IsNullOrWhiteSpace($MasterKey)) {
    [Environment]::SetEnvironmentVariable("BRIXIGO_CONNECTOR_ENV", "production", "Machine")
    [Environment]::SetEnvironmentVariable("BRIXIGO_CONNECTOR_MASTER_KEY", $MasterKey, "Machine")
    $env:BRIXIGO_CONNECTOR_ENV = "production"
    $env:BRIXIGO_CONNECTOR_MASTER_KEY = $MasterKey
    Write-Host "Production env + master key configured at machine scope."
} else {
    Write-Warning "BRIXIGO_CONNECTOR_MASTER_KEY not provided — service installed in soft-production mode."
    Write-Warning "After generating a key, set both variables and restart the service:"
    Write-Warning '  [Environment]::SetEnvironmentVariable("BRIXIGO_CONNECTOR_ENV", "production", "Machine")'
    Write-Warning '  [Environment]::SetEnvironmentVariable("BRIXIGO_CONNECTOR_MASTER_KEY", "<long-random-secret>", "Machine")'
}

$existing = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($existing) {
    if ($existing.Status -eq "Running") {
        Stop-Service -Name $ServiceName -Force -ErrorAction SilentlyContinue
    }
    & sc.exe delete $ServiceName | Out-Null
    Start-Sleep -Seconds 2
}

# Delayed auto-start + failure recovery (no duplicate services)
& sc.exe create $ServiceName binPath= "`"$ExePath`"" start= delayed-auto DisplayName= "$DisplayName" | Out-Null
if ($LASTEXITCODE -ne 0) {
    # Fallback for older hosts that reject delayed-auto on create
    & sc.exe create $ServiceName binPath= "`"$ExePath`"" start= auto DisplayName= "$DisplayName" | Out-Null
    & sc.exe config $ServiceName start= delayed-auto | Out-Null
}
& sc.exe description $ServiceName "Synchronizes biometric attendance devices with Brixigo Cloud." | Out-Null
& sc.exe failure $ServiceName reset= 86400 actions= restart/5000/restart/15000/restart/60000 | Out-Null
& sc.exe failureflag $ServiceName 1 | Out-Null

Start-Service -Name $ServiceName

Write-Host "Service $ServiceName installed with delayed-auto start and crash recovery."
Write-Host "Tray UI may be started separately; it will run UI-only while the service owns sync."

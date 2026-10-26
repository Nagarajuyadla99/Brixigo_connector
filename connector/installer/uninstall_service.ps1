#Requires -RunAsAdministrator
$ErrorActionPreference = "Continue"
$ServiceName = "BrixigoConnector"

$svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($svc) {
    if ($svc.Status -eq "Running") {
        Stop-Service -Name $ServiceName -Force
    }
    & sc.exe delete $ServiceName | Out-Null
    Write-Host "Service $ServiceName removed."
} else {
    Write-Host "Service $ServiceName not found."
}

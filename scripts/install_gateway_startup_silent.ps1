$ErrorActionPreference = "Stop"

$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$vbsPath = Join-Path $root "scripts\start_gateway_hidden.vbs"

if (-not (Test-Path $vbsPath)) {
    throw "Missing startup script: $vbsPath"
}

$runKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
$name = "NanoBotGateway"
$value = "wscript.exe `"$vbsPath`""

Set-ItemProperty -Path $runKey -Name $name -Value $value

Write-Output "Installed silent startup entry:"
Write-Output "$name = $value"

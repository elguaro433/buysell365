# panel.ps1 — Abre el panel admin del bot a través de un túnel SSH seguro.
# Uso:  .\tools\panel.ps1
#
# Desde 2026-09-19 el puerto 5001 del VPS está cerrado a internet (el panel
# mandaba usuario/contraseña en claro por HTTP). Este script:
#   1) abre un túnel SSH (con tu key) que trae el puerto 5001 del VPS a tu PC
#   2) abre el navegador en http://localhost:5001
# Cierra la ventana de PowerShell (o Ctrl+C) para cerrar el túnel.
$ErrorActionPreference = "Stop"
$Key   = "$env:USERPROFILE\.ssh\id_ed25519_buysell365"
$Host_ = "root@208.73.204.188"
$Port  = 5001

if (-not (Test-Path $Key)) { Write-Host "No encuentro la key SSH: $Key" -ForegroundColor Red; exit 1 }

# ¿Ya hay un túnel abierto?
$busy = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($busy) {
    Write-Host "Ya hay algo escuchando en localhost:$Port — abriendo el navegador directamente." -ForegroundColor Yellow
    Start-Process "http://localhost:$Port"
    exit 0
}

Write-Host "Abriendo túnel SSH al panel (localhost:$Port -> VPS:$Port)..." -ForegroundColor Cyan
$ssh = Start-Process -FilePath "ssh" -ArgumentList @(
    "-i", $Key, "-N",
    "-o", "ServerAliveInterval=30", "-o", "ExitOnForwardFailure=yes",
    "-L", "${Port}:127.0.0.1:${Port}", $Host_
) -PassThru -WindowStyle Hidden

# Esperar a que el túnel escuche (máx 15 s)
$ok = $false
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Milliseconds 500
    if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) { $ok = $true; break }
    if ($ssh.HasExited) { break }
}
if (-not $ok) { Write-Host "No se pudo abrir el túnel (¿VPS caído? ¿key?)." -ForegroundColor Red; exit 1 }

Write-Host "Túnel abierto. Panel en http://localhost:$Port" -ForegroundColor Green
Write-Host "Deja esta ventana abierta mientras uses el panel. Ctrl+C o cerrarla = cerrar túnel." -ForegroundColor DarkGray
Start-Process "http://localhost:$Port"
try { Wait-Process -Id $ssh.Id } finally { if (-not $ssh.HasExited) { Stop-Process -Id $ssh.Id -Force } }

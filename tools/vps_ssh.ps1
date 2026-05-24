# Conecta por SSH al VPS BuySell365 con la key dedicada.
# Uso:
#   .\tools\vps_ssh.ps1                              # sesion interactiva
#   .\tools\vps_ssh.ps1 "systemctl status buysell365"  # ejecuta un comando

$VPS = "root@208.73.204.188"
$KEY = "$HOME\.ssh\id_ed25519_buysell365"

if (-not (Test-Path $KEY)) {
    Write-Host "ERR  No existe SSH key: $KEY" -ForegroundColor Red
    exit 1
}

if ($args.Count -eq 0) {
    Write-Host "Conectando a $VPS ..." -ForegroundColor Cyan
    ssh -i $KEY $VPS
} else {
    $cmd = $args -join " "
    ssh -i $KEY -o BatchMode=yes $VPS $cmd
}

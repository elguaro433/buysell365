# Reinicia el bot sin tocar codigo (no hace git pull).
# Uso:
#   .\tools\vps_restart.ps1              # reinicia bot principal
#   .\tools\vps_restart.ps1 -Admin       # tambien reinicia panel admin
#   .\tools\vps_restart.ps1 -All         # ambos

param(
    [switch]$Admin,
    [switch]$All
)

$VPS = "root@208.73.204.188"
$KEY = "$HOME\.ssh\id_ed25519_buysell365"

$services = @("buysell365")
if ($Admin -or $All) { $services += "buysell365_admin" }

foreach ($svc in $services) {
    Write-Host "Reiniciando $svc ..." -ForegroundColor Cyan
    $out = ssh -i $KEY -o BatchMode=yes $VPS "systemctl restart $svc && sleep 2 && systemctl is-active $svc"
    if ($out -match "^active$") {
        Write-Host "  OK  $svc esta active" -ForegroundColor Green
    } else {
        Write-Host "  ERR $svc -> $out" -ForegroundColor Red
        ssh -i $KEY -o BatchMode=yes $VPS "journalctl -u $svc -n 10 --no-pager"
    }
}

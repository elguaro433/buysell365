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
    Write-Host "Reiniciando $svc (stop + clean locks + start) ..." -ForegroundColor Cyan
    if ($svc -eq "buysell365") {
        # Limpieza de locks (critical para el bot principal — lesson learned 2026-05-24)
        $cmd = "systemctl stop $svc && sleep 4 && " +
               "rm -f /opt/buysell365/app/.bot.singleton.lock /opt/buysell365/app/.copier.singleton.lock /opt/buysell365/app/.copier.lock /opt/buysell365/app/.bot.heartbeat /opt/buysell365/app/.copier.heartbeat && " +
               "systemctl start $svc && sleep 60 && systemctl is-active $svc"
    } else {
        $cmd = "systemctl restart $svc && sleep 3 && systemctl is-active $svc"
    }
    $out = ssh -i $KEY -o BatchMode=yes $VPS $cmd
    if ($out -match "^active$") {
        Write-Host "  OK  $svc esta active" -ForegroundColor Green
        if ($svc -eq "buysell365") {
            $cnt = ssh -i $KEY -o BatchMode=yes $VPS "ps aux | grep -E 'launcher|bot.py|signal_copier|monitor_real' | grep -v grep | wc -l"
            Write-Host "       Procesos hijo: $($cnt.Trim()) (esperado 4+)" -ForegroundColor Cyan
        }
    } else {
        Write-Host "  ERR $svc -> $out" -ForegroundColor Red
        ssh -i $KEY -o BatchMode=yes $VPS "journalctl -u $svc -n 10 --no-pager"
    }
}

# Tail logs del bot en vivo desde el VPS.
# Uso:
#   .\tools\vps_logs.ps1                  # bot principal en vivo
#   .\tools\vps_logs.ps1 -Tail 200        # ultimas 200 lineas y salir
#   .\tools\vps_logs.ps1 -Service buysell365_admin   # logs del panel admin
#   .\tools\vps_logs.ps1 -Errors          # solo errores

param(
    [string]$Service = "buysell365",
    [int]$Tail = 0,
    [switch]$Errors,
    [switch]$Follow
)

$VPS = "root@208.73.204.188"
$KEY = "$HOME\.ssh\id_ed25519_buysell365"

$cmd = "journalctl -u $Service --no-pager"
if ($Tail -gt 0)  { $cmd += " -n $Tail" } else { $cmd += " -f"; $Follow = $true }
if ($Errors)      { $cmd += " -p err" }

Write-Host "Logs $Service desde VPS ..." -ForegroundColor Cyan
Write-Host "Comando: $cmd" -ForegroundColor DarkGray
Write-Host ""
ssh -i $KEY -o BatchMode=yes $VPS $cmd

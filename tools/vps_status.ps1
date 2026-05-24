# Snapshot rapido del estado del bot en el VPS.
# Uso:  .\tools\vps_status.ps1

$VPS = "root@208.73.204.188"
$KEY = "$HOME\.ssh\id_ed25519_buysell365"

$cmd = @"
echo '=== Servicios ==='
systemctl is-active buysell365 buysell365_admin 2>&1
echo ''
echo '=== Bot procesos ==='
ps aux | grep -E 'launcher.py|web_admin' | grep -v grep | awk '{printf "PID %s  CPU %s%%  RAM %s%%  %s\n", \$2, \$3, \$4, \$11}'
echo ''
echo '=== Git status en /opt/buysell365 ==='
cd /opt/buysell365 && git log -1 --oneline && git status -s
echo ''
echo '=== Disco ==='
df -h / | tail -1
echo ''
echo '=== Memoria ==='
free -h | head -2
echo ''
echo '=== Ultimas 5 lineas de log ==='
journalctl -u buysell365 -n 5 --no-pager
"@

ssh -i $KEY -o BatchMode=yes $VPS $cmd

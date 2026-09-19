# vps_backup_pull.ps1 - Baja el último backup cifrado del VPS a Desktop\BuySell365_Backups
# Uso:  .\tools\vps_backup_pull.ps1
# El VPS genera /opt/backups/buysell365/bs365_<fecha>.tar.enc cada día a las 04:10
# (scripts/vps_backup.sh). La clave para descifrar está en
# Desktop\BuySell365_Backups\CLAVE_BACKUP_VPS.txt - NO la borres ni la subas a ningún sitio.
#
# Descifrar (en Linux/WSL/Git Bash):
#   openssl enc -d -aes-256-cbc -pbkdf2 -pass file:CLAVE_BACKUP_VPS.txt -in bs365_X.tar.enc | tar xz -C restore/
$ErrorActionPreference = "Stop"
$Key  = "$env:USERPROFILE\.ssh\id_ed25519_buysell365"
$Host_ = "root@208.73.204.188"
$Dst  = "$env:USERPROFILE\Desktop\BuySell365_Backups"
New-Item -ItemType Directory -Force $Dst | Out-Null

$latest = (& ssh -i $Key $Host_ "ls -t /opt/backups/buysell365/bs365_*.tar.enc | head -1").Trim()
if (-not $latest) { Write-Host "No hay backups en el VPS" -ForegroundColor Red; exit 1 }
$name = Split-Path $latest -Leaf
& scp -q -i $Key "${Host_}:$latest" "$Dst\$name"
Write-Host "Descargado: $Dst\$name ($([math]::Round((Get-Item "$Dst\$name").Length/1KB)) KB)" -ForegroundColor Green

# Conservar solo los 10 más recientes en local
Get-ChildItem "$Dst\bs365_*.tar.enc" | Sort-Object LastWriteTime -Descending | Select-Object -Skip 10 | Remove-Item -Force

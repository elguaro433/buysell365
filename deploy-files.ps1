# ============================================================
# deploy-files.ps1 — Deploy directo de archivos via scp
# ============================================================
# Usar cuando /opt/buysell365 NO es git clone (estado actual al 2026-05-24).
# Sube uno o más archivos, hace backup en VPS, restart con limpieza de locks,
# verifica que el bot arranca correctamente.
#
# Uso:
#   .\deploy-files.ps1 app/bot.py                          # un archivo
#   .\deploy-files.ps1 app/bot.py app/signal_copier.py    # varios
#   .\deploy-files.ps1 -Files @("app/bot.py")             # explicito
#   .\deploy-files.ps1 -DeleteRemote app/transparency_publisher.py  # borrar
#   .\deploy-files.ps1 -NoRestart app/foo.py              # subir sin restart
#
# Cada archivo se sube con su path RELATIVO desde la raiz del proyecto local
# y se coloca en el mismo path bajo $VPS_PATH del VPS.
# ============================================================

param(
    [Parameter(ValueFromRemainingArguments=$true, Position=0)]
    [string[]]$Files,

    [string]$DeleteRemote = "",

    [switch]$NoRestart,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# ── Config ──
$VPS_HOST  = "208.73.204.188"
$VPS_USER  = "root"
$VPS_PATH  = "/opt/buysell365"
$SSH_KEY   = "$HOME\.ssh\id_ed25519_buysell365"
$SERVICE   = "buysell365"
$ADMIN_SVC = "buysell365_admin"

# ── Helpers ──
function Show-Header($txt) {
    Write-Host ""
    Write-Host ("=" * 62) -ForegroundColor Cyan
    Write-Host " $txt" -ForegroundColor Cyan
    Write-Host ("=" * 62) -ForegroundColor Cyan
}
function Show-OK($txt)   { Write-Host "    OK  $txt" -ForegroundColor Green }
function Show-Warn($txt) { Write-Host "    WARN $txt" -ForegroundColor Yellow }
function Show-Err($txt)  { Write-Host "    ERR  $txt" -ForegroundColor Red }

function Invoke-Ssh($cmd) {
    & ssh -i $SSH_KEY -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 "$VPS_USER@$VPS_HOST" $cmd 2>&1
}

# ── Pre-checks ──
Show-Header "BuySell365 Deploy (archivos via scp)"

if (-not (Test-Path $SSH_KEY)) {
    Show-Err "No existe SSH key: $SSH_KEY"
    exit 1
}

if (-not $Files -and -not $DeleteRemote) {
    Show-Err "Sin archivos. Uso: .\deploy-files.ps1 app/bot.py [app/...]"
    Show-Err "  o:  .\deploy-files.ps1 -DeleteRemote app/transparency_publisher.py"
    exit 1
}

# ── Verificar archivos locales ──
foreach ($f in $Files) {
    if (-not (Test-Path $f)) {
        Show-Err "No existe localmente: $f"
        exit 1
    }
}

# ── Sintaxis Python (solo .py) ──
$pyFiles = $Files | Where-Object { $_ -like "*.py" }
if ($pyFiles) {
    Write-Host ""
    Write-Host ">>> Verificando sintaxis Python local..." -ForegroundColor Yellow
    foreach ($f in $pyFiles) {
        $check = python -c "import ast; ast.parse(open('$f',encoding='utf-8').read()); print('OK')" 2>&1
        if ($check -notmatch "OK") {
            Show-Err "$f sintaxis INVALIDA: $check"
            exit 1
        }
        Show-OK "$f sintaxis OK"
    }
}

if ($DryRun) {
    Show-Warn "DRY-RUN: ningun archivo se subira ni el bot se reiniciara."
    exit 0
}

# ── SSH test ──
Write-Host ""
Write-Host ">>> Test SSH..." -ForegroundColor Yellow
$t = Invoke-Ssh "echo OK_SSH"
if ($t -notmatch "OK_SSH") {
    Show-Err "SSH no funciona: $t"
    exit 1
}
Show-OK "SSH passwordless OK"

# ── Timestamp para backups ──
$TS = Get-Date -Format "yyyyMMdd_HHmmss"

# ── Subir archivos ──
foreach ($f in $Files) {
    Write-Host ""
    Write-Host ">>> Procesando $f" -ForegroundColor Yellow
    $remote = "$VPS_PATH/$f"
    $remoteBak = "$remote.bak_pre_deploy_$TS"

    # Backup en VPS
    Invoke-Ssh "if [ -f $remote ]; then cp $remote $remoteBak && echo OK; else echo NEW_FILE; fi" | Out-Null
    Show-OK "backup en VPS: $remoteBak"

    # SCP
    & scp -i $SSH_KEY -o BatchMode=yes -q $f "${VPS_USER}@${VPS_HOST}:$remote"
    if ($LASTEXITCODE -ne 0) {
        Show-Err "SCP fallo para $f"
        exit 1
    }
    Show-OK "subido a $remote"
}

# ── Borrado remoto opcional ──
if ($DeleteRemote) {
    Write-Host ""
    Write-Host ">>> Borrando remoto $DeleteRemote" -ForegroundColor Yellow
    $delPath = "$VPS_PATH/$DeleteRemote"
    $delBak = "$delPath.bak_deleted_$TS"
    $r = Invoke-Ssh "if [ -f $delPath ]; then mv $delPath $delBak && echo MOVED; else echo NOT_EXISTS; fi"
    Show-OK "$r"
}

# ── Restart con limpieza de locks ──
if ($NoRestart) {
    Show-Warn "NoRestart: archivos subidos pero el bot NO se reinicio."
    exit 0
}

Write-Host ""
Write-Host ">>> Stop + clean locks + start..." -ForegroundColor Yellow
$restartCmd = @"
systemctl stop $SERVICE
sleep 4
rm -f $VPS_PATH/app/.bot.singleton.lock $VPS_PATH/app/.copier.singleton.lock $VPS_PATH/app/.copier.lock $VPS_PATH/app/.bot.heartbeat $VPS_PATH/app/.copier.heartbeat
systemctl start $SERVICE
echo STOP_CLEAN_START_OK
"@
$rOut = Invoke-Ssh $restartCmd
if ($rOut -notmatch "STOP_CLEAN_START_OK") {
    Show-Err "Restart fallo: $rOut"
    exit 1
}
Show-OK "stop + clean locks + start ejecutado"

Write-Host ""
Write-Host ">>> Esperando 75s para que el launcher levante todo..." -ForegroundColor Yellow
Start-Sleep -Seconds 75

# ── Verificar ──
$logCheck = Invoke-Ssh "tail -8 $VPS_PATH/app/logs/launcher.log | grep -c 'Bot iniciado'"
$procCount = Invoke-Ssh "ps aux | grep -E 'launcher|bot.py|signal_copier|monitor_real' | grep -v grep | wc -l"
$logOK = ($logCheck -replace '\s+','').Trim()
$procOK = ($procCount -replace '\s+','').Trim()

Write-Host ""
Write-Host "    Procesos bot: $procOK (esperado 4+), 'Bot iniciado' en log: $logOK"

if ([int]$logOK -ge 1 -and [int]$procOK -ge 4) {
    Show-OK "$SERVICE arrancado correctamente con $procOK procesos hijo"
    Write-Host ""
    Write-Host ("=" * 62) -ForegroundColor Green
    Write-Host " DEPLOY OK" -ForegroundColor Green
    Write-Host ("=" * 62) -ForegroundColor Green
    Write-Host ""
    Write-Host "  Panel: http://${VPS_HOST}:5001" -ForegroundColor Cyan
    Write-Host "  Backups VPS: *.bak_pre_deploy_$TS" -ForegroundColor Cyan
    Write-Host ""
} else {
    Show-Err "$SERVICE NO arranco bien"
    Write-Host "    Ultimas 20 lineas launcher.log:" -ForegroundColor Yellow
    $log = Invoke-Ssh "tail -20 $VPS_PATH/app/logs/launcher.log"
    Write-Host $log
    Write-Host ""
    Write-Host "    ROLLBACK MANUAL: restaurar los archivos *.bak_pre_deploy_$TS" -ForegroundColor Red
    foreach ($f in $Files) {
        Write-Host "      ssh -i $SSH_KEY $VPS_USER@${VPS_HOST} `"cp $VPS_PATH/$f.bak_pre_deploy_$TS $VPS_PATH/$f`"" -ForegroundColor DarkGray
    }
    Write-Host "    Luego: stop + clean locks + start" -ForegroundColor Red
    exit 1
}

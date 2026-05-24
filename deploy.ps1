# ============================================================
# BuySell365 — Deploy script
# ============================================================
# Workflow rapido: commit local → push GitHub → pull en VPS → restart
#
# Uso:
#   .\deploy.ps1 "fix: descripcion del cambio"
#   .\deploy.ps1 -Message "feat: nueva funcionalidad" -Branch main
#   .\deploy.ps1 -DryRun                       # ver que harias sin hacerlo
#   .\deploy.ps1 -SkipGit                      # solo redeploy en VPS sin push
#   .\deploy.ps1 -Branch migration/clean-2026-05-24
#
# Requisitos:
#   - SSH key id_ed25519_buysell365 instalada en VPS (~/.ssh/authorized_keys de root)
#   - Repo git inicializado y conectado a origin
#   - VPS tiene /opt/buysell365 como git clone
# ============================================================

param(
    [Parameter(Position=0)]
    [string]$Message = "",

    [string]$Branch = "main",

    [switch]$DryRun,
    [switch]$SkipGit,
    [switch]$SkipRestart,
    [switch]$Force
)

$ErrorActionPreference = "Stop"

# ── Config (editar si cambia) ────────────────────────────────
$VPS_HOST    = "208.73.204.188"
$VPS_USER    = "root"
$VPS_PATH    = "/opt/buysell365"
$SSH_KEY     = "$HOME\.ssh\id_ed25519_buysell365"
$SERVICE     = "buysell365"
$ADMIN_SVC   = "buysell365_admin"
# ─────────────────────────────────────────────────────────────

function Show-Header($txt) {
    Write-Host ""
    Write-Host ("=" * 62) -ForegroundColor Cyan
    Write-Host " $txt" -ForegroundColor Cyan
    Write-Host ("=" * 62) -ForegroundColor Cyan
}

function Show-Step($n, $txt) {
    Write-Host ""
    Write-Host ">>> Paso $n : $txt" -ForegroundColor Yellow
}

function Show-OK($txt)   { Write-Host "    OK  $txt" -ForegroundColor Green }
function Show-Warn($txt) { Write-Host "    WARN $txt" -ForegroundColor Yellow }
function Show-Err($txt)  { Write-Host "    ERR  $txt" -ForegroundColor Red }

function Invoke-Ssh($cmd) {
    $sshArgs = @(
        "-i", $SSH_KEY,
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "ConnectTimeout=10",
        "-o", "BatchMode=yes",
        "$VPS_USER@$VPS_HOST",
        $cmd
    )
    return (& ssh @sshArgs 2>&1)
}

# ── Pre-checks ──────────────────────────────────────────────
Show-Header "BuySell365 Deploy"
Set-Location $PSScriptRoot

if (-not (Test-Path $SSH_KEY)) {
    Show-Err "No existe SSH key: $SSH_KEY"
    Show-Err "Genera con: ssh-keygen -t ed25519 -f `$HOME\.ssh\id_ed25519_buysell365 -N `"`""
    exit 1
}

if (-not (Test-Path ".git")) {
    Show-Err "No estamos en un repo git. cwd=$(Get-Location)"
    exit 1
}

# ── 1) Status local ─────────────────────────────────────────
Show-Step 1 "Estado local"
$changes = git status --porcelain
if ($changes -and -not $SkipGit) {
    Write-Host "    Cambios pendientes:"
    git status -s | ForEach-Object { Write-Host "      $_" }
} elseif (-not $SkipGit) {
    Show-OK "Working tree limpio (nada que commitear)"
} else {
    Show-OK "SkipGit activo - se ignora estado local"
}

# ── 2) Commit + push ────────────────────────────────────────
if (-not $SkipGit -and $changes) {
    if (-not $Message) {
        Show-Err "Hay cambios pero no diste mensaje de commit. Usa: .\deploy.ps1 'mi mensaje'"
        exit 1
    }
    Show-Step 2 "Commit + push a origin/$Branch"
    if ($DryRun) {
        Show-Warn "DRY-RUN — saltando git add/commit/push"
    } else {
        git add -A
        git commit -m $Message | Out-Null
        Show-OK "Commit local creado"

        $pushResult = git push origin "HEAD:$Branch" 2>&1
        if ($LASTEXITCODE -ne 0) {
            Show-Err "Push fallo:"
            Write-Host $pushResult
            exit 1
        }
        Show-OK "Push a origin/$Branch OK"
    }
} elseif (-not $SkipGit) {
    Show-Step 2 "Commit + push"
    Show-OK "Nada que pushear, salto al deploy"
}

# ── 3) Verificar SSH al VPS ─────────────────────────────────
Show-Step 3 "Test SSH al VPS"
$test = Invoke-Ssh "echo OK_SSH"
if ($test -notmatch "OK_SSH") {
    Show-Err "SSH no funciona. Output:"
    Write-Host $test
    Show-Err "Verifica que la SSH key esta en $VPS_USER@$VPS_HOST :~/.ssh/authorized_keys"
    exit 1
}
Show-OK "SSH funcionando con key (sin password)"

# ── 4) Pull en VPS ──────────────────────────────────────────
if (-not $DryRun) {
    Show-Step 4 "git pull en VPS ($VPS_PATH, branch $Branch)"
    $pullCmd = "cd $VPS_PATH && git fetch --all --prune && git checkout $Branch && git pull && git log -1 --oneline"
    $pullOut = Invoke-Ssh $pullCmd
    Write-Host "    $pullOut"
    if ($LASTEXITCODE -ne 0) {
        Show-Err "Pull fallo. Revisa output arriba."
        exit 1
    }
    Show-OK "Codigo actualizado en VPS"

    # 4.b) Si requirements.txt cambio, pip install
    Show-Step "4b" "Comprobar requirements.txt"
    $reqChanged = Invoke-Ssh "cd $VPS_PATH && git diff HEAD@{1} HEAD --name-only 2>/dev/null | grep -q 'requirements.txt' && echo CHANGED || echo SAME"
    if ($reqChanged -match "CHANGED") {
        Show-Warn "requirements.txt cambio, ejecutando pip install..."
        $pipOut = Invoke-Ssh "cd $VPS_PATH/app && pip install -r requirements.txt --quiet 2>&1 | tail -5"
        Write-Host "    $pipOut"
    } else {
        Show-OK "requirements.txt sin cambios"
    }
} else {
    Show-Warn "DRY-RUN — saltando pull y restart en VPS"
    exit 0
}

# ── 5) Restart del servicio (CRITICO: stop + limpiar locks + start) ──
# Lesson learned 2026-05-24: 'systemctl restart' SOLO falla porque deja locks
# stale (.bot.singleton.lock, .copier.singleton.lock, .copier.lock). El launcher
# nuevo los ve y se cuelga sin spawn de bot.py + signal_copier.py + monitor_real.py.
# Solucion confirmada: stop -> sleep 4 -> rm locks -> start.
if (-not $SkipRestart) {
    Show-Step 5 "Restart $SERVICE (stop + clean locks + start)"
    $restartCmd = @"
systemctl stop $SERVICE
sleep 4
rm -f $VPS_PATH/app/.bot.singleton.lock $VPS_PATH/app/.copier.singleton.lock $VPS_PATH/app/.copier.lock $VPS_PATH/app/.bot.heartbeat $VPS_PATH/app/.copier.heartbeat
systemctl start $SERVICE
echo STOP_CLEAN_START_OK
"@
    $rOut = Invoke-Ssh $restartCmd
    if ($rOut -notmatch "STOP_CLEAN_START_OK") {
        Show-Err "Stop+clean+start fallo: $rOut"
        exit 1
    }
    Show-OK "stop + clean locks + start ejecutado"

    Show-Step "5b" "Esperando 75s para que el launcher levante todos los hijos..."
    Start-Sleep -Seconds 75

    Show-Step "5c" "Verificando que el bot realmente arranco"
    $logCheck = Invoke-Ssh "tail -8 $VPS_PATH/app/logs/launcher.log | grep -c 'Bot iniciado'"
    $procCount = Invoke-Ssh "ps aux | grep -E 'launcher|bot.py|signal_copier|monitor_real' | grep -v grep | wc -l"

    $logOK = ($logCheck -replace '\s+','').Trim()
    $procOK = ($procCount -replace '\s+','').Trim()
    Write-Host "    Procesos bot: $procOK (esperado 4+), 'Bot iniciado' en launcher.log: $logOK"

    if ([int]$logOK -ge 1 -and [int]$procOK -ge 4) {
        Show-OK "$SERVICE arrancado correctamente con $procOK procesos hijo"
    } else {
        Show-Err "$SERVICE NO arranco bien (procesos=$procOK, log=$logOK)"
        Write-Host ""
        Write-Host "    Ultimas 20 lineas de launcher.log:" -ForegroundColor Yellow
        $launcherLog = Invoke-Ssh "tail -20 $VPS_PATH/app/logs/launcher.log"
        Write-Host $launcherLog
        Write-Host ""
        Write-Host "    Si tienes backup local previo a este deploy, rollback manualmente." -ForegroundColor Yellow
        exit 1
    }

    # Restart admin tambien
    $adminOut = Invoke-Ssh "systemctl restart $ADMIN_SVC && sleep 2 && systemctl is-active $ADMIN_SVC"
    if ($adminOut -match "^active$") {
        Show-OK "$ADMIN_SVC esta active"
    } else {
        Show-Warn "$ADMIN_SVC no quedo active: $adminOut"
    }
} else {
    Show-Warn "SkipRestart activo - servicio NO reiniciado"
}

# ── 6) Verificar estado final ───────────────────────────────
Show-Step 6 "Estado final"
$status = Invoke-Ssh "systemctl status $SERVICE --no-pager -l | head -5"
Write-Host $status

Write-Host ""
Write-Host ("=" * 62) -ForegroundColor Green
Write-Host " DEPLOY COMPLETADO" -ForegroundColor Green
Write-Host ("=" * 62) -ForegroundColor Green
Write-Host ""
Write-Host "  Panel: http://${VPS_HOST}:5001" -ForegroundColor Cyan
Write-Host "  Logs:  .\tools\vps_logs.ps1" -ForegroundColor Cyan
Write-Host "  SSH:   .\tools\vps_ssh.ps1" -ForegroundColor Cyan
Write-Host ""

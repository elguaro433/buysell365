# ============================================================
# snap_state_now.ps1
# DÍA DEL SWITCH — Tomado en TU PC LOCAL después de parar el bot
# Genera snapshot fresco de TODOS los JSONs de estado en una carpeta
# con timestamp, listo para subir al VPS.
# ============================================================

$ErrorActionPreference = "Stop"
$src = "C:\Users\hpint\Desktop\BuySell365_Bot"
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$snapDir = "C:\Users\hpint\Desktop\BuySell365_VPS_Migration\scripts\state_snapshot_$timestamp"

# Validar que el bot está parado
$running = Get-Process pythonw, terminal64 -ErrorAction SilentlyContinue
if ($running) {
    Write-Host "⚠️  Detectados procesos del bot aún corriendo:" -ForegroundColor Yellow
    $running | Format-Table ProcessName, Id, @{N='RAM_MB';E={[math]::Round($_.WorkingSet64/1MB,1)}} -AutoSize
    Write-Host ""
    $ans = Read-Host "¿Continuar de todos modos? El snapshot puede estar inconsistente. (S/N)"
    if ($ans -notmatch "^[SsYy]") {
        Write-Host "Cancelado. Para el bot primero:" -ForegroundColor Yellow
        Write-Host "  Get-Process pythonw, terminal64 | Stop-Process -Force" -ForegroundColor White
        exit 1
    }
}

New-Item -ItemType Directory -Path $snapDir -Force | Out-Null
Write-Host "📁 Snapshot dir: $snapDir" -ForegroundColor Cyan

# Lista de archivos de estado que cambian en runtime
$stateFiles = @(
    "copier_open_signals.json","copier_sent_state.json","copier_stats.json",
    "generator_state.json","generator_signals_queue.json","generated_signals.json",
    "historial_real.json","daily_summary_state.json","monthly_summary_state.json",
    "pub_state.json","estado.json","gift_history.json","gift_tracker.json",
    "ig_follow_log.json","ig_rate_state.json","llm_features_stats.json",
    "manual_signals.json","mt5_circuit_breaker.json","mt5_realtime.json",
    "mt5_trades_sync.json","tmp_msgs_hoy.json","whatsapp_recipients.json",
    "weekly_stats.json","translations.json","launcher_config.json"
)

$copied = 0; $missing = @()
foreach ($f in $stateFiles) {
    $sp = Join-Path $src $f
    if (Test-Path $sp) {
        Copy-Item $sp $snapDir -Force
        $size = (Get-Item $sp).Length
        Write-Host ("  ✅ {0,-45} {1,8} bytes" -f $f, $size) -ForegroundColor Green
        $copied++
    } else {
        $missing += $f
    }
}

# Manifiesto con metadata del snapshot
$manifest = @{
    timestamp = (Get-Date -Format "o")
    files_copied = $copied
    files_missing = $missing
    source = $src
    bot_was_running = ($running -ne $null)
    files = $stateFiles
} | ConvertTo-Json -Depth 5
Set-Content -Path (Join-Path $snapDir "_manifest.json") -Value $manifest -Encoding utf8

Write-Host ""
Write-Host "✅ Snapshot completo: $copied archivos" -ForegroundColor Green
if ($missing.Count -gt 0) {
    Write-Host "⚠️  No existen en source: $($missing -join ', ')" -ForegroundColor Yellow
}

# Comprimir
$zipPath = "$snapDir.zip"
Write-Host ""
Write-Host "Comprimiendo a: $zipPath"
Compress-Archive -Path "$snapDir\*" -DestinationPath $zipPath -Force
$zipSize = [math]::Round((Get-Item $zipPath).Length/1KB,1)
Write-Host "✅ ZIP creado: $zipSize KB" -ForegroundColor Green

Write-Host ""
Write-Host "═══════════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host " PRÓXIMOS PASOS:" -ForegroundColor Cyan
Write-Host "═══════════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host ""
Write-Host "  1. Copiar $zipPath al VPS por RDP (drag&drop al escritorio)" -ForegroundColor White
Write-Host "  2. En el VPS ejecutar:" -ForegroundColor White
Write-Host "     cd C:\BuySell365\scripts" -ForegroundColor Yellow
Write-Host "     .\restore_state.ps1 -SnapshotZip C:\Users\Administrator\Desktop\$(Split-Path $zipPath -Leaf)" -ForegroundColor Yellow
Write-Host "  3. Arrancar el bot:" -ForegroundColor White
Write-Host "     cd C:\BuySell365\app; python launcher.py" -ForegroundColor Yellow
Write-Host ""

# Checklist operativo — Día del switch (VPS Linux + sin MT5)

> Imprime esta página o ténla abierta en el portátil mientras haces el switch.
> Tacha cada casilla `[ ]` → `[x]` según avances.

---

## Pre-switch (Día -1, noche anterior)

- [ ] VPS Linux contratado, IP guardada: `_______________`
- [ ] SSH funciona desde tu PC: `ssh root@TU_IP`
- [ ] `VPS_SETUP.sh` ejecutado sin errores en el VPS
- [ ] `.env` revisado en `/opt/buysell365/app/.env` (TELEGRAM_TOKEN, ANTHROPIC_API_KEY, SYNC_SECRET)
- [ ] Smoke test pasado: `bash /opt/buysell365/scripts/04_test_smoke.sh` → todo ✅
- [ ] systemd service registrado: `sudo systemctl status buysell365` muestra "enabled"
- [ ] Saldo Anthropic API > $5
- [ ] TextMeBot subscription activa
- [ ] Render dashboard accesible: https://buysell365.pro

## T-15 min (preparación final en PC)

- [ ] Sin operaciones MT5 a punto de TP/SL en tu trading manual
- [ ] Anotar **señales abiertas en `copier_open_signals.json`:** `_______________`
- [ ] Screenshot dashboard Render
- [ ] (Opcional) Avisar al admin/personal: "Mantenimiento técnico 15 min"

## T-0 (STOP del bot en PC)

- [ ] Cerrar launcher GUI o ejecutar:
  ```powershell
  Get-Process pythonw, terminal64 -ErrorAction SilentlyContinue | Stop-Process -Force
  ```
- [ ] Confirmar 0 procesos del bot: `Get-Process pythonw, terminal64 -ErrorAction SilentlyContinue`
- [ ] Hora exacta del stop: `_______________`

## T+1 min (snapshot fresco)

- [ ] `cd C:\Users\hpint\Desktop\BuySell365_VPS_Migration\scripts`
- [ ] `.\snap_state_now.ps1`
- [ ] Confirmar carpeta `state_snapshot_YYYYMMDD_HHMMSS` creada
- [ ] Confirmar 25 JSONs dentro de la carpeta
- [ ] Confirmar archivo `.zip` generado

## T+3 min (subir snapshot al VPS)

- [ ] `scp .\state_snapshot_*.zip root@TU_IP:/tmp/`
- [ ] Verificar en VPS: `ls -la /tmp/state_snapshot_*.zip`

## T+5 min (aplicar snapshot en VPS)

- [ ] `sudo bash /opt/buysell365/scripts/restore_state.sh /tmp/state_snapshot_*.zip`
- [ ] Confirmar "✅ RESTORE COMPLETO" + 25 archivos restaurados en el output

## T+7 min (ARRANCAR bot en VPS)

- [ ] `sudo systemctl start buysell365`
- [ ] `sudo journalctl -u buysell365 -f` (ver logs en vivo)
- [ ] Hora exacta del arranque: `_______________`

## T+10 min (validación rápida)

- [ ] Heartbeat recibido en Telegram (canal admin)
- [ ] Logs en journalctl sin ERROR/CRITICAL
- [ ] Dashboard Render: timestamp avanzando
- [ ] `price_feed` devuelve precios reales (visible en logs)

## T+15 min (test publicación)

- [ ] Enviar mensaje a chat de prueba con `/status` o equivalente
- [ ] Verificar respuesta del bot

## T+30 min (estabilización)

- [ ] Sin restarts inesperados (`sudo systemctl status buysell365` muestra "active running")
- [ ] Si llega una señal real → verificar flujo completo (parser → publicación VIP → WhatsApp)

---

## Día +1 (validación 24h)

- [ ] Briefing 07:00 publicado correctamente
- [ ] Al menos 1 señal copiada con publicación VIP + WhatsApp OK
- [ ] Daily recap 19:00 publicado
- [ ] Sin alertas del heartbeat
- [ ] Render dashboard con datos actualizados de hoy

## Día +7 (apagado PC local)

- [ ] 7 días sin incidentes mayores
- [ ] Apagar definitivamente launcher en PC (si seguía abierto)
- [ ] Mover `C:\Users\hpint\Desktop\BuySell365_Bot` a backup externo
- [ ] Apagar PC o usar normal (ya no es producción)

---

## Comandos útiles en el VPS Linux

| Necesito... | Comando |
|---|---|
| Ver logs en vivo | `sudo journalctl -u buysell365 -f` |
| Últimas 100 líneas | `sudo journalctl -u buysell365 -n 100` |
| Logs de la última hora | `sudo journalctl -u buysell365 --since "1 hour ago"` |
| Estado del servicio | `sudo systemctl status buysell365` |
| Reiniciar | `sudo systemctl restart buysell365` |
| Parar | `sudo systemctl stop buysell365` |
| Editar `.env` | `nano /opt/buysell365/app/.env` (después reinicia el servicio) |
| Smoke test on-demand | `bash /opt/buysell365/scripts/04_test_smoke.sh` |
| Activar venv para tests manuales | `cd /opt/buysell365/app && source .venv/bin/activate` |
| Re-snapshot estado del VPS | `cp /opt/buysell365/app/state/*.json /tmp/backup_$(date +%Y%m%d)/` |

---

## Si algo falla → ROLLBACK INMEDIATO

```bash
# En VPS:
sudo systemctl stop buysell365
```
```powershell
# En tu PC:
cd C:\Users\hpint\Desktop\BuySell365_Bot
python launcher.py
```

El PC retoma con el estado anterior. Pérdida: 0-10 min de actividad.
Anota qué falló para reintentar en la siguiente ventana.

---

## Si el refactor sin MT5 da problemas inesperados

Tienes 3 backups disponibles:

1. **Tu carpeta original `BuySell365_Bot/`** — intocada, lista para retomar
2. **Backups dentro del paquete:** `app/.env.bak_pre_nomt5`, `app/requirements.txt.bak_pre_nomt5`, `app/_archive_mt5_only/`
3. **Scripts Windows originales:** `scripts/_fallback_windows/` (por si decides volver a Windows+MT5)

Reverso a Windows+MT5 si fuera necesario: <1h de trabajo restaurando los archivos.

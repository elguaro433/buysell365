# Plan de migración BuySell365 → VPS Linux (sin MT5)

> **Objetivo:** Trasladar el sistema completo (bot Telegram + signal copier + generators + publishers + notifiers + IA) desde tu PC Windows a un VPS Linux 24/7. **Sin MT5.** Sin interrumpir el servicio VIP más de 15 min.
>
> **Pre-requisito:** El refactor sin MT5 ya está aplicado en `BuySell365_VPS_Migration/app/`. Ver `REFACTOR_NO_MT5.md` para detalles.

---

## Fase 0 — Pre-requisitos

### 0.1 Contratar VPS
**Recomendado: Hetzner CX32** (4 vCPU / 8 GB / 80 GB / **€7,55/mes**)
- URL: https://www.hetzner.com/cloud
- Datacenter: Falkenstein (Alemania) o Helsinki (Finlandia) — ambos en Europa, latencia OK a APIs externas
- OS al crear: **Debian 12** (mejor estabilidad para producción)
- Añadir tu clave SSH al crear (saltarse el password)

Alternativas:
- **Hostinger KVM 2 Linux** ~€7-9/mes (soporte español)
- **OVH VPS-2 Linux** €10,27/mes (overprovisioned)

### 0.2 Acceso SSH desde tu PC
Desde PowerShell:
```powershell
ssh root@TU_IP_DEL_VPS
# o si añadiste tu clave SSH:
ssh -i ~/.ssh/id_rsa root@TU_IP_DEL_VPS
```

**Primera vez:** acepta el fingerprint.

### 0.3 Configurar el VPS (5 min iniciales)
```bash
# En el VPS, primera conexión:
apt update && apt upgrade -y
adduser buysell    # crea usuario no-root (opcional pero recomendado)
usermod -aG sudo buysell
mkdir -p /opt/buysell365
chown -R buysell:buysell /opt/buysell365
```

---

## Fase 1 — Subir el paquete al VPS

### 1.1 Comprimir la carpeta en tu PC
```powershell
# En PowerShell:
Compress-Archive -Path C:\Users\hpint\Desktop\BuySell365_VPS_Migration\* `
                 -DestinationPath C:\Users\hpint\Desktop\BuySell365_VPS_Migration.zip
```

### 1.2 Subir al VPS por SCP
```powershell
# Desde PowerShell (Windows tiene scp nativo desde Win10):
scp C:\Users\hpint\Desktop\BuySell365_VPS_Migration.zip root@TU_IP_VPS:/tmp/
```

(Tarda ~2-5 min según tu velocidad de subida, ~285 MB)

**Alternativa más rápida si tienes WinSCP o FileZilla:** arrastrar la carpeta entera vía SFTP.

### 1.3 Descomprimir en el VPS
```bash
# En el VPS:
cd /opt
unzip /tmp/BuySell365_VPS_Migration.zip -d buysell365_temp
mv buysell365_temp/* buysell365/
rmdir buysell365_temp
rm /tmp/BuySell365_VPS_Migration.zip
ls /opt/buysell365/  # debe mostrar app/ scripts/ docs/ etc.
```

---

## Fase 2 — Instalación

### 2.1 Permisos de ejecución
```bash
chmod +x /opt/buysell365/scripts/*.sh
```

### 2.2 Ejecutar master installer
```bash
sudo bash /opt/buysell365/scripts/VPS_SETUP.sh
```

Esto ejecuta:
1. **01_install_python.sh** — Python 3.11 + paquetes sistema (build-essential, libssl, libopenblas, etc.)
2. **02_install_deps.sh** — venv + `pip install -r requirements.txt` (~5-8 min)
3. **03_setup_systemd.sh** — Crea servicio `buysell365.service`, lo habilita pero NO lo arranca
4. **04_test_smoke.sh** — Health check completo

**Tiempo total:** ~15-20 min.

### 2.3 Revisar `.env` final
```bash
nano /opt/buysell365/app/.env
```

Verifica que las claves API críticas están bien:
- `TELEGRAM_TOKEN` (tu bot)
- `ANTHROPIC_API_KEY` (LLM)
- `SYNC_SECRET` (para Render)
- `TEXTMEBOT_APIKEY` (WhatsApp)

Las variables `MT5_*` deben estar comentadas (con `# [refactor sin MT5]` delante).

---

## Fase 3 — Smoke test extendido (antes del switch)

### 3.1 Arrancar manualmente en foreground (para ver logs en vivo)
```bash
cd /opt/buysell365/app
source .venv/bin/activate
DRY_RUN=true python launcher.py
```

Validar durante 5-10 min:
- ✅ Sin errores Python
- ✅ Telethon conecta y lee canales aliados
- ✅ Bot Telegram responde a `/start` en chat de prueba
- ✅ `price_feed.get_tick("BTCUSD")` devuelve precio real

Parar con `Ctrl+C`.

### 3.2 Arrancar como servicio
```bash
sudo systemctl start buysell365
sudo journalctl -u buysell365 -f    # ver logs en vivo
```

Si todo va bien, los logs muestran el bot arrancando módulo por módulo.

Parar para Fase 4:
```bash
sudo systemctl stop buysell365
```

---

## Fase 4 — Día del switch

> **Ventana recomendada:** Domingo entre 18:00 y 20:00 Andorra (mercados Forex cerrados, mínimo movimiento).

### 4.1 Pre-switch en TU PC (T-15 min)
- Anotar saldo MT5 actual: ______________
- Anotar número señales abiertas en `copier_open_signals.json`: ______________
- Screenshot dashboard Render: https://buysell365.pro

### 4.2 Stop del bot en PC (T-0)
```powershell
# En tu PC Windows:
Get-Process pythonw, terminal64 -ErrorAction SilentlyContinue | Stop-Process -Force
```

### 4.3 Snapshot fresco del estado
```powershell
# En tu PC:
cd C:\Users\hpint\Desktop\BuySell365_VPS_Migration\scripts
.\snap_state_now.ps1
# Genera: state_snapshot_YYYYMMDD_HHMMSS\ + .zip
```

### 4.4 Subir snapshot al VPS
```powershell
# En PowerShell:
scp .\state_snapshot_*.zip root@TU_IP_VPS:/tmp/
```

### 4.5 Aplicar snapshot en VPS
```bash
# En el VPS (SSH):
sudo bash /opt/buysell365/scripts/restore_state.sh /tmp/state_snapshot_*.zip
```

Verifica el output: `✅ RESTORE COMPLETO` + número de archivos restaurados (debería ser 25).

### 4.6 Arrancar el bot en VPS
```bash
sudo systemctl start buysell365
sudo journalctl -u buysell365 -f
```

### 4.7 Validación post-arranque (T+5 min)
- ✅ Heartbeat en Telegram al admin (tarda <2 min)
- ✅ Logs sin ERROR / CRITICAL
- ✅ Dashboard Render: timestamp avanzando
- ✅ Bot responde a `/status` en chat de prueba

---

## Fase 5 — Validación 24h

- Briefing 07:00 publicado ✓
- Al menos 1 señal copiada (parser → publicación VIP → WhatsApp) ✓
- Daily recap 19:00 publicado ✓
- Sin alertas heartbeat ✓

---

## Fase 5.5 — Configurar Web Admin Panel (opcional pero recomendado)

Tras arrancar el bot, configura el panel web para gestión visual.

### 5.5.1 Cambiar credenciales por defecto
En `/opt/buysell365/app/.env` añadir (si no están):
```
WEB_ADMIN_USER=tu_usuario_admin
WEB_ADMIN_PASSWORD=password_largo_y_unico_aqui
WEB_ADMIN_SECRET=string_aleatorio_de_64_caracteres_para_sesiones
WEB_ADMIN_PORT=5001
```

Genera `WEB_ADMIN_SECRET` con:
```bash
python3 -c "import secrets; print(secrets.token_urlsafe(64))"
```

### 5.5.2 Arrancar el panel
Ya está habilitado por systemd (lo hace `03_setup_systemd.sh`). Para arrancarlo manualmente:
```bash
sudo systemctl start buysell365_admin
sudo journalctl -u buysell365_admin -f
```

### 5.5.3 Abrir firewall del VPS para puerto 5001
```bash
sudo ufw allow 5001/tcp
```

### 5.5.4 Acceder al panel
Desde tu navegador: `http://TU_IP_VPS:5001`

Login con las credenciales que pusiste en .env.

### 5.5.5 (Opcional) HTTPS con subdominio
Si quieres `https://admin.buysell365.pro` en lugar de IP+puerto:

1. Apuntar subdominio `admin.buysell365.pro` → IP de tu VPS (registro A en tu DNS)
2. Instalar nginx + certbot:
```bash
sudo apt install -y nginx certbot python3-certbot-nginx
sudo certbot --nginx -d admin.buysell365.pro
```
3. Configurar nginx como proxy reverso al puerto 5001:
```nginx
# /etc/nginx/sites-available/admin.buysell365.pro
server {
    listen 443 ssl;
    server_name admin.buysell365.pro;
    # ssl_certificate ... (certbot lo añade solo)
    location / {
        proxy_pass http://127.0.0.1:5001;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_buffering off;  # importante para SSE de logs
        proxy_read_timeout 86400;
    }
}
```
4. Activar:
```bash
sudo ln -s /etc/nginx/sites-available/admin.buysell365.pro /etc/nginx/sites-enabled/
sudo systemctl reload nginx
```

Ya puedes acceder vía `https://admin.buysell365.pro` con certificado SSL gratis.

---

## Fase 6 — Apagado PC local (Día +7)

- 7 días sin incidentes mayores
- Apagar bot en PC (si seguía abierto por error)
- Mover `C:\Users\hpint\Desktop\BuySell365_Bot` a unidad externa como backup
- Apagar PC

---

## Rollback de emergencia

**Si algo va mal en Fase 4:**
```bash
# En VPS:
sudo systemctl stop buysell365
```
```powershell
# En PC:
cd C:\Users\hpint\Desktop\BuySell365_Bot
python launcher.py
```

PC retoma con estado anterior al snapshot. Pérdida: minutos de actividad.

**Si descubres que necesitas MT5 después de todo:**
```bash
# En VPS Linux: NO se puede instalar MT5 nativo
# Tu PC original tiene todo intacto — vuelve allí
```
O bien, contratar un VPS Windows separado y restaurar el código original desde `_archive_mt5_only/` + reactivar `.env.bak_pre_nomt5`.

---

## Troubleshooting

### "ModuleNotFoundError: No module named 'price_feed'"
- Estás corriendo desde directorio incorrecto. Asegúrate de `cd /opt/buysell365/app` antes de python.

### "Telethon pide código SMS"
- Las sesiones `*.session` no se copiaron. Verifica:
  ```bash
  ls /opt/buysell365/app/*.session
  ```
  Debe haber: `signal_copier_session.session` y `userbot_session.session`.

### "Instagram pide verificación"
- Cambio de IP detectado por Meta. Ejecuta `python ig_relogin.py` por SSH y completa el code 2FA.

### "yfinance falla con rate-limit"
- Demasiadas queries en poco tiempo. price_feed.py tiene cache 5s — verifica que no hay loops sin cache.
- Alternativa: configurar `TWELVE_DATA_KEY` en .env (ya la tienes) para fallback.

### "systemctl status buysell365 muestra failed"
```bash
sudo journalctl -u buysell365 -n 100    # ver últimas 100 líneas de log
```
El error real está en los logs. 99% de las veces es:
- `.env` mal configurado
- Falta permiso de lectura en algún archivo
- `price_feed.py` no se encuentra (verifica `cd /opt/buysell365/app && python -c "import price_feed"`)

### "Render dashboard no recibe updates"
```bash
cd /opt/buysell365/app && source .venv/bin/activate && python sync_web_now.py
```
Verifica logs. Si HTTP 401/403 → `SYNC_SECRET` mal configurado.

### "El bot crashea al arrancar"
1. Smoke test manual: `bash /opt/buysell365/scripts/04_test_smoke.sh`
2. Logs detallados: `sudo journalctl -u buysell365 -n 200`
3. Si es bug del refactor: tu PC original sigue intacta — reverte allí mientras debuggeas

---

## Tras la migración: mejoras opcionales

1. **Snapshots automáticos del VPS** (Hetzner: panel → Snapshots, ~€0,40/mes)
2. **Backup diario del estado a S3/Backblaze** (~€0,10/mes para los 1 MB de JSONs)
3. **Reorganizar código en paquete `buysell365`** (post-estabilización, ver conversación previa)
4. **Migrar GitHub a privado** (pendiente desde 12-may según memoria)
5. **Endurecer launcher con auto-restart y dashboard interno** ("Launcher Pro")
6. **Probar Twelvedata/Finnhub como fallback** de yfinance si sufre rate-limits frecuentes

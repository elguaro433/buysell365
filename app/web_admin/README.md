# BuySell365 Web Admin Panel

Panel web para gestionar el bot desde cualquier navegador (PC, móvil, tablet).

## Arrancar el panel

### Local (tu PC, para probarlo)
```powershell
cd C:\Users\hpint\Desktop\BuySell365_VPS_Migration\app
python -m web_admin
```

Abre el navegador en `http://localhost:5001`.

### VPS Linux (producción)
Crear servicio systemd dedicado (opcional, recomendado):
```bash
# /etc/systemd/system/buysell365_admin.service
[Unit]
Description=BuySell365 Web Admin Panel
After=network-online.target

[Service]
WorkingDirectory=/opt/buysell365/app
EnvironmentFile=/opt/buysell365/app/.env
ExecStart=/opt/buysell365/app/.venv/bin/python -m web_admin
Restart=on-failure
User=root

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable buysell365_admin
sudo systemctl start buysell365_admin
```

Acceso: `http://TU_VPS_IP:5001` (configurar HTTPS con nginx + Let's Encrypt para producción).

## Credenciales por defecto

- **Usuario:** `admin`
- **Contraseña:** `buysell365`

**⚠️ CAMBIARLAS antes de exponer el VPS** vía `.env`:
```bash
WEB_ADMIN_USER=tu_usuario
WEB_ADMIN_PASSWORD=tu_password_largo_y_unico
WEB_ADMIN_SECRET=string_aleatorio_de_64_caracteres
WEB_ADMIN_PORT=5001
```

## Funcionalidades

| Página | Funcionalidad |
|---|---|
| **📊 Dashboard** | Stats vivas del bot, conexiones, LLM stats, últimas señales, botones control |
| **📡 Señales** | Pegar señal manual, ver abiertas + historial |
| **📜 Logs** | Streaming en vivo (SSE), filtros INFO/WARN/ERROR, búsqueda, descarga |
| **👑 VIP** | Tabla suscriptores, dar VIP manual, revocar |
| **📱 WhatsApp** | **CRUD completo**: añadir, editar, eliminar, test send, bulk pause/enable |
| **⚙️ Config** | Editor visual del `.env` con grupos lógicos + backup automático |

## Cómo se conecta con el bot

El panel **NO importa el bot directamente** — lee/escribe los JSON de estado que el bot ya genera:
- `whatsapp_recipients.json` (CRUD WhatsApp)
- `copier_stats.json` (stats)
- `copier_open_signals.json` (señales abiertas)
- `historial_real.json` (recientes)
- `gift_history.json` (VIPs)
- `llm_features_stats.json` (LLM stats)
- `mt5_realtime.json` (estado bot)
- `manual_signals.json` (cola de señales manuales)
- `.env` (config)

Y ejecuta `systemctl` para start/stop/restart del bot.

**Cero acoplamiento al código del bot** — si el bot crashea, el panel sigue OK.

## Seguridad

- ✅ Auth con sesión Flask (30 días)
- ✅ Backup automático antes de cualquier escritura en `.env` o JSON crítico
- ✅ Confirmación JS para acciones destructivas (delete, revoke)
- ⚠️ HTTP sin TLS — usar nginx con Let's Encrypt para HTTPS en VPS
- ⚠️ 2FA no implementado (TODO futuro)

## Archivos del panel

```
web_admin/
├── __init__.py
├── __main__.py              # python -m web_admin
├── app.py                   # Flask factory + blueprint registration
├── auth.py                  # login_required decorator
├── config.py                # Settings (port, user, paths)
├── data_access.py           # Helpers JSON read/write atómico
├── README.md
├── routes/
│   ├── auth.py              # login / logout
│   ├── dashboard.py         # / (home)
│   ├── whatsapp.py          # /whatsapp/* (CRUD completo)
│   ├── vip.py               # /vip/* (CRUD)
│   ├── signals.py           # /signals/* (manual paste)
│   ├── logs.py              # /logs/* (stream SSE + tail)
│   ├── config_route.py      # /config/* (.env editor)
│   └── control.py           # /control/{start,stop,restart}
├── templates/
│   ├── base.html            # Layout común (header + tabs)
│   ├── login.html
│   ├── dashboard.html
│   ├── whatsapp.html        # CRUD + modales add/edit
│   ├── vip.html
│   ├── signals.html
│   ├── logs.html            # Streaming SSE + filtros
│   └── config.html          # Editor .env por grupos
└── static/                  # CSS/JS extra (vacío — usa Tailwind CDN)
```

## Próximas mejoras posibles

- [ ] 2FA con TOTP (Google Authenticator)
- [ ] WebSocket en lugar de SSE para logs bidireccional
- [ ] Charts Chart.js con stats históricas (pips por día/semana)
- [ ] Notificaciones push del navegador cuando entra señal
- [ ] Multi-usuario con roles (admin vs viewer)
- [ ] Activity log (auditoría de quién hizo qué)

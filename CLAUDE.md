# BuySell365 — Contexto para Claude Code

> Lee este archivo PRIMERO al empezar cualquier sesión. Resume el estado del proyecto,
> la arquitectura real, cómo se despliega y las políticas vigentes.

---

## 🎯 Qué es esto

Bot privado de señales de trading (Telegram + Instagram + WhatsApp + web).
**Propietario:** Emmanuel Diaz Sanchez (`emmanuel050216@gmail.com`).

Producto comercial activo con clientes VIP (4 a fecha de la última auditoría).
NO es un proyecto experimental — cualquier cambio que se despliegue afecta
a usuarios reales y dinero real.

---

## 🗂 Estructura de carpetas

Después de la consolidación del **2026-05-24**:

```
C:\Users\hpint\Desktop\
├── BuySell365\                    ← ESTA carpeta (la única de trabajo)
│   ├── app\                       ← código (mirror del VPS /opt/buysell365)
│   │   ├── bot.py                  (22 K líneas)
│   │   ├── signal_copier.py        (12 K líneas)
│   │   ├── launcher.py
│   │   ├── daily_summary_publisher.py
│   │   ├── weekly_summary_publisher.py
│   │   ├── monthly_summary_publisher.py
│   │   ├── daily_promo_publisher.py
│   │   ├── signals_promo_publisher.py
│   │   ├── instagram_poster.py
│   │   ├── btc_eth_generator.py
│   │   ├── whatsapp_notifier.py
│   │   ├── price_feed.py           ← abstracción de precios (post-MT5)
│   │   ├── web_admin\              ← Flask panel admin (puerto 5001)
│   │   ├── web\                    ← Flask landing/dashboard público
│   │   ├── .env                    ← secrets (NO commitear)
│   │   └── ... (~69 archivos)
│   ├── scripts\                   ← installers + setup Linux (.sh)
│   ├── _extras_local\             ← cosas que NO se despliegan al VPS
│   ├── .gitignore
│   ├── CLAUDE.md                  ← este archivo
│   └── README.md
│
└── _backups\
    └── BuySell365_Bot_OLD_20260524.zip  ← carpeta vieja Windows+MT5 (583 MB)
```

> ⚠️ **Si ves una carpeta `BuySell365_Bot` o `BuySell365_VPS_Migration` en el Desktop,
> está obsoleta.** La única buena es `BuySell365`.
>
> Hasta que la sesión actual se cierre, la carpeta sigue llamándose
> `BuySell365_VPS_Migration` (bloqueada por el cwd del proceso claude).
> Hay un script `_RENAME_AL_CERRAR_CLAUDE.ps1` para renombrar después.

---

## 🚀 Arquitectura runtime

### Producción
- **Bot vive en:** VPS Linux InterServer KVM, IP `208.73.204.188`
- **Path:** `/opt/buysell365/` (estructura idéntica a `app/` local)
- **Servicios systemd:** `buysell365.service` (bot) + `buysell365_admin.service` (panel web)
- **Stack:** Python 3.11 + Telegram Bot API + Telethon + Flask
- **MT5: DESACTIVADO** (no se ejecutan órdenes reales, solo se publican señales)

### Conexiones externas
- **Telegram Bot** `@Andoperandobot` — canal VIP + grupo público `@BUYSELL_365_24_7`
- **Telethon** — userbot que lee canales aliados de señales
- **Instagram** `@buysell365.pro_tradingsignals` (vía `instagrapi`)
- **WhatsApp** (4 destinatarios fijos, vía TextMeBot API)
- **Anthropic Claude API** — `claude-sonnet-4-6` para análisis y % probabilidad
- **Render** — dashboard web público en `buysell365.pro`

### Paneles
- **Panel admin del bot:** `http://208.73.204.188:5001` (auth básica)
  - Start/Stop/Restart bot vía systemctl
  - Editar `.env` desde browser
  - Logs en vivo, lista VIPs, WhatsApp, señales recientes
- **Dashboard público:** `https://buysell365.pro/dashboard` (Render)
- **InterServer panel:** `my.interserver.net` (gestión VPS, tickets)

---

## 🔧 Cómo desplegar cambios al VPS

### Workflow rápido (recomendado, 30-60 s)

```powershell
# 1) Editar archivos en app/
# 2) Deploy con un solo comando
.\deploy.ps1 "fix: descripcion del cambio"
```

Eso hace automáticamente:
1. `git add -A` + `git commit` + `git push origin HEAD:main`
2. SSH al VPS con la key dedicada
3. `cd /opt/buysell365 && git fetch && git pull`
4. Si `requirements.txt` cambió → `pip install -r requirements.txt`
5. `systemctl restart buysell365` + `buysell365_admin`
6. Verifica `is-active` y muestra últimas líneas de log

### Helpers disponibles (en `tools/`)

| Comando | Qué hace |
|---|---|
| `.\deploy.ps1 "msg"` | Pipeline completo de deploy |
| `.\deploy.ps1 -DryRun` | Preview sin tocar nada |
| `.\deploy.ps1 -SkipGit` | Solo redeploy en VPS, sin commit |
| `.\tools\vps_ssh.ps1` | SSH interactivo al VPS |
| `.\tools\vps_ssh.ps1 "comando"` | Ejecuta comando puntual |
| `.\tools\vps_logs.ps1` | Logs del bot en vivo (tail -f) |
| `.\tools\vps_logs.ps1 -Tail 200` | Últimas 200 líneas |
| `.\tools\vps_logs.ps1 -Errors` | Solo errores |
| `.\tools\vps_status.ps1` | Snapshot (servicios, git, RAM, disco) |
| `.\tools\vps_restart.ps1` | Restart sin pull |

### Flujo manual (si los scripts fallan)

```bash
# 1) En local
git add -A && git commit -m "fix: ..." && git push

# 2) SSH al VPS
ssh -i ~/.ssh/id_ed25519_buysell365 root@208.73.204.188
cd /opt/buysell365 && git pull
systemctl restart buysell365
journalctl -u buysell365 -f       # verificar arranque
```

> **Reglas duras de deploy:**
> - NO `git push origin main` directo — siempre branch + revisión
> - NO desplegar sin verificar `journalctl -u buysell365 -f` después
> - SIEMPRE hacer backup de `.env` antes de cambios de config
> - Si tocas captions/promo, ENSEÑAR al usuario antes (no asumir)

---

## 📜 Políticas vigentes (NO romper)

Reglas que se han ido endureciendo por feedback del usuario:

### Voz del bot / marketing
- **No publicidad MT5 demo + credentials investor** — eliminada el 2026-05-24
  porque ya no se usa MT5. Verbatim eliminados: "VIP channel connected to a
  LIVE MT5 account", "read-only credentials to verify", etc.
- **Recaps daily/weekly/monthly = SOLO se publican si net positivo** (regla
  solo-positivo global, 2026-05-24). Si net < 0 → skip a VIP + grupo + IG.
- **Recap del grupo público = SOLO positivo** (regla más vieja: el grupo
  nunca ve pérdidas, solo VIP las veía hasta el 2026-05-24).
- **Recap del canal VIP = ahora también solo-positivo** (cambio 2026-05-24,
  antes era "verdad siempre").
- **XM afiliado SE MANTIENE** — los anuncios programados de XM Global con
  código `BUYSELL365` (bot.py ~16444) son revenue stream, no tocar.

### Datos / state
- `.env` y `*.session` y `*.lock` NUNCA al repo (ya en `.gitignore`).
- Los `*.json` de estado runtime tampoco — los modifica el bot en vivo.
- Si necesitas resetear estado, BACKUP primero.

### Bot
- `signal_copier.py` es el core (12 K líneas) — leer despacio antes de editar.
- `bot.py` es el entrypoint principal de Telegram (22 K líneas).
- No comentar código muerto si MT5 — borrarlo. El refactor a no-MT5 ya está hecho.

---

## 🔑 Accesos

> Las credenciales reales NO están en este archivo (por seguridad). Están en:
> - **`.env`** dentro de `app/` (no committeado)
> - **InterServer panel** para SSH password y panel VPS
> - **Bitwarden / gestor del usuario** para todo lo demás

| Servicio | URL / IP | Notas |
|---|---|---|
| VPS SSH | `root@208.73.204.188` | Password reset 2026-05-24 (ticket RMK-122-71751) |
| Panel admin bot | `http://208.73.204.188:5001` | Auth basic, user/pwd en `.env` |
| InterServer | `my.interserver.net` | Cuenta `emmanuel050216@gmail.com` |
| GitHub | `https://github.com/elguaro433/buysell365` | Owner `elguaro433` |
| Render | dashboard `buysell365.pro` | Auto-deploy en push a `main` |
| Telegram Admin | usuario Emmanuel | ID en `.env` como `ADMIN_IDS` |

---

## 🌿 Branches activos (al 2026-05-24)

- **`main` (origin)** — última versión productiva (5 commits ahead localmente
  que NO se pushearon en su día, ver `git log origin/main..HEAD` en repo viejo)
- **`migration/clean-2026-05-24`** — branch limpio inicial creado tras la
  consolidación de carpetas, contiene las mejoras de hoy (sin MT5, solo-positivo)
- ~50 branches `claude/*` de sesiones previas — pueden borrarse en bulk si
  ya están mergeadas o abandonadas

---

## 📝 Historial reciente (qué pasó hoy)

**2026-05-24** — Día de consolidación grande:
1. Usuario pidió eliminar publi MT5 demo (credenciales investor read-only)
   que se publicaban a las 20:15 al canal VIP + en captions de promo/recap
2. Se borró `transparency_publisher.py` entero
3. Se limpiaron menciones MT5 en 6 archivos: `daily_promo_publisher.py`,
   `daily_summary_publisher.py`, `signal_copier.py`, `bot.py`
4. Se aplicó regla solo-positivo global a recaps daily + weekly + monthly
   (antes solo se silenciaba grupo público + IG, el VIP siempre recibía)
5. Se descubrió que existían 2 carpetas (`BuySell365_Bot` vieja Windows+MT5,
   y `BuySell365_VPS_Migration` nueva Linux). Se archivó la vieja en zip de
   583 MB en `Desktop\_backups\` y se borró
6. Se inició git en la carpeta nueva, conectada al repo existente, push a
   branch `migration/clean-2026-05-24`
7. SSH al VPS desbloqueado (ticket RMK-122-71751 resuelto por Akshay Pradeep)
8. Pendiente: renombrar `BuySell365_VPS_Migration` → `BuySell365` y deploy al VPS

---

## 🤝 Cómo trabajar con el usuario

Patrones que el usuario prefiere (observados en sesiones previas):

- **Investigar antes de actuar.** El usuario dice "quiero que entiendas todo
  primero" y se molesta si actúas con suposiciones. Lee los archivos, no
  asumas estructura.
- **Confirmar cambios públicos / destructivos.** Cualquier cosa que toque
  el canal VIP, grupo público, IG, WhatsApp o el VPS de producción → pedir
  OK explícito con AskUserQuestion antes.
- **Conciso en español.** Sin emojis salvo que él los use. Tablas markdown
  funcionan bien para resumir.
- **No verbose.** No explicar lo que ya hizo claramente — el usuario lee
  los diffs. Reportar solo: qué cambió, dónde, y qué pasa después.
- **Trabajar en branch, nunca push directo a main.**
- **No exponer credenciales en chat.** El usuario ha dicho explícitamente
  que no le gusta tener cosas guardadas en el historial.

---

## ❓ Si algo no cuadra

1. Lee este archivo otra vez.
2. Si la estructura del repo difiere de lo descrito aquí, INVESTIGAR antes
   de actuar — la realidad del disco gana sobre este doc.
3. Si tienes dudas sobre el VPS, abrir el panel `208.73.204.188:5001` o
   conectar por SSH y verificar.
4. Si vas a desplegar, primero confirmar con el usuario.
5. Actualizar este `CLAUDE.md` cuando tomes una decisión que cambie las
   reglas o la arquitectura.

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

---

## 🚀 Arquitectura runtime

### Producción
- **Bot vive en:** VPS Linux InterServer KVM, IP `208.73.204.188`
- **Path:** `/opt/buysell365/` (estructura idéntica a `app/` local)
- **Servicios systemd:** `buysell365.service` (bot) + `buysell365_admin.service` (panel web)
- **Stack:** Python 3.13 (Debian 13) + Telegram Bot API + Telethon + Flask
- **MT5: DESACTIVADO** (no se ejecutan órdenes reales, solo se publican señales)
- **LLM: DESACTIVADO desde 2026-09-19** (`LLM_PARSER_ENABLED=false`, `LLM_VISION_ENABLED=false`
  en `.env` del VPS por crédito agotado). Parser = regex; probabilidad = solo técnica.

### Conexiones externas
- **Telegram Bot** `@Andoperandobot` — canal VIP + grupo público `@BUYSELL_365_24_7`
- **Telethon** — userbot que lee canales aliados de señales
- **Instagram** `@buysell365.pro_tradingsignals` (vía `instagrapi`)
- **WhatsApp** (2 destinatarios: Emmanuel +376 y Elibel, vía TextMeBot API; sender +34…6572)
- **Anthropic Claude API** — `claude-sonnet-4-6` para análisis y % probabilidad
- **Render** — dashboard web público en `buysell365.pro`

### Paneles
- **Panel admin del bot:** `.\tools\panel.ps1` → abre túnel SSH y `http://localhost:5001`
  (desde 2026-09-19 el puerto 5001 está CERRADO a internet por firewall; auth básica)
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

### Helpers disponibles

| Comando | Qué hace |
|---|---|
| `.\deploy-files.ps1 app/bot.py [...]` | **(USAR HOY)** Deploy archivo(s) por scp + stop+clean+start + verify |
| `.\deploy-files.ps1 -DeleteRemote app/foo.py` | Borrar archivo del VPS |
| `.\deploy.ps1 "msg"` | (Futuro, cuando VPS sea git clone) git commit + push + pull + restart |
| `.\tools\vps_ssh.ps1` | SSH interactivo al VPS |
| `.\tools\vps_ssh.ps1 "comando"` | Ejecuta comando puntual |
| `.\tools\vps_logs.ps1` | Logs del bot en vivo (tail -f) |
| `.\tools\vps_logs.ps1 -Tail 200` | Últimas 200 líneas |
| `.\tools\vps_logs.ps1 -Errors` | Solo errores |
| `.\tools\vps_status.ps1` | Snapshot (servicios, git, RAM, disco) |
| `.\tools\vps_restart.ps1` | Restart sin pull (incluye limpieza de locks) |
| `.\tools\panel.ps1` | Abre el panel admin por túnel SSH (localhost:5001) |
| `.\tools\vps_backup_pull.ps1` | Baja el último backup cifrado del VPS a `Desktop\BuySell365_Backups` |

### ⚠️ Lección crítica del 2026-05-24: lock files stale

**`systemctl restart buysell365` SOLO falla**. El launcher queda colgado sin
spawnear `bot.py` + `signal_copier.py` + `monitor_real.py`.

**Causa**: el bot anterior deja `.bot.singleton.lock`, `.copier.singleton.lock`
y `.copier.lock` en `/opt/buysell365/app/`. El launcher nuevo los detecta y
asume que hay otra instancia, queda esperando.

**Fix**: SIEMPRE usar el patrón **stop → sleep 4 → rm locks → start**:
```bash
systemctl stop buysell365 && sleep 4 && \
  rm -f /opt/buysell365/app/.*.lock /opt/buysell365/app/.*.heartbeat && \
  systemctl start buysell365
```

Ya está integrado en `deploy-files.ps1`, `deploy.ps1` y `tools/vps_restart.ps1`.
**Nunca uses `systemctl restart buysell365` directo** — usa estos scripts.

### ⚠️ Lección crítica del 2026-09-19: NUNCA `systemctl reload ssh`

En este Debian 13 el reload (SIGHUP) de `sshd` falla con "Cannot bind any address" y
**mata el servicio** → puerto 22 cerrado, sin acceso. Se recuperó con **Restart del VPS
desde my.interserver.net** (el bot volvió solo: servicios `enabled`, locks son flock).
- `reload` está deshabilitado en el unit (drop-in `ssh.service.d/10-run-sshd.conf`).
  Para aplicar config: `sshd -t && systemctl restart ssh` (no corta sesiones abiertas).
- El usuario entra a InterServer con Google y **no tiene la password root a mano** →
  la consola VNC no es un fallback. Pedirle que la fije con `passwd` y la guarde.

### Seguridad VPS (estado desde 2026-09-19)
- SSH: **solo key** (`PasswordAuthentication no`, `PermitRootLogin prohibit-password`,
  `MaxAuthTries 3`) + **fail2ban** (4 fallos/10 min → 2 h ban).
- Firewall ufw: ALLOW 22; DENY 8080 (HTTP legacy del bot), 8765/5201 (fio/iperf3 de
  InterServer, deshabilitados). **5001 (panel) ABIERTO** a petición del usuario.
- Panel: rate limit login (5 fallos → 15 min), sin SECRET por defecto en código.
- Backup cifrado diario 04:10 → `/opt/backups/buysell365/bs365_*.tar.enc` (14 días);
  clave en `/root/.backup_pass` y copia en `Desktop\BuySell365_Backups\CLAVE_BACKUP_VPS.txt`.
- Alertas Telegram al admin (`admin_alerts.py`, 1/día por tipo): crédito LLM agotado,
  TextMeBot 411, feed de precios sin datos, copier sin heartbeat >5 min.

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
- **WR (win rate) OCULTO en todo lo público** (2026-06-07) — el WR real es bajo
  (~19%). Quitado de `daily_promo_publisher.py` (imágenes + captions grupo/IG) y
  del bloque semanal `_get_weekly_stats_block` en `signal_copier.py`. Se siguen
  mostrando pts y nº de TPs, pero NUNCA el % de aciertos. (En logs internos sí queda.)
- **Cadencia de promo del GRUPO recortada** (2026-06-06/07, grupo de ~60, evitar
  spam-feeling):
  - Rotación VIP (`loop_publicidad_grupo`, 5 modelos auto-borra): **1×/día a las
    21:00** (desde 2026-06-21; antes 2×/día).
  - "2 FREE SIGNALS" (signals_promo): solo 09:00 (el repe 19:00 DESACTIVADO en
    bot.py ~16956 con `if False`).
  - Promo IG de las 14:00 en grupo (`signal_copier.py` ~6611): DESACTIVADA (`if False`).
  - Briefing 07:00 al grupo: caption limpio "Market Briefing" (sin ad "Want VIP").
  - Eli intro (`signal_copier.py` ~6545): de diaria a **semanal (lunes) y solo EN**.
  - "Discover all platforms" al GRUPO (`daily_promo_publisher.py`): ELIMINADO
    (se mantiene SOLO el post de Instagram). Los links viven en un **mensaje
    FIJADO** del grupo (msg_id 3180).
  - Lo que NO se toca: señales FREE, celebraciones TP HIT, afiliado XM.

### Precios de referencia (TP/SL, validación de entry, "señal muerta")
- **NUNCA usar futuros como precio actual** de oro/índices: `GC=F`, `YM=F`, `NQ=F`,
  `ES=F` van decenas de $ / cientos de pts por encima del CFD de los aliados.
  Oro → `price_feed.get_spot_gold()`; índices → `^DJI/^NDX/^GSPC` (cash) con
  futuro−basis cuando el contado está cerrado. Lista en `_SPOT_ROUTED_PAIRS`.
- Guard del monitor: primeros 15 min con desvío >0,5 % entre precio de referencia
  y entry → no se evalúa TP/SL (log `GUARD feed`).

### Pips / unidades (display)
- **Convención por activo** (cuidado: hay ~9 formateadores inline en
  `signal_copier.py` + `_get_pips_info` + daily tracker, deben coincidir):
  GOLD ×10 "pips" · OIL/GAS (BRENT/OIL/WTI/USOIL/UKOIL/**NATGAS**/NGAS/XNGUSD)
  ×100 "pts" · índices ×1 "pts" · JPY ×100 "pips" · forex ×10000 "pips".
- FIX 2026-06-06: petróleo/gas con precio <100 caían en forex ×10000 (USOIL
  mostraba "+30000 pips" en vez de "+300 pts"). NATGAS añadido a la lista oil.
- Reportes diarios categorizan con `stats_normalizer.classify_pair` (NO el
  heurístico `entry>=100`, que metía AUD/JPY en "Indices" y oil en "Forex").

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

**2026-09-19 (tarde)** — Auditoría completa + hardening (10 puntos autorizados):
1. Repo: 14 archivos de la universidad movidos a `Desktop\Universidad_archivos`; 2 branches
   mergeados borrados; `requirements.txt` regenerado del `pip freeze` real del VPS.
2. `app/docs/informe_aliados_2026-09-19.txt`: por fuente/par/categoría con pips con signo.
   Hallazgos: SureShotFX pierde en ORO (−3188) y FOREX (−2229); UnitedKings ORO +2939 (WR 65 %);
   ORO SELL WR 26 % vs BUY 74 %; el % de probabilidad NO discrimina (prob 60+ pierde).
3. `admin_alerts.py` + enganches; panel rate limit; log copier: precios a DEBUG, fix
   mapeo `OILCASH`→`CL=F` (1.400 líneas "possibly delisted" por archivo).
4. `.env` VPS: `LLM_PARSER_ENABLED=false`, `LLM_VISION_ENABLED=false` (sin crédito).
5. Backup cifrado en cron + `vps_backup_pull.ps1`. Borrado `copier_stderr.log.1` (200 MB).
6. SSH solo key + fail2ban; ufw; fio/iperf3 apagados; panel por túnel (`panel.ps1`).
   **Incidente**: un `reload ssh` mató sshd 20 min (ver lección arriba). Bot no afectado
   salvo 3 min de reinicio del VPS (sábado, mercado cerrado).
7. Pendiente usuario: fijar password root (`passwd`), crédito Anthropic si quiere LLM.

**2026-09-19** — WhatsApp reconectado + indicador del panel:
1. TextMeBot (número emisor +34…6572) estaba desconectado desde ≤ 9-sep (411 en cada
   envío). El usuario reescaneó el QR el 18/19-sep; test desde el panel llega OK.
2. El indicador WhatsApp del panel solo miraba el último `[WSP]` de `copier.log` → seguía
   en rojo aunque ya funcionara. Ahora el botón 🧪 Test guarda `.wsp_last_probe.json`
   (lo lee el dashboard) y un fallo >6 h sin intentos posteriores se muestra ámbar
   "Sin verificar" en vez de CAÍDO. Deploy solo de `buysell365_admin` (commit ec74a96).
3. Sigue pendiente: crédito Anthropic agotado (LLM PRO "SIN CREDITO").

**2026-09-17** — Auditoría completa + fix del feed de precios:
1. **BUG CRÍTICO (desde 29-jul)**: el monitor TP/SL usaba futuros de yfinance
   (`GC=F`, `YM=F`, `NQ=F`) como precio "actual". El basis futuro-spot subió a
   +37 $ (oro) / +412 pts (US30) → **171 "SL HIT" falsos** en segundos sobre
   SELL ORO/US30 y BUY marcados "señal muerta" y retirados en silencio. Arreglo:
   `price_feed.get_spot_gold()` (gold-api.com → Bitfinex XAUT → Kraken PAXG →
   GC=F−basis) y `^DJI/^NDX/^GSPC` cash con futuro−basis fuera de sesión;
   `_SPOT_ROUTED_PAIRS` en `signal_copier._get_current_price`; guard anti-SL/TP
   instantáneo (15 min, >0,5 % de desvío → no evaluar); velas de gráficos
   ajustadas por basis; nota visible en señales "muertas".
   `scripts/clean_false_sl_stats.py` limpia los 171 SL falsos de `copier_stats.json`.
2. VPS ≠ repo: el VPS corría el branch `fix/rate-limit-collision-y-eod-2026-07-02`
   + 2 parches manuales (stub `winsound` en launcher, Binance borrado en
   price_feed que rompía `_TWELVEDATA_MAP`). Ambos portados al repo de forma
   limpia (Binance se autodesactiva 6 h al recibir 451).
3. HTTPS interno :8443 del bot colgado desde 03-jul (handshake bloqueante, backlog
   lleno, 722 CLOSE-WAIT). Nuevo `HTTPS_ENABLED=false` en `.env` del VPS (la web
   pública es Render). Logs `*_stderr.log` rotan a los 20 MB (había uno de 200 MB).
4. Resumen EOD admin 18:00 sumaba los SL como positivos → usa `stats_normalizer`.
   Formato "-150.0 pts" → "-150 pts".
5. **Pendiente del usuario**: crédito Anthropic agotado (≤ 18-ago, parser LLM,
   Vision, probabilidad en `tech_only`); key TwelveData responde vacío;
   `COPIER_PAIRS_DISABLED=` vacío en `.env` del VPS anula la blacklist de pares.
6. Recortes de promo del 21-jun que este doc no reflejaba: briefing 07:00 VIP,
   promo diaria 12:00 y "2 free signals" 09:00 DESACTIVADOS; rotación VIP al
   grupo **1×/día a las 21:00**. `INSTAGRAM_DISABLED=1` en el VPS.

**2026-06-06 / 07** — Auditoría de señales + limpieza de promo:
1. **Bug pips OIL/GAS**: USOIL mostraba "+30000 pips" / "-15000 pips" (caía en
   forex ×10000 por precio <100). Arregladas las ~9 ramas inline de pips +
   añadido NATGAS a la lista oil. Ahora ×100 "pts".
2. **Reportes diarios**: categorización con `classify_pair` (AUD/JPY ya es Forex,
   USOIL/NATGAS son Oil/Gas). Contador "+N trades today" ahora acumulado (antes
   contaba solo señales abiertas y bajaba al cerrarse). WR oculto en público.
3. **Recorte de autopromo del grupo** (ver Políticas): rotación VIP 2×/día, quitados
   07:00, repe 19:00, promo IG 14h, "Discover platforms" del grupo; Eli semanal EN;
   creado mensaje FIJADO con links (msg 3180).
4. **Panel admin** (`web_admin/data_access.py`): (a) indicador "Telethon" usaba la
   antigüedad de `copier_stats.json` (falso "Inactivo" si no había cierres recientes)
   → ahora mide `.copier.heartbeat`. (b) "Últimas señales" leía `historial_real.json`,
   archivo MUERTO de la era MT5 (congelado en marzo, sin probabilidad) → ahora lee
   `copier_stats.json` (vivo, con prob).
5. WhatsApp: añadido destinatario "piera" (+376691445, filter ALL) — config OK.
6. Deploys con `deploy-files.ps1` (bot) y scp+restart `buysell365_admin` (panel).
   Nota: hubo un deploy que colgó el `scp`; el patrón stop→limpiar locks→start
   funcionó igual. Log lleno de `server accept() SSLError` = escáneres de internet
   al puerto HTTPS público (ruido inofensivo, pendiente silenciar el logger waitress).

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
   - SSH key dedicada en `~/.ssh/id_ed25519_buysell365` (autoriza passwordless)
   - **La password root quedó visible en una captura de chat — cambiarla.**
8. **Deploy al VPS INTENTADO y REVERTIDO (22:05):**
   - Subí 6 .py + borré `transparency_publisher.py`
   - Tras restart, **el launcher no spawn los hijos** (bot.py, signal_copier.py)
   - Logs no mostraron errors visibles, launcher quedó "sleeping" sin hijos
   - **Rollback automático con los .bak_pre_deploy_20260524_215729 restauró todo**
   - Bot operativo de nuevo desde 22:02 con código viejo
   - **Las versiones merged listas para reintentar están en `_staging_vps/`**
     (también en la carpeta local — incluye los edits sobre versiones actuales del VPS)
9. **Pendiente próxima sesión:**
   - Investigar por qué falló el launcher con mi versión (sintaxis y encoding OK)
   - Sospechas: race con xvfb-run, algún lock stale, dependencia oculta
   - Estrategia recomendada: deploy archivo a archivo con restart+verify entre cada uno
   - Renombrar `BuySell365_VPS_Migration` → `BuySell365` (script en Desktop)

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

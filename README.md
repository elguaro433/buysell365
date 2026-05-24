# BuySell365 — Paquete de migración a VPS **Linux (sin MT5)**

Carpeta autocontenida para mover todo el sistema BuySell365 desde tu PC Windows a un VPS Linux barato.

> **Estado (24-may-2026):**
> - ✅ Refactor sin MT5 COMPLETO (`price_feed.py` drop-in)
> - ✅ Web Admin Panel COMPLETO (gestión visual desde navegador, móvil incluido)
> - ✅ Scripts Linux LISTOS (systemd doble servicio: bot + panel)
> - ✅ Auditorías PASADAS (subsistemas + bugs fixados)

## 🆕 Web Admin Panel (NUEVO)

Tendrás un panel web accesible desde **cualquier navegador** (PC, móvil, tablet) para gestionar el bot — sustituye y mejora la consola Tkinter actual.

**URL típica:** `http://TU_IP_VPS:5001` (o tu dominio admin)

**Funcionalidades:**
- 📊 Dashboard con stats vivas + botones de control (start/stop/restart)
- 📡 Señales abiertas + historial + **pegar señal manual**
- 📜 Logs en vivo (streaming SSE, filtros, búsqueda, descarga)
- 👑 Gestión de VIPs (dar/revocar, ver expiración)
- 📱 **WhatsApp CRUD completo** — añadir destinatarios con filtros (pares, eventos, probabilidad mínima, quiet hours) + test send
- ⚙️ Editor visual del `.env` con grupos lógicos + backup automático

**Credenciales:** `admin` / `buysell365` (cambiar antes de exponer el VPS — ver MIGRATION_PLAN.md).

Ver detalles técnicos en `app/web_admin/README.md`.

## Qué cambió respecto al plan original

| | Plan original (Windows + MT5) | Plan actual (Linux + sin MT5) ✅ |
|---|---|---|
| OS del VPS | Windows Server 2022 | Linux Debian 12 / Ubuntu 22.04 |
| Coste mensual | €25-28 (Windows + licencia + MT5) | **€5-15** (Hetzner / Hostinger / OVH Linux) |
| MT5 | Instalado + cuenta XM logueada | **No se usa.** Reemplazado por `price_feed.py` (yfinance + Binance) |
| Auto-ejecución | Sí (estaba ya OFF con MT5_EXECUTION_DISABLED=1) | No — tú ejecutas manual desde el móvil |
| Auto-arranque | Tarea Programada Windows | systemd service |
| Acceso | RDP (lento, GUI) | SSH (rápido, terminal) |
| Stack | Windows + Python + MT5 | Linux + Python (sin MT5) |

## Estructura final del paquete (consolidada)

```
BuySell365_VPS_Migration/    (287 MB total)
│
├── 📄 Documentación
│   ├── README.md                      ← Estás aquí
│   ├── REFACTOR_NO_MT5.md             ← Qué cambió en el código (sin MT5)
│   ├── MIGRATION_PLAN.md              ← Pasos paso a paso para subir al VPS
│   └── CHECKLIST.md                   ← Lista operativa día del switch
│
├── 📁 app/  (75 elementos en raíz — ya consolidado)
│   │
│   ├── 33 archivos .py productivos ← código vivo (publishers, notifiers, IA, monitoring)
│   │   ├── launcher.py · bot.py · signal_copier.py · btc_eth_generator.py
│   │   ├── price_feed.py 🆕 (reemplaza MT5) · monitor_real.py 🆕 (stub)
│   │   ├── 6 publishers (daily/weekly/monthly/promo/transparency/signals_promo)
│   │   ├── whatsapp_notifier.py · instagram_poster.py
│   │   ├── llm_features.py · signal_probability.py · finbert_module.py · news_image.py
│   │   ├── heartbeat.py · health_check.py · monitor_audusd.py
│   │   ├── schemas.py · i18n.py · outbox.py · state_lock.py · stats_normalizer.py
│   │   └── sync_web_now.py · web_sync.py · pandas_ta.py · cot_module.py · events_log.py
│   │
│   ├── 28 archivos .json (estado leído directo por el código)
│   ├── 2 *.session + ig_session.json + whatsapp_recipients.json
│   ├── 5 assets críticos (bull_bear.png, og_image.png, ssl_cert/key.pem, historial_trades.csv)
│   ├── Config (.env, .env.template, .env.bak_pre_nomt5, requirements.txt + .bak, .gitignore)
│   │
│   ├── 📁 Carpetas productivas (8)
│   │   ├── state/         (25 JSONs snapshot)
│   │   ├── sessions/      (backup sesiones)
│   │   ├── web/           (dashboard Render)
│   │   ├── static/        (assets web)
│   │   ├── templates/     (HTML)
│   │   ├── docs/          (docs internas)
│   │   ├── ig_images/     (248 MB — histórico IG)
│   │   ├── xm_images/     (imágenes XM)
│   │   └── logs/          (vacío — nace fresco en VPS)
│   │
│   ├── 📁 Carpetas de archivo (3) — preservadas, recuperables
│   │   ├── _archive_mt5_only/   (4 archivos: código original con MT5)
│   │   ├── _archive_scripts/    (27 archivos: backtests, one-shots, generators)
│   │   └── _archive_windows/    (12 archivos: .bat .ps1 .vbs Windows-only)
│   │
│   └── 📁 assets/  (30 archivos organizados en 7 subcarpetas)
│       ├── docs/              (2 PDFs: manual, gold track record)
│       ├── presentations/     (5 PPTX)
│       ├── pine_scripts/      (6 .pine TradingView)
│       ├── mockups/           (6 imágenes + 1 HTML mockup)
│       ├── marketing/         (3 textos marketing)
│       ├── channel_stats/     (4 stats canales aliados)
│       └── env_templates/     (4 plantillas .env legacy + cifradas)
│
└── 📁 scripts/  (8 scripts: 6 Linux + 1 PowerShell + 1 carpeta fallback)
    ├── VPS_SETUP.sh                ← 🆕 Master installer Linux
    ├── 01_install_python.sh        ← Python 3.11 + paquetes sistema
    ├── 02_install_deps.sh          ← venv + pip install
    ├── 03_setup_systemd.sh         ← systemd service (auto-arranque)
    ├── 04_test_smoke.sh            ← Health check Linux
    ├── restore_state.sh            ← Aplicar snapshot fresco en VPS
    ├── snap_state_now.ps1          ← Para ejecutar en TU PC Windows día del switch
    └── _fallback_windows/          ← 8 scripts viejos Windows+MT5 (rollback)
```

> **Estado tras consolidación:** de 157 elementos sueltos en `app/` se redujo a 75 (52% menos). Cero archivos productivos eliminados — todo lo movido vive en `_archive_*` o `assets/` y se puede recuperar.

## Orden de lectura

1. **REFACTOR_NO_MT5.md** — entiende qué cambió en el código (15 min)
2. **MIGRATION_PLAN.md** — pasos detallados para el VPS Linux (15 min)
3. **CHECKLIST.md** — abrir el día del switch

## Resumen del flujo

```
Día -7    Contratas VPS Linux (recomendado: Hetzner CX32 €7,55/mes)
Día -3    Subes este paquete al VPS por scp/rsync/SFTP
Día -2    Ejecutas VPS_SETUP.sh → instala Python + deps + systemd
Día -1    Configuras .env, smoke test, verificas servicio
Día  0    Switch: paras PC → snap_state_now.ps1 → subes → restore_state.sh → systemctl start
Día +1    Validación 24h
Día +7    Apagas PC local (o lo dejas como cold backup)
```

## VPS Linux recomendados (precio real, mayo 2026)

| Proveedor | Plan | Specs | Precio | Notas |
|---|---|---|---|---|
| **Hetzner CX32** ⭐ | CX32 | 4 vCPU / 8 GB / 80 GB SSD | **€7,55/mes** | Alemán, EUR, mejor relación precio/perf |
| **Hetzner CX22** | CX22 | 2 vCPU / 4 GB / 40 GB | €4,15/mes | Lo mínimo viable |
| **Hostinger KVM 2** | KVM 2 | 2 vCPU / 8 GB / 100 GB NVMe | ~€7-9/mes | Soporte español 24/7 |
| **OVH VPS-2 Linux** | VPS-2 | 6 vCPU / 12 GB / 100 GB NVMe | €10,27/mes | Más caro pero overprovisioned |
| **Oracle Cloud Free** | ARM A1 | 4 vCPU / 24 GB / 200 GB | **€0** | Always free (registro complejo) |

## Notas críticas

- **Secretos:** El `.env` real con tus claves está en `app/.env`. NO subir a Git ni compartir.
- **Snapshot de estado:** El `app/state/` está congelado al momento de generar este paquete. El día del switch, re-ejecutar `snap_state_now.ps1` para regenerarlo.
- **Render dashboard NO se toca** — sigue funcionando solo, recibirá datos del VPS Linux igual que ahora los recibe del PC Windows.
- **Tu cuenta MT5 sigue intacta** en tu PC. El refactor no la borra, solo desconecta el bot de ella. Sigues operando manual desde el móvil.
- **Si quieres revertir a Windows+MT5:** los originales están en `app/_archive_mt5_only/` y los scripts en `scripts/_fallback_windows/`. Se reconstruye en <1h.

## Costes recurrentes esperados (todo cloud)

| Servicio | Antes | Después |
|---|---|---|
| Render (dashboard) | $0-7/mes | sin cambios |
| Anthropic API | ~$16/mes | sin cambios |
| TextMeBot (WhatsApp) | ~$60/año | sin cambios |
| Tu PC (electricidad 24/7) | ~€10-20/mes | **€0** (apagado) |
| **VPS Linux** | — | **€5-15/mes** |
| **TOTAL ahorro vs Windows+MT5** | — | **~€200-250/año** |

## Soporte

- Dudas sobre el refactor: ver `REFACTOR_NO_MT5.md`
- Dudas sobre el día del switch: ver `MIGRATION_PLAN.md` + `CHECKLIST.md`
- Troubleshooting en VPS: la última sección de `MIGRATION_PLAN.md`

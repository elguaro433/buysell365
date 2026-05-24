# Plan REFACTOR: Eliminar MT5 + Migrar a Linux

> **Resultado tras el refactor:** el bot deja de usar MT5 completamente. Solo publica señales al VIP + WhatsApp + IG. El usuario ejecuta manual desde el móvil. El bot vive en VPS Linux barato (€5-10/mes).
>
> **NO se toca la carpeta original** `C:\Users\hpint\Desktop\BuySell365_Bot\`. Todo el trabajo pasa en `C:\Users\hpint\Desktop\BuySell365_VPS_Migration\app\`.

---

## TL;DR

| Métrica | Antes | Después |
|---|---|---|
| Archivos a tocar | 10 importan MT5 | 6 modificados + 4 eliminados |
| Líneas a cambiar | ~150 | (la mayoría son borrados) |
| Esfuerzo estimado | — | **4-8 horas concentradas** |
| Stack runtime | Windows + MT5 + Python | Linux + Python |
| Coste mensual VPS | €25-28 | **€5-10** |
| Estabilidad | Alta (excepto MT5) | Muy alta |

---

## Estado actual descubierto en el audit

Tu bot **ya tiene gran parte del refactor hecho de facto**:

1. **`MT5_EXECUTION_DISABLED=1` activo en .env línea 107** → todas las `mt5.order_send()` hacen skip silencioso (línea 9224 signal_copier.py)
2. **`yfinance` ya importado y usado** en bot.py, backtests, scalper — el price feed alternativo ya existe
3. **Generator ya tiene flag `GENERATOR_MT5_EXECUTE=true`** pero está override por `MT5_EXECUTION_DISABLED`
4. **`COPIER_MT5_ENABLED=true` y `AUTO_TRADING=True`** son flags zombi — los lee el código pero el kill-switch global los anula

**Implicación:** el refactor consiste sobre todo en **borrar código muerto** (paths MT5 ya inactivos), no en reescribir lógica nueva.

---

## Archivos afectados (mapeo completo)

### 🟥 ELIMINAR enteros (4 archivos)

| Archivo | Razón | Pérdida funcional |
|---|---|---|
| `monitor_real.py` | Solo lee posiciones MT5 cada 30s para `mt5_realtime.json` | Ninguna — dashboard se alimenta de señales publicadas |
| `run_mt5_sync.py` | Script CLI manual para sync deals MT5 → Render | Ninguna — el historial se reconstruye desde señales |
| `sync_mt5_to_render.py` | Worker que copia historial MT5 a web | Reemplazar con `web_sync.py` que ya existe y NO usa MT5 |
| `preview_video_opcion_a.py` | Demo de video, no producción | Ninguna |

### 🟧 REFACTORIZAR (6 archivos)

| Archivo | Cambios |
|---|---|
| **`signal_copier.py`** (12K líneas) | Quitar `import MetaTrader5`. Borrar todos los bloques `if MT5_EXECUTION_ENABLED:`. Reemplazar `mt5.symbol_info_tick()` por wrapper `get_current_price(symbol)` que usa yfinance. Borrar `mt5.account_info()`, `mt5.positions_get()`, `mt5.history_deals_get()` calls (devuelven listas vacías o None — el código ya tiene branches para "MT5 unavailable"). |
| **`btc_eth_generator.py`** | Quitar import MT5. Reemplazar `mt5.symbol_info_tick()` por **Binance API REST** (gratis, sin key, sub-segundo) para BTC/ETH. |
| **`bot.py`** (22K líneas) | Quitar import MT5. Borrar comandos `/mt5_status`, `/positions` que dependan de positions_get. Mantener todo lo demás. |
| **`launcher.py`** | Quitar import MT5. Quitar arranque de monitor_real.py. Quitar checks de salud MT5 (terminal64 process check). |
| **`signal_probability.py`** | Quitar `mt5.copy_rates_*()`. Reemplazar por yfinance `download()` (ya usado en backtests — patrón conocido). |
| **`instagram_poster.py`** | Quitar import MT5 (uso mínimo, mencionado en audit). |

### 🟩 NO TOCAR (todo lo demás)

- ✅ Publishers (daily, weekly, monthly, promo, transparency, signals_promo)
- ✅ WhatsApp notifier
- ✅ Todo `ai/` (llm_features, signal_probability, finbert, news_image)
- ✅ Telethon (signal_copier parte de leer canales aliados — solo cambia la parte de ejecución MT5)
- ✅ Web dashboard (Render — ya es Linux, ya funciona)
- ✅ Heartbeat, health_check, outbox, state_lock
- ✅ Backtests (los mantenemos por valor futuro — usan yfinance, no MT5)

---

## Nuevo módulo a crear: `price_feed.py`

Wrapper único que abstrae de dónde viene cada precio:

```python
# price_feed.py — fachada única para datos de mercado
def get_tick(symbol: str) -> dict:
    """Devuelve {bid, ask, last, time} — sustituto de mt5.symbol_info_tick()"""
    # BTC/ETH → Binance API
    # Forex (EURUSD, GBPUSD, USDJPY...) → yfinance
    # Oro / XAUUSD → yfinance (GC=F) o Twelvedata
    # Indices (NAS100, US30) → yfinance
    ...

def get_ohlc(symbol: str, timeframe: str, bars: int) -> pd.DataFrame:
    """Devuelve OHLC histórico — sustituto de mt5.copy_rates_*()"""
    # Solo se usa en signal_probability.py — yfinance lo cubre
    ...
```

**Tamaño estimado del módulo:** ~150 líneas. Una sola tarde de trabajo.

---

## Cambios en archivos de configuración

### `requirements.txt`
**Quitar:**
```
metatrader5==5.0.5640; sys_platform == "win32"
```

**Mantener todo lo demás** (las dependencias Windows-only restantes como `pandas`, `matplotlib`, etc. funcionan idéntico en Linux — solo cambia el filter `; sys_platform == "win32"` que se quita).

### `.env`
**Quitar / comentar:**
```
MT5_PATH=...
MT5_LOGIN=...
MT5_PASSWORD=...
MT5_SERVER=...
AUTO_TRADING=...
COPIER_MT5_ENABLED=...
COPIER_GUARDS=...
COPIER_MAX_POSITIONS=...
RISK_PER_TRADE_PCT=...
AUTO_BREAKEVEN_ENABLED=...
AUTO_HALF_CLOSE_PCT=...
GENERATOR_MT5_EXECUTE=...
GENERATOR_LOT_SIZE=...
MT5_EXECUTION_DISABLED=...  ← ya no hace falta el override, está eliminada la ejecución
```

**Mantener todas las demás** (Telegram, Anthropic, WhatsApp, Render, etc.).

### State files
**Eliminar:**
- `mt5_realtime.json` — ya no se genera ni se lee
- `mt5_trades_sync.json` — ya no se genera
- `mt5_circuit_breaker.json` — circuit breaker desaparece

**Mantener** los 22 JSONs restantes (operaciones publicadas, historial real, stats, sesiones, etc.).

---

## Plan de ejecución por pasos

### Paso 1: Crear `price_feed.py` (1-2h)
Wrapper único. Tests aislados de cada función. No toca el resto.

### Paso 2: Refactorizar `signal_copier.py` (2-3h)
1. Borrar `import MetaTrader5 as mt5`
2. Buscar y borrar bloques `if MT5_EXECUTION_ENABLED:` ... `else:` → mantener solo la rama "no ejecutar"
3. Reemplazar todos los `mt5.symbol_info_tick(sym)` por `price_feed.get_tick(sym)`
4. Borrar bloques de detección TP/SL basados en MT5 → reemplazar por polling de price_feed cada 30s (función `monitor_signals_by_price()`)
5. Borrar usos de `positions_get` (devolver `[]`), `account_info` (devolver None), `history_deals_get` (devolver `[]`)
6. Test: arrancar en local en modo DRY_RUN — ver que parsea, evalúa y publica una señal de prueba

### Paso 3: Refactorizar `btc_eth_generator.py` (1h)
1. Reemplazar `mt5.symbol_info_tick()` por llamadas a Binance API REST
2. Borrar `import MetaTrader5`
3. Test: generar una señal de prueba en local

### Paso 4: Limpiar `bot.py`, `launcher.py`, `signal_probability.py`, `instagram_poster.py` (1h)
1. Quitar imports MT5
2. Borrar comandos / funciones MT5-dependientes
3. Reemplazar `mt5.copy_rates_*` en signal_probability por yfinance.download (1-shot)

### Paso 5: Eliminar archivos MT5-only (15min)
1. Borrar `monitor_real.py`, `run_mt5_sync.py`, `sync_mt5_to_render.py`, `preview_video_opcion_a.py`
2. Quitar referencias a estos archivos en `launcher.py`

### Paso 6: Limpiar `.env` y `requirements.txt` (15min)
1. Comentar variables MT5_* en .env
2. Quitar `metatrader5` de requirements.txt

### Paso 7: Smoke test completo en local (1h)
1. Arrancar `launcher.py` en local (en `BuySell365_VPS_Migration/app/`)
2. Verificar: parser funciona, publica al canal de prueba, WhatsApp envía, IG poster intenta postear
3. Logs sin ERROR
4. Si todo OK → listo para VPS

### Paso 8: Generar nuevos scripts Linux para VPS (1h)
Reemplazar los `.ps1` actuales por equivalentes `.sh`:
- `install_python.sh` (apt install python3.11)
- `install_deps.sh` (pip install)
- `setup_systemd.sh` (systemd service para auto-arranque)
- `smoke_test.sh`
- `restore_state.sh`

### Paso 9: Actualizar documentación (30min)
- README.md, MIGRATION_PLAN.md, CHECKLIST.md → camino Linux + sin MT5
- Mover `.ps1` viejos a `scripts/_fallback_windows/` por si acaso

---

## Riesgos identificados y mitigaciones

| Riesgo | Probabilidad | Mitigación |
|---|---|---|
| yfinance da rate-limit con polling frecuente de TP/SL | Media | Cache 30s + fallback a Twelvedata (ya tienes key) |
| Latencia detección TP/SL: yfinance 60-120s vs MT5 tiempo real | Alta | Aceptable — el VIP recibe celebración con 1-2 min de retraso, no afecta usuario que ya ejecutó manual |
| Símbolos exóticos no en yfinance (algunos índices XM) | Media | Twelvedata cubre la mayoría — para algún caso raro, omitir esa señal con log |
| Stats históricas dependen de mt5_realtime.json | Baja | Reconstruir stats desde `historial_real.json` (que se nutre de señales publicadas, no MT5) |
| Bug oculto en signal_copier al borrar branches MT5 | Media | Test en local antes de VPS. Si algo falla en VPS, rollback = volver a usar carpeta original |
| Generator BTC/ETH cambia de comportamiento al usar Binance | Baja | Binance da prices más realistas para crypto que XM en realidad. Mejora probable. |

---

## Lo que GANAS

- ✅ **VPS de €5-10/mes** en lugar de €25-28 (Hetzner CX22/CX32 o Hostinger KVM 2 con Linux)
- ✅ **Sin licencia Windows** (€10-15/mes ahorrados)
- ✅ **Sin terminal64.exe** colgándose, sin RDP lento
- ✅ **Stack más simple** (menos cosas que mantener)
- ✅ **Bot más resistente** (sin depender de XM, sin desconexiones)
- ✅ **Backtests siguen funcionando** (ya usan yfinance)
- ✅ **Render dashboard sin cambios** (sigue tal cual)
- ✅ **Si algún día quieres volver a MT5:** el módulo `price_feed.py` puede tener un backend MT5 también — refactor reversible

## Lo que PIERDES

- ❌ Auto-ejecución MT5 — pero ya está DESACTIVADA con `MT5_EXECUTION_DISABLED=1`, así que no pierdes nada en práctica
- ❌ Equity/balance real visible en stats — ahora se calcula desde señales publicadas (mismo número, distinto origen)
- ❌ Detección TP/SL en tiempo real (segundos) — pasa a ser 1-2 min (yfinance polling)
- ❌ Auto-breakeven, half-close, full-close — pero ya NO se ejecutaban porque MT5 estaba off

---

## VPS Linux recomendados (precio real)

| Proveedor | Plan | Specs | Precio | Notas |
|---|---|---|---|---|
| **Hetzner CX22** | CX22 | 2 vCPU / 4 GB / 40 GB | **€4,15/mes** | Sobra para tu stack |
| **Hetzner CX32** ⭐ | CX32 | 4 vCPU / 8 GB / 80 GB | **€7,55/mes** | Mi recomendación — margen cómodo |
| **Hostinger KVM 2** | KVM 2 | 2 vCPU / 8 GB / 100 GB NVMe | ~€7-9/mes | Soporte español 24/7 |
| **DigitalOcean Basic** | s-2vcpu-2gb | 2 vCPU / 2 GB / 60 GB | ~€11/mes | Snapshots fáciles |
| **Vultr Cloud Compute** | 2c2g | 2 vCPU / 2 GB / 55 GB | ~€11/mes | Pago por hora |
| **Oracle Cloud Always Free** | ARM Ampere A1 | 4 vCPU / 24 GB / 200 GB | **€0** | Gratis para siempre, registro complejo |

**Sweet spot:** **Hetzner CX32 (€7,55/mes)** o **Hostinger KVM 2 (€7-9/mes)**. Ambos europeos, ambos sobrados para tu carga real (~400 MB RAM).

---

## Decisión que necesito de ti antes de empezar

1. ✋ **¿Apruebas el plan?** Si es sí, empiezo por crear `price_feed.py` (paso 1).
2. ✋ **¿Prefieres que vaya paso a paso pidiendo OK entre cada uno, o en bloque (pasos 1-7) y te enseño al final?**
3. ✋ **¿Qué hago con los archivos `_fallback_windows/`?** Los mantengo por si algún día rehacéis Windows, o los borro completamente?

Cuando confirmes, arranco con el paso 1.

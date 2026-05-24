"""
monitor_real.py — STUB (sistema sin MT5)

El monitor_real.py original (archivado en _archive_mt5_only/) leía posiciones MT5
cada 30s y escribía mt5_realtime.json. Sin MT5, no hay nada que monitorear.

Este stub mantiene contento al watchdog de bot.py (que vigila que monitor_real.py
esté vivo) — simplemente arranca y duerme indefinidamente.

Si algún día reactivas MT5, restaura el original desde _archive_mt5_only/.
"""
from __future__ import annotations
import os
import sys
import time
import json
import logging
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [monitor_real-stub] %(levelname)s %(message)s",
)
log = logging.getLogger("monitor_real")

# Crear un mt5_realtime.json vacío para que bot.py no falle al leerlo
_STATE_FILE = Path(__file__).parent / "mt5_realtime.json"
_EMPTY_STATE = {
    "posiciones_abiertas": [],
    "historial_hoy": [],
    "stats_hoy": {"pips": 0, "ops": 0, "wins": 0, "losses": 0, "wr_pct": 0},
    "stats_historico": {"pips": 0, "ops": 0, "wins": 0, "losses": 0, "wr_pct": 0},
    "equity": 0.0,
    "balance": 0.0,
    "last_update": int(time.time()),
    "stub": True,
    "note": "Sistema sin MT5 — datos reales no disponibles",
}


def _write_stub_state() -> None:
    try:
        _STATE_FILE.write_text(json.dumps(_EMPTY_STATE, indent=2), encoding="utf-8")
    except Exception as e:
        log.warning(f"No se pudo escribir mt5_realtime.json stub: {e}")


def main() -> None:
    log.info("STUB monitor_real iniciado (sistema sin MT5 — sin datos reales que mostrar)")
    log.info(f"PID={os.getpid()} — durmiendo indefinidamente.")
    _write_stub_state()
    try:
        while True:
            # Refresca timestamp del state cada hora para que bot.py no piense que está stale
            time.sleep(3600)
            _EMPTY_STATE["last_update"] = int(time.time())
            _write_stub_state()
    except KeyboardInterrupt:
        log.info("STUB monitor_real terminado por usuario")
        sys.exit(0)


if __name__ == "__main__":
    main()

"""
BuySell365 Pro — events_log.py
Append-only event journal para auditoria, debugging y reconstruccion de estado.

DISENO:
- Un solo archivo JSONL: logs/events.jsonl
- Cada linea es un evento self-contained: {ts, type, source, data}
- Append-only (nunca se modifica, nunca se sobreescribe)
- Thread-safe (lock interno)
- Sin dependencias externas
- Coste despreciable (1 fwrite + flush por evento)

EVENTOS A REGISTRAR (criticos):
- signal.parsed       — parser detecto senal nueva
- signal.published    — publicada al canal VIP (msg_id)
- signal.tp_hit       — TP alcanzado y celebrado
- signal.sl_hit       — SL alcanzado y notificado
- signal.cancelled    — descartada (con razon)
- signal.expired      — auto-expirada por timeout
- signal.autofix      — auto-correccion aplicada (SL invertido, etc)
- mt5.order_send      — orden enviada a MT5
- mt5.order_close     — orden cerrada en MT5
- bot.start, bot.stop — ciclo de vida bot
- copier.start, copier.stop, copier.crash
- llm.call            — call a Anthropic (feature, tokens, cost)
- llm.error           — error API
- vip.subscribed, vip.expired
- admin.command       — comando admin ejecutado
- channel.delete      — mensaje borrado del canal

USO:
    from events_log import log_event
    log_event("signal.published", source="copier",
              data={"pair": "XAUUSD", "direction": "BUY", "msg_id": 4626})

QUERY (lectura):
    from events_log import read_events
    eventos = read_events(since=time.time()-86400, types=["signal.tp_hit","signal.sl_hit"])
"""
import os
import json
import time
import threading
from pathlib import Path
from datetime import datetime
from typing import Optional, Iterable, List, Dict, Any

BASE_DIR = Path(__file__).parent
EVENTS_FILE = BASE_DIR / "logs" / "events.jsonl"
EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)

_lock = threading.Lock()


def log_event(event_type: str, *, source: str = "", data: Optional[Dict[str, Any]] = None) -> bool:
    """Escribe un evento al journal append-only.

    Args:
        event_type: tipo del evento (ej. "signal.published", "mt5.order_send")
        source: proceso/modulo que origina (ej. "copier", "bot", "manual")
        data: dict con datos del evento (serializable JSON)

    Returns:
        True si se escribio OK, False si fallo (pero NO lanza — eventos no
        deben romper el flujo critico).
    """
    try:
        evt = {
            "ts": time.time(),
            "iso": datetime.now().isoformat(timespec="seconds"),
            "type": event_type,
            "source": source or "",
            "data": data or {},
        }
        line = json.dumps(evt, ensure_ascii=False, default=str) + "\n"
        with _lock:
            # Append + flush + fsync para garantizar disco (evento critico no debe perderse)
            with open(EVENTS_FILE, "a", encoding="utf-8") as f:
                f.write(line)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except Exception:
                    pass  # Algunos FS no soportan fsync
        return True
    except Exception:
        # NUNCA propagar excepciones de logging — el flujo critico NO depende del journal
        return False


def read_events(
    since: Optional[float] = None,
    until: Optional[float] = None,
    types: Optional[Iterable[str]] = None,
    source: Optional[str] = None,
    limit: int = 0,
) -> List[Dict[str, Any]]:
    """Lee eventos del journal con filtros.

    Args:
        since: timestamp Unix — solo eventos >= since
        until: timestamp Unix — solo eventos <= until
        types: lista de tipos a incluir (ej. ["signal.tp_hit"]). None = todos.
        source: filtrar por source. None = todos.
        limit: max eventos a retornar (0 = sin limite).

    Returns:
        Lista de dicts (en orden cronologico, mas viejos primero).
    """
    if not EVENTS_FILE.exists():
        return []
    types_set = set(types) if types else None
    out = []
    try:
        with open(EVENTS_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    evt = json.loads(line)
                except Exception:
                    continue
                ts = evt.get("ts", 0)
                if since is not None and ts < since:
                    continue
                if until is not None and ts > until:
                    continue
                if types_set and evt.get("type") not in types_set:
                    continue
                if source and evt.get("source") != source:
                    continue
                out.append(evt)
                if limit and len(out) >= limit:
                    break
    except Exception:
        pass
    return out


def summary_today() -> Dict[str, Any]:
    """Resumen rapido de eventos de hoy — util para auditor LLM o admin DM.
    Retorna conteos por tipo y lista de errores recientes."""
    _now = time.time()
    _start = _now - 86400  # 24h
    eventos = read_events(since=_start)
    counts: Dict[str, int] = {}
    errors = []
    for e in eventos:
        t = e.get("type", "?")
        counts[t] = counts.get(t, 0) + 1
        if "error" in t or e.get("data", {}).get("error"):
            errors.append({"iso": e.get("iso"), "type": t, "data": e.get("data")})
    return {
        "total_events": len(eventos),
        "by_type": dict(sorted(counts.items(), key=lambda x: -x[1])),
        "recent_errors": errors[-20:],
    }


def rotate_if_large(max_mb: int = 50) -> bool:
    """Rota el archivo si supera max_mb. Renombra a events_YYYYMMDD_HHMMSS.jsonl
    y abre uno nuevo. Llamar periodicamente (ej. desde monitor o cron)."""
    try:
        if not EVENTS_FILE.exists():
            return False
        size_mb = EVENTS_FILE.stat().st_size / (1024 * 1024)
        if size_mb < max_mb:
            return False
        rotated = EVENTS_FILE.with_name(
            f"events_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
        )
        with _lock:
            os.replace(EVENTS_FILE, rotated)
        log_event("events.rotated", source="self",
                  data={"old": str(rotated), "size_mb": round(size_mb, 1)})
        return True
    except Exception:
        return False


if __name__ == "__main__":
    # Smoke test rapido
    log_event("test.smoke", source="cli", data={"ok": True})
    print("Eventos hoy:")
    print(json.dumps(summary_today(), ensure_ascii=False, indent=2))

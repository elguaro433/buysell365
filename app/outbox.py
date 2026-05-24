"""
BuySell365 Pro — outbox.py
Cola persistente para mensajes criticos al canal VIP / grupo / admin.

PROBLEMA QUE RESUELVE:
Cuando el bot scanner detecta SL/TP HIT y delega notificacion al copier
(porque fuente=Manual), si el copier muere antes de publicar, la notificacion
SE PIERDE para siempre. Lo mismo si Telegram API esta caida temporalmente.

SOLUCION:
Outbox persistente JSONL: pendientes guardan en disco. Worker reintenta cada
30s con backoff. Solo se elimina cuando confirma 200 OK de Telegram.

USO:
    from outbox import enqueue, run_worker
    # En el detector de SL/TP HIT:
    enqueue("channel_vip", text="🛑 STOP LOSS — ORO\\n-13 pips", priority=1)
    # Worker se ejecuta como thread daemon en bot.py
    threading.Thread(target=run_worker, daemon=True).start()

PRIORIDADES:
- 1: critico (SL/TP HIT — el VIP DEBE verlo)
- 2: importante (publicidad, daily summary)
- 3: best-effort (auto-promo, bot status)

Worker procesa en orden de prioridad (1 primero).
"""
import os
import json
import time
import threading
import requests
import uuid
from pathlib import Path
from typing import Optional, Dict, Any
from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

OUTBOX_FILE = BASE_DIR / "outbox.jsonl"
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))
GROUP_ID = os.getenv("GROUP_ID", "@BUYSELL_365_24_7").strip()
ADMIN_ID = int(os.getenv("ADMIN_USER_ID", "0") or os.getenv("USER_ID_1", "0"))

_lock = threading.Lock()
_worker_running = False

DEST_MAP = {
    "channel_vip": CHANNEL_ID,
    "group": GROUP_ID,
    "admin": ADMIN_ID,
}


def enqueue(destination: str, text: str = "", *, photo_url: str = "",
            reply_to: Optional[int] = None, priority: int = 2,
            tag: str = "") -> str:
    """Encola un mensaje al outbox. Retorna msg_id interno (UUID).

    Args:
        destination: "channel_vip", "group", "admin"
        text: contenido (si es texto)
        photo_url: URL de foto (si es foto con caption=text)
        reply_to: msg_id padre para reply
        priority: 1 (critico) | 2 (normal) | 3 (best-effort)
        tag: identificador opcional para dedup (ej. "tp_hit_XAUUSD_4604")
    """
    if destination not in DEST_MAP:
        raise ValueError(f"destination desconocida: {destination}")
    msg = {
        "uuid": str(uuid.uuid4()),
        "ts_enqueued": time.time(),
        "destination": destination,
        "text": text,
        "photo_url": photo_url,
        "reply_to": reply_to,
        "priority": priority,
        "tag": tag,
        "attempts": 0,
        "next_retry": time.time(),
    }
    line = json.dumps(msg, ensure_ascii=False) + "\n"
    with _lock:
        with open(OUTBOX_FILE, "a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
            try:
                os.fsync(f.fileno())
            except Exception:
                pass
    return msg["uuid"]


def _read_pending() -> list:
    """Lee todos los pendientes del archivo."""
    if not OUTBOX_FILE.exists():
        return []
    out = []
    try:
        with open(OUTBOX_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
    except Exception:
        pass
    return out


def _rewrite(messages: list) -> None:
    """Reescribe el archivo con la lista actual (atomico via tmp+rename)."""
    tmp = str(OUTBOX_FILE) + ".tmp"
    with _lock:
        with open(tmp, "w", encoding="utf-8") as f:
            for m in messages:
                f.write(json.dumps(m, ensure_ascii=False) + "\n")
            f.flush()
            try:
                os.fsync(f.fileno())
            except Exception:
                pass
        os.replace(tmp, OUTBOX_FILE)


def _send_one(msg: Dict[str, Any]) -> bool:
    """Intenta enviar un mensaje. Retorna True si OK, False si fallo."""
    if not TELEGRAM_TOKEN:
        return False
    chat_id = DEST_MAP.get(msg["destination"])
    if not chat_id:
        return False
    try:
        if msg.get("photo_url"):
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
            payload = {"chat_id": chat_id, "photo": msg["photo_url"], "caption": msg.get("text", "")}
        else:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            payload = {"chat_id": chat_id, "text": msg.get("text", "")}
        if msg.get("reply_to"):
            payload["reply_to_message_id"] = msg["reply_to"]
        resp = requests.post(url, json=payload, timeout=10)
        return resp.status_code == 200 and resp.json().get("ok", False)
    except Exception:
        return False


def process_once() -> Dict[str, int]:
    """Procesa la cola una vez. Retorna {sent, failed, retained}."""
    pending = _read_pending()
    if not pending:
        return {"sent": 0, "failed": 0, "retained": 0}
    # Orden por prioridad (1 primero) y luego por timestamp
    pending.sort(key=lambda m: (m.get("priority", 2), m.get("ts_enqueued", 0)))
    _now = time.time()
    sent, failed, retained = 0, 0, 0
    survivors = []
    for m in pending:
        if m.get("next_retry", 0) > _now:
            survivors.append(m)
            retained += 1
            continue
        ok = _send_one(m)
        if ok:
            sent += 1
            try:
                from events_log import log_event as _log_event
                _log_event("outbox.sent", source="outbox", data={
                    "destination": m["destination"], "tag": m.get("tag"),
                    "attempts": m.get("attempts", 0) + 1,
                })
            except Exception:
                pass
        else:
            m["attempts"] = m.get("attempts", 0) + 1
            failed += 1
            # Backoff exponencial: 30s, 60s, 120s, 240s, 480s, max 600s
            _delay = min(600, 30 * (2 ** min(m["attempts"], 4)))
            m["next_retry"] = _now + _delay
            # Drop tras 20 attempts (>10h)
            if m["attempts"] >= 20:
                try:
                    from events_log import log_event as _log_event
                    _log_event("outbox.dropped", source="outbox", data={
                        "destination": m["destination"], "tag": m.get("tag"),
                        "text_preview": (m.get("text") or "")[:80],
                        "priority": m.get("priority", 2),
                    })
                except Exception:
                    pass
                # FIX 2026-05-06 (Capa 5): alertar al admin cuando se descarta un
                # mensaje, especialmente prioridad=1 (SL/TP críticos del canal VIP).
                # Antes se perdía silenciosamente tras 20 reintentos sin que
                # nadie supiera que había ocurrido.
                try:
                    if TELEGRAM_TOKEN and ADMIN_ID:
                        _prio = m.get("priority", 2)
                        _emoji_drop = "🚨" if _prio == 1 else "⚠️"
                        _alert_text = (
                            f"{_emoji_drop} *OUTBOX DROP* (prio={_prio})\n\n"
                            f"Destino: `{m['destination']}` tag: `{m.get('tag','-')}`\n"
                            f"Tras 20 intentos sin éxito (>10h). Mensaje descartado.\n\n"
                            f"_Preview:_\n```\n{(m.get('text') or '')[:200]}\n```"
                        )
                        requests.post(
                            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                            json={"chat_id": ADMIN_ID, "text": _alert_text, "parse_mode": "Markdown"},
                            timeout=5,
                        )
                except Exception:
                    pass
                continue  # No survivor — drop
            survivors.append(m)
    _rewrite(survivors)
    return {"sent": sent, "failed": failed, "retained": retained}


def run_worker(interval_sec: int = 30) -> None:
    """Worker loop — corre indefinidamente. Llamar en thread daemon desde bot.py."""
    global _worker_running
    if _worker_running:
        return
    _worker_running = True
    try:
        from events_log import log_event as _log_event
        _log_event("outbox.worker_start", source="outbox")
    except Exception:
        pass
    while _worker_running:
        try:
            stats = process_once()
            if stats["sent"] > 0 or stats["failed"] > 0:
                # Solo log si hubo actividad
                pass
        except Exception:
            pass
        time.sleep(interval_sec)


def stop_worker() -> None:
    global _worker_running
    _worker_running = False


def stats() -> Dict[str, Any]:
    pending = _read_pending()
    return {
        "pending_count": len(pending),
        "by_priority": {p: sum(1 for m in pending if m.get("priority") == p) for p in [1, 2, 3]},
        "oldest_age_sec": int(time.time() - min((m["ts_enqueued"] for m in pending), default=time.time())) if pending else 0,
    }


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "process":
        print(process_once())
    elif len(sys.argv) > 1 and sys.argv[1] == "stats":
        print(json.dumps(stats(), indent=2))
    else:
        print("Uso: python outbox.py [process|stats]")
        print("Pendientes:", stats())

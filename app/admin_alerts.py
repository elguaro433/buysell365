"""
admin_alerts.py — Aviso por Telegram al admin cuando algo falla en silencio.

Añadido 2026-09-19 tras tres fallos que duraron semanas sin que nadie lo viera
(crédito Anthropic agotado ~1 mes, TextMeBot desconectado 10 días, feed de precios
vacío). El bot los escribía en el log y seguía; el admin no mira el log a diario.

Uso:
    from admin_alerts import alert_admin
    alert_admin("llm_credit", "Anthropic sin crédito — parser LLM y Vision caídos")

- `key` identifica el tipo de fallo. Se envía como máximo 1 aviso por `key` cada
  `cooldown_s` (24 h por defecto) aunque el fallo se repita 100 veces.
- Estado en `.admin_alerts_state.json` junto a este archivo, así el dedupe sobrevive
  a restarts y es compartido entre bot.py y signal_copier.py.
- Best-effort: nunca lanza excepción ni bloquea (timeout 5 s).
- Destino: USER_ID_1 (+ USER_ID_2 si existe) con TELEGRAM_TOKEN. Nunca a canales.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

_STATE = Path(__file__).resolve().parent / ".admin_alerts_state.json"
_LOCK = threading.Lock()


def _admin_ids() -> list[str]:
    ids = []
    for var in ("USER_ID_1", "USER_ID_2", "ADMIN_ID"):
        v = (os.getenv(var) or "").strip()
        if v and v.isdigit() and v not in ids:
            ids.append(v)
    return ids


def _load() -> dict:
    try:
        return json.loads(_STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(state: dict) -> None:
    try:
        tmp = _STATE.with_suffix(".tmp")
        tmp.write_text(json.dumps(state), encoding="utf-8")
        os.replace(tmp, _STATE)
    except Exception:
        pass


def alert_admin(key: str, text: str, cooldown_s: int = 24 * 3600) -> bool:
    """Envía `text` al admin si no se envió ya un aviso con la misma `key` en
    las últimas `cooldown_s`. Devuelve True si se envió."""
    try:
        token = (os.getenv("TELEGRAM_TOKEN") or os.getenv("BOT_TOKEN") or "").strip()
        ids = _admin_ids()
        if not token or not ids:
            return False
        now = time.time()
        with _LOCK:
            state = _load()
            last = float(state.get(key, 0) or 0)
            if now - last < cooldown_s:
                return False
            state[key] = now
            _save(state)
        import requests
        msg = f"🚨 BuySell365 — {text}\n\n(aviso `{key}`, máx. 1 cada {cooldown_s // 3600} h)"
        for cid in ids:
            try:
                requests.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": cid, "text": msg, "parse_mode": "Markdown"},
                    timeout=5,
                )
            except Exception:
                pass
        return True
    except Exception:
        return False


def clear_alert(key: str) -> None:
    """Olvida el último aviso de `key` (p. ej. cuando el servicio se recupera),
    para que el próximo fallo avise de inmediato."""
    try:
        with _LOCK:
            state = _load()
            if key in state:
                del state[key]
                _save(state)
    except Exception:
        pass

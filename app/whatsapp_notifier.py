"""
WhatsApp Notifier — señales ORO a multiples destinatarios via WhatsApp.

Backend principal: TextMeBot (https://textmebot.com) — envia desde el numero
del usuario vinculado por QR (+34 674 97 65 72 BuySell365 Pro). Sin footer ad.
Backend secundario: CallMeBot — soportado en codigo pero ELIMINADO en producccion
2026-05-09 porque el plan Free inyecta "I need your support https://callmebot.com/..."
en cada mensaje, arruinando aspecto profesional.

Activos: GOLD / XAUUSD / XAUUSD=X (cualquier variante con "XAU").
Eventos: Senal nueva · Movimiento de SL (BE) · Cierre final (TP final, full close, SL hit).

DESTINATARIOS — whatsapp_recipients.json (obligatorio):
  - name: identificacion legible (no se envia, solo logs)
  - phone: con prefijo internacional (+34xxx, +376xxx)
  - apikey: API key del backend correspondiente
  - backend: "textmebot" (default recomendado) o "callmebot"
  - filter_pair: "GOLD" → solo ORO, null/"ALL" → todos los pares
  - enabled: true/false

CONFIGURAR NUEVO DESTINATARIO (TextMeBot, sin ads):
  Como TextMeBot envia desde TU numero (+34 BuySell365 Pro), el destinatario
  no necesita registrar nada. Solo añade su entrada al JSON con la APIKEY tuya.
  Ejemplo: {"name":"Esposa", "phone":"+34xxxxxxxxx", "apikey":"ReJEFMj44U7Q",
            "backend":"textmebot", "filter_pair":"GOLD", "enabled":true}

Rate limit: 6s entre envios (TextMeBot/CallMeBot exigen >=5s para evitar ban WhatsApp).
Failsafe: si el backend falla o tarda, NO bloquea el flujo Telegram/MT5/IG.
Activacion: env var WHATSAPP_PERSONAL_ENABLED=true. Por defecto OFF.

Norma del usuario (memoria feedback_no_enviar_sin_permiso.md):
no enviar sin autorizacion explicita → arranca apagado por diseno.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from urllib.parse import quote

import requests

log = logging.getLogger("whatsapp")

_DEDUP_WINDOW = 300  # 5 min — el mismo evento no se reenvia
_recent_lock = threading.Lock()
_recent: dict[str, float] = {}

# Rate limit GLOBAL entre cualquier par de envios sucesivos (cross-call).
# TextMeBot exige >=5s entre mensajes desde la misma APIKEY o silencia el envio.
# Subido 2026-05-10 de 6s a 7s tras detectar fallos 403 con gap de exactamente 5s
# (regla TextMeBot ">5s" estricta + latencia HTTP de 2-3s descuadran nuestro reloj
# vs el de TextMeBot). 7s da 2s de margen real desde la perspectiva de TextMeBot.
_GLOBAL_SEND_GAP = 7.0  # seg (margen sobre el limite duro de 5s de TextMeBot)
_global_send_lock = threading.Lock()
_last_global_send: float = 0.0

# Flag-file de pausa runtime (toggle desde bot.py via boton/comando Telegram).
# Si existe → notifier silenciado, sin necesidad de reiniciar el copier.
_FLAG_PAUSED = os.path.join(os.path.dirname(os.path.abspath(__file__)), "whatsapp_paused.flag")
_RECIPIENTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "whatsapp_recipients.json")
_recipients_cache: list = []
_recipients_mtime: float = 0.0


def is_runtime_paused() -> bool:
    return os.path.exists(_FLAG_PAUSED)


def _enabled() -> bool:
    if is_runtime_paused():
        return False
    return os.getenv("WHATSAPP_PERSONAL_ENABLED", "false").lower() in ("true", "1", "yes")


def _dedup(key: str) -> bool:
    """True = ya se envio recientemente, debe SKIPearse."""
    now = time.time()
    with _recent_lock:
        last = _recent.get(key, 0)
        if last and (now - last) < _DEDUP_WINDOW:
            return True
        _recent[key] = now
        if len(_recent) > 50:
            for k in sorted(_recent, key=lambda k: _recent[k])[:25]:
                _recent.pop(k, None)
    return False


def _is_gold(pair: str) -> bool:
    if not pair:
        return False
    p = pair.upper()
    return p in ("GOLD", "XAUUSD", "XAUUSD=X") or "XAU" in p


def _pair_category(pair: str) -> str:
    """Categoriza un par. Devuelve: GOLD | CRYPTO | INDICES | FOREX | OTHER."""
    if not pair:
        return "OTHER"
    p = pair.upper().replace(" ", "").replace("/", "").replace("=X", "")
    if "XAU" in p or p == "GOLD":
        return "GOLD"
    if "BTC" in p or "ETH" in p or p in ("BTCUSD", "ETHUSD"):
        return "CRYPTO"
    if p in ("NAS100", "NAS100CASH", "US100", "SPX500", "US500", "US500CASH",
             "US30", "DJ30", "GER40", "UK100", "JP225", "AUS200", "FRA40", "ESP35"):
        return "INDICES"
    # Forex: 6 letras alfabeticas (EURUSD, GBPJPY, etc.)
    if len(p) == 6 and p.isalpha():
        return "FOREX"
    return "OTHER"


# Eventos por defecto al crear destinatario nuevo (los 3 criticos, sin spam de TPs intermedios)
DEFAULT_EVENTS = ["new_signal", "be_activated", "position_closed"]
# Todos los eventos posibles
ALL_EVENTS = ["new_signal", "be_activated", "tp_hit", "partial_close", "position_closed"]


def _load_recipients() -> list:
    """Carga la lista de destinatarios desde whatsapp_recipients.json.
    Cachea con mtime — si el archivo cambia, recarga sin reiniciar el bot.
    Si el archivo no existe o falla parseo, retorna [] (sin fallback CallMeBot,
    eliminado 2026-05-09 por inyectar footer publicitario)."""
    global _recipients_cache, _recipients_mtime
    if os.path.exists(_RECIPIENTS_FILE):
        try:
            mt = os.path.getmtime(_RECIPIENTS_FILE)
            if mt != _recipients_mtime:
                with open(_RECIPIENTS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    _recipients_cache = [r for r in data if isinstance(r, dict)]
                    _recipients_mtime = mt
                    log.info(f"[WSP] {len(_recipients_cache)} destinatarios cargados")
            return _recipients_cache
        except Exception as e:
            log.warning(f"[WSP] error leyendo {_RECIPIENTS_FILE}: {e}")
    return []


def _matches_filter(recipient: dict, pair: str) -> bool:
    """True si el destinatario debe recibir esta senal segun su filter_pair.
    Soporta multi-filtro coma-separado ('GOLD,CRYPTO') y aliases por categoria.
    Aliases: ALL, GOLD, CRYPTO, FOREX, INDICES, BTC, ETH. Tambien acepta simbolo exacto."""
    fp = (recipient.get("filter_pair") or "").upper().strip()
    if not fp or fp == "ALL":
        return True
    filters = [f.strip() for f in fp.split(",") if f.strip()]
    if "ALL" in filters:
        return True
    cat = _pair_category(pair)
    pair_upper = (pair or "").upper().strip().replace("/", "").replace("=X", "")
    for f in filters:
        if f in ("GOLD", "CRYPTO", "FOREX", "INDICES") and cat == f:
            return True
        if f == "BTC" and "BTC" in pair_upper:
            return True
        if f == "ETH" and "ETH" in pair_upper:
            return True
        if f == pair_upper:
            return True
    return False


def _event_authorized(recipient: dict, event: str) -> bool:
    """True si el destinatario quiere recibir este tipo de evento.
    Si el campo 'events' no existe, usa DEFAULT_EVENTS (compatibilidad)."""
    if not event:
        return True  # sin event especificado -> permitir (compat con notify_test)
    events = recipient.get("events")
    if events is None:
        events = DEFAULT_EVENTS
    return event in events


def _probability_authorized(recipient: dict, probability) -> bool:
    """True si el destinatario debe recibir esta senal segun min_probability.
    Reglas (FIX 2026-05-11):
      - Si el destinatario no define 'min_probability' (o es 0/null) -> sin filtro -> True.
      - Si la senal NO trae probability (None) -> True (no podemos filtrar a ciegas).
        El usuario decide si quiere strict mode anadiendo 'strict_probability': true.
      - Si probability < min_probability -> False (skip)."""
    try:
        min_p = recipient.get("min_probability")
        if min_p is None or float(min_p) <= 0:
            return True
        if probability is None:
            return not bool(recipient.get("strict_probability", False))
        return float(probability) >= float(min_p)
    except (TypeError, ValueError):
        return True


def _send_one(recipient: dict, text: str) -> None:
    """Envia mensaje a UN destinatario via CallMeBot o TextMeBot.
    Backend se selecciona con campo 'backend' del recipient:
      - 'callmebot' (default) — envia desde +34 644 51 71 75 (numero CallMeBot)
      - 'textmebot' — envia desde el numero del usuario (vinculado por QR)"""
    name = recipient.get("name", "?")
    phone = (recipient.get("phone") or "").strip()
    apikey = (recipient.get("apikey") or "").strip()
    backend = (recipient.get("backend") or "callmebot").strip().lower()
    if not phone or not apikey:
        log.warning(f"[WSP] {name}: phone/apikey vacios — skip")
        return
    # Rate limit GLOBAL: esperar si fue <_GLOBAL_SEND_GAP desde el ultimo envio.
    # Fix 2026-05-10: el _last_global_send se actualiza DESPUES del HTTP request,
    # no antes — antes daba falsos OK porque la HTTP tarda 2-3s y nuestro contador
    # pensaba que el gap era mayor del que TextMeBot mide desde su lado.
    global _last_global_send
    with _global_send_lock:
        gap = time.time() - _last_global_send
        if gap < _GLOBAL_SEND_GAP:
            wait = _GLOBAL_SEND_GAP - gap
            log.debug(f"[WSP] global rate limit: esperando {wait:.1f}s antes de enviar a {name}")
            time.sleep(wait)
    try:
        if backend == "textmebot":
            url = (
                "https://api.textmebot.com/send.php"
                f"?recipient={quote(phone)}"
                f"&apikey={quote(apikey)}"
                f"&text={quote(text)}"
            )
        else:  # callmebot (default)
            url = (
                "https://api.callmebot.com/whatsapp.php"
                f"?phone={quote(phone)}"
                f"&text={quote(text)}"
                f"&apikey={quote(apikey)}"
            )
        resp = requests.get(url, timeout=10)
        # Set _last_global_send AFTER HTTP completes — asi reflejamos cuando
        # TextMeBot realmente vio el mensaje (no cuando empezamos a enviar).
        _last_global_send = time.time()
        ok = resp.status_code == 200
        if ok and backend == "textmebot":
            # TextMeBot devuelve HTML con "Success!" en body cuando OK
            ok = "Success" in resp.text
        if ok:
            log.info(f"[WSP] {name} ({backend}) enviado ({len(text)}c)")
        else:
            log.warning(f"[WSP] {name} ({backend}) fallo status={resp.status_code} body={resp.text[:200]}")
    except Exception as e:
        # Si hubo excepcion sin completar HTTP, igual marcamos el timestamp
        # para no acumular intentos rapidos hacia TextMeBot que pueden bannear.
        _last_global_send = time.time()
        log.warning(f"[WSP] {name} ({backend}) error: {e}")


def _send(text: str, pair: str = "", event: str = "", probability=None) -> None:
    """Envia mensaje a TODOS los destinatarios habilitados que pasen filtros (par + evento + %).
    Best-effort, nunca raisea hacia el caller.
    Rate-limit: 6s entre envios sucesivos (TextMeBot/CallMeBot exigen >=5s para evitar ban WhatsApp)."""
    if not _enabled():
        return
    recipients = _load_recipients()
    if not recipients:
        log.warning("[WSP] sin destinatarios configurados")
        return
    sent = 0
    skipped_prob = 0
    for r in recipients:
        if not r.get("enabled", True):
            continue
        if pair and not _matches_filter(r, pair):
            continue
        if event and not _event_authorized(r, event):
            continue
        if not _probability_authorized(r, probability):
            skipped_prob += 1
            continue
        if sent > 0:
            time.sleep(6)
        _send_one(r, text)
        sent += 1
    if sent == 0:
        log.debug(f"[WSP] ningun destinatario coincide para pair={pair} event={event} prob={probability}")
    if skipped_prob:
        log.info(f"[WSP] {skipped_prob} destinatario(s) saltado(s) por min_probability (prob={probability})")


def _async_send(text: str, pair: str = "", event: str = "", probability=None) -> None:
    """Dispara el envio en thread aparte para no bloquear el flujo."""
    if not _enabled():
        return
    try:
        threading.Thread(target=_send, args=(text, pair, event, probability), daemon=True).start()
    except Exception as e:
        log.warning(f"[WSP] thread error: {e}")


def _fmt(v) -> str:
    """Formato sin separador de miles (regla canal VIP del usuario)."""
    try:
        return f"{float(v):.2f}"
    except Exception:
        return str(v)


def _hhmm() -> str:
    from datetime import datetime
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Europe/Andorra")).strftime("%H:%M")
    except Exception:
        return datetime.now().strftime("%H:%M")


# === EVENTOS PUBLICOS ===

def notify_raw(text: str, pair: str = "", event: str = "new_signal", probability=None) -> None:
    """Envía el texto exacto del canal VIP a WhatsApp (sin reconstruir).
    Garantiza que el suscriptor recibe el mensaje idéntico al canal.
    probability: score 0-100 del análisis pre-publicación (signal_probability).
    Los destinatarios con min_probability filtran sobre este valor."""
    if not _enabled():
        return
    if _dedup(f"raw_{event}_{pair}_{hash(text) % 100000}"):
        return
    _async_send(text, pair=pair, event=event, probability=probability)


def notify_new_signal(pair: str, direction: str, entry: float, sl: float,
                      tps: list[float] | None = None) -> None:
    """Senal nueva publicada en el canal VIP."""
    if not _enabled():
        return
    direction = (direction or "").upper()
    if _dedup(f"new_{direction}_{pair}_{int(entry or 0)}"):
        return
    pair_label = pair.upper() if pair else "?"
    if _is_gold(pair):
        pair_label = "ORO"
    lines = [f"{pair_label} - {direction}", ""]
    if entry and entry > 0:
        lines.append(f"Entry: {_fmt(entry)}")
    else:
        lines.append("Entry: Market")
    tps = [t for t in (tps or []) if t and t > 0]
    for i, tp in enumerate(tps, start=1):
        lines.append(f"TP{i}:   {_fmt(tp)}")
    if sl and sl > 0:
        lines.append(f"SL:    {_fmt(sl)}")
    lines.append("")
    lines.append(_hhmm())
    _async_send("\n".join(lines), pair=pair, event="new_signal")


def notify_sl_moved(pair: str, sl_old: float, sl_new: float, kind: str = "BE") -> None:
    """SL movido (BE activado / trailing). kind: 'BE' o texto libre corto."""
    if not _enabled():
        return
    if _dedup(f"slmove_{kind}_{pair}_{int(sl_new or 0)}"):
        return
    pair_label = "ORO" if _is_gold(pair) else (pair.upper() if pair else "?")
    lines = [f"{pair_label} - {kind} ACTIVADO", ""]
    if sl_old and sl_new:
        lines.append(f"SL movido: {_fmt(sl_old)} -> {_fmt(sl_new)}")
    elif sl_new:
        lines.append(f"SL movido a: {_fmt(sl_new)}")
    lines.append("Posicion protegida")
    lines.append("")
    lines.append(_hhmm())
    _async_send("\n".join(lines), pair=pair, event="be_activated")


def notify_tp_hit(pair: str, tp_level: int = 0, pips: float = 0) -> None:
    """TP intermedio alcanzado (la posicion sigue corriendo en otros TPs)."""
    if not _enabled():
        return
    _lvl = f"TP{tp_level}" if tp_level and tp_level > 0 else "TP"
    if _dedup(f"tphit_{_lvl}_{pair}_{int(pips or 0)}"):
        return
    pair_label = "ORO" if _is_gold(pair) else (pair.upper() if pair else "?")
    lines = [f"{pair_label} - {_lvl} ALCANZADO", ""]
    if pips:
        signo = "+" if pips >= 0 else ""
        lines.append(f"Resultado: {signo}{pips:.0f} pips")
    lines.append("Posicion sigue corriendo")
    lines.append("")
    lines.append(_hhmm())
    _async_send("\n".join(lines), pair=pair, event="tp_hit")


def notify_partial_close(pair: str, action: str, pips: float = 0) -> None:
    """Cierre parcial (50% o porcion). action: 'close_half' o 'close_partial'."""
    if not _enabled():
        return
    if _dedup(f"partial_{action}_{pair}_{int(pips or 0)}"):
        return
    pair_label = "ORO" if _is_gold(pair) else (pair.upper() if pair else "?")
    label = "CIERRE PARCIAL 50%" if action == "close_half" else "CIERRE PARCIAL"
    lines = [f"{pair_label} - {label}", ""]
    if pips:
        signo = "+" if pips >= 0 else ""
        lines.append(f"Asegurado: {signo}{pips:.0f} pips")
    lines.append("Posicion sigue corriendo")
    lines.append("")
    lines.append(_hhmm())
    _async_send("\n".join(lines), pair=pair, event="partial_close")


def notify_test(text: str = "Test BuySell365 Pro - integracion OK") -> int:
    """Envia mensaje de prueba a TODOS los destinatarios habilitados (bypassa filtro GOLD).
    Util para validar conectividad multi-backend (CallMeBot + TextMeBot) sin esperar senal real.
    Retorna numero de destinatarios contactados."""
    if not _enabled():
        log.warning("[WSP] notify_test: notifier deshabilitado (WHATSAPP_PERSONAL_ENABLED no es true o pausado)")
        return 0
    recipients = _load_recipients()
    if not recipients:
        log.warning("[WSP] notify_test: sin destinatarios")
        return 0
    sent = 0
    for r in recipients:
        if not r.get("enabled", True):
            continue
        if sent > 0:
            time.sleep(6)
        _send_one(r, text)
        sent += 1
    log.info(f"[WSP] notify_test: {sent} destinatarios contactados")
    return sent


def notify_position_closed(pair: str, direction: str, entry: float,
                           exit_price: float, pips: float, reason: str = "CIERRE") -> None:
    """Cierre final de la posicion (TP final, full close manual, SL hit).
    reason: 'TP', 'SL', 'CIERRE' (etiqueta corta opcional)."""
    if not _enabled():
        return
    direction = (direction or "").upper()
    if _dedup(f"close_{direction}_{pair}_{int(entry or 0)}_{reason}"):
        return
    pair_label = "ORO" if _is_gold(pair) else (pair.upper() if pair else "?")
    lines = [f"{pair_label} - POSICION CERRADA", ""]
    if direction:
        lines.append(f"{direction} @ {_fmt(entry)}" if entry else direction)
    if exit_price and exit_price > 0:
        lines.append(f"Cierre @ {_fmt(exit_price)}")
    if pips is not None:
        signo = "+" if pips >= 0 else ""
        lines.append(f"Resultado: {signo}{pips:.0f} pips")
    if reason and reason not in ("CIERRE", ""):
        lines.append(f"Motivo: {reason}")
    lines.append("")
    lines.append(_hhmm())
    _async_send("\n".join(lines), pair=pair, event="position_closed")

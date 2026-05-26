"""
BuySell365 Pro — Web Application (Render deployment)
Separated from the bot for independent hosting.
The bot pushes data via POST /api/sync.
TradingView webhooks are received and queued for the bot to pick up.
"""
import os
import json
import time
import hmac
import threading
import logging
from datetime import datetime, timedelta
from flask import Flask, jsonify, request, redirect, send_from_directory, send_file, make_response
# FIX 2026-04-28: escape de strings dinamicos para prevenir XSS en landing.
# Antes: pair_display y otros campos del JSON se inyectaban directamente en HTML
# con f-strings sin escape. Si un trade tenia caracteres maliciosos en pair_display,
# se ejecutaba como HTML/JS.
from markupsafe import escape as _e

# ============================================================
#  LOGGING
# ============================================================
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("BuySell365Web")

# ============================================================
#  FLASK APP
# ============================================================
app = Flask(__name__)

# ============================================================
#  CONFIGURATION
# ============================================================
# FIX 2026-04-26: secrets requeridos — sin fallback hardcoded.
# Antes: SYNC_SECRET tenia fallback "buysell365_sync_2026" → si la env var
# fallaba en Render, el secret quedaba expuesto en GitHub publico y el
# endpoint /api/sync aceptaba cualquier request con ese secret conocido.
SYNC_SECRET = os.getenv("SYNC_SECRET", "").strip()
if not SYNC_SECRET:
    raise RuntimeError(
        "SYNC_SECRET env var es OBLIGATORIO. Configurarlo en Render Dashboard "
        "antes de arrancar la web."
    )
TV_SECRET = os.getenv("TV_SECRET", "").strip()
API_SECRET_KEY = os.getenv("API_SECRET_KEY", "").strip()
LOGS_PASSWORD = os.getenv("LOGS_PASSWORD", "").strip()
VIP_PRECIO_EUR = int(os.getenv("VIP_PRECIO_EUR", "149"))
VIP_MONEDA = os.getenv("VIP_MONEDA", "\u20ac")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ============================================================
#  IN-MEMORY DATA STORE (updated by bot via /api/sync)
# ============================================================
_lock = threading.RLock()

# Cargar historial real si existe
_historial_real = []
try:
    import json as _json
    _hr_path = os.path.join(os.path.dirname(__file__), "historial_real.json")
    if os.path.exists(_hr_path):
        with open(_hr_path, "r") as _f:
            _historial_real = _json.load(_f)
except Exception:
    pass

# FIX 2026-04-23: Historial del canal VIP (trades publicados por signal_copier)
# Se sincroniza desde el bot local via POST /api/sync con key 'copier_trades'.
# Reemplaza a _historial_real (MT5 personal) como fuente del dashboard público.
# FIX 2026-04-26: persistir a disco para sobrevivir reinicios del proceso
# (gunicorn workers, restarts sin redeploy). Render filesystem es efimero entre
# redeploys completos pero estable mientras el contenedor este vivo.
_COPIER_TRADES_CACHE = os.path.join(os.path.dirname(__file__), "copier_trades_cache.json")
_copier_trades: list = []
try:
    if os.path.exists(_COPIER_TRADES_CACHE):
        with open(_COPIER_TRADES_CACHE, "r", encoding="utf-8") as _f:
            _ct = _json.load(_f)
            if isinstance(_ct, list):
                _copier_trades = _ct
except Exception:
    pass

# Bot state — pushed by the bot every ~30 seconds
_store = {
    "operaciones_activas": {},
    "historial_operaciones": _historial_real,
    "estadisticas_diarias": {"ganadas": 0, "perdidas": 0, "pips_ganados": 0.0, "pips_perdidos": 0.0, "senales_hoy": 0},
    "winning_trades": [],
    "bot_active": False,
    "auto_trading": True,
    "ultimo_sync": 0,
    "assets_count": 20,
    "capital_usuario": 1000.0,
    "mt5_status": "DESCONECTADO",
    "active_ops_detail": [],  # Pre-computed by bot for /api/active_ops
}

# Pending webhooks queue for the bot to pick up
_pending_signals = []
_signals_lock = threading.Lock()

# ============================================================
#  VISITOR TRACKING — Built-in analytics
# ============================================================
_VISITORS_FILE = os.path.join(BASE_DIR, "visitors_data.json")
_visitors_lock = threading.Lock()
_visitors = {
    "total_visits": 0,
    "unique_ips": {},      # ip -> {country, city, last_visit, visits, user_agent}
    "daily_visits": {},    # "2026-03-17" -> count
    "page_views": {},      # "/dashboard" -> count
    "countries": {},       # "ES" -> count
    "recent_visitors": [], # últimos 50 visitantes
}

def _load_visitors():
    global _visitors
    try:
        if os.path.exists(_VISITORS_FILE):
            with open(_VISITORS_FILE, "r", encoding="utf-8") as f:
                _visitors.update(json.load(f))
    except Exception:
        pass

def _save_visitors():
    try:
        with _visitors_lock:
            with open(_VISITORS_FILE, "w", encoding="utf-8") as f:
                json.dump(_visitors, f, ensure_ascii=False)
    except Exception:
        pass

_load_visitors()

def _is_bot_ua(ua):
    """Detecta si el User-Agent es un bot/crawler (no humano)."""
    _bots = ["bot", "crawl", "spider", "slurp", "curl", "wget", "python-requests",
             "httpclient", "fetcher", "scanner", "monitoring", "uptime", "pingdom",
             "telegrambot", "twitterbot", "facebookexternalhit", "whatsapp", "preview"]
    ua_lower = ua.lower()
    return any(b in ua_lower for b in _bots)

# Cache de geolocalización por IP (evita llamadas repetidas)
_geo_cache = {}
_GEO_CACHE_MAX = 5000  # Límite para evitar memory leak

def _geolocate_ip(ip):
    """Obtiene país y ciudad de una IP usando ip-api.com (gratis, 45 req/min)."""
    if ip in _geo_cache:
        return _geo_cache[ip]
    # Evitar crecimiento ilimitado del cache
    if len(_geo_cache) >= _GEO_CACHE_MAX:
        # Eliminar las primeras 1000 entradas (las más antiguas)
        for old_key in list(_geo_cache.keys())[:1000]:
            del _geo_cache[old_key]
    try:
        import requests as _req
        resp = _req.get(f"http://ip-api.com/json/{ip}?fields=status,country,countryCode,city,regionName",
                        timeout=3)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("status") == "success":
                result = {
                    "country": data.get("country", "Desconocido"),
                    "country_code": data.get("countryCode", "??"),
                    "city": data.get("city", ""),
                    "region": data.get("regionName", ""),
                }
                _geo_cache[ip] = result
                return result
    except Exception:
        pass
    result = {"country": "Desconocido", "country_code": "??", "city": "", "region": ""}
    _geo_cache[ip] = result
    return result

def _track_visitor():
    """Track page visit — solo humanos reales, con geolocalización."""
    try:
        ip = request.headers.get("X-Forwarded-For", request.remote_addr or "unknown")
        if "," in ip:
            ip = ip.split(",")[0].strip()
        page = request.path
        today = datetime.utcnow().strftime("%Y-%m-%d")
        now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        ua = request.headers.get("User-Agent", "")[:150]
        lang = request.headers.get("Accept-Language", "")[:30]

        # ── FILTRAR BOTS: no contar crawlers ni bots de preview ──
        is_bot = _is_bot_ua(ua)

        # Geolocalización async (no bloquear la respuesta)
        geo = {"country": "...", "country_code": "??", "city": "", "region": ""}
        if ip in _geo_cache:
            geo = _geo_cache[ip]

        with _visitors_lock:
            # Contadores totales (incluye todo)
            _visitors["total_visits"] = _visitors.get("total_visits", 0) + 1

            # Contadores HUMANOS (excluye bots)
            _visitors.setdefault("human_visits", 0)
            _visitors.setdefault("human_daily", {})
            _visitors.setdefault("bot_visits", 0)
            if not is_bot:
                _visitors["human_visits"] += 1
                _visitors["human_daily"][today] = _visitors["human_daily"].get(today, 0) + 1
            else:
                _visitors["bot_visits"] += 1

            # Daily visits (total)
            _visitors.setdefault("daily_visits", {})
            _visitors["daily_visits"][today] = _visitors["daily_visits"].get(today, 0) + 1

            # Page views
            _visitors.setdefault("page_views", {})
            _visitors["page_views"][page] = _visitors["page_views"].get(page, 0) + 1

            # Unique IPs
            # FIX 2026-04-26: LRU cap a 10000 IPs — antes el dict crecia sin
            # limite (memory leak: tras meses MB de bloat, _save_store lento).
            _visitors.setdefault("unique_ips", {})
            _MAX_UNIQUE_IPS = 10000
            if ip not in _visitors["unique_ips"]:
                # Si supera el cap, evictar la IP con last_visit mas antigua
                if len(_visitors["unique_ips"]) >= _MAX_UNIQUE_IPS:
                    try:
                        _oldest_ip = min(
                            _visitors["unique_ips"].items(),
                            key=lambda kv: kv[1].get("last_visit", kv[1].get("first_visit", ""))
                        )[0]
                        del _visitors["unique_ips"][_oldest_ip]
                    except Exception:
                        # Fallback: borrar la primera entrada por orden de insercion
                        _visitors["unique_ips"].pop(next(iter(_visitors["unique_ips"])), None)
                _visitors["unique_ips"][ip] = {
                    "first_visit": now_str, "visits": 0, "lang": lang, "ua": ua,
                    "is_bot": is_bot,
                    "country": geo.get("country", "..."),
                    "country_code": geo.get("country_code", "??"),
                    "city": geo.get("city", ""),
                }
            _visitors["unique_ips"][ip]["visits"] = _visitors["unique_ips"][ip].get("visits", 0) + 1
            _visitors["unique_ips"][ip]["last_visit"] = now_str

            # Países (solo humanos)
            _visitors.setdefault("countries", {})
            if not is_bot and geo.get("country_code", "??") != "??":
                cc = geo["country_code"]
                _visitors["countries"][cc] = _visitors["countries"].get(cc, 0) + 1

            # Recent visitors (últimos 50 — solo humanos)
            _visitors.setdefault("recent_visitors", [])
            entry = {
                "ip": ip[:12] + "***",
                "page": page,
                "time": now_str,
                "lang": lang[:10],
                "ua": ua[:80],
                "is_bot": is_bot,
                "country": geo.get("country", "..."),
                "country_code": geo.get("country_code", "??"),
                "city": geo.get("city", ""),
            }
            _visitors["recent_visitors"].insert(0, entry)
            _visitors["recent_visitors"] = _visitors["recent_visitors"][:50]

        # Save + geo lookup async
        def _async_save_and_geo():
            if ip not in _geo_cache:
                g = _geolocate_ip(ip)
                with _visitors_lock:
                    if ip in _visitors["unique_ips"]:
                        _visitors["unique_ips"][ip]["country"] = g.get("country", "")
                        _visitors["unique_ips"][ip]["country_code"] = g.get("country_code", "??")
                        _visitors["unique_ips"][ip]["city"] = g.get("city", "")
                    # Actualizar countries
                    if not is_bot and g.get("country_code", "??") != "??":
                        cc = g["country_code"]
                        _visitors["countries"][cc] = _visitors["countries"].get(cc, 0) + 1
                    # Actualizar el entry más reciente con geo real
                    for v in _visitors.get("recent_visitors", []):
                        if v.get("time") == now_str and v.get("ip") == ip[:12] + "***":
                            v["country"] = g.get("country", "")
                            v["country_code"] = g.get("country_code", "??")
                            v["city"] = g.get("city", "")
                            break
            _save_visitors()
        threading.Thread(target=_async_save_and_geo, daemon=True).start()
    except Exception:
        pass


# ============================================================
#  PERSISTENCE — Save/Load store to disk (survives restarts)
# ============================================================
_DATA_FILE = os.path.join(BASE_DIR, "web_data.json")

def _save_store():
    """Save current store to disk."""
    try:
        with _lock:
            data = {k: v for k, v in _store.items()}
        with open(_DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Error saving store: {e}")

def _load_store():
    """Load store from disk on startup."""
    global _store
    try:
        if os.path.exists(_DATA_FILE):
            with open(_DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            with _lock:
                _store.update(data)
            logger.info(f"Store loaded from disk ({len(data)} keys)")
    except Exception as e:
        logger.error(f"Error loading store: {e}")

_load_store()

# ============================================================
#  HELPER FUNCTIONS
# ============================================================
def _check_api_auth():
    """Check API key authentication.
    NOTA: El Referer se usa solo como conveniencia para peticiones del propio dashboard
    (navegador del mismo dominio). Para APIs externas siempre se exige API key.
    El Referer es fácil de falsificar, pero aquí solo protege endpoints de lectura
    del dashboard que ya son públicos visualmente.

    FIX 2026-04-26: si API_SECRET_KEY no esta configurada, RECHAZAR todo
    (antes retornaba True permitiendo acceso anonimo a endpoints sensibles).
    """
    if not API_SECRET_KEY:
        return False
    # Primero intentar API key (método seguro)
    key = request.args.get("key", "") or request.headers.get("X-API-Key", "")
    if key and hmac.compare_digest(str(key), str(API_SECRET_KEY)):
        return True
    # Fallback: permitir peticiones del propio sitio (solo GET del dashboard)
    if request.method == "GET":
        referer = request.headers.get("Referer", "")
        if referer:
            from urllib.parse import urlparse
            ref_host = urlparse(referer).hostname or ""
            req_host = request.host.split(":")[0] if request.host else ""
            if ref_host == req_host:
                return True
    return False

def _ahora():
    """Current time in CET timezone."""
    import pytz
    return datetime.now(pytz.timezone('Europe/Andorra'))

# ============================================================
#  SECURITY MIDDLEWARE
# ============================================================
_rate_limit_web = {}
_RATE_LIMIT_MAX = 30
_RATE_LIMIT_WINDOW = 60
_rate_limit_last_cleanup = 0

def _check_rate_limit(ip, max_req=_RATE_LIMIT_MAX, window=_RATE_LIMIT_WINDOW):
    global _rate_limit_last_cleanup
    now = time.time()
    # Limpieza periódica cada 5 minutos para evitar memory leak
    if now - _rate_limit_last_cleanup > 300:
        stale = [k for k, v in _rate_limit_web.items() if not v or (now - v[-1]) > window]
        for k in stale:
            del _rate_limit_web[k]
        _rate_limit_last_cleanup = now
    if ip not in _rate_limit_web:
        _rate_limit_web[ip] = []
    _rate_limit_web[ip] = [t for t in _rate_limit_web[ip] if now - t < window]
    if len(_rate_limit_web[ip]) >= max_req:
        return True
    _rate_limit_web[ip].append(now)
    return False

@app.before_request
def _redirect_http_to_https():
    # Skip redirect en local (localhost/127.0.0.1) para permitir desarrollo y previews
    _host = (request.host or "").split(":")[0]
    if _host in ("localhost", "127.0.0.1", "0.0.0.0"):
        return None
    if not request.is_secure and request.method == "GET" and request.headers.get("X-Forwarded-Proto", "http") != "https":
        if not request.path.startswith("/api/sync") and request.method != "POST":
            url = request.url.replace("http://", "https://", 1)
            return redirect(url, code=301)

@app.after_request
def _add_security_headers(response):
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'same-origin'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    if request.is_secure or request.headers.get("X-Forwarded-Proto") == "https":
        response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    # Cache headers for API responses
    if request.path in _DATA_API_PATHS or (request.path.startswith('/api/') and request.path not in ('/api/sync',)):
        if 'Cache-Control' not in response.headers:
            response.headers['Cache-Control'] = 'no-store'
    return response

_DATA_API_PATHS = {'/api/stats', '/api/winning_trades', '/api/all_trades', '/api/active_ops'}

@app.before_request
def _enforce_rate_limit():
    path = request.path
    if path.startswith('/logs') or path.startswith('/api/'):
        ip = request.remote_addr or request.headers.get("X-Forwarded-For", "unknown") or "unknown"
        if path.startswith('/logs'):
            if _check_rate_limit(f"logs_{ip}", max_req=10, window=60):
                return "Rate limit exceeded.", 429
        elif path in _DATA_API_PATHS:
            if _check_rate_limit(f"dataapi_{ip}", max_req=60, window=60):
                return "Rate limit exceeded.", 429
        elif not path.startswith('/api/sync'):
            if _check_rate_limit(f"api_{ip}", max_req=30, window=60):
                return "Rate limit exceeded.", 429

# ============================================================
#  SYNC API — Bot pushes data here
# ============================================================
@app.route("/api/sync", methods=["POST"])
def api_sync():
    """Receive state update from the bot."""
    secret = request.headers.get("X-Sync-Secret", "")
    if not hmac.compare_digest(secret, SYNC_SECRET):
        return jsonify({"error": "unauthorized"}), 401
    try:
        data = request.get_json(force=True) or {}
        with _lock:
            for key in ("operaciones_activas", "estadisticas_diarias",
                        "winning_trades", "bot_active", "auto_trading", "assets_count",
                        "mt5_status", "active_ops_detail"):
                if key in data:
                    _store[key] = data[key]
            # historial_real: trades reales MT5 — actualizar si el bot envía datos frescos
            if "historial_real" in data:
                global _historial_real
                incoming_real = data["historial_real"]
                if isinstance(incoming_real, list) and incoming_real:
                    _historial_real = incoming_real
            # FIX 2026-04-23: copier_trades — historial del canal VIP (signal_copier)
            # Reemplaza la lista completa en cada sync.
            if "copier_trades" in data:
                global _copier_trades
                incoming_copier = data["copier_trades"]
                if isinstance(incoming_copier, list):
                    _copier_trades = incoming_copier
                    # FIX 2026-04-26: persistir a disco para sobrevivir
                    # reinicios de gunicorn (Render filesystem estable entre
                    # restarts del proceso, efimero solo en redeploys).
                    try:
                        with open(_COPIER_TRADES_CACHE, "w", encoding="utf-8") as _fc:
                            _json.dump(_copier_trades, _fc)
                    except Exception:
                        pass
            # historial_operaciones: merge (dedupe by id or ticker+fecha+hora)
            if "historial_operaciones" in data:
                incoming = data["historial_operaciones"]
                if isinstance(incoming, list) and incoming:
                    existing = {
                        f"{h.get('ticker','')}{h.get('fecha','')}{h.get('hora','')}": h
                        for h in _store.get("historial_operaciones", [])
                    }
                    for h in incoming:
                        k = f"{h.get('ticker','')}{h.get('fecha','')}{h.get('hora','')}"
                        existing[k] = h
                    merged = sorted(existing.values(), key=lambda x: (x.get('fecha',''), x.get('hora','')))
                    _store["historial_operaciones"] = merged[-500:]  # keep last 500
            _store["ultimo_sync"] = time.time()
        _save_store()
        # Return any pending signals for the bot
        pending = []
        with _signals_lock:
            pending = list(_pending_signals)
            _pending_signals.clear()
        return jsonify({"status": "ok", "pending_signals": pending}), 200
    except Exception as e:
        logger.error(f"Sync error: {e}")
        return jsonify({"error": str(e)}), 500

# ============================================================
#  WEBHOOK — TradingView signals
# ============================================================
@app.route("/tv_signal", methods=["POST"])
@app.route("/webhook", methods=["POST"])
@app.route("/signal", methods=["POST"])
@app.route("/tradingview", methods=["POST"])
def route_tv_signal():
    """Receive TradingView webhook and queue for bot pickup."""
    try:
        raw_body = request.get_data(as_text=True)
        logger.info(f"Webhook received: {raw_body[:500]}")
        try:
            data = request.get_json(force=True) or {}
        except Exception:
            data = {}

        # Authenticate
        # FIX 2026-04-26: TV_SECRET es OBLIGATORIO. Antes: si TV_SECRET=""
        # cualquiera podia inyectar señales (`if TV_SECRET and not ...`).
        if not TV_SECRET:
            logger.error(f"TV webhook rechazado: TV_SECRET no configurado en env")
            return jsonify({"error": "server misconfigured"}), 503
        tv_secret_received = str(data.get("secret", data.get("passphrase", "")))
        if not hmac.compare_digest(tv_secret_received, TV_SECRET):
            logger.warning(f"Webhook unauthorized from {request.remote_addr}")
            return jsonify({"error": "unauthorized"}), 401

        # Queue signal for bot pickup
        signal_entry = {
            "data": data,
            "raw": raw_body[:2000],
            "timestamp": time.time(),
            "ip": request.headers.get("X-Forwarded-For", request.remote_addr),
        }
        with _signals_lock:
            _pending_signals.append(signal_entry)
            # Keep max 50 pending signals
            if len(_pending_signals) > 50:
                _pending_signals.pop(0)

        logger.info(f"Webhook queued ({len(_pending_signals)} pending)")
        return jsonify({"status": "received"}), 200
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return jsonify({"error": "server error"}), 500

# ============================================================
#  STATIC ASSETS
# ============================================================
@app.route("/img/<path:filename>")
def serve_img(filename):
    _allowed_ext = ('.png', '.jpg', '.jpeg', '.gif', '.ico', '.svg', '.webp')
    if not filename.lower().endswith(_allowed_ext):
        return "Not found", 404
    if '..' in filename or filename.startswith('.') or '/' in filename or '\\' in filename:
        return "Not found", 404
    try:
        return send_from_directory(BASE_DIR, filename, mimetype="image/png")
    except Exception:
        return "Not found", 404

@app.route("/static/<path:filename>")
def serve_static(filename):
    static_dir = os.path.join(BASE_DIR, "static")
    try:
        return send_from_directory(static_dir, filename)
    except Exception:
        return "Not found", 404

@app.route("/manifest.json")
def serve_manifest():
    manifest = {
        "name": "BuySell365 Pro",
        "short_name": "BuySell365",
        "description": "Se\u00f1ales de trading automatizadas con Inteligencia Artificial",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#080b0f",
        "theme_color": "#00d4aa",
        "orientation": "portrait-primary",
        "icons": [
            {"src": "/img/bull_bear.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
            {"src": "/img/bull_bear.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"}
        ]
    }
    resp = make_response(json.dumps(manifest, ensure_ascii=False))
    resp.headers['Content-Type'] = 'application/manifest+json'
    resp.headers['Cache-Control'] = 'public, max-age=86400'
    return resp

@app.route("/i18n/<lang>.json")
def serve_translations(lang):
    _allowed = ("es", "en", "pt", "fr")
    if lang not in _allowed:
        lang = "es"
    tr_path = os.path.join(BASE_DIR, "translations.json")
    try:
        with open(tr_path, "r", encoding="utf-8") as f:
            all_tr = json.load(f)
        data = all_tr.get(lang, all_tr.get("es", {}))
        return app.response_class(response=json.dumps(data, ensure_ascii=False), status=200, mimetype="application/json; charset=utf-8")
    except Exception:
        return "{}", 200, {"Content-Type": "application/json"}

# ============================================================
#  API ENDPOINTS
# ============================================================
@app.route("/api/stats")
def api_stats():
    if not _check_api_auth():
        return jsonify({"error": "unauthorized"}), 401
    try:
        with _lock:
            hist = _store.get("historial_operaciones", [])
            ops = _store.get("operaciones_activas", {})

        wins = sum(1 for h in hist if h.get('pips', 0) > 0)
        total = len(hist)
        wr = round(wins / total * 100, 1) if total > 0 else 0
        pips = round(sum(h.get('pips', 0) for h in hist), 1)
        losses = total - wins
        avg_win = round(sum(h.get('pips', 0) for h in hist if h.get('pips', 0) > 0) / max(wins, 1), 1)
        avg_loss = round(sum(abs(h.get('pips', 0)) for h in hist if h.get('pips', 0) <= 0) / max(losses, 1), 1)
        rr = round(avg_win / avg_loss, 2) if avg_loss > 0 else 0
        n_ops = sum(1 for op in ops.values() if isinstance(op, dict) and op.get('mt5_ejecutado', False))

        now = _ahora()
        hoy = now.strftime("%d/%m/%Y")
        hoy_total = sum(1 for h in hist if h.get('fecha', '') == hoy)
        hoy_wins = sum(1 for h in hist if h.get('fecha', '') == hoy and h.get('pips', 0) > 0)

        week_total = week_wins = month_total = month_wins = 0
        for h in hist:
            f = h.get('fecha', '')
            p = h.get('pips', 0)
            try:
                parts = f.split('/')
                if len(parts) == 3:
                    d, m, y = int(parts[0]), int(parts[1]), int(parts[2])
                    dt = datetime(y, m, d)
                    if dt.month == now.month and dt.year == now.year:
                        month_total += 1
                        if p > 0: month_wins += 1
                    if dt >= datetime(now.year, now.month, now.day) - timedelta(days=now.weekday()):
                        week_total += 1
                        if p > 0: week_wins += 1
            except Exception:
                pass

        week_wr = round(week_wins / week_total * 100, 1) if week_total > 0 else 0
        month_wr = round(month_wins / month_total * 100, 1) if month_total > 0 else 0

        last = []
        for h in reversed(hist[-20:]):
            last.append({
                "nombre": h.get("nombre", ""), "ticker": h.get("ticker", ""),
                "tipo": h.get("tipo", ""), "pips": round(h.get("pips", 0), 1),
                "resultado": "WIN" if h.get("pips", 0) > 0 else "LOSS",
                "fecha": h.get("fecha", "")
            })
            if len(last) >= 6:
                break

        is_bot_alive = (time.time() - _store.get("ultimo_sync", 0)) < 120

        return jsonify({
            "winrate": wr, "total_signals": total, "pips": pips,
            "active_ops": n_ops, "rr_ratio": rr, "wins": wins, "losses": losses,
            "today_signals": hoy_total, "today_wins": hoy_wins,
            "week_signals": week_total, "week_wins": week_wins, "week_wr": week_wr,
            "month_signals": month_total, "month_wins": month_wins, "month_wr": month_wr,
            "assets_count": _store.get("assets_count", 6),
            "bot_active": is_bot_alive, "auto_trading": _store.get("auto_trading", False),
            "last_signals": last
        })
    except Exception as e:
        logger.error(f"Stats error: {e}")
        return jsonify({"error": "server error"}), 500

# FIX 2026-03-19: Mostrar TODAS las operaciones (wins + losses) — transparencia total
@app.route("/api/winning_trades")  # Mantener ruta vieja por compatibilidad
@app.route("/api/all_trades")
def api_all_trades():
    try:
        with _lock:
            # Usar siempre historial_real (trades XM verificados con hora_salida completa).
            # Si el bot envía trades nuevos posteriores a la última fecha de historial_real,
            # los añadimos al final para mantener el dashboard actualizado.
            if _historial_real:
                real_tickets = {str(r.get("ticket_mt5")) for r in _historial_real if r.get("ticket_mt5")}
                last_real_fecha = max((r.get("fecha_apertura") or r.get("open_date") or r.get("fecha","") for r in _historial_real), default="")
                bot_trades = _store.get("historial_operaciones", [])
                # Solo añadir trades del bot que sean genuinamente nuevos (ticket no en historial_real)
                nuevos = [t for t in bot_trades if str(t.get("ticket_mt5","")) not in real_tickets and t.get("ticket_mt5")]
                trades = list(_historial_real) + nuevos
            else:
                trades = list(_store.get("historial_operaciones", []) or _store.get("winning_trades", []))
        return app.response_class(response=json.dumps(trades, ensure_ascii=False), status=200, mimetype="application/json")
    except Exception as e:
        logger.error(f"api_all_trades error: {e}")
        return "[]", 200, {"Content-Type": "application/json"}

@app.route("/api/active_ops")
def api_active_ops():
    # No auth required — public dashboard data fetched by frontend JS
    try:
        with _lock:
            ops = _store.get("active_ops_detail", [])
        return app.response_class(response=json.dumps(ops, ensure_ascii=False), status=200, mimetype="application/json")
    except Exception:
        return "[]", 200, {"Content-Type": "application/json"}

@app.route("/api/health")
def api_health():
    """Health check for monitoring."""
    last_sync = _store.get("ultimo_sync", 0)
    bot_alive = (time.time() - last_sync) < 120
    return jsonify({
        "web": "ok",
        "bot_connected": bot_alive,
        "last_sync_ago": int(time.time() - last_sync) if last_sync > 0 else -1,
        "pending_signals": len(_pending_signals),
    })


@app.route("/api/visitors")
def api_visitors():
    """Endpoint para la consola: estadísticas de visitantes."""
    if not _check_api_auth():
        return jsonify({"error": "Unauthorized"}), 401
    with _visitors_lock:
        today = datetime.utcnow().strftime("%Y-%m-%d")
        unique_ips = _visitors.get("unique_ips", {})
        total_unique = len(unique_ips)
        # Únicos humanos (excluyendo bots)
        human_unique = sum(1 for v in unique_ips.values() if not v.get("is_bot", False))
        visits_today = _visitors.get("daily_visits", {}).get(today, 0)
        human_today = _visitors.get("human_daily", {}).get(today, 0)
        total_visits = _visitors.get("total_visits", 0)
        human_visits = _visitors.get("human_visits", 0)
        bot_visits = _visitors.get("bot_visits", 0)

        # Últimos 7 días (humanos)
        daily = {}
        daily_human = {}
        for i in range(7):
            d = (datetime.utcnow() - timedelta(days=i)).strftime("%Y-%m-%d")
            daily[d] = _visitors.get("daily_visits", {}).get(d, 0)
            daily_human[d] = _visitors.get("human_daily", {}).get(d, 0)

        # Top páginas
        pages = dict(sorted(_visitors.get("page_views", {}).items(), key=lambda x: x[1], reverse=True)[:10])

        # Países
        countries = dict(sorted(_visitors.get("countries", {}).items(), key=lambda x: x[1], reverse=True)[:20])

        # Recientes (solo humanos primero, luego bots)
        recent_all = _visitors.get("recent_visitors", [])[:30]
        recent_humans = [v for v in recent_all if not v.get("is_bot", False)][:20]

        return jsonify({
            "total_visits": total_visits,
            "total_unique": total_unique,
            "human_visits": human_visits,
            "human_unique": human_unique,
            "bot_visits": bot_visits,
            "visits_today": visits_today,
            "human_today": human_today,
            "daily_last_7": daily,
            "daily_human_7": daily_human,
            "top_pages": pages,
            "countries": countries,
            "recent_visitors": recent_humans
        })

# ============================================================
#  LANDING PAGE
# ============================================================
@app.route("/", methods=["GET", "POST"])
def index_web():
    if request.method == "POST":
        return route_tv_signal()

    _track_visitor()
    # FIX 2026-05-08: HERO counters usan ahora _copier_trades (canal VIP) en vez
    # de _historial_real (MT5 personal trades, congelado). Asi HERO y Live Performance
    # muestran numeros consistentes y se actualizan en cada cierre VIP.
    # Misma logica que index_web aggregate stats (excluyendo dia actual).
    with _lock:
        hist = list(_historial_real) if _historial_real else []  # mantengo para tabla hero (rows)
        _ctr_all = list(_copier_trades) if _copier_trades else []
    try:
        from datetime import datetime as _dt_h, timezone as _tz_h, timedelta as _td_h
        _hoy_h = (_dt_h.now(_tz_h.utc) + _td_h(hours=1)).strftime("%d/%m/%Y")
    except Exception:
        _hoy_h = ""
    _ctr = [t for t in _ctr_all if t.get("fecha", "") != _hoy_h]
    _CTR_WIN = ("tp", "close_half", "close_partial", "full_close")
    _ctr_wins = sum(1 for t in _ctr if t.get("result") in _CTR_WIN and float(t.get("pips", 0) or 0) > 0)
    _ctr_losses = sum(1 for t in _ctr if t.get("result") == "sl")
    _ctr_closed = _ctr_wins + _ctr_losses
    total = _ctr_closed if _ctr_closed > 0 else len(hist)  # fallback si no hay copier trades aun
    wr = round(_ctr_wins / _ctr_closed * 100, 1) if _ctr_closed > 0 else 0
    _ctr_pips_won = sum(float(t.get("pips", 0) or 0) for t in _ctr
                        if t.get("result") in _CTR_WIN and float(t.get("pips", 0) or 0) > 0)
    _ctr_pips_lost = sum(float(t.get("pips", 0) or 0) for t in _ctr if t.get("result") == "sl")
    pips = round(_ctr_pips_won - _ctr_pips_lost, 1) if _ctr_closed > 0 else round(sum(float(h.get('pips', 0)) for h in hist), 1)
    _raw_profit = round(sum(float(h.get('profit_mt5', 0) or 0) for h in hist), 2)
    profit_str = ("+$" if _raw_profit >= 0 else "-$") + f"{abs(_raw_profit):,.2f}"

    # FIX 2026-04-08: Generar tabla hero dinámica — últimos 3 trades ganadores
    _recent_wins = [h for h in reversed(hist) if float(h.get('profit_mt5', 0) or 0) > 0][:3]
    _hero_rows = ""
    for _rw in _recent_wins:
        _rw_fecha = _rw.get("fecha", "")
        _rw_nombre = _rw.get("nombre", "GOLD")
        _rw_tipo = _rw.get("tipo", "COMPRA")
        _rw_entry = float(_rw.get("entrada", 0))
        _rw_profit = float(_rw.get("profit_mt5", 0) or 0)
        _rw_color = "#00ffc8" if _rw_tipo == "COMPRA" else "#ff3b30"
        _rw_tipo_en = "BUY" if _rw_tipo == "COMPRA" else "SELL"
        _rw_entry_fmt = f"{_rw_entry:,.2f}"
        # FIX 2026-04-28: escape de campos derivados de JSON (potencial XSS)
        _hero_rows += (
            f'<tr style="border-bottom:1px solid rgba(255,255,255,.04)">'
            f'<td style="padding:6px 8px;color:#8b9fc4">{_e(_rw_fecha)}</td>'
            f'<td style="padding:6px 8px;font-weight:700;color:#fff">{_e(_rw_nombre)}</td>'
            f'<td style="padding:6px 8px"><span style="color:{_rw_color}">&#9679;</span> {_e(_rw_tipo_en)}</td>'
            f'<td style="padding:6px 8px;font-family:monospace">{_e(_rw_entry_fmt)}</td>'
            f'<td style="padding:6px 8px;text-align:right;color:#00e676;font-weight:700">+${_rw_profit:,.2f}</td>'
            f'</tr>'
        )
    n_ops = sum(1 for op in _store.get("operaciones_activas", {}).values() if isinstance(op, dict) and op.get('mt5_ejecutado', False))
    activos = _store.get("assets_count", 6)
    is_alive = (time.time() - _store.get("ultimo_sync", 0)) < 120

    # ============================================================
    #  SECCIÓN #en-vivo — Señales del Canal VIP (desde signal_copier)
    #  FIX 2026-04-23: Fuente de datos = _copier_trades (lo que publica el
    #  canal VIP), NO _historial_real (trades MT5 del usuario — son cosas
    #  distintas). Quitada la sección "Operaciones Activas" por petición.
    # ============================================================
    _WIN_RESULTS = ("tp", "close_half", "close_partial", "full_close")
    _copier_sorted = sorted(_copier_trades, key=lambda t: t.get("closed_at", 0))

    # FIX 2026-05-08: excluir el día corriente de los AGREGADOS (total/WR/pips/streak)
    # para que un día parcial todavía sin cerrar no distorsione la reputación.
    # La tabla "Señales Recientes" sí muestra el día actual (live).
    try:
        from datetime import datetime as _dtnow_a, timezone as _tznow_a, timedelta as _tdnow_a
        _hoy_andorra = (_dtnow_a.now(_tznow_a.utc) + _tdnow_a(hours=1)).strftime("%d/%m/%Y")
    except Exception:
        _hoy_andorra = ""
    # FIX 2026-05-11: excluir fuentes internas/legacy de los AGREGADOS publicos.
    # Historico = canales retirados (FXPremiere, NasdaqMasters, etc., 177 trades
    # legacy relabelados). Manual/MT5_Reinsert = inserciones internas, no senales
    # del canal. Unknown = fallback cuando chat_title no matchea. Estos NO deben
    # contar en "Senales Totales" ni en WR — solo distorsionan la reputacion.
    _SOURCES_EXCLUIR_AGREGADOS = {"Historico", "Unknown", "Manual", "MT5_Reinsert", "Internal", "Bot"}
    _copier_for_stats = [
        t for t in _copier_sorted
        if t.get("fecha", "") != _hoy_andorra
        and (t.get("source") or "").strip() not in _SOURCES_EXCLUIR_AGREGADOS
    ]

    # Rango temporal cubierto en stats (para subtitle "Datos verificados del X al Y")
    # FIX 2026-05-20: usar _copier_sorted (incluye HOY) para que el rango bumpee en
    # tiempo real. Los AGREGADOS (468/WR/pips) siguen excluyendo el dia en curso
    # via _copier_for_stats — el subtitle solo refleja el rango visual.
    _fechas_stats_set = sorted(set(
        t.get("fecha", "") for t in _copier_sorted if t.get("fecha")
    ), key=lambda s: (s[6:10], s[3:5], s[0:2]) if len(s) == 10 else s)
    if len(_fechas_stats_set) >= 2:
        _rango_subtitle = (
            f"Datos verificados del {_fechas_stats_set[0]} al "
            f"{_fechas_stats_set[-1]} &middot; d&iacute;a actual en curso "
            f"(agregados al cierre 23:59 Andorra)"
        )
    elif len(_fechas_stats_set) == 1:
        _rango_subtitle = (
            f"Datos verificados desde {_fechas_stats_set[0]} &middot; "
            f"d&iacute;a actual en curso (agregados al cierre)"
        )
    else:
        _rango_subtitle = ""

    _total_senales = len(_copier_for_stats)
    _wins_vip = sum(1 for t in _copier_for_stats
                    if t.get("result") in _WIN_RESULTS and float(t.get("pips", 0) or 0) > 0)
    _losses_vip = sum(1 for t in _copier_for_stats if t.get("result") == "sl")
    _closed_total = _wins_vip + _losses_vip
    _wr_vip = round(_wins_vip / _closed_total * 100, 1) if _closed_total > 0 else 0
    # FIX 2026-04-28e: Net pips REAL = ganancias - perdidas (antes solo sumaba bruto).
    # Antes: +16,143 (bruto, engañoso). Ahora: +10,103 (neto real, transparente).
    _pips_ganados = round(sum(
        float(t.get("pips", 0) or 0) for t in _copier_for_stats
        if t.get("result") in _WIN_RESULTS and float(t.get("pips", 0) or 0) > 0
    ), 1)
    _pips_perdidos = round(sum(
        float(t.get("pips", 0) or 0) for t in _copier_for_stats
        if t.get("result") == "sl"
    ), 1)
    _pips_totales = round(_pips_ganados - _pips_perdidos, 1)
    _wins_count_card = _wins_vip
    _losses_count_card = _losses_vip

    _profit_color_live = '#00e676' if _pips_totales >= 0 else '#ff3b30'
    _wr_color_live = '#00d4aa' if _wr_vip >= 60 else ('#f0b90b' if _wr_vip >= 45 else '#ff3b30')

    # FIX 2026-05-08: pips por periodo para calculadora interactiva
    # Usa _copier_sorted (incluye hoy) para mostrar datos vivos en la calc
    try:
        from datetime import datetime as _dt_calc, timezone as _tz_calc, timedelta as _td_calc
        _now_calc = _dt_calc.now(_tz_calc.utc) + _td_calc(hours=1)
        _today_start_ts = _now_calc.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        _week_start_ts = (_now_calc - _td_calc(days=7)).timestamp()
        _month_start_ts = (_now_calc - _td_calc(days=30)).timestamp()
    except Exception:
        _today_start_ts = _week_start_ts = _month_start_ts = 0

    def _pips_in_range(start_ts):
        gan = sum(float(t.get("pips", 0) or 0) for t in _copier_sorted
                  if t.get("closed_at", 0) >= start_ts and t.get("result") in _WIN_RESULTS
                  and float(t.get("pips", 0) or 0) > 0)
        per = sum(float(t.get("pips", 0) or 0) for t in _copier_sorted
                  if t.get("closed_at", 0) >= start_ts and t.get("result") == "sl")
        return round(gan - per, 1)

    pips_today_real = _pips_in_range(_today_start_ts)
    pips_week_real = _pips_in_range(_week_start_ts)
    pips_month_real = _pips_in_range(_month_start_ts)

    # FIX 2026-05-20: stats LIVE de hoy para el card "En vivo" (peticion del usuario:
    # ver que el sistema sigue trabajando AHORA, no solo numeros congelados al 19/05).
    _today_tps = sum(1 for t in _copier_sorted
                     if t.get("closed_at", 0) >= _today_start_ts
                     and t.get("result") == "tp"
                     and float(t.get("pips", 0) or 0) > 0)
    _today_sls = sum(1 for t in _copier_sorted
                     if t.get("closed_at", 0) >= _today_start_ts
                     and t.get("result") == "sl")
    _today_partials = sum(1 for t in _copier_sorted
                          if t.get("closed_at", 0) >= _today_start_ts
                          and t.get("result") in ("close_half", "close_partial", "full_close")
                          and float(t.get("pips", 0) or 0) > 0)
    _today_total = _today_tps + _today_sls
    _today_wr = round(_today_tps / _today_total * 100, 0) if _today_total > 0 else 0
    _today_color = '#00e676' if pips_today_real >= 0 else '#ff3b30'

    # FIX 2026-05-11: calculo de "_current_streak" eliminado junto con su card
    # publica. Si vuelves a necesitar la racha, restaura el loop de _copier_for_stats
    # iterando en reversed() y rompiendo en "sl".

    # Historial reciente — últimas 10 señales cerradas del canal VIP
    _result_icon = {
        "tp": ("&#9989;", "#00e676"),          # ✅ verde
        "close_half": ("&#9889;", "#00d4aa"),  # ⚡ turquesa
        "close_partial": ("&#9889;", "#00d4aa"),
        "full_close": ("&#128274;", "#00d4aa"),# 🔒
        "sl": ("&#128721;", "#ff3b30"),        # 🛑 rojo
    }
    # FIX 2026-05-20: subido de 10 a 15 ultimas senales — mas visibilidad para
    # el cliente sin necesidad de scroll (peticion del usuario).
    _RECENT_TRADES_LIMIT = 15
    _recent_trades_html = ""
    for _rt in list(reversed(_copier_sorted))[:_RECENT_TRADES_LIMIT]:
        _rt_fecha = _rt.get("fecha", "")
        _rt_nombre = _rt.get("pair_display") or _rt.get("pair", "N/A")
        _rt_tipo_en = _rt.get("direction", "")
        _rt_pips = float(_rt.get("pips", 0) or 0)
        _rt_unit = _rt.get("pips_unit", "pips")
        _rt_result = _rt.get("result", "")
        _rt_icon, _rt_color = _result_icon.get(_rt_result, ("&#9679;", "#8b9fc4"))
        _tipo_color = "#00e676" if _rt_tipo_en == "BUY" else "#ff3b30"
        # FIX 2026-05-08: SL = perdida → pips siempre negativos en display.
        # En copier_stats.json los SLs se guardan con magnitud positiva
        # (distancia al SL en pips); el signo lo da el campo "result".
        if _rt_result == "sl" and _rt_pips > 0:
            _rt_pips = -_rt_pips
        _pips_sign = "+" if _rt_pips >= 0 else ""
        # FIX 2026-04-28: escape de campos derivados de JSON (potencial XSS)
        _recent_trades_html += (
            f'<tr style="border-bottom:1px solid rgba(255,255,255,.04)">'
            f'<td style="padding:8px;color:#8b9fc4;font-size:12px">{_e(_rt_fecha)}</td>'
            f'<td style="padding:8px;font-weight:700;color:#fff;font-size:13px">{_e(_rt_nombre)}</td>'
            f'<td style="padding:8px;font-size:12px"><span style="color:{_tipo_color}">&#9679;</span> {_e(_rt_tipo_en)}</td>'
            f'<td style="padding:8px;font-family:monospace;font-size:12px;color:{_rt_color}">{_pips_sign}{_rt_pips:.1f} {_e(_rt_unit)}</td>'
            f'<td style="padding:8px;text-align:right;font-size:14px">{_rt_icon}</td>'
            f'</tr>'
        )
    if not _recent_trades_html:
        _recent_trades_html = '<tr><td colspan="5" style="padding:20px;text-align:center;color:#8b9fc4;font-size:12px">A\xfan no hay se\xf1ales publicadas</td></tr>'

    # FIX 2026-04-26: Bloque "Rendimiento por Activo" eliminado por solicitud del usuario.

    _html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<!-- Google Ads tag -->
<script async src="https://www.googletagmanager.com/gtag/js?id=AW-18090606337"></script>
<script>window.dataLayer=window.dataLayer||[];function gtag(){{dataLayer.push(arguments);}}gtag('js',new Date());gtag('config','AW-18090606337');</script>
<!-- Cookie Consent + Conditional Analytics -->
<script>
(function(){{
  var c=localStorage.getItem('bs365_consent');
  function _loadGA(){{
    if(window._ga_loaded) return; window._ga_loaded=true;
    var s=document.createElement('script'); s.async=true;
    s.src='https://www.googletagmanager.com/gtag/js?id=G-L514BL7E83';
    document.head.appendChild(s);
    window.dataLayer=window.dataLayer||[];
    window.gtag=function(){{dataLayer.push(arguments);}};
    gtag('js',new Date()); gtag('config','G-L514BL7E83'); gtag('config','AW-18090606337');
  }}
  if(c==='accepted') _loadGA();
  window._acceptCookies=function(){{
    localStorage.setItem('bs365_consent','accepted');
    var b=document.getElementById('bs365-cb'); if(b) b.remove(); _loadGA();
  }};
  window._declineCookies=function(){{
    localStorage.setItem('bs365_consent','declined');
    var b=document.getElementById('bs365-cb'); if(b) b.remove();
  }};
  if(!c) document.addEventListener('DOMContentLoaded',function(){{
    var el=document.getElementById('bs365-cb'); if(el) el.style.display='flex';
  }});
}})();
</script>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>BuySell365 Pro — Se\u00f1ales de Trading con IA H\u00edbrida + Visi\u00f3n</title>
<meta name="description" content="Se\u00f1ales de trading con sistema h\u00edbrido IA + Visi\u00f3n. Cada se\u00f1al incluye % de probabilidad calculado en tiempo real. Oro, NASDAQ, S&amp;P 500, Forex. Entry/SL/TP exactos, listos para ejecutar.">
<meta name="keywords" content="se\u00f1ales trading IA, se\u00f1ales con probabilidad, sistema h\u00edbrido trading, se\u00f1ales oro telegram, gold signals AI, se\u00f1ales NASDAQ con IA, se\u00f1ales EUR/USD, BuySell365, trading con visi\u00f3n IA, se\u00f1ales con porcentaje, se\u00f1ales premium telegram, se\u00f1ales XAUUSD, forex signals AI">
<meta property="og:title" content="BuySell365 Pro \u2014 Sistema H\u00edbrido IA + Visi\u00f3n con % de Probabilidad">
<meta property="og:description" content="Cada se\u00f1al pasa por dos capas de an\u00e1lisis (t\u00e9cnico + IA con visi\u00f3n) y llega con un % de probabilidad. Oro, NASDAQ, Forex en tiempo real.">
<meta property="og:image" content="https://buysell365.pro/img/og_image.png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:type" content="website">
<meta property="og:url" content="https://buysell365.pro">
<meta property="og:site_name" content="BuySell365 Pro">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="BuySell365 Pro \u2014 IA H\u00edbrida + Visi\u00f3n con % por se\u00f1al">
<meta name="twitter:description" content="Sistema h\u00edbrido IA + Visi\u00f3n. Cada se\u00f1al con % de probabilidad calculado en tiempo real.">
<meta name="twitter:image" content="https://buysell365.pro/img/og_image.png">
<meta name="twitter:site" content="@buysell365pro">
<link rel="canonical" href="https://buysell365.pro">
<link rel="manifest" href="/manifest.json">
<meta name="theme-color" content="#080b0f">
<link rel="icon" href="/img/bull_bear.png" type="image/png">
<script type="application/ld+json">
{{
  "@context": "https://schema.org",
  "@type": "SoftwareApplication",
  "name": "BuySell365 Pro",
  "applicationCategory": "FinanceApplication",
  "operatingSystem": "Web, Telegram",
  "description": "Se\u00f1ales de trading automatizadas con Inteligencia Artificial. NASDAQ, S&P 500, US30, GER40 y m\u00e1s activos. An\u00e1lisis con IA avanzada y datos institucionales.",
  "url": "https://buysell365.pro",
  "offers": {{
    "@type": "Offer",
    "price": "0",
    "priceCurrency": "USD",
    "description": "Plan Comunidad gratuito disponible"
  }}
}}
</script>
<script type="application/ld+json">
{{
  "@context": "https://schema.org",
  "@type": "FAQPage",
  "mainEntity": [
    {{"@type":"Question","name":"\u00bfNecesito experiencia en trading?","acceptedAnswer":{{"@type":"Answer","text":"No. Las se\u00f1ales son claras y f\u00e1ciles de seguir. Te decimos exactamente d\u00f3nde entrar, d\u00f3nde colocar el Stop Loss y los Take Profits. Adem\u00e1s, nuestra comunidad te ayudar\u00e1 a aprender."}}}},
    {{"@type":"Question","name":"\u00bfPuedo retirar mi dinero cuando quiera?","acceptedAnswer":{{"@type":"Answer","text":"S\u00ed. Tu capital est\u00e1 en tu propia cuenta del broker \u2014 nosotros nunca lo tocamos. Puedes retirar en cualquier momento directamente desde tu broker, sin restricciones ni penalizaciones."}}}},
    {{"@type":"Question","name":"\u00bfCu\u00e1ntas se\u00f1ales recibo al d\u00eda?","acceptedAnswer":{{"@type":"Answer","text":"En promedio entre 5 y 15 se\u00f1ales diarias repartidas entre los +20 activos. El bot analiza el mercado cada 3 minutos y solo env\u00eda se\u00f1ales cuando detecta una oportunidad de alta probabilidad."}}}}
  ]
}}
</script>
<script defer data-domain="buysell365.pro" src="https://plausible.io/js/script.js"></script>
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&display=swap');
*{{margin:0;padding:0;box-sizing:border-box}}
:root{{
  --bg:#07091f;--bg2:#0e1628;--bg3:#162035;
  --green:#00ffcc;--green2:#00f5c4;--blue:#4d9fff;--purple:#a78bfa;
  --gold:#fbbf24;--red:#f87171;--text:#f0f6ff;--text2:#b0bdd0;
  --glass:rgba(255,255,255,0.07);--border:rgba(255,255,255,0.1);
  --glow-green:rgba(0,255,204,0.25);--glow-gold:rgba(251,191,36,0.25);
}}
html{{scroll-behavior:smooth}}
body{{font-family:'Inter',sans-serif;background:var(--bg);color:var(--text);overflow-x:hidden;
  background-image:radial-gradient(ellipse 80% 40% at 50% -10%,rgba(0,255,204,0.08) 0%,transparent 70%)}}

/* ═══ HERO ═══ */
.hero{{min-height:60vh;display:flex;align-items:center;justify-content:center;position:relative;overflow:hidden;padding:100px 20px 40px}}
.hero::before{{content:'';position:absolute;top:-50%;left:-50%;width:200%;height:200%;
  background:radial-gradient(circle at 30% 40%,rgba(0,255,204,0.22) 0%,transparent 45%),
             radial-gradient(circle at 70% 60%,rgba(77,159,255,0.18) 0%,transparent 45%),
             radial-gradient(circle at 50% 30%,rgba(167,139,250,0.14) 0%,transparent 45%);
  animation:heroGlow 15s ease infinite alternate}}
@keyframes heroGlow{{0%{{transform:rotate(0deg)}}100%{{transform:rotate(12deg)}}}}
.hero-content{{text-align:center;max-width:900px;z-index:2;position:relative}}
.hero-badge{{display:inline-flex;align-items:center;gap:8px;background:rgba(0,212,170,0.12);border:1px solid rgba(0,212,170,0.35);
  border-radius:50px;padding:7px 20px;font-size:13px;color:var(--green);margin-bottom:24px;font-weight:600;
  box-shadow:0 0 20px rgba(0,212,170,0.15)}}
.hero-badge .dot{{width:8px;height:8px;background:var(--green);border-radius:50%;animation:pulse 2s infinite}}
@keyframes pulse{{0%,100%{{opacity:1}}50%{{opacity:0.3}}}}
.hero h1{{font-size:clamp(2.5rem,6vw,4.2rem);font-weight:900;line-height:1.1;margin-bottom:20px;
  background:linear-gradient(135deg,#fff 0%,#78ffe8 30%,#00ffcc 55%,#4d9fff 100%);
  -webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;
  text-shadow:none;filter:drop-shadow(0 0 40px rgba(0,255,204,0.3))}}
.hero p{{font-size:clamp(1rem,2vw,1.18rem);color:#c0cfe0;max-width:620px;margin:0 auto 32px;line-height:1.7}}
.hero-buttons{{display:flex;gap:16px;justify-content:center;flex-wrap:wrap}}
.btn{{display:inline-flex;align-items:center;gap:8px;padding:14px 32px;border-radius:12px;font-size:15px;font-weight:600;
  text-decoration:none;transition:all 0.3s ease;cursor:pointer;border:none}}
.btn-primary{{background:linear-gradient(135deg,#00ffcc,#00d4aa,#00b894);color:#060a12;box-shadow:0 4px 30px rgba(0,255,204,0.5),0 0 60px rgba(0,255,204,0.15);font-weight:800;animation:ctaGlow 2.5s ease-in-out infinite}}
.btn-primary:hover{{transform:translateY(-3px);box-shadow:0 8px 50px rgba(0,255,204,0.7),0 0 80px rgba(0,255,204,0.2)}}
@keyframes ctaGlow{{0%,100%{{box-shadow:0 4px 30px rgba(0,255,204,0.5),0 0 60px rgba(0,255,204,0.15)}}50%{{box-shadow:0 6px 50px rgba(0,255,204,0.85),0 0 100px rgba(0,255,204,0.4)}}}}
.btn-secondary{{background:rgba(255,255,255,0.08);color:var(--text);border:1px solid rgba(255,255,255,0.18)}}
.btn-secondary:hover{{background:rgba(255,255,255,0.14);transform:translateY(-3px);border-color:rgba(0,255,204,0.4);box-shadow:0 4px 20px rgba(0,255,204,0.1)}}

/* ═══ STATS BAR ═══ */
.stats-bar{{display:flex;justify-content:center;gap:40px;margin-top:40px;flex-wrap:wrap;padding:24px 28px;background:rgba(0,255,204,0.06);border:1px solid rgba(0,255,204,0.22);border-radius:24px;max-width:720px;margin-left:auto;margin-right:auto;backdrop-filter:blur(12px);box-shadow:0 0 40px rgba(0,255,204,0.07)}}
.stat-item{{text-align:center}}
.stat-value{{font-size:2.2rem;font-weight:900;color:#00ffcc;text-shadow:0 0 24px rgba(0,255,204,0.7),0 0 48px rgba(0,255,204,0.3)}}
.stat-value.blue{{color:#4d9fff;text-shadow:0 0 24px rgba(77,159,255,0.7),0 0 48px rgba(77,159,255,0.3)}}
.stat-value.gold{{color:#fbbf24;text-shadow:0 0 24px rgba(251,191,36,0.7),0 0 48px rgba(251,191,36,0.3)}}
.stat-value.purple{{color:#a78bfa;text-shadow:0 0 24px rgba(167,139,250,0.7),0 0 48px rgba(167,139,250,0.3)}}
.stat-label{{font-size:11px;color:var(--text2);text-transform:uppercase;letter-spacing:1.5px;margin-top:6px;font-weight:600}}

/* ═══ SECTIONS ═══ */
section{{padding:60px 20px}}
.section-title{{text-align:center;margin-bottom:36px}}
.section-title h2{{font-size:clamp(1.8rem,4vw,2.8rem);font-weight:900;margin-bottom:14px;
  filter:drop-shadow(0 0 20px rgba(0,255,204,0.2))}}
.section-title h2 span{{
  background:linear-gradient(135deg,#fff 20%,#00ffcc 55%,#4d9fff 100%);
  -webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text}}
.section-title p{{color:var(--text2);font-size:1.1rem;max-width:650px;margin:0 auto;line-height:1.6}}

/* ═══ FEATURES ═══ */
.features{{background:linear-gradient(180deg,var(--bg) 0%,var(--bg2) 30%,var(--bg2) 70%,var(--bg) 100%)}}
.features-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:20px;max-width:1100px;margin:0 auto}}
.feature-card{{background:linear-gradient(145deg,rgba(22,32,53,0.9),rgba(14,22,40,0.85));border:1px solid rgba(255,255,255,0.12);border-radius:20px;padding:30px;transition:all 0.5s cubic-bezier(.25,.8,.25,1);box-shadow:0 4px 24px rgba(0,0,0,0.3)}}
.feature-card:hover{{transform:translateY(-12px) scale(1.04);border-color:rgba(0,255,204,0.6);box-shadow:0 20px 60px rgba(0,255,204,0.25),0 0 40px rgba(0,255,204,0.15),0 4px 20px rgba(0,0,0,0.4);background:linear-gradient(145deg,rgba(0,255,204,0.08),rgba(22,32,53,0.95))}}
.feature-icon{{width:48px;height:48px;border-radius:12px;display:flex;align-items:center;justify-content:center;font-size:24px;margin-bottom:16px}}
.feature-icon.green{{background:rgba(0,212,170,0.18);box-shadow:0 0 16px rgba(0,212,170,0.2)}}
.feature-icon.blue{{background:rgba(59,130,246,0.18);box-shadow:0 0 16px rgba(59,130,246,0.2)}}
.feature-icon.purple{{background:rgba(139,92,246,0.18);box-shadow:0 0 16px rgba(139,92,246,0.2)}}
.feature-icon.gold{{background:rgba(245,158,11,0.18);box-shadow:0 0 16px rgba(245,158,11,0.2)}}
.feature-card h3{{font-size:1.15rem;font-weight:700;margin-bottom:8px;color:#e8f4ff}}
.feature-card p{{color:var(--text2);font-size:0.9rem;line-height:1.7}}

/* ═══ ASSETS ═══ */
.assets-grid{{display:flex;flex-wrap:wrap;justify-content:center;gap:16px;max-width:960px;margin:0 auto}}
.asset-card{{background:linear-gradient(145deg,rgba(22,32,53,0.95),rgba(14,22,40,0.85));border:1px solid rgba(255,255,255,0.1);border-radius:16px;padding:24px 16px;text-align:center;transition:all 0.3s ease;width:160px;flex-shrink:0;box-shadow:0 4px 16px rgba(0,0,0,0.25)}}
.asset-card:hover{{transform:scale(1.07);border-color:var(--green);box-shadow:0 6px 30px rgba(0,255,204,0.2)}}
.asset-emoji{{font-size:2.5rem;margin-bottom:8px}}
.asset-icon{{width:56px;height:56px;border-radius:14px;margin:0 auto 12px;display:flex;align-items:center;justify-content:center;position:relative;overflow:hidden}}
.asset-icon svg{{width:32px;height:32px}}
.asset-icon.gold{{background:linear-gradient(135deg,rgba(240,185,11,.15),rgba(240,185,11,.05));border:1px solid rgba(240,185,11,.3)}}
.asset-icon.btc{{background:linear-gradient(135deg,rgba(247,147,26,.15),rgba(247,147,26,.05));border:1px solid rgba(247,147,26,.3)}}
.asset-icon.eth{{background:linear-gradient(135deg,rgba(98,126,234,.15),rgba(98,126,234,.05));border:1px solid rgba(98,126,234,.3)}}
.asset-icon.eur{{background:linear-gradient(135deg,rgba(0,82,180,.15),rgba(0,82,180,.05));border:1px solid rgba(0,82,180,.3)}}
.asset-icon.jpy{{background:linear-gradient(135deg,rgba(188,0,45,.15),rgba(188,0,45,.05));border:1px solid rgba(188,0,45,.3)}}
.asset-icon.nasdaq{{background:linear-gradient(135deg,rgba(0,212,170,.15),rgba(0,212,170,.05));border:1px solid rgba(0,212,170,.3)}}
.asset-icon.sp500{{background:linear-gradient(135deg,rgba(59,130,246,.15),rgba(59,130,246,.05));border:1px solid rgba(59,130,246,.3)}}
.asset-name{{font-weight:700;font-size:0.95rem}}
.asset-tag{{font-size:0.75rem;color:var(--text2);margin-top:4px}}

/* ═══ PRICING ═══ */
.pricing{{background:linear-gradient(180deg,rgba(0,255,204,0.03) 0%,var(--bg2) 40%,rgba(251,191,36,0.03) 100%);padding:80px 20px!important;position:relative}}
.pricing::before{{content:'';position:absolute;top:0;left:50%;transform:translateX(-50%);width:60%;height:2px;background:linear-gradient(90deg,transparent,#00ffcc,#fbbf24,#00ffcc,transparent);border-radius:2px}}
.pricing-cards{{display:grid;grid-template-columns:repeat(3,1fr);gap:24px;max-width:1280px;margin:0 auto;justify-items:center;align-items:stretch}}
.pricing-cards .price-card{{width:100%;max-width:400px;display:flex;flex-direction:column}}
@media(max-width:1100px){{.pricing-cards{{grid-template-columns:repeat(2,1fr);max-width:880px}}}}
@media(max-width:760px){{.pricing-cards{{grid-template-columns:1fr!important;max-width:420px}}}}
.price-card{{background:linear-gradient(160deg,rgba(22,32,53,0.97),rgba(14,22,40,0.97));border:1px solid rgba(255,255,255,0.12);border-radius:24px;padding:48px 32px;text-align:center;position:relative;transition:all .5s cubic-bezier(.25,.8,.25,1);backdrop-filter:blur(16px);box-shadow:0 8px 32px rgba(0,0,0,0.3)}}
.price-card:hover{{transform:translateY(-14px) scale(1.03);box-shadow:0 28px 70px rgba(0,0,0,.5),0 0 50px rgba(0,255,204,0.15),0 0 80px rgba(0,255,204,0.08);border-color:rgba(0,255,204,0.4)}}
.price-card.featured{{border-color:var(--gold);box-shadow:0 0 60px rgba(251,191,36,0.2),0 0 120px rgba(251,191,36,0.08)}}
.price-badge{{position:absolute;top:-16px;left:50%;transform:translateX(-50%);background:linear-gradient(135deg,var(--green),#00b894);
  color:#060a12;padding:8px 28px;border-radius:24px;font-size:14px;font-weight:900;white-space:nowrap;letter-spacing:0.5px;box-shadow:0 4px 20px rgba(0,255,204,0.3)}}
.price-name{{font-size:1.4rem;font-weight:900;margin-bottom:10px;letter-spacing:-0.5px}}
.price-amount{{font-size:3.5rem;font-weight:900;margin:16px 0}}
.price-amount span{{font-size:1rem;color:var(--text2);font-weight:400}}
.price-amount .old{{text-decoration:line-through;color:var(--text2);font-size:1.5rem;display:block;font-weight:400}}
.price-list{{list-style:none;text-align:left;margin:24px 0}}
.price-list li{{padding:10px 0;color:var(--text);font-size:0.95rem;display:flex;align-items:center;gap:10px;font-weight:500}}
.price-list li::before{{content:'\u2714\ufe0f';font-size:14px}}
@keyframes shimmer{{0%{{background-position:-200% 0}}100%{{background-position:200% 0}}}}
@keyframes glowPulse{{0%,100%{{box-shadow:0 0 30px rgba(0,255,204,0.35)}}50%{{box-shadow:0 0 60px rgba(0,255,204,0.7)}}}}
@keyframes goldPulse{{0%,100%{{box-shadow:0 0 50px rgba(251,191,36,0.25),0 0 100px rgba(251,191,36,0.1)}}50%{{box-shadow:0 0 80px rgba(251,191,36,0.4),0 0 140px rgba(251,191,36,0.15)}}}}

/* ═══ CTA ═══ */
.cta{{text-align:center;padding:70px 20px;background:radial-gradient(ellipse at 50% 50%,rgba(0,255,204,0.07) 0%,transparent 70%)}}
.cta h2{{font-size:clamp(1.8rem,4vw,2.5rem);font-weight:900;margin-bottom:16px;
  filter:drop-shadow(0 0 24px rgba(0,255,204,0.25))}}
.cta p{{color:var(--text2);margin-bottom:32px;font-size:1.1rem}}

/* ═══ FOOTER ═══ */
.footer{{background:var(--bg2);border-top:1px solid var(--border);padding:40px 20px;text-align:center}}
.footer-links{{display:flex;justify-content:center;gap:24px;margin-bottom:16px;flex-wrap:wrap}}
.footer-links a{{color:var(--text2);text-decoration:none;font-size:0.9rem;transition:color 0.3s;display:inline-flex;align-items:center;min-height:44px;padding:8px 6px}}
.footer-links a:hover{{color:var(--green)}}
.footer p{{color:var(--text2);font-size:0.8rem}}

/* ═══ NAV ═══ */
.nav{{position:fixed;top:0;left:0;right:0;z-index:100;padding:14px 28px;display:flex;justify-content:space-between;align-items:center;
  background:transparent;backdrop-filter:none;-webkit-backdrop-filter:none;border-bottom:none;
  transition:background .3s,backdrop-filter .3s,border-bottom .3s,padding .3s}}
.nav.scrolled{{background:rgba(6,10,18,0.85);backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px);border-bottom:1px solid rgba(0,255,204,0.1);padding:8px 28px;box-shadow:0 4px 30px rgba(0,0,0,0.3)}}
.nav-logo{{display:flex;align-items:center;gap:14px;font-weight:800;font-size:24px;color:#fff;text-decoration:none;flex-shrink:0;z-index:101;letter-spacing:-.5px}}
.nav-logo img{{width:72px;height:72px;min-width:72px;min-height:72px;border-radius:14px;object-fit:contain;flex-shrink:0;display:block;border:1px solid rgba(0,212,170,.2);box-shadow:0 4px 16px rgba(0,212,170,.15)}}
.nav-links{{display:flex;gap:24px;align-items:center}}
.nav-links a{{color:var(--text2);text-decoration:none;font-size:0.9rem;font-weight:500;transition:color 0.3s}}
.nav-links a:hover{{color:var(--green)}}
.nav-cta{{background:linear-gradient(135deg,#00e6c0,#00d4aa);color:#0a0e17;padding:9px 18px;border-radius:8px;font-weight:700;font-size:0.85rem;border:none;text-decoration:none;display:flex;align-items:center;gap:6px;transition:all .2s;box-shadow:0 2px 14px rgba(0,212,170,0.4)}}
.nav-cta:hover{{background:linear-gradient(135deg,#00ffcc,#00e6b8);transform:translateY(-1px);box-shadow:0 4px 20px rgba(0,212,170,0.6)}}
/* Barra de banderas inline DESKTOP — 4 banderas siempre visibles, 1 click cambia idioma (FIX 2026-05-09) */
.lang-bar-desktop{{display:inline-flex;align-items:center;gap:6px;margin-left:8px;padding:4px 6px;background:rgba(0,212,170,0.06);border:1px solid rgba(0,212,170,0.18);border-radius:12px}}
.lang-bar-desktop .lang-flag{{background:rgba(255,255,255,0.04);border:1.5px solid rgba(255,255,255,0.10);border-radius:9px;padding:8px 10px;cursor:pointer;font-size:22px;line-height:1;min-width:42px;min-height:42px;display:inline-flex;align-items:center;justify-content:center;transition:all .18s;font-family:inherit;position:relative}}
.lang-bar-desktop .lang-flag:hover{{background:rgba(0,212,170,0.15);border-color:rgba(0,212,170,0.5);transform:scale(1.1)}}
.lang-bar-desktop .lang-flag.active{{background:rgba(0,212,170,0.28);border-color:#00d4aa;box-shadow:0 0 14px rgba(0,212,170,.4),inset 0 0 10px rgba(0,212,170,.15)}}
.lang-bar-desktop .lang-flag.active::after{{content:'';position:absolute;bottom:-2px;left:50%;transform:translateX(-50%);width:24px;height:3px;background:#00d4aa;border-radius:3px;box-shadow:0 0 8px rgba(0,212,170,.6)}}

/* MOBILE-ONLY barra de banderas siempre visible (FIX 2026-05-08) */
.mob-lang-bar{{display:none;align-items:center;gap:4px;margin-right:8px}}
.mob-flag{{background:rgba(255,255,255,0.06);border:1.5px solid rgba(255,255,255,0.12);border-radius:8px;padding:6px 8px;cursor:pointer;font-size:18px;line-height:1;min-width:36px;min-height:36px;display:inline-flex;align-items:center;justify-content:center;transition:all .15s;font-family:inherit;position:relative}}
.mob-flag:hover,.mob-flag:active{{background:rgba(0,212,170,.18);border-color:rgba(0,212,170,.5);transform:scale(1.08)}}
.mob-flag.active{{background:rgba(0,212,170,0.28);border-color:#00d4aa;box-shadow:0 0 12px rgba(0,212,170,.4)}}

/* ═══ ABOUT US ═══ */
.about-grid{{max-width:900px;margin:0 auto;display:grid;grid-template-columns:1fr 1fr;gap:30px}}

/* ═══ HAMBURGER MENU ═══ */
.hamburger{{display:none;background:none;border:none;cursor:pointer;padding:8px;z-index:200}}
.hamburger span{{display:block;width:24px;height:2px;background:#fff;margin:5px 0;border-radius:2px;transition:all .3s}}
.hamburger.active span:nth-child(1){{transform:rotate(45deg) translate(5px,5px)}}
.hamburger.active span:nth-child(2){{opacity:0}}
.hamburger.active span:nth-child(3){{transform:rotate(-45deg) translate(5px,-5px)}}
.mobile-overlay{{display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(10,14,23,.97);z-index:150;flex-direction:column;align-items:center;justify-content:center;gap:24px;backdrop-filter:blur(20px)}}
.mobile-overlay.show{{display:flex}}
.mobile-overlay a{{color:#fff;text-decoration:none;font-size:1.3rem;font-weight:600;padding:12px 32px;border-radius:12px;transition:all .2s}}
.mobile-overlay a:hover{{background:rgba(0,212,170,.15);color:var(--green)}}
.mobile-overlay .mob-lang{{display:flex;gap:12px;margin-top:16px}}
.mobile-overlay .mob-lang a{{font-size:1.8rem;padding:8px}}

/* ═══ FAQ ═══ */
.faq{{padding:40px 20px}}
.faq-list{{max-width:800px;margin:0 auto}}
.faq-item{{background:linear-gradient(145deg,rgba(22,32,53,0.9),rgba(14,22,40,0.85));border:1px solid rgba(255,255,255,0.1);border-radius:16px;margin-bottom:12px;overflow:hidden;transition:border-color .3s,box-shadow .3s}}
.faq-item:hover{{border-color:rgba(0,212,170,.4);box-shadow:0 4px 24px rgba(0,212,170,0.08)}}
.faq-q{{padding:20px 24px;cursor:pointer;display:flex;justify-content:space-between;align-items:center;font-weight:600;font-size:.95rem;color:#fff;user-select:none}}
.faq-q::after{{content:'+';font-size:1.4rem;color:var(--green);transition:transform .3s;flex-shrink:0;margin-left:16px}}
.faq-item.open .faq-q::after{{content:'\u2212'}}
.faq-a{{max-height:0;overflow:hidden;transition:max-height .4s ease,padding .3s;padding:0 24px;color:var(--text2);font-size:.9rem;line-height:1.7}}
.faq-item.open .faq-a{{max-height:300px;padding:0 24px 20px}}

/* ═══ FLOATING BUTTONS ═══ */
.float-telegram{{position:fixed;bottom:24px;right:24px;z-index:90;width:56px;height:56px;border-radius:50%;background:linear-gradient(135deg,#0088cc,#00a8e8);display:flex;align-items:center;justify-content:center;box-shadow:0 4px 20px rgba(0,136,204,.4);cursor:pointer;text-decoration:none;transition:all .3s;animation:floatPulse 2s infinite}}
.float-telegram:hover{{transform:scale(1.1);box-shadow:0 6px 30px rgba(0,136,204,.6)}}
.float-telegram svg{{width:28px;height:28px;fill:#fff}}
@keyframes floatPulse{{0%,100%{{box-shadow:0 4px 20px rgba(0,136,204,.4)}}50%{{box-shadow:0 4px 30px rgba(0,136,204,.7)}}}}
.back-to-top{{position:fixed;bottom:90px;right:28px;z-index:90;width:44px;height:44px;border-radius:50%;background:var(--bg3);border:1px solid var(--border);display:flex;align-items:center;justify-content:center;cursor:pointer;opacity:0;pointer-events:none;transition:all .3s;color:var(--green);font-size:18px}}
.back-to-top.show{{opacity:1;pointer-events:auto}}
.back-to-top:hover{{background:rgba(0,212,170,.15);border-color:var(--green)}}

/* countdown removed */


/* ═══ FADE-IN ANIMATIONS ═══ */
.fade-in{{opacity:0;transform:translateY(30px);transition:opacity 0.7s ease-out,transform 0.7s ease-out}}
.fade-in.visible{{opacity:1;transform:none}}

/* ═══ RESPONSIVE ═══ */
@media(max-width:768px){{
  .nav{{padding:10px 16px}}
  .nav.scrolled{{padding:6px 16px}}
  .nav-logo{{display:flex!important;visibility:visible!important;opacity:1!important;gap:10px;font-size:18px}}
  .nav-logo img{{width:48px;height:48px;min-width:48px;min-height:48px;display:block!important;visibility:visible!important}}
  .nav-links{{display:none}}
  .hamburger{{display:block}}
  .mob-lang-bar{{display:inline-flex!important}}
  /* En mobile usamos mob-lang-bar (mas compacta), ocultamos la desktop */
  .lang-bar-desktop{{display:none!important}}
  .stats-bar{{gap:16px;flex-wrap:wrap;justify-content:center;padding:18px 14px}}
  .stat-value{{font-size:1.5rem}}
  .hero{{padding:80px 12px 30px}}
  .hero h1{{font-size:2rem}}
  .hero p{{font-size:.9rem;padding:0 8px}}
  .hero-buttons{{flex-direction:column;gap:10px;align-items:center}}
  .hero-buttons .btn{{width:100%;max-width:320px;justify-content:center}}
  .section-title h2{{font-size:1.5rem}}
  .features-grid{{grid-template-columns:1fr!important}}
  .about-grid{{grid-template-columns:1fr}}
  .assets-grid{{gap:10px}}
  .assets-grid .asset-card{{width:calc(50% - 8px);min-width:0}}
  .pricing-cards{{grid-template-columns:1fr!important;gap:20px}}
  .price-card.featured{{transform:none!important}}
  .price-card{{padding:36px 24px}}
  .price-amount{{font-size:2.8rem}}
  .cta h2{{font-size:1.5rem}}
  .cta .hero-buttons{{flex-direction:column;gap:10px}}
  .footer-links{{gap:12px}}
  .float-telegram{{bottom:16px;right:16px;width:50px;height:50px}}
  .back-to-top{{bottom:76px;right:20px;width:38px;height:38px}}
  section{{padding:40px 12px}}
  .pricing{{padding:50px 12px!important}}
  /* FIX 2026-05-08: tabla 'Senales Recientes' como cards verticales en mobile */
  #en-vivo table thead{{display:none}}
  #en-vivo table tr{{display:flex;flex-wrap:wrap;border:1px solid rgba(255,255,255,.08);border-radius:10px;margin-bottom:8px;padding:10px;background:rgba(22,32,53,0.6)}}
  #en-vivo table td{{padding:4px 6px!important;border:none!important;flex:1 1 50%;font-size:12px}}
  #en-vivo table td:first-child{{flex:1 1 100%;color:#8b9fc4!important;font-size:11px!important;font-weight:600}}
  #en-vivo table td:nth-child(2){{font-weight:800;color:#fff!important;font-size:14px!important}}
  #en-vivo table td:last-child{{text-align:right!important;font-size:18px!important}}
}}
@media(max-width:480px){{
  .hero h1{{font-size:1.6rem}}
  .hero p{{font-size:.85rem}}
  .mob-lang-bar{{gap:2px;margin-right:4px}}
  .mob-flag{{font-size:15px;min-width:30px;min-height:32px;padding:5px 6px}}
  .stats-bar{{gap:10px;padding:14px 10px}}
  .stat-value{{font-size:1.2rem}}
  .stat-label{{font-size:.6rem;letter-spacing:.8px}}
  .assets-grid .asset-card{{width:calc(50% - 6px);padding:14px 8px}}
  .asset-icon svg{{width:28px;height:28px}}
  .asset-name{{font-size:.8rem}}
  .asset-tag{{font-size:.65rem}}
  .faq-q{{padding:16px 18px;font-size:.88rem}}
  .price-card{{padding:28px 18px}}
  .promo-features{{gap:8px}}
}}
</style>
<script>
if('serviceWorker' in navigator){{
  window.addEventListener('load', function(){{
    navigator.serviceWorker.register('/sw.js').catch(function(){{}});
  }});
}}
</script>
</head>
<body>

<!-- FLOATING EMOJI PARTICLES BACKGROUND -->
<canvas id="particlesCanvas" style="position:fixed;top:0;left:0;width:100%;height:100%;z-index:0;pointer-events:none;opacity:.55"></canvas>

<!-- NAV -->
<nav class="nav">
  <a href="/" class="nav-logo">
    <img src="/img/bull_bear.png" alt="BS365" loading="lazy"><div style="display:flex;flex-direction:column;line-height:1.1">BuySell365 <span style="color:#00d4aa;font-weight:800">Pro</span><small style="font-size:10px;color:rgba(255,255,255,.5);font-weight:400;letter-spacing:1px;margin-top:2px" data-i18n="dash.tagline">TRADING CON INTELIGENCIA ARTIFICIAL</small></div>
  </a>
  <div class="nav-links">
    <a href="#features" data-i18n="nav.technology">Tecnolog\u00eda</a>
    <a href="#assets" data-i18n="nav.assets">Activos</a>
    <a href="#pricing" data-i18n="nav.pricing">Servicios</a>
    <a href="#en-vivo" data-i18n="nav.dashboard">En Vivo</a>
    <a href="/resultados" data-i18n="nav.resultados">Resultados</a>
    <!-- Barra de banderas inline desktop (FIX 2026-05-09: reemplaza dropdown - 1 click directo) -->
    <div class="lang-bar-desktop" id="langBarDesktop" aria-label="Idioma">
      <button class="lang-flag" data-lang="es" onclick="setLang('es')" title="Espa\u00f1ol" aria-label="Espa\u00f1ol">\U0001f1ea\U0001f1f8</button>
      <button class="lang-flag" data-lang="en" onclick="setLang('en')" title="English" aria-label="English">\U0001f1fa\U0001f1f8</button>
      <button class="lang-flag" data-lang="pt" onclick="setLang('pt')" title="Portugu\u00eas" aria-label="Portugu\u00eas">\U0001f1e7\U0001f1f7</button>
      <button class="lang-flag" data-lang="fr" onclick="setLang('fr')" title="Fran\u00e7ais" aria-label="Fran\u00e7ais">\U0001f1eb\U0001f1f7</button>
    </div>
  </div>
  <!-- MOBILE-ONLY: barra de banderas siempre visible (FIX 2026-05-08) -->
  <div class="mob-lang-bar" aria-label="Idioma">
    <button class="mob-flag" data-lang="es" onclick="setLang('es')" title="Espa&ntilde;ol" aria-label="Espa&ntilde;ol">\U0001f1ea\U0001f1f8</button>
    <button class="mob-flag" data-lang="en" onclick="setLang('en')" title="English" aria-label="English">\U0001f1fa\U0001f1f8</button>
    <button class="mob-flag" data-lang="pt" onclick="setLang('pt')" title="Portugu&ecirc;s" aria-label="Portugu&ecirc;s">\U0001f1e7\U0001f1f7</button>
    <button class="mob-flag" data-lang="fr" onclick="setLang('fr')" title="Fran&ccedil;ais" aria-label="Fran&ccedil;ais">\U0001f1eb\U0001f1f7</button>
  </div>
  <button class="hamburger" id="hamburgerBtn" onclick="toggleMobileMenu()">
    <span></span><span></span><span></span>
  </button>
</nav>

<!-- MOBILE MENU OVERLAY -->
<div class="mobile-overlay" id="mobileMenu">
  <a href="#features" onclick="closeMobileMenu()" data-i18n="nav.technology">Tecnolog\u00eda</a>
  <a href="#assets" onclick="closeMobileMenu()" data-i18n="nav.assets">Activos</a>
  <a href="#pricing" onclick="closeMobileMenu()" data-i18n="nav.pricing">Precios</a>
  <a href="#en-vivo" onclick="closeMobileMenu()" data-i18n="nav.dashboard">En Vivo</a>
  <a href="https://t.me/BUYSELL_365_24_7" target="_blank" style="background:linear-gradient(135deg,#00d4aa,#00b894);color:#0a0e17;font-weight:700">\U0001f4ac Telegram</a>
  <div class="mob-lang">
    <a onclick="setLang('es');closeMobileMenu()">\U0001f1ea\U0001f1f8</a>
    <a onclick="setLang('en');closeMobileMenu()">\U0001f1fa\U0001f1f8</a>
    <a onclick="setLang('pt');closeMobileMenu()">\U0001f1e7\U0001f1f7</a>
    <a onclick="setLang('fr');closeMobileMenu()">\U0001f1eb\U0001f1f7</a>
  </div>
</div>

<!-- HERO -->
<section class="hero">
  <div class="hero-content">
    <div class="hero-badge"><span class="dot"></span> <span data-i18n="hero.badge">{'Bot activo' if is_alive else 'Dashboard Online'}</span></div>
    <!-- LIVE COUNTER comunidad activa (FIX 2026-05-08) — i18n con data-i18n-vars -->
    <div style="display:inline-flex;align-items:center;gap:6px;margin-top:10px;padding:5px 14px;background:rgba(0,255,204,0.05);border:1px solid rgba(0,255,204,0.18);border-radius:50px;font-size:.78rem;color:#a0aec0">
      <span style="display:inline-block;width:6px;height:6px;background:#00e676;border-radius:50%;animation:pulse 2s infinite"></span>
      <span data-i18n="hero.live_count" data-i18n-vars='{{"N":"{_total_senales}","WR":"{_wr_vip}","P":"{int(_pips_totales):,}"}}'>
        <strong style="color:#00ffcc">{_total_senales}</strong> señales · <strong style="color:#00ffcc">{_wr_vip}%</strong> WR · <strong style="color:#fbbf24">+{int(_pips_totales):,}</strong> pips netos
      </span>
    </div>
    <h1 data-i18n="hero.title">Señales VIP en tiempo real<br><span style="background:linear-gradient(90deg,#00ffc8,#4d9fff);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text">Opera con precisión, 24/7</span></h1>
    <p data-i18n="hero.subtitle">Recibe señales con <strong style="color:#f0f6ff">Entry, Stop Loss y Take Profit exactos</strong> en ORO, NASDAQ, US30, US500, GER40, Forex, BTC y Petróleo. Análisis con IA y alertas al instante \u2014 solo ejecuta y gana.</p>
    <div class="hero-buttons">
      <a href="#pricing" class="btn btn-primary" style="font-size:1.05rem;padding:16px 36px"><span data-i18n="hero.btn_start">\U0001f680 Empezar Ahora</span></a>
      <a href="#en-vivo" class="btn btn-secondary">\U0001f4ca <span data-i18n="hero.btn_dashboard">Rendimiento en Vivo</span></a>
      <a href="https://t.me/BUYSELL_365_24_7" target="_blank" class="btn btn-secondary">\U0001f4e2 <span data-i18n="hero.btn_telegram">Telegram</span></a>
    </div>

    <!-- SHARE BUTTONS — viralidad B2C (FIX 2026-05-08) -->
    <div style="margin-top:18px;display:flex;gap:10px;justify-content:center;flex-wrap:wrap;align-items:center">
      <span style="color:#8b9fc4;font-size:.82rem" data-i18n="hero.share_label">\U0001f4e3 Comparte con amigos:</span>
      <a href="https://wa.me/?text=Mira%20este%20sistema%20de%20se%C3%B1ales%20de%20trading%20con%20IA%20H%C3%ADbrida%20%2B%20Visi%C3%B3n%20que%20da%20%25%20de%20probabilidad%20por%20cada%20se%C3%B1al%3A%20https%3A%2F%2Fbuysell365.pro" target="_blank" rel="noopener" style="display:inline-flex;align-items:center;gap:6px;padding:8px 14px;background:rgba(37,211,102,.15);border:1px solid rgba(37,211,102,.4);border-radius:8px;color:#25d366;font-size:.85rem;text-decoration:none;font-weight:600;min-height:36px"><svg viewBox="0 0 24 24" width="14" height="14" fill="#25d366"><path d="M.057 24l1.687-6.163c-1.041-1.804-1.588-3.849-1.587-5.946.003-6.556 5.338-11.891 11.893-11.891 3.181.001 6.167 1.24 8.413 3.488 2.245 2.248 3.481 5.236 3.48 8.414-.003 6.557-5.338 11.892-11.893 11.892-1.99-.001-3.951-.5-5.688-1.448L.057 24zm6.597-3.807c1.676.995 3.276 1.591 5.392 1.592 5.448 0 9.886-4.434 9.889-9.885.002-5.462-4.415-9.89-9.881-9.892-5.452 0-9.887 4.434-9.889 9.884-.001 2.225.651 3.891 1.746 5.634l-.999 3.648 3.742-.981zm11.387-5.464c-.074-.124-.272-.198-.57-.347-.297-.149-1.758-.868-2.031-.967-.272-.099-.47-.149-.669.149-.198.297-.768.967-.941 1.165-.173.198-.347.223-.644.074-.297-.149-1.255-.462-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.297-.347.446-.521.151-.172.2-.296.3-.495.099-.198.05-.372-.025-.521-.075-.148-.669-1.611-.916-2.206-.242-.579-.487-.501-.669-.51l-.57-.01c-.198 0-.52.074-.792.372s-1.04 1.016-1.04 2.479 1.065 2.876 1.213 3.074c.149.198 2.095 3.2 5.076 4.487.709.306 1.263.489 1.694.626.712.226 1.36.194 1.872.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413z"/></svg>WhatsApp</a>
      <a href="https://t.me/share/url?url=https%3A%2F%2Fbuysell365.pro&amp;text=Sistema%20de%20se%C3%B1ales%20de%20trading%20con%20IA%20H%C3%ADbrida%20%2B%20Visi%C3%B3n%20%2B%20%25%20de%20probabilidad%20por%20cada%20se%C3%B1al" target="_blank" rel="noopener" style="display:inline-flex;align-items:center;gap:6px;padding:8px 14px;background:rgba(0,136,204,.15);border:1px solid rgba(0,136,204,.4);border-radius:8px;color:#0088cc;font-size:.85rem;text-decoration:none;font-weight:600;min-height:36px"><svg viewBox="0 0 24 24" width="14" height="14" fill="#0088cc"><path d="M11.944 0A12 12 0 0 0 0 12a12 12 0 0 0 12 12 12 12 0 0 0 12-12A12 12 0 0 0 12 0a12 12 0 0 0-.056 0zm4.962 7.224c.1-.002.321.023.465.14a.506.506 0 0 1 .171.325c.016.093.036.306.02.472-.18 1.898-.962 6.502-1.36 8.627-.168.9-.499 1.201-.82 1.23-.696.065-1.225-.46-1.9-.902-1.056-.693-1.653-1.124-2.678-1.8-1.185-.78-.417-1.21.258-1.91.177-.184 3.247-2.977 3.307-3.23.007-.032.014-.15-.056-.212s-.174-.041-.249-.024c-.106.024-1.793 1.14-5.061 3.345-.479.33-.913.49-1.302.48-.428-.008-1.252-.241-1.865-.44-.752-.245-1.349-.374-1.297-.789.027-.216.325-.437.893-.663 3.498-1.524 5.83-2.529 6.998-3.014 3.332-1.386 4.025-1.627 4.476-1.635z"/></svg>Telegram</a>
      <button onclick="const t=this;const orig=t.getAttribute('data-i18n')||'hero.copy_link';navigator.clipboard.writeText('https://buysell365.pro').then(()=>{{t.textContent=(window.translations&&window.translations['hero.copied'])||'✓ Copiado';setTimeout(()=>{{const tr=window.translations&&window.translations[orig];t.innerHTML=tr||'\U0001f517 Copiar link';}},2000);}})" data-i18n="hero.copy_link" style="cursor:pointer;display:inline-flex;align-items:center;gap:6px;padding:8px 14px;background:rgba(0,212,170,.15);border:1px solid rgba(0,212,170,.4);border-radius:8px;color:#00d4aa;font-size:.85rem;font-weight:600;min-height:36px;font-family:inherit">\U0001f517 Copiar link</button>
    </div>
    <div class="stats-bar" id="statsBar">
      <div class="stat-item"><div class="stat-value" id="counterWr" data-target="{wr}">0%</div><div class="stat-label" data-i18n="stats.winrate">WIN RATE</div></div>
      <div class="stat-item"><div class="stat-value blue" id="counterTotal" data-target="{total}">0+</div><div class="stat-label" data-i18n="stats.signals">SE\u00d1ALES GENERADAS</div></div>
      <div class="stat-item"><div class="stat-value gold" id="counterPips" data-target="{pips:.0f}">0</div><div class="stat-label" data-i18n="stats.pips">PIPS ACUMULADOS</div></div>
      <div class="stat-item"><div class="stat-value purple">24/7</div><div class="stat-label" data-i18n="stats.analysis">AN\u00c1LISIS ACTIVO</div></div>
    </div>

    <!-- FIX 2026-04-24: Eliminado el DASHBOARD MOCKUP PREVIEW (datos fake 30/03).
         El usuario prefiere que la gente baje al section #en-vivo con data real. -->
  </div>
</section>

<!-- EN VIVO — Sección integrada con datos del dashboard -->
<section class="fade-in" id="en-vivo" style="padding:60px 20px;background:linear-gradient(180deg,transparent,rgba(0,212,170,0.03),transparent)">
  <div class="section-title" style="margin-bottom:32px">
    <h2 style="font-size:clamp(1.8rem,4.5vw,2.6rem)">
      <span style="display:inline-flex;align-items:center;gap:10px">
        <span style="width:12px;height:12px;border-radius:50%;background:{'#00e676' if is_alive else '#8b9fc4'};box-shadow:0 0 14px {'#00e676' if is_alive else '#8b9fc4'};animation:pulse 1.5s infinite"></span>
        <span data-i18n="live.title" style="background:linear-gradient(135deg,#fff 20%,#00ffcc 60%,#4d9fff 100%);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text">Rendimiento en Vivo</span>
      </span>
    </h2>
    <p style="color:#a0aec0;font-size:1rem" data-i18n="live.subtitle">Se&ntilde;ales publicadas en el Canal VIP con TP, cierre parcial y resultado real</p>
    {f'<p style="color:#7a90a8;font-size:0.85rem;margin-top:6px;font-style:italic">{_rango_subtitle}</p>' if _rango_subtitle else ''}
  </div>

  <div style="max-width:1100px;margin:0 auto">

    <!-- STAT CARDS -->
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:14px;margin-bottom:24px">
      <div style="background:linear-gradient(145deg,rgba(22,32,53,0.95),rgba(14,22,40,0.85));border:1px solid rgba(0,212,170,.25);border-radius:14px;padding:18px;text-align:center;box-shadow:0 4px 16px rgba(0,0,0,.25)">
        <div style="font-size:11px;color:#8b9fc4;text-transform:uppercase;letter-spacing:1px;margin-bottom:8px" data-i18n="live.signals">Se&ntilde;ales Totales</div>
        <div style="font-size:2rem;font-weight:900;color:#00d4aa">{_total_senales}</div>
        <div style="font-size:11px;color:#8b9fc4;margin-top:4px">{_wins_count_card}W &bull; {_losses_count_card}L</div>
      </div>
      <div style="background:linear-gradient(145deg,rgba(22,32,53,0.95),rgba(14,22,40,0.85));border:1px solid {_wr_color_live}50;border-radius:14px;padding:18px;text-align:center;box-shadow:0 4px 16px rgba(0,0,0,.25)">
        <div style="font-size:11px;color:#8b9fc4;text-transform:uppercase;letter-spacing:1px;margin-bottom:8px" data-i18n="live.winrate">Win Rate</div>
        <div style="font-size:2rem;font-weight:900;color:{_wr_color_live}">{_wr_vip}%</div>
        <div style="margin-top:6px;height:4px;background:rgba(255,255,255,.06);border-radius:2px;overflow:hidden"><div style="width:{_wr_vip}%;height:100%;background:{_wr_color_live}"></div></div>
      </div>
      <div style="background:linear-gradient(145deg,rgba(22,32,53,0.95),rgba(14,22,40,0.85));border:1px solid {_profit_color_live}50;border-radius:14px;padding:18px;text-align:center;box-shadow:0 4px 16px rgba(0,0,0,.25)">
        <div style="font-size:11px;color:#8b9fc4;text-transform:uppercase;letter-spacing:1px;margin-bottom:8px" data-i18n="live.profit">Net Pips</div>
        <div style="font-size:2rem;font-weight:900;color:{_profit_color_live}">{_pips_totales:+.1f}</div>
        <div style="font-size:10px;color:#8b9fc4;margin-top:4px;line-height:1.4">+{_pips_ganados:.0f} won &bull; -{_pips_perdidos:.0f} lost<br>VIP channel net total</div>
      </div>
      <!-- FIX 2026-05-11: card "Racha Ganadora" eliminado por peticion del usuario.
           El grid auto-fit reacomoda los 3 cards restantes a ancho mayor. -->
      <!-- FIX 2026-05-20: card LIVE TODAY — visibiliza actividad del dia en curso
           para complementar los agregados (que excluyen hoy por politica). -->
      <div style="background:linear-gradient(145deg,rgba(22,32,53,0.95),rgba(14,22,40,0.85));border:1px solid {_today_color}50;border-radius:14px;padding:18px;text-align:center;box-shadow:0 4px 16px rgba(0,0,0,.25);position:relative">
        <div style="position:absolute;top:8px;right:10px;display:flex;align-items:center;gap:4px;font-size:9px;color:#ff3b30;font-weight:700;letter-spacing:1px">
          <span style="display:inline-block;width:6px;height:6px;background:#ff3b30;border-radius:50%;animation:pulse-live 1.4s infinite"></span>LIVE
        </div>
        <div style="font-size:11px;color:#8b9fc4;text-transform:uppercase;letter-spacing:1px;margin-bottom:8px">En Vivo Hoy</div>
        <div style="font-size:2rem;font-weight:900;color:{_today_color}">{pips_today_real:+.0f}</div>
        <div style="font-size:10px;color:#8b9fc4;margin-top:4px;line-height:1.4">{_today_tps}&#9989; &middot; {_today_sls}&#128721; &middot; {_today_partials}&#9889;<br>pips · WR {_today_wr:.0f}%</div>
      </div>
    </div>
    <style>@keyframes pulse-live{{0%,100%{{opacity:1}}50%{{opacity:.3}}}}</style>

    <!-- RECENT HISTORY (ancho completo) -->
    <div style="margin-bottom:28px">
      <div style="background:linear-gradient(145deg,rgba(22,32,53,0.95),rgba(14,22,40,0.85));border:1px solid rgba(255,255,255,.08);border-radius:14px;padding:20px">
        <div style="display:flex;align-items:center;gap:8px;font-weight:800;color:#fff;font-size:14px;margin-bottom:14px">
          <span style="font-size:16px">&#128200;</span>
          <span data-i18n="live.history_title">Se&ntilde;ales Recientes del Canal VIP</span>
          <span style="margin-left:auto;font-size:11px;color:#8b9fc4">&Uacute;ltimas {_RECENT_TRADES_LIMIT}</span>
        </div>
        <div style="overflow-x:auto">
          <table style="width:100%;border-collapse:collapse;font-size:12px">
            <thead>
              <tr style="color:#8b9fc4;border-bottom:1px solid rgba(255,255,255,.08)">
                <td style="padding:8px;font-weight:600" data-i18n="live.col_date">Fecha</td>
                <td style="padding:8px;font-weight:600" data-i18n="live.col_asset">Activo</td>
                <td style="padding:8px;font-weight:600" data-i18n="live.col_type">Tipo</td>
                <td style="padding:8px;font-weight:600" data-i18n="live.col_pips">Pips</td>
                <td style="padding:8px;text-align:right;font-weight:600" data-i18n="live.col_result">Resultado</td>
              </tr>
            </thead>
            <tbody>{_recent_trades_html}</tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- ASSET PERFORMANCE — eliminado por solicitud usuario 2026-04-26 -->

    <!-- CTA TELEGRAM -->
    <div style="text-align:center;margin-top:24px">
      <a href="https://t.me/BUYSELL_365_24_7" target="_blank" style="display:inline-block;padding:14px 32px;background:linear-gradient(135deg,#0088cc,#00a3e0);color:#fff;border-radius:30px;font-weight:700;text-decoration:none;box-shadow:0 4px 20px rgba(0,136,204,.3);transition:all .3s" data-i18n="live.cta_telegram">&#128172; Recibe las se&ntilde;ales en Telegram &rarr;</a>
    </div>
  </div>
</section>

<!-- PRICING — Visible inmediatamente después del Hero -->
<section class="pricing fade-in" id="pricing">
  <div class="section-title" style="margin-bottom:48px">
    <h2 style="font-size:clamp(2rem,5vw,3rem);filter:drop-shadow(0 0 20px rgba(251,191,36,0.2))">\U0001f4b0 <span data-i18n="pricing.title" style="background:linear-gradient(135deg,#fff 20%,#fbbf24 50%,#00ffcc 80%);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text">Servicios y Planes</span></h2>
    <p data-i18n="pricing.subtitle" style="font-size:1.15rem;color:#a0aec0">Elige el plan que mejor se adapte a tu estilo de trading.</p>
  </div>
  <div class="pricing-cards">
    <div class="price-card" style="border:1px solid rgba(77,159,255,0.3);box-shadow:0 0 30px rgba(77,159,255,0.1)">
      <div class="price-name" style="color:#4d9fff;font-size:1.4rem" data-i18n="pricing.community">Comunidad</div>
      <div class="price-amount" style="color:#4d9fff;text-shadow:0 0 30px rgba(77,159,255,0.6);font-size:2rem" data-i18n="pricing.free">Acceso Libre</div>
      <p style="color:#b0bec5;margin-bottom:16px" data-i18n="pricing.community_desc">Acceso al grupo p\u00fablico de Telegram</p>
      <ul class="price-list">
        <li style="color:#90caf9" data-i18n="pricing.cm1">&#128227; Resumen diario de mercado</li>
        <li style="color:#90caf9" data-i18n="pricing.cm2">&#128218; Educaci\u00f3n y an\u00e1lisis general</li>
        <li style="color:#90caf9" data-i18n="pricing.cm3">&#129309; Soporte de la comunidad</li>
        <li style="color:#90caf9" data-i18n="pricing.cm4">&#128202; Dashboard p\u00fablico limitado</li>
      </ul>
      <a href="https://t.me/BUYSELL_365_24_7" target="_blank" style="display:block;width:100%;text-align:center;margin-top:16px;padding:16px 24px;background:linear-gradient(135deg,#1565c0,#42a5f5,#64b5f6);border-radius:14px;color:#fff;font-weight:800;font-size:1.1rem;text-decoration:none;cursor:pointer;box-shadow:0 4px 25px rgba(66,165,245,0.5);transition:all .3s"><span data-i18n="pricing.join_group">&#128172; Unirse al Grupo</span></a>
    </div>
    <div class="price-card featured" style="border:2px solid #fbbf24;box-shadow:0 0 50px rgba(251,191,36,0.3),0 0 100px rgba(251,191,36,0.1);transform:scale(1.04)">
      <div class="price-badge" style="background:linear-gradient(135deg,#ff6d00,#fbbf24);box-shadow:0 0 30px rgba(251,191,36,.8);animation:pulse 2s infinite;font-size:15px;padding:10px 32px">\U0001f525 <span data-i18n="pricing.badge">50% OFF \u2014 LANZAMIENTO</span></div>
      <div class="price-name" style="color:#fbbf24;font-size:1.5rem;text-shadow:0 0 20px rgba(251,191,36,0.3)" data-i18n="pricing.vip">VIP Pro</div>
      <!-- Stats banner: numeros reales del canal VIP -->
      <div style="margin:10px auto 6px;padding:10px 14px;background:rgba(0,255,204,0.08);border:1px solid rgba(0,255,204,0.35);border-radius:10px;display:inline-flex;flex-direction:column;gap:4px;font-size:0.78rem;line-height:1.3">
        <div style="color:#00ffc8;font-weight:800;letter-spacing:1px;text-transform:uppercase">
          \U0001f680 <span data-i18n="pricing.stats_label">Estad\u00edsticas reales del canal</span>
        </div>
        <div style="color:#fff;font-weight:700">
          <span data-i18n="pricing.stats_signals">~18 se\u00f1ales/d\u00eda</span> &middot;
          <span data-i18n="pricing.stats_weekdays">Lunes a Viernes</span> &middot;
          <span data-i18n="pricing.stats_monthly">~390/mes</span> &middot;
          <span style="color:#22c55e" data-i18n="pricing.stats_wr">70% Win Rate</span>
        </div>
      </div>
      <div class="price-amount" style="font-size:3.8rem;text-shadow:0 0 30px rgba(255,255,255,0.1)">
        <span class="old" data-i18n="pricing.old_price">€299/mes</span>
        €149<span data-i18n="pricing.month">/mes</span>
      </div>
      <div style="margin:12px 0 4px;font-size:12px;color:rgba(255,215,0,.7);letter-spacing:1px" data-i18n="pricing.founders">&#9733; PRECIO ESPECIAL FUNDADORES &#9733;</div>
      <!-- COUNTDOWN urgencia (FIX 2026-05-08) -->
      <div id="vipCountdown" style="margin:12px auto 0;padding:10px 16px;background:rgba(255,107,53,0.12);border:1px solid rgba(255,107,53,0.4);border-radius:10px;display:inline-flex;align-items:center;gap:8px;font-size:.78rem;flex-wrap:wrap;justify-content:center">
        <span style="color:#ff8a8a;font-weight:700">⏳</span>
        <span style="color:#ff8a8a;font-weight:600" data-i18n="pricing.countdown_label">Oferta termina en:</span>
        <span id="vipCountdownTimer" style="color:#fff;font-weight:800;font-family:monospace;letter-spacing:.5px">--d --h --m</span>
      </div>
      <ul class="price-list" style="margin-top:20px">
        <li style="color:#ffd740" data-i18n="pricing.vp_hybrid">&#129504; Sistema H\u00edbrido IA + Visi\u00f3n exclusivo VIP</li>
        <li style="color:#ffd740" data-i18n="pricing.vp_probability">&#128202; % de probabilidad en cada se\u00f1al</li>
        <li style="color:#ffd740" data-i18n="pricing.vp1">&#128293; Se\u00f1ales en tiempo real con Entry/SL/TPs exactos</li>
        <li style="color:#ffd740" data-i18n="pricing.vp2">&#128081; Canal VIP privado de Telegram</li>
        <li style="color:#ffd740" data-i18n="pricing.vp3">&#129302; Acceso total al bot de trading y an\u00e1lisis bajo petici\u00f3n</li>
        <li style="color:#ffd740" data-i18n="pricing.vp4">&#9889; Alertas instant\u00e1neas \u2014 nunca pierdas una se\u00f1al</li>
        <li style="color:#ffd740" data-i18n="pricing.vp5">&#128161; Soporte prioritario directo</li>
        <li style="color:#ffd740" data-i18n="pricing.vp7">&#128200; Briefing matutino + Cierre nocturno</li>
        <li style="color:#22c55e;border-top:1px dashed rgba(255,215,64,0.25);padding-top:10px;margin-top:8px" data-i18n="pricing.vp_wsp_addon">&#128241; <strong>Complemento WhatsApp Alerts</strong> &mdash; opcional &euro;19/mes (ver abajo)</li>
      </ul>
      <a href="https://t.me/Andoperandobot?start=vip" target="_blank" style="display:block;width:100%;text-align:center;margin-top:16px;padding:18px 24px;background:linear-gradient(135deg,#ff6d00,#ffd740,#ffab00);border-radius:14px;color:#000;font-weight:900;font-size:1.15rem;text-decoration:none;cursor:pointer;box-shadow:0 4px 30px rgba(255,215,64,0.5);transition:all .3s"><span data-i18n="pricing.subscribe_vip">&#128081; Suscribirme al VIP</span></a>
    </div>
    <!-- FIX 2026-04-26: card 'Acceso Gratis VIP' alineado a los otros 2.
         Sin emoji gigante ni texto extra que rompiera uniformidad mobile.
         Sin ✅ en HTML (CSS ya pone uno automatico via ::before). -->
    <div class="price-card" style="border:2px solid #00e676;box-shadow:0 0 30px rgba(0,230,118,0.18);position:relative">
      <div class="price-badge" style="background:linear-gradient(135deg,#00c853,#00e676);box-shadow:0 0 25px rgba(0,230,118,.6);font-size:14px;padding:8px 24px;animation:pulse 2s infinite">&#127873; <span data-i18n="pricing.trial_badge">TRIAL EXCLUSIVO</span></div>
      <div class="price-name" style="color:#00e676;font-size:1.5rem;text-shadow:0 0 20px rgba(0,230,118,0.3)" data-i18n="pricing.trial">Trial 24h</div>
      <div class="price-amount" style="color:#00e676;text-shadow:0 0 30px rgba(0,230,118,0.5);font-size:3rem" data-i18n="pricing.free_trial">24H GRATIS</div>
      <div style="margin:12px 0 4px;font-size:12px;color:rgba(0,230,118,.7);letter-spacing:1px" data-i18n="pricing.trial_subtitle">&#9733; PRUEBA EL SISTEMA H&Iacute;BRIDO COMPLETO &#9733;</div>
      <ul class="price-list" style="margin-top:20px">
        <li style="color:#a7f3d0" data-i18n="pricing.tr_hybrid">&#129504; Sistema H&iacute;brido IA + Visi&oacute;n completo</li>
        <li style="color:#a7f3d0" data-i18n="pricing.tr_prob">&#128202; % de probabilidad en cada se&ntilde;al</li>
        <li style="color:#a7f3d0" data-i18n="pricing.tr2">Mismas se&ntilde;ales que el VIP de pago</li>
        <li style="color:#a7f3d0" data-i18n="pricing.tr3">Sin tarjeta de cr&eacute;dito</li>
        <li style="color:#a7f3d0" data-i18n="pricing.tr4">Sin compromiso</li>
        <li style="color:#a7f3d0" data-i18n="pricing.tr_24h">&#9201;&#65039; Acceso completo de 24 horas</li>
      </ul>
      <a href="https://t.me/BuySell365traiding?text=Hola%2C+quiero+probar+el+trial+24h+del+VIP" target="_blank" style="display:block;width:100%;text-align:center;margin-top:16px;padding:18px 24px;background:linear-gradient(135deg,#00c853,#00e676,#69f0ae);border-radius:14px;color:#000;font-weight:900;font-size:1.15rem;text-decoration:none;cursor:pointer;box-shadow:0 4px 30px rgba(0,230,118,0.5);transition:all .3s"><span data-i18n="pricing.contact_admin">&#128172; Solicitar Trial 24h</span></a>
    </div>
  </div>
</section>

<!-- ═══ WHATSAPP ALERTS PERSONAL — Complemento exclusivo VIPs ═══ -->
<section id="whatsapp-alerts" class="fade-in" style="padding:80px 20px;background:linear-gradient(180deg,rgba(37,211,102,0.04) 0%,var(--bg2) 50%,rgba(37,211,102,0.06) 100%)">
  <div style="max-width:1080px;margin:0 auto">
    <div style="text-align:center;margin-bottom:36px">
      <div style="display:inline-block;padding:6px 16px;border:1px solid rgba(37,211,102,0.5);border-radius:999px;background:rgba(37,211,102,0.08);color:#25d366;font-size:0.78rem;font-weight:800;letter-spacing:2px;text-transform:uppercase;margin-bottom:14px">
        <span data-i18n="wsp.badge">&#9989; Complemento exclusivo para VIPs activos</span>
      </div>
      <h2 style="font-size:2.4rem;font-weight:900;color:#fff;line-height:1.15;margin:0 0 12px;letter-spacing:-0.5px">
        &#128241; <span data-i18n="wsp.title">WhatsApp Alerts Personal</span>
      </h2>
      <p style="font-size:1.1rem;color:#a0aec0;max-width:680px;margin:0 auto 18px;line-height:1.6">
        <span data-i18n="wsp.subtitle">Recibe las se&ntilde;ales que T&Uacute; elijas, en TU WhatsApp, al instante. Sin abrir Telegram, sin perder ninguna.</span>
      </p>
      <!-- Stats reales — justifica que €19/mes es muy barato por el volumen de señales -->
      <div style="display:inline-flex;flex-wrap:wrap;justify-content:center;gap:12px 18px;padding:14px 22px;background:rgba(37,211,102,0.08);border:1px solid rgba(37,211,102,0.35);border-radius:12px;font-size:0.92rem;line-height:1.4">
        <div style="color:#cbd5e0">
          <span style="color:#25d366;font-weight:800">\U0001f680</span>
          <strong style="color:#fff">~18</strong>
          <span data-i18n="wsp.stats_per_day">señales/día (Lun-Vie)</span>
        </div>
        <div style="color:#475569;font-weight:300">|</div>
        <div style="color:#cbd5e0">
          <span style="color:#25d366;font-weight:800">\U0001f4c5</span>
          <strong style="color:#fff">~390</strong>
          <span data-i18n="wsp.stats_per_month">señales/mes</span>
        </div>
        <div style="color:#475569;font-weight:300">|</div>
        <div style="color:#cbd5e0">
          <span style="color:#22c55e;font-weight:800">\U0001f3af</span>
          <strong style="color:#fff">70%</strong>
          <span data-i18n="wsp.stats_winrate">Win Rate</span>
        </div>
        <div style="color:#475569;font-weight:300">|</div>
        <div style="color:#cbd5e0">
          <span style="color:#fbbf24;font-weight:800">\U0001f4b0</span>
          <span data-i18n="wsp.stats_cost_intro">Sólo</span>
          <strong style="color:#fff">&euro;0,05</strong>
          <span data-i18n="wsp.stats_cost_outro">por señal</span>
        </div>
      </div>
    </div>

    <div style="display:grid;grid-template-columns:1.05fr 0.95fr;gap:32px;align-items:stretch">
      <!-- Columna izquierda: Beneficios -->
      <div style="background:rgba(13,17,23,0.6);border:1px solid rgba(37,211,102,0.25);border-radius:18px;padding:28px 28px;box-shadow:0 0 40px rgba(37,211,102,0.06)">
        <h3 style="color:#25d366;font-size:1.15rem;margin:0 0 18px;font-weight:800">
          <span data-i18n="wsp.benefits_title">&#127919; T&uacute; mismo configuras tu servicio</span>
        </h3>
        <ul style="list-style:none;padding:0;margin:0;display:flex;flex-direction:column;gap:14px">
          <li style="display:flex;gap:12px;color:#cbd5e0;font-size:0.98rem;line-height:1.5">
            <span style="color:#25d366;font-size:1.2rem;flex-shrink:0">&#9989;</span>
            <span><strong data-i18n="wsp.b1_title">T&Uacute; eliges los activos:</strong> <span data-i18n="wsp.b1_desc">ORO, CRIPTO, DIVISAS, &Iacute;NDICES o todo junto</span></span>
          </li>
          <li style="display:flex;gap:12px;color:#cbd5e0;font-size:0.98rem;line-height:1.5">
            <span style="color:#25d366;font-size:1.2rem;flex-shrink:0">&#9989;</span>
            <span><strong data-i18n="wsp.b2_title">T&Uacute; eliges los avisos:</strong> <span data-i18n="wsp.b2_desc">se&ntilde;al nueva, Stop en Entrada, objetivos, cierres</span></span>
          </li>
          <li style="display:flex;gap:12px;color:#cbd5e0;font-size:0.98rem;line-height:1.5">
            <span style="color:#25d366;font-size:1.2rem;flex-shrink:0">&#9989;</span>
            <span><strong data-i18n="wsp.b3_title">Notificaci&oacute;n push al instante:</strong> <span data-i18n="wsp.b3_desc">no necesitas abrir Telegram para no perder nada</span></span>
          </li>
          <li style="display:flex;gap:12px;color:#cbd5e0;font-size:0.98rem;line-height:1.5">
            <span style="color:#25d366;font-size:1.2rem;flex-shrink:0">&#9989;</span>
            <span><strong data-i18n="wsp.b4_title">Mensaje directo desde nuestro n&uacute;mero Business:</strong> <span data-i18n="wsp.b4_desc">aspecto profesional, sin spam ni grupos</span></span>
          </li>
          <li style="display:flex;gap:12px;color:#cbd5e0;font-size:0.98rem;line-height:1.5">
            <span style="color:#25d366;font-size:1.2rem;flex-shrink:0">&#9989;</span>
            <span><strong data-i18n="wsp.b5_title">Cancela o ajusta cuando quieras:</strong> <span data-i18n="wsp.b5_desc">cambia activos y avisos cuando lo necesites, sin coste extra</span></span>
          </li>
        </ul>
      </div>

      <!-- Columna derecha: Tarifa + CTA -->
      <div style="background:linear-gradient(135deg,rgba(37,211,102,0.10),rgba(37,211,102,0.03));border:2px solid #25d366;border-radius:18px;padding:32px 28px;box-shadow:0 0 50px rgba(37,211,102,0.18);display:flex;flex-direction:column;justify-content:center;text-align:center">
        <div style="font-size:0.78rem;color:#25d366;letter-spacing:2px;text-transform:uppercase;font-weight:800;margin-bottom:14px" data-i18n="wsp.price_label">Tarifa</div>
        <div style="font-size:3.6rem;color:#fff;font-weight:900;line-height:1;text-shadow:0 0 30px rgba(37,211,102,0.4)">
          &euro;19<span style="font-size:1rem;color:#a0aec0;font-weight:600" data-i18n="wsp.month">/mes</span>
        </div>

        <div style="margin:22px auto 0;padding:14px 18px;background:rgba(37,211,102,0.10);border:1px dashed rgba(37,211,102,0.5);border-radius:12px;max-width:340px">
          <div style="font-size:0.85rem;color:#25d366;font-weight:800;letter-spacing:1px;text-transform:uppercase;margin-bottom:6px">
            &#127873; <span data-i18n="wsp.bonus_label">Bonus de fidelidad</span>
          </div>
          <div style="font-size:0.95rem;color:#cbd5e0;line-height:1.5">
            <span data-i18n="wsp.bonus_desc">Si renuevas ANTES de que venza tu mes, el siguiente cuesta solo</span>
            <strong style="color:#fff">&euro;15,20</strong>
            <span data-i18n="wsp.bonus_off">(&minus;20%)</span>
          </div>
        </div>

        <div style="margin-top:20px;padding:10px 14px;background:rgba(245,158,11,0.10);border:1px solid rgba(245,158,11,0.4);border-radius:10px">
          <span style="color:#fbbf24;font-weight:700;font-size:0.9rem">&#9889; <span data-i18n="wsp.limited">Plazas limitadas &mdash; servicio configurado a medida</span></span>
        </div>

        <a href="https://t.me/BuySell365traiding?text=Hola%2C+soy+VIP+y+quiero+contratar+WhatsApp+Alerts+Personal" target="_blank"
           style="display:block;margin-top:24px;padding:16px 24px;background:linear-gradient(135deg,#25d366,#128c7e);border-radius:14px;color:#fff;font-weight:900;font-size:1.05rem;text-decoration:none;box-shadow:0 4px 30px rgba(37,211,102,0.5);transition:all .3s">
          <span data-i18n="wsp.cta">&#128172; Hablar con el administrador</span>
        </a>

        <p style="margin-top:14px;font-size:0.78rem;color:#94a3b8;line-height:1.5">
          <span data-i18n="wsp.requires_vip">&#9888;&#65039; Servicio exclusivo para VIPs activos. Necesitas estar suscrito al plan VIP Pro.</span>
        </p>
      </div>
    </div>
  </div>

  <style>
    @media(max-width:880px){{
      #whatsapp-alerts .pricing-cards,
      #whatsapp-alerts > div > div:nth-child(2){{grid-template-columns:1fr !important}}
    }}
  </style>
</section>

<!-- ═══ XM PARTNER — SECCION BIG (FIX 2026-04-27) ═══ -->
<section id="partners" class="fade-in" style="padding:80px 20px">
  <div style="max-width:920px;margin:0 auto;padding:48px 32px;border:2px solid #fbbf24;border-radius:24px;background:linear-gradient(135deg,rgba(251,191,36,0.10),rgba(0,255,204,0.04));box-shadow:0 0 60px rgba(251,191,36,0.18);text-align:center;position:relative;overflow:hidden">
    <div style="font-size:0.85rem;font-weight:800;color:#fbbf24;letter-spacing:2px;margin-bottom:14px;text-transform:uppercase">
      \U0001f91d <span data-i18n="footer.xm_partner_label">Socio Oficial XM Global</span>
    </div>
    <h2 style="font-size:2.4rem;font-weight:900;color:#fff;line-height:1.2;margin:0 0 16px;letter-spacing:-0.5px">
      <span data-i18n="partners.headline">Opera con el broker oficial de BuySell365</span>
    </h2>
    <p style="font-size:1.05rem;color:var(--text2);max-width:640px;margin:0 auto 8px;line-height:1.6">
      <span data-i18n="partners.subline">El mismo broker que usamos para nuestro análisis VIP.</span>
    </p>
    <p style="font-size:0.95rem;color:var(--text2);max-width:640px;margin:0 auto 24px;line-height:1.6">
      <span data-i18n="footer.xm_intro">Operamos con XM Global. Abre tu cuenta real con nuestro código de afiliado oficial:</span>
    </p>

    <div style="margin:24px auto 28px;padding:22px 32px;background:rgba(0,255,204,0.06);border:1px dashed rgba(0,255,204,0.45);border-radius:14px;display:inline-block">
      <div style="font-size:0.78rem;color:var(--text2);margin-bottom:6px;letter-spacing:2px;text-transform:uppercase">
        <span data-i18n="footer.xm_code_label">Código</span>
      </div>
      <div style="font-size:2.6rem;font-weight:900;color:#00ffcc;letter-spacing:5px;font-family:'Courier New',monospace;text-shadow:0 0 20px rgba(0,255,204,0.4)">BUYSELL365</div>
    </div>

    <div style="display:flex;gap:14px;justify-content:center;flex-wrap:wrap;margin:8px 0 14px">
      <a href="https://clicks.pipaffiliates.com/c?c=1197262&amp;l=en&amp;p=1" target="_blank" rel="nofollow noopener"
         style="padding:14px 32px;background:linear-gradient(135deg,#fbbf24,#f59e0b);color:#07091f;border-radius:12px;font-weight:800;font-size:1rem;text-decoration:none;letter-spacing:0.3px;box-shadow:0 4px 20px rgba(251,191,36,0.4);transition:transform .2s">
        \U0001f7e2 <span data-i18n="footer.xm_open_btn">Abrir Cuenta</span>
      </a>
      <a href="https://clicks.pipaffiliates.com/c?c=1197262&amp;l=en&amp;p=17" target="_blank" rel="nofollow noopener"
         style="padding:14px 32px;background:transparent;border:2px solid #fbbf24;color:#fbbf24;border-radius:12px;font-weight:700;font-size:1rem;text-decoration:none">
        \U0001f4f2 <span data-i18n="footer.xm_app_btn">App XM Global</span>
      </a>
      <a href="https://clicks.pipaffiliates.com/c?c=1197262&amp;l=en&amp;p=6" target="_blank" rel="nofollow noopener"
         style="padding:14px 32px;background:transparent;border:2px solid #00ffcc;color:#00ffcc;border-radius:12px;font-weight:700;font-size:1rem;text-decoration:none">
        \U0001f381 <span data-i18n="footer.xm_bonuses_btn">Bonificaciones</span>
      </a>
    </div>

    <p style="font-size:0.78rem;color:#f87171;font-weight:600;margin:18px 0 0">
      ℹ️ <span data-i18n="footer.xm_disclaimer">Servicio no disponible para residentes de España.</span>
    </p>
  </div>
</section>

<!-- HOW IT WORKS -->
<section class="fade-in" id="how-it-works" style="padding:60px 20px">
  <div class="section-title" style="margin-bottom:32px">
    <h2>\U0001f680 <span data-i18n="how.title">C\u00f3mo Funciona</span></h2>
    <p data-i18n="how.subtitle">En 3 simples pasos empiezas a recibir se\u00f1ales</p>
  </div>
  <div style="display:flex;flex-wrap:wrap;justify-content:center;gap:24px;max-width:1000px;margin:0 auto">
    <div style="flex:1;min-width:250px;max-width:320px;background:linear-gradient(135deg,rgba(0,255,200,.05),rgba(0,100,255,.05));border:1px solid rgba(0,212,170,.2);border-radius:16px;padding:28px;text-align:center;position:relative">
      <div style="position:absolute;top:-16px;left:50%;transform:translateX(-50%);background:linear-gradient(135deg,#00ffc8,#00d4aa);color:#000;width:36px;height:36px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-weight:900;font-size:18px">1</div>
      <div style="font-size:2.5rem;margin:12px 0">\U0001f4f2</div>
      <h3 style="color:#00ffc8;font-size:1.1rem;margin-bottom:8px" data-i18n="how.step1_title">\u00danete al Canal VIP</h3>
      <p style="color:var(--text2);font-size:.9rem" data-i18n="how.step1_desc">Activa el canal VIP y obt\u00e9n acceso completo al bot de trading y soporte prioritario.</p>
    </div>
    <div style="flex:1;min-width:250px;max-width:320px;background:linear-gradient(135deg,rgba(255,200,0,.05),rgba(255,150,0,.05));border:1px solid rgba(255,200,0,.2);border-radius:16px;padding:28px;text-align:center;position:relative">
      <div style="position:absolute;top:-16px;left:50%;transform:translateX(-50%);background:linear-gradient(135deg,#ffd700,#f0b90b);color:#000;width:36px;height:36px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-weight:900;font-size:18px">2</div>
      <div style="font-size:2.5rem;margin:12px 0">\U0001f4ca</div>
      <h3 style="color:#ffd700;font-size:1.1rem;margin-bottom:8px" data-i18n="how.step2_title">Recibe Se\u00f1ales</h3>
      <p style="color:var(--text2);font-size:.9rem" data-i18n="how.step2_desc">Nuestra IA analiza +20 activos cada 3 minutos y te env\u00eda se\u00f1ales con Entry, TP y SL exactos.</p>
    </div>
    <div style="flex:1;min-width:250px;max-width:320px;background:linear-gradient(135deg,rgba(0,230,118,.05),rgba(0,180,90,.05));border:1px solid rgba(0,230,118,.2);border-radius:16px;padding:28px;text-align:center;position:relative">
      <div style="position:absolute;top:-16px;left:50%;transform:translateX(-50%);background:linear-gradient(135deg,#00e676,#00c853);color:#000;width:36px;height:36px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-weight:900;font-size:18px">3</div>
      <div style="font-size:2.5rem;margin:12px 0">\U0001f4b0</div>
      <h3 style="color:#00e676;font-size:1.1rem;margin-bottom:8px" data-i18n="how.step3_title">Opera a tu ritmo</h3>
      <p style="color:var(--text2);font-size:.9rem" data-i18n="how.step3_desc">Sigue las se\u00f1ales en el canal VIP. T\u00fa decides cu\u00e1ndo entrar y salir.</p>
    </div>
  </div>
</section>

<!-- FEATURES -->
<section class="features fade-in" id="features" style="padding:40px 20px">
  <div class="section-title" style="margin-bottom:24px">
    <h2>\U0001f9e0 <span data-i18n="features.title">Tecnolog\u00eda Institucional</span></h2>
  </div>
  <div class="features-grid" style="gap:16px">
    <div class="feature-card" style="padding:20px">
      <div class="feature-icon green">\U0001f9e0</div>
      <h3 data-i18n="features.hybrid.title">Sistema H\u00edbrido IA + Visi\u00f3n</h3>
      <p data-i18n="features.hybrid.desc">Cada se\u00f1al pasa por dos capas independientes: an\u00e1lisis t\u00e9cnico instant\u00e1neo (EMA, RSI, ATR, R:R) sobre datos de mercado en vivo, m\u00e1s Claude Sonnet 4.6 con Visi\u00f3n leyendo la gr\u00e1fica real como un trader profesional. El resultado: un % de probabilidad por se\u00f1al, antes de publicarla.</p>
    </div>
    <div class="feature-card" style="padding:20px">
      <div class="feature-icon gold">\U0001f4ca</div>
      <h3 data-i18n="features.probability.title">Probabilidad por Se\u00f1al</h3>
      <p data-i18n="features.probability.desc">Cada operaci\u00f3n incluye un score 0\u201395% calculado en tiempo real al momento de la entrada. Ya no operas a ciegas \u2014 sabes la probabilidad estad\u00edstica antes de decidir.</p>
    </div>
    <div class="feature-card" style="padding:20px">
      <div class="feature-icon blue">\U0001f4f0</div>
      <h3 data-i18n="features.finbert.title">An\u00e1lisis de Noticias</h3>
      <p data-i18n="features.finbert.desc">Sistema de procesamiento de lenguaje natural que analiza noticias financieras en tiempo real. Si el sentimiento contradice la se\u00f1al, se bloquea autom\u00e1ticamente.</p>
    </div>
    <div class="feature-card" style="padding:20px">
      <div class="feature-icon purple">\U0001f3e6</div>
      <h3 data-i18n="features.cot.title">Datos Institucionales</h3>
      <p data-i18n="features.cot.desc">Datos de posicionamiento de grandes fondos (CFTC) actualizados semanalmente. Detecta si las instituciones est\u00e1n comprando o vendiendo para alinear las se\u00f1ales.</p>
    </div>
    <div class="feature-card" style="padding:20px">
      <div class="feature-icon green">\u26a1</div>
      <h3 data-i18n="features.mt5.title">Se\u00f1ales Listas para Ejecutar</h3>
      <p data-i18n="features.mt5.desc">Cada se\u00f1al llega con entry, Stop Loss y TPs precalculados \u2014 listas para copiar y ejecutar manualmente en tu broker preferido.</p>
    </div>
    <div class="feature-card" style="padding:20px">
      <div class="feature-icon blue">\U0001f6e1\ufe0f</div>
      <h3 data-i18n="features.risk.title">Control de Riesgo</h3>
      <p data-i18n="features.risk.desc">Stop Loss calculado con volatilidad real (ATR), l\u00edmite de operaciones simult\u00e1neas, pausa por activo tras p\u00e9rdidas y protecci\u00f3n autom\u00e1tica ante drawdown excesivo.</p>
    </div>
  </div>
</section>

<!-- ABOUT / TRUST BAR -->
<section id="about" class="fade-in" style="padding:30px 20px;background:var(--bg2)">
  <div style="max-width:1000px;margin:0 auto;display:flex;flex-wrap:wrap;justify-content:center;gap:16px">
    <div style="background:linear-gradient(145deg,rgba(22,32,53,0.95),rgba(14,22,40,0.85));border:1px solid rgba(0,255,204,0.2);border-radius:16px;padding:20px 28px;text-align:center;flex:1;min-width:160px;box-shadow:0 0 20px rgba(0,255,204,0.06)">
      <div style="font-size:1.8rem;font-weight:900;color:#00ffcc;text-shadow:0 0 20px rgba(0,255,204,0.6)">IA + Visi\u00f3n</div>
      <div style="font-size:.75rem;color:var(--text2);text-transform:uppercase;letter-spacing:1px;margin-top:4px" data-i18n="about.stat_ai">Sistema H\u00edbrido</div>
    </div>
    <div style="background:linear-gradient(145deg,rgba(22,32,53,0.95),rgba(14,22,40,0.85));border:1px solid rgba(0,230,118,0.2);border-radius:16px;padding:20px 28px;text-align:center;flex:1;min-width:140px;box-shadow:0 0 20px rgba(0,230,118,0.06)">
      <div style="font-size:1.8rem;font-weight:900;color:#00e676;text-shadow:0 0 20px rgba(0,230,118,0.6)">% por se\u00f1al</div>
      <div style="font-size:.75rem;color:var(--text2);text-transform:uppercase;letter-spacing:1px;margin-top:4px" data-i18n="about.stat_probability">Probabilidad</div>
    </div>
    <div style="background:linear-gradient(145deg,rgba(22,32,53,0.95),rgba(14,22,40,0.85));border:1px solid rgba(77,159,255,0.2);border-radius:16px;padding:20px 28px;text-align:center;flex:1;min-width:140px;box-shadow:0 0 20px rgba(77,159,255,0.06)">
      <div style="font-size:1.8rem;font-weight:900;color:#4d9fff;text-shadow:0 0 20px rgba(77,159,255,0.6)">24/7</div>
      <div style="font-size:.75rem;color:var(--text2);text-transform:uppercase;letter-spacing:1px;margin-top:4px" data-i18n="about.stat_monitor">Monitoreo</div>
    </div>
    <div style="background:linear-gradient(145deg,rgba(22,32,53,0.95),rgba(14,22,40,0.85));border:1px solid rgba(167,139,250,0.2);border-radius:16px;padding:20px 28px;text-align:center;flex:1;min-width:140px;box-shadow:0 0 20px rgba(167,139,250,0.06)">
      <div style="font-size:1.8rem;font-weight:900;color:#a78bfa;text-shadow:0 0 20px rgba(167,139,250,0.6)">+15</div>
      <div style="font-size:.75rem;color:var(--text2);text-transform:uppercase;letter-spacing:1px;margin-top:4px" data-i18n="about.stat_verified">Activos Cubiertos</div>
    </div>
    <div style="background:linear-gradient(145deg,rgba(22,32,53,0.95),rgba(14,22,40,0.85));border:1px solid rgba(251,191,36,0.2);border-radius:16px;padding:20px 28px;text-align:center;flex:1;min-width:140px;box-shadow:0 0 20px rgba(251,191,36,0.06)">
      <div style="font-size:1.8rem;font-weight:900;color:#fbbf24;text-shadow:0 0 20px rgba(251,191,36,0.6)">100%</div>
      <div style="font-size:.75rem;color:var(--text2);text-transform:uppercase;letter-spacing:1px;margin-top:4px" data-i18n="about.stat_transparent">Transparente</div>
    </div>
    <div style="background:linear-gradient(145deg,rgba(22,32,53,0.95),rgba(14,22,40,0.85));border:1px solid rgba(255,107,107,0.2);border-radius:16px;padding:20px 28px;text-align:center;flex:1;min-width:140px;box-shadow:0 0 20px rgba(255,107,107,0.06)">
      <div style="font-size:1.8rem;font-weight:900;color:#ff8a8a;text-shadow:0 0 20px rgba(255,107,107,0.4)">+15</div>
      <div style="font-size:.75rem;color:var(--text2);text-transform:uppercase;letter-spacing:1px;margin-top:4px" data-i18n="about.stat_assets">Activos</div>
    </div>
  </div>
  <p style="text-align:center;font-size:.8rem;color:var(--text2);margin:16px auto 0;max-width:800px;opacity:.7" data-i18n="about.powered">\u26a1 Sistema H\u00edbrido IA + Visi\u00f3n \u00b7 Probabilidad por Se\u00f1al \u00b7 24/7</p>
</section>

<!-- CALCULADORA INTERACTIVA — engagement + visualización (FIX 2026-05-08) -->
<section class="fade-in" id="calculadora" style="padding:60px 20px;background:linear-gradient(180deg,transparent,rgba(251,191,36,0.04),transparent)">
  <div class="section-title" style="margin-bottom:30px">
    <h2 style="font-size:clamp(1.6rem,4vw,2.4rem)">
      <span style="background:linear-gradient(135deg,#fff 20%,#fbbf24 60%,#ff6d00 100%);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text">\U0001f4b0 Calcula tus Ganancias Potenciales</span>
    </h2>
    <p style="color:var(--text2);font-size:1rem;max-width:600px;margin:0 auto">Mueve el slider y descubre cu&aacute;nto hubieras ganado siguiendo nuestras se&ntilde;ales esta semana real.</p>
  </div>
  <div style="max-width:680px;margin:0 auto;background:linear-gradient(145deg,rgba(22,32,53,0.95),rgba(14,22,40,0.85));border:1px solid rgba(251,191,36,0.25);border-radius:18px;padding:32px 28px;box-shadow:0 8px 40px rgba(251,191,36,0.08)">
    <div style="margin-bottom:24px">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
        <label style="font-size:.95rem;font-weight:700;color:#fff">Tama&ntilde;o de lote</label>
        <div style="font-size:1.5rem;font-weight:900;color:#fbbf24" id="calcLotDisplay">0.10</div>
      </div>
      <input type="range" id="calcLot" min="1" max="100" value="10" step="1" style="width:100%;height:8px;border-radius:4px;background:rgba(251,191,36,0.2);outline:none;-webkit-appearance:none;cursor:pointer;accent-color:#fbbf24">
      <div style="display:flex;justify-content:space-between;font-size:.7rem;color:var(--text2);margin-top:4px"><span>0.01</span><span>0.50</span><span>1.00</span></div>
    </div>
    <div style="margin-bottom:24px">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
        <label style="font-size:.95rem;font-weight:700;color:#fff">Per&iacute;odo</label>
        <div style="display:flex;gap:6px">
          <button class="calcPeriodBtn" data-days="1" style="cursor:pointer;padding:6px 14px;background:rgba(0,212,170,0.15);border:1px solid rgba(0,212,170,0.4);border-radius:8px;color:#00d4aa;font-size:.85rem;font-weight:700;font-family:inherit">Hoy</button>
          <button class="calcPeriodBtn active" data-days="7" style="cursor:pointer;padding:6px 14px;background:rgba(0,212,170,0.4);border:1px solid #00d4aa;border-radius:8px;color:#fff;font-size:.85rem;font-weight:700;font-family:inherit">Semana</button>
          <button class="calcPeriodBtn" data-days="30" style="cursor:pointer;padding:6px 14px;background:rgba(0,212,170,0.15);border:1px solid rgba(0,212,170,0.4);border-radius:8px;color:#00d4aa;font-size:.85rem;font-weight:700;font-family:inherit">Mes</button>
        </div>
      </div>
    </div>
    <div style="background:linear-gradient(135deg,rgba(0,212,170,0.08),rgba(251,191,36,0.05));border:1px solid rgba(0,212,170,0.3);border-radius:14px;padding:24px;text-align:center">
      <div style="font-size:.8rem;color:var(--text2);text-transform:uppercase;letter-spacing:1.5px;margin-bottom:6px">Ganancia estimada</div>
      <div id="calcResult" style="font-size:clamp(2.2rem,6vw,3.2rem);font-weight:900;color:#00ffcc;text-shadow:0 0 30px rgba(0,255,204,0.5);line-height:1">+€0.00</div>
      <div id="calcDetail" style="font-size:.85rem;color:var(--text2);margin-top:8px">Basado en pips reales del per&iacute;odo seleccionado</div>
    </div>
    <p style="font-size:.7rem;color:#5a6a7a;text-align:center;margin-top:16px;line-height:1.5">⚠️ Estimaci&oacute;n educativa basada en el rendimiento hist&oacute;rico real. Resultados pasados no garantizan futuros.</p>
  </div>
</section>

<!-- ASSETS -->
<section id="assets" class="fade-in">
  <div class="section-title">
    <h2>\U0001f30d <span data-i18n="assets.title">+20 Activos de Clase Mundial</span></h2>
    <p data-i18n="assets.subtitle">Cada activo tiene par\u00e1metros de detecci\u00f3n calibrados individualmente para m\u00e1xima precisi\u00f3n.</p>
  </div>
  <div class="assets-grid">
    <div class="asset-card">
      <div class="asset-icon gold"><svg viewBox="0 0 64 64" fill="none"><defs><linearGradient id="gbar1" x1="10" y1="15" x2="54" y2="55"><stop offset="0%" stop-color="#ffe88a"/><stop offset="25%" stop-color="#f0c030"/><stop offset="50%" stop-color="#d4a020"/><stop offset="75%" stop-color="#f0c030"/><stop offset="100%" stop-color="#b8860b"/></linearGradient><linearGradient id="gbar2" x1="10" y1="20" x2="10" y2="50"><stop offset="0%" stop-color="#d4a020"/><stop offset="100%" stop-color="#8b6914"/></linearGradient><linearGradient id="gtop" x1="20" y1="20" x2="45" y2="32"><stop offset="0%" stop-color="#ffe88a"/><stop offset="100%" stop-color="#f0c030"/></linearGradient><linearGradient id="gline" x1="0" y1="0" x2="64" y2="0"><stop offset="0%" stop-color="#f0b90b" stop-opacity=".2"/><stop offset="100%" stop-color="#f0b90b" stop-opacity=".6"/></linearGradient></defs><path d="M6 48l10-6 10 3 10-10 10-8 8-6" stroke="url(#gline)" stroke-width="1.5" stroke-linecap="round" fill="none" opacity=".5"/><path d="M6 48l10-6 10 3 10-10 10-8 8-6v32H6z" fill="#f0b90b" opacity=".06"/><path d="M12 52l8-3h24l8 3z" fill="#8b6914"/><path d="M20 49l-8 3V44l5-12h30l5 12v11l-8-3v-3z" fill="url(#gbar2)"/><path d="M17 32h30l5 12H12z" fill="url(#gbar1)"/><path d="M17 32l5-12h20l5 12z" fill="url(#gtop)"/><path d="M22 20h20l5 12H17z" fill="none" stroke="#ffe88a" stroke-width=".5" opacity=".5"/><line x1="17" y1="32" x2="47" y2="32" stroke="#b8860b" stroke-width=".5"/><text x="32" y="29" text-anchor="middle" font-size="7" font-weight="800" fill="#7a5a00" font-family="Arial" letter-spacing=".5">GOLD</text><text x="32" y="41" text-anchor="middle" font-size="5.5" font-weight="700" fill="#5a4200" font-family="Arial">999.9</text><circle cx="50" cy="14" r="8" fill="#f0c030" opacity=".12"/><circle cx="50" cy="14" r="5" fill="#f0c030" opacity=".08"/></svg></div>
      <div class="asset-name" data-i18n="assets.gold">ORO</div><div class="asset-tag">XAU/USD</div>
    </div>
    <div class="asset-card">
      <div class="asset-icon usdcad"><svg viewBox="0 0 40 40" fill="none"><circle cx="14" cy="20" r="12" fill="#002868"/><text x="14" y="25" text-anchor="middle" font-size="12" font-weight="900" fill="#fff" font-family="Arial">$</text><circle cx="28" cy="20" r="12" fill="#FF0000"/><text x="28" y="25" text-anchor="middle" font-size="10" font-weight="900" fill="#fff" font-family="Arial">C$</text></svg></div>
      <div class="asset-name">USD/CAD</div><div class="asset-tag">Forex Principal</div>
    </div>
    <div class="asset-card">
      <div class="asset-icon nasdaq"><svg viewBox="0 0 40 40" fill="none"><line x1="8" y1="22" x2="8" y2="32" stroke="#ef4444" stroke-width="1.2"/><rect x="6" y="24" width="4" height="6" rx=".5" fill="#ef4444"/><line x1="15" y1="12" x2="15" y2="28" stroke="#00d4aa" stroke-width="1.2"/><rect x="13" y="14" width="4" height="10" rx=".5" fill="#00d4aa"/><line x1="22" y1="16" x2="22" y2="30" stroke="#ef4444" stroke-width="1.2"/><rect x="20" y="18" width="4" height="8" rx=".5" fill="#ef4444"/><line x1="29" y1="6" x2="29" y2="24" stroke="#00d4aa" stroke-width="1.2"/><rect x="27" y="8" width="4" height="12" rx=".5" fill="#00d4aa"/><line x1="35" y1="4" x2="35" y2="20" stroke="#00d4aa" stroke-width="1.2"/><rect x="33" y="6" width="4" height="10" rx=".5" fill="#00d4aa"/><path d="M5 35l7-8 7 4 7-12 7-8 4-3" stroke="#00d4aa" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" opacity=".5"/></svg></div>
      <div class="asset-name">NASDAQ</div><div class="asset-tag">NQ \u2022 <span data-i18n="assets.us_tech">Tecnol\u00f3gicas EE.UU.</span></div>
    </div>
    <div class="asset-card">
      <div class="asset-icon sp500"><svg viewBox="0 0 40 40" fill="none"><defs><linearGradient id="spg" x1="20" y1="8" x2="20" y2="36" gradientUnits="userSpaceOnUse"><stop offset="0%" stop-color="#3b82f6" stop-opacity=".5"/><stop offset="100%" stop-color="#3b82f6" stop-opacity="0"/></linearGradient></defs><path d="M4 30L10 24 16 27 22 18 28 14 34 8 38 6v30H4z" fill="url(#spg)"/><path d="M4 30L10 24 16 27 22 18 28 14 34 8 38 6" stroke="#3b82f6" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/><circle cx="34" cy="8" r="3" fill="#3b82f6"/><circle cx="34" cy="8" r="5" fill="#3b82f6" opacity=".2"/><text x="34" y="10" text-anchor="middle" font-size="4" fill="#fff" font-weight="700" font-family="Arial">\u2191</text></svg></div>
      <div class="asset-name">S&P 500</div><div class="asset-tag">ES \u2022 <span data-i18n="assets.us_market">Mercado EE.UU.</span></div>
    </div>
    <div class="asset-card">
      <div class="asset-icon gbpusd"><svg viewBox="0 0 40 40" fill="none"><circle cx="14" cy="20" r="12" fill="#012169"/><text x="14" y="25" text-anchor="middle" font-size="12" font-weight="900" fill="#fff" font-family="Arial">\u00a3</text><circle cx="28" cy="20" r="12" fill="#1a6b3c"/><text x="28" y="25" text-anchor="middle" font-size="12" font-weight="900" fill="#fff" font-family="Arial">$</text></svg></div>
      <div class="asset-name">GBP/USD</div><div class="asset-tag">Forex Principal</div>
    </div>
    <div class="asset-card">
      <div class="asset-icon usdchf"><svg viewBox="0 0 40 40" fill="none"><circle cx="14" cy="20" r="12" fill="#002868"/><text x="14" y="25" text-anchor="middle" font-size="12" font-weight="900" fill="#fff" font-family="Arial">$</text><circle cx="28" cy="20" r="12" fill="#FF0000"/><text x="28" y="25" text-anchor="middle" font-size="10" font-weight="900" fill="#fff" font-family="Arial">Fr</text></svg></div>
      <div class="asset-name">USD/CHF</div><div class="asset-tag">Forex Principal</div>
    </div>
    <div class="asset-card">
      <div class="asset-icon audusd"><svg viewBox="0 0 40 40" fill="none"><circle cx="14" cy="20" r="12" fill="#00008B"/><text x="14" y="25" text-anchor="middle" font-size="10" font-weight="900" fill="#fff" font-family="Arial">A$</text><circle cx="28" cy="20" r="12" fill="#1a6b3c"/><text x="28" y="25" text-anchor="middle" font-size="12" font-weight="900" fill="#fff" font-family="Arial">$</text></svg></div>
      <div class="asset-name">AUD/USD</div><div class="asset-tag">Forex Principal</div>
    </div>
    <div class="asset-card">
      <div class="asset-icon nzdusd"><svg viewBox="0 0 40 40" fill="none"><circle cx="14" cy="20" r="12" fill="#00247D"/><text x="14" y="25" text-anchor="middle" font-size="9" font-weight="900" fill="#fff" font-family="Arial">NZ</text><circle cx="28" cy="20" r="12" fill="#1a6b3c"/><text x="28" y="25" text-anchor="middle" font-size="12" font-weight="900" fill="#fff" font-family="Arial">$</text></svg></div>
      <div class="asset-name">NZD/USD</div><div class="asset-tag">Forex</div>
    </div>
    <div class="asset-card">
      <div class="asset-icon gbpjpy"><svg viewBox="0 0 40 40" fill="none"><circle cx="14" cy="20" r="12" fill="#012169"/><text x="14" y="25" text-anchor="middle" font-size="12" font-weight="900" fill="#fff" font-family="Arial">\u00a3</text><circle cx="28" cy="20" r="12" fill="#bc002d"/><text x="28" y="25" text-anchor="middle" font-size="12" font-weight="900" fill="#fff" font-family="Arial">\u00a5</text></svg></div>
      <div class="asset-name">GBP/JPY</div><div class="asset-tag">Cross Volatil</div>
    </div>
    <div class="asset-card">
      <div class="asset-icon eurjpy"><svg viewBox="0 0 40 40" fill="none"><circle cx="14" cy="20" r="12" fill="#003399"/><text x="14" y="25" text-anchor="middle" font-size="12" font-weight="900" fill="#FFD700" font-family="Arial">\u20ac</text><circle cx="28" cy="20" r="12" fill="#bc002d"/><text x="28" y="25" text-anchor="middle" font-size="12" font-weight="900" fill="#fff" font-family="Arial">\u00a5</text></svg></div>
      <div class="asset-name">EUR/JPY</div><div class="asset-tag">Cross</div>
    </div>
    <div class="asset-card">
      <div class="asset-icon us30"><svg viewBox="0 0 40 40" fill="none"><rect x="4" y="4" width="32" height="32" rx="6" fill="#1a365d"/><text x="20" y="23" text-anchor="middle" font-size="8" font-weight="900" fill="#60a5fa" font-family="Arial">US30</text><path d="M8 28l6-4 6 2 6-8 6-6" stroke="#60a5fa" stroke-width="1.5" stroke-linecap="round" opacity=".6"/></svg></div>
      <div class="asset-name">US30</div><div class="asset-tag">Dow Jones</div>
    </div>
    <div class="asset-card">
      <div class="asset-icon ger40"><svg viewBox="0 0 40 40" fill="none"><rect x="4" y="4" width="32" height="32" rx="6" fill="#1a1a2e"/><rect x="12" y="8" width="16" height="5" fill="#000"/><rect x="12" y="13" width="16" height="5" fill="#DD0000"/><rect x="12" y="18" width="16" height="5" fill="#FFCC00"/><text x="20" y="32" text-anchor="middle" font-size="7" font-weight="900" fill="#FFCC00" font-family="Arial">DAX</text></svg></div>
      <div class="asset-name">GER40</div><div class="asset-tag">DAX \u2022 Alemania</div>
    </div>
    <div class="asset-card">
      <div class="asset-icon eurgbp"><svg viewBox="0 0 40 40" fill="none"><circle cx="14" cy="20" r="12" fill="#003399"/><text x="14" y="25" text-anchor="middle" font-size="12" font-weight="900" fill="#FFD700" font-family="Arial">\u20ac</text><circle cx="28" cy="20" r="12" fill="#012169"/><text x="28" y="25" text-anchor="middle" font-size="12" font-weight="900" fill="#fff" font-family="Arial">\u00a3</text></svg></div>
      <div class="asset-name">EUR/GBP</div><div class="asset-tag">Cross Europa</div>
    </div>
    <div class="asset-card">
      <div class="asset-icon btc"><svg viewBox="0 0 40 40" fill="none"><circle cx="20" cy="20" r="16" fill="#f7931a"/><text x="20" y="26" text-anchor="middle" font-size="20" font-weight="900" fill="#fff" font-family="Arial">₿</text></svg></div>
      <div class="asset-name">BTC/USD</div><div class="asset-tag" data-i18n="assets.bitcoin">Bitcoin</div>
    </div>
    <div class="asset-card">
      <div class="asset-icon eth"><svg viewBox="0 0 40 40" fill="none"><circle cx="20" cy="20" r="16" fill="#627eea"/><polygon points="20,7 13,21 20,17 27,21" fill="#fff" opacity="0.7"/><polygon points="20,7 13,21 20,25" fill="#fff"/><polygon points="20,27 13,23 20,33 27,23" fill="#fff" opacity="0.7"/><polygon points="20,27 13,23 20,33" fill="#fff"/></svg></div>
      <div class="asset-name">ETH/USD</div><div class="asset-tag" data-i18n="assets.ethereum">Ethereum</div>
    </div>
    <div class="asset-card">
      <div class="asset-icon eur"><svg viewBox="0 0 40 40" fill="none"><circle cx="20" cy="20" r="16" fill="#0052b4"/><circle cx="20" cy="20" r="13" fill="none" stroke="#fbbf24" stroke-width="1.5"/><text x="20" y="26" text-anchor="middle" font-size="16" font-weight="900" fill="#fbbf24" font-family="Arial">€</text></svg></div>
      <div class="asset-name">EUR/USD</div><div class="asset-tag" data-i18n="assets.forex_major">Forex Principal</div>
    </div>
    <div class="asset-card">
      <div class="asset-icon jpy"><svg viewBox="0 0 40 40" fill="none"><circle cx="20" cy="20" r="16" fill="#bc002d"/><circle cx="20" cy="20" r="6" fill="#fff"/><text x="20" y="34" text-anchor="middle" font-size="7" font-weight="900" fill="#fff" font-family="Arial">JPY</text></svg></div>
      <div class="asset-name">USD/JPY</div><div class="asset-tag">Forex Principal</div>
    </div>
    <div class="asset-card">
      <div class="asset-icon usoil"><svg viewBox="0 0 40 40" fill="none"><rect x="4" y="4" width="32" height="32" rx="6" fill="#1a1a1a"/><text x="20" y="18" text-anchor="middle" font-size="9" font-weight="900" fill="#888" font-family="Arial">WTI</text><text x="20" y="29" text-anchor="middle" font-size="11" font-weight="900" fill="#10b981" font-family="Arial">OIL</text></svg></div>
      <div class="asset-name">USOIL / WTI</div><div class="asset-tag">Petr&oacute;leo USA</div>
    </div>
    <div class="asset-card">
      <div class="asset-icon brent"><svg viewBox="0 0 40 40" fill="none"><rect x="4" y="4" width="32" height="32" rx="6" fill="#0d1f12"/><text x="20" y="18" text-anchor="middle" font-size="8" font-weight="900" fill="#888" font-family="Arial">BRENT</text><text x="20" y="29" text-anchor="middle" font-size="11" font-weight="900" fill="#34d399" font-family="Arial">OIL</text></svg></div>
      <div class="asset-name">BRENT</div><div class="asset-tag">Petr&oacute;leo UK</div>
    </div>
    <div class="asset-card">
      <div class="asset-icon silver"><svg viewBox="0 0 40 40" fill="none"><circle cx="20" cy="20" r="16" fill="#c0c0c0" opacity=".3"/><circle cx="20" cy="20" r="13" fill="none" stroke="#e8e8e8" stroke-width="2"/><text x="20" y="25" text-anchor="middle" font-size="11" font-weight="900" fill="#fff" font-family="Arial">Ag</text></svg></div>
      <div class="asset-name">PLATA</div><div class="asset-tag">XAG/USD</div>
    </div>
  </div>
</section>

<!-- FAQ -->
<section class="faq fade-in" id="faq">
  <div class="section-title">
    <h2>\u2753 <span data-i18n="faq.title">Preguntas Frecuentes</span></h2>
    <p data-i18n="faq.subtitle">Todo lo que necesitas saber antes de empezar.</p>
  </div>
  <div class="faq-list">
    <div class="faq-item" onclick="this.classList.toggle('open')">
      <div class="faq-q" data-i18n="faq.q1">¿Cómo funciona el grupo de comunidad?</div>
      <div class="faq-a" data-i18n="faq.a1">El grupo público de Telegram te da acceso a resúmenes diarios de mercado, educación y análisis general. Para recibir señales VIP en tiempo real con Entry, TP y SL exactos, necesitas el plan VIP Pro.</div>
    </div>
    <div class="faq-item" onclick="this.classList.toggle('open')">
      <div class="faq-q" data-i18n="faq.q2">\u00bfC\u00f3mo recibo las se\u00f1ales?</div>
      <div class="faq-a" data-i18n="faq.a2">Las se\u00f1ales se env\u00edan directamente a tu Telegram en tiempo real. Cada se\u00f1al incluye: activo, direcci\u00f3n (compra/venta), precio de entrada, TP y Stop Loss exactos.</div>
    </div>
    <div class="faq-item" onclick="this.classList.toggle('open')">
      <div class="faq-q" data-i18n="faq.q3">\u00bfNecesito experiencia en trading?</div>
      <div class="faq-a" data-i18n="faq.a3">No. Las se\u00f1ales son claras y f\u00e1ciles de seguir. Te decimos exactamente d\u00f3nde entrar, d\u00f3nde colocar el Stop Loss y los Take Profits. Adem\u00e1s, nuestra comunidad te ayudar\u00e1 a aprender.</div>
    </div>
    <div class="faq-item" onclick="this.classList.toggle('open')">
      <div class="faq-q" data-i18n="faq.q4">\u00bfQu\u00e9 broker necesito?</div>
      <div class="faq-a" data-i18n="faq.a4">Puedes usar cualquier broker que soporte los activos que operamos (EUR/USD, NASDAQ, S&amp;P 500 y m\u00e1s). Recomendamos brokers regulados con buen spread para mejor ejecuci\u00f3n de las se\u00f1ales.</div>
    </div>
    <div class="faq-item" onclick="this.classList.toggle('open')">
      <div class="faq-q" data-i18n="faq.q5">\u00bfC\u00f3mo cancelo mi suscripci\u00f3n VIP?</div>
      <div class="faq-a" data-i18n="faq.a5">Simplemente escribe al bot de Telegram. No hay contratos ni cargos ocultos. Tu suscripci\u00f3n se cancela de forma inmediata sin preguntas.</div>
    </div>
    <div class="faq-item" onclick="this.classList.toggle('open')">
      <div class="faq-q" data-i18n="faq.q6">\u00bfCu\u00e1ntas se\u00f1ales recibo al d\u00eda?</div>
      <div class="faq-a" data-i18n="faq.a6">En promedio entre 5 y 15 se\u00f1ales diarias repartidas entre los +20 activos. El bot analiza el mercado cada 3 minutos y solo env\u00eda se\u00f1ales cuando detecta una oportunidad de alta probabilidad.</div>
    </div>
  </div>
</section>


<!-- WHY CHOOSE US -->
<section class="fade-in" style="padding:60px 20px">
  <div class="section-title" style="margin-bottom:32px">
    <h2>\U0001f3c6 <span data-i18n="why.title">Por Qu\u00e9 Elegirnos</span></h2>
    <p data-i18n="why.subtitle">Lo que nos diferencia de otros servicios de se\u00f1ales</p>
  </div>
  <div style="display:flex;flex-wrap:wrap;justify-content:center;gap:20px;max-width:1100px;margin:0 auto">
    <div style="flex:1;min-width:220px;max-width:260px;background:linear-gradient(145deg,rgba(22,32,53,0.95),rgba(14,22,40,0.85));border:1px solid rgba(255,255,255,0.1);border-radius:16px;padding:24px;text-align:center;box-shadow:0 4px 20px rgba(0,0,0,0.25);transition:all .3s"
         onmouseover="this.style.transform='translateY(-6px)';this.style.borderColor='rgba(0,255,204,0.35)'"
         onmouseout="this.style.transform='';this.style.borderColor='rgba(255,255,255,0.1)'"
         >
      <div style="font-size:2.2rem;margin-bottom:10px">\U0001f916</div>
      <h4 style="color:#00ffc8;margin-bottom:8px" data-i18n="why.ai_title">IA Real, No Opiniones</h4>
      <p style="color:var(--text2);font-size:.85rem" data-i18n="why.ai_desc">Nuestro bot analiza datos reales cada 3 minutos. Sin emociones, sin sesgos \u2014 solo datos y algoritmos.</p>
    </div>
    <div style="flex:1;min-width:220px;max-width:260px;background:linear-gradient(145deg,rgba(22,32,53,0.95),rgba(14,22,40,0.85));border:1px solid rgba(255,255,255,0.1);border-radius:16px;padding:24px;text-align:center;box-shadow:0 4px 20px rgba(0,0,0,0.25);transition:all .3s"
         onmouseover="this.style.transform='translateY(-6px)';this.style.borderColor='rgba(0,255,204,0.35)'"
         onmouseout="this.style.transform='';this.style.borderColor='rgba(255,255,255,0.1)'"
         >
      <div style="font-size:2.2rem;margin-bottom:10px">\U0001f4ca</div>
      <h4 style="color:#00e676;margin-bottom:8px" data-i18n="why.transparent_title">100% Transparente</h4>
      <p style="color:var(--text2);font-size:.85rem" data-i18n="why.transparent_desc">Dashboard p\u00fablico con resultados en vivo. Cada operaci\u00f3n visible con entrada, SL, TP y resultado.</p>
    </div>
    <div style="flex:1;min-width:220px;max-width:260px;background:linear-gradient(145deg,rgba(22,32,53,0.95),rgba(14,22,40,0.85));border:1px solid rgba(255,255,255,0.1);border-radius:16px;padding:24px;text-align:center;box-shadow:0 4px 20px rgba(0,0,0,0.25);transition:all .3s"
         onmouseover="this.style.transform='translateY(-6px)';this.style.borderColor='rgba(0,255,204,0.35)'"
         onmouseout="this.style.transform='';this.style.borderColor='rgba(255,255,255,0.1)'"
         >
      <div style="font-size:2.2rem;margin-bottom:10px">\u26a1</div>
      <h4 style="color:#a855f7;margin-bottom:8px" data-i18n="why.instant_title">Ejecuci\u00f3n Instant\u00e1nea</h4>
      <p style="color:var(--text2);font-size:.85rem" data-i18n="why.instant_desc">Las se\u00f1ales se ejecutan en menos de 1 segundo. Sin retrasos, sin slippage. Tu cuenta siempre sincronizada.</p>
    </div>
  </div>
</section>

<!-- SIGNAL PREVIEW -->
<section style="padding:60px 20px;text-align:center" class="fade-in">
<div style="max-width:500px;margin:0 auto">
    <div style="font-size:11px;font-weight:700;color:#a855f7;text-transform:uppercase;letter-spacing:2px;margin-bottom:14px" data-i18n="signal.badge">&#128227; Ejemplo de Se\u00f1al en Vivo</div>
    <h2 style="font-size:1.6rem;font-weight:800;margin-bottom:20px;background:linear-gradient(135deg,#fff 20%,#a78bfa 60%,#4d9fff 100%);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text" data-i18n="signal.title">As\u00ed Recibir\u00e1s las Se\u00f1ales</h2>
    <div style="background:linear-gradient(135deg,#0f1e2e,#1a0d2e);border:1px solid rgba(0,212,170,.3);border-radius:16px;padding:24px;text-align:left;margin-bottom:24px;box-shadow:0 4px 30px rgba(0,212,170,0.08)">
        <div style="display:flex;align-items:center;gap:10px;margin-bottom:12px">
            <div style="width:36px;height:36px;border-radius:50%;background:linear-gradient(135deg,#00d4aa,#00a080);display:flex;align-items:center;justify-content:center;font-size:18px">&#129302;</div>
            <div><div style="font-weight:700;font-size:14px;color:#fff">BuySell365 Pro</div><div style="font-size:10px;color:#5a6a7a" data-i18n="signal.channel">Canal de Se\u00f1ales</div></div>
        </div>
        <div style="font-size:14px;line-height:1.8;color:#e2e8f0">
            <div style="color:#00e676;font-weight:800;font-size:16px">&#128994; BUY &mdash; ORO</div>
            <div style="margin-top:8px;font-size:13px">&#128205; <strong>Entry:</strong> 4720.50</div>
            <div style="color:#00e676;margin-top:2px;font-size:13px">&#127919; <strong>TP1:</strong> 4735.00</div>
            <div style="color:#00e676;margin-top:2px;font-size:13px">&#127919; <strong>TP2:</strong> 4750.00</div>
            <div style="color:#00e676;margin-top:2px;font-size:13px">&#127919; <strong>TP3:</strong> 4765.00</div>
            <div style="color:#ff6b35;margin-top:2px;font-size:13px">&#128737; <strong>SL:</strong> 4710.00</div>
            <div style="margin-top:10px;padding-top:10px;border-top:1px dashed rgba(0,255,204,.25);font-size:13px">&#128202; <strong style="color:#00ffcc">Probabilidad: 78%</strong></div>
            <div style="margin-top:8px;font-size:12px;color:#7a90a8;font-style:italic">&mdash; Eli &middot; BuySell365 Pro &#129302;</div>
            <div style="margin-top:8px;font-size:12px;color:#7a90a8">Rendimiento en vivo &bull; <a href="#en-vivo" style="color:#00d4aa;text-decoration:none">Ver Rendimiento &rarr;</a></div>
        </div>
    </div>
    <p style="color:#8a9ab5;font-size:13px;margin-bottom:12px" data-i18n="signal.receive">Recibe alertas como esta directamente en tu Telegram</p>
</div>
</section>

<!-- CTA -->
<section class="cta fade-in" style="padding:60px 20px;background:linear-gradient(180deg,transparent,rgba(0,212,170,0.03),transparent)">
  <h2>\U0001f680 <span style="background:linear-gradient(135deg,#fff 20%,#00ffcc 60%,#4d9fff 100%);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text" data-i18n="cta.title">Empieza a Operar con IA</span></h2>
  <p style="font-size:1.1rem" data-i18n="cta.subtitle">Accede al Canal VIP Pro. Sin contratos, cancela cuando quieras.</p>
  <div class="hero-buttons">
    <a href="#pricing" class="btn btn-primary"><span data-i18n="cta.plans">\U0001f451 Ver Planes</span></a>
    <a href="https://t.me/BUYSELL_365_24_7" target="_blank" class="btn btn-secondary"><span data-i18n="cta.community">\U0001f4ac Comunidad Gratis</span></a>
    <a href="https://www.instagram.com/buysell365.pro_tradingsignals/" target="_blank" class="btn btn-secondary" style="border-color:rgba(225,48,108,0.4);color:#fff;background:linear-gradient(45deg,#f09433,#e6683c,#dc2743,#cc2366,#bc1888);font-weight:700;padding:12px 28px"><svg viewBox="0 0 24 24" width="18" height="18" fill="#fff" style="vertical-align:middle;margin-right:6px"><path d="M12 2.163c3.204 0 3.584.012 4.85.07 3.252.148 4.771 1.691 4.919 4.919.058 1.265.069 1.645.069 4.849 0 3.205-.012 3.584-.069 4.849-.149 3.225-1.664 4.771-4.919 4.919-1.266.058-1.644.07-4.85.07-3.204 0-3.584-.012-4.849-.07-3.26-.149-4.771-1.699-4.919-4.92-.058-1.265-.07-1.644-.07-4.849 0-3.204.013-3.583.07-4.849.149-3.227 1.664-4.771 4.919-4.919 1.266-.057 1.645-.069 4.849-.069zM12 0C8.741 0 8.333.014 7.053.072 2.695.272.273 2.69.073 7.052.014 8.333 0 8.741 0 12c0 3.259.014 3.668.072 4.948.2 4.358 2.618 6.78 6.98 6.98C8.333 23.986 8.741 24 12 24c3.259 0 3.668-.014 4.948-.072 4.354-.2 6.782-2.618 6.979-6.98.059-1.28.073-1.689.073-4.948 0-3.259-.014-3.667-.072-4.947-.196-4.354-2.617-6.78-6.979-6.98C15.668.014 15.259 0 12 0zm0 5.838a6.162 6.162 0 100 12.324 6.162 6.162 0 000-12.324zM12 16a4 4 0 110-8 4 4 0 010 8zm6.406-11.845a1.44 1.44 0 100 2.881 1.44 1.44 0 000-2.881z"/></svg>Instagram</a>
  </div>
</section>

<!-- INSTAGRAM BANNER -->
<section class="fade-in" style="padding:40px 20px;background:linear-gradient(135deg,rgba(240,148,51,0.08),rgba(225,48,108,0.08),rgba(188,24,136,0.08));border-top:1px solid rgba(225,48,108,0.15);border-bottom:1px solid rgba(225,48,108,0.15)">
  <div style="max-width:800px;margin:0 auto;text-align:center">
    <div style="display:inline-flex;align-items:center;gap:12px;margin-bottom:16px">
      <svg viewBox="0 0 24 24" width="36" height="36" fill="url(#ig-grad-banner)"><defs><linearGradient id="ig-grad-banner" x1="0%" y1="100%" x2="100%" y2="0%"><stop offset="0%" style="stop-color:#f09433"/><stop offset="25%" style="stop-color:#e6683c"/><stop offset="50%" style="stop-color:#dc2743"/><stop offset="75%" style="stop-color:#cc2366"/><stop offset="100%" style="stop-color:#bc1888"/></linearGradient></defs><path d="M12 2.163c3.204 0 3.584.012 4.85.07 3.252.148 4.771 1.691 4.919 4.919.058 1.265.069 1.645.069 4.849 0 3.205-.012 3.584-.069 4.849-.149 3.225-1.664 4.771-4.919 4.919-1.266.058-1.644.07-4.85.07-3.204 0-3.584-.012-4.849-.07-3.26-.149-4.771-1.699-4.919-4.92-.058-1.265-.07-1.644-.07-4.849 0-3.204.013-3.583.07-4.849.149-3.227 1.664-4.771 4.919-4.919 1.266-.057 1.645-.069 4.849-.069zM12 0C8.741 0 8.333.014 7.053.072 2.695.272.273 2.69.073 7.052.014 8.333 0 8.741 0 12c0 3.259.014 3.668.072 4.948.2 4.358 2.618 6.78 6.98 6.98C8.333 23.986 8.741 24 12 24c3.259 0 3.668-.014 4.948-.072 4.354-.2 6.782-2.618 6.979-6.98.059-1.28.073-1.689.073-4.948 0-3.259-.014-3.667-.072-4.947-.196-4.354-2.617-6.78-6.979-6.98C15.668.014 15.259 0 12 0zm0 5.838a6.162 6.162 0 100 12.324 6.162 6.162 0 000-12.324zM12 16a4 4 0 110-8 4 4 0 010 8zm6.406-11.845a1.44 1.44 0 100 2.881 1.44 1.44 0 000-2.881z"/></svg>
      <span style="font-size:1.6rem;font-weight:800;background:linear-gradient(135deg,#f09433,#e6683c,#dc2743,#cc2366,#bc1888);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text">S\u00edguenos en Instagram</span>
    </div>
    <p style="color:#a0aec0;font-size:1rem;margin-bottom:20px">Resultados diarios, TPs en vivo, tips de trading y contenido exclusivo. Transparencia total.</p>
    <a href="https://www.instagram.com/buysell365.pro_tradingsignals/" target="_blank" style="display:inline-flex;align-items:center;gap:10px;padding:14px 36px;border-radius:30px;background:linear-gradient(45deg,#f09433,#e6683c,#dc2743,#cc2366,#bc1888);color:#fff;font-weight:700;font-size:1.1rem;text-decoration:none;transition:transform 0.2s,box-shadow 0.2s;box-shadow:0 4px 20px rgba(225,48,108,0.3)" onmouseover="this.style.transform='scale(1.05)';this.style.boxShadow='0 6px 30px rgba(225,48,108,0.5)'" onmouseout="this.style.transform='scale(1)';this.style.boxShadow='0 4px 20px rgba(225,48,108,0.3)'">
      <svg viewBox="0 0 24 24" width="22" height="22" fill="#fff"><path d="M12 2.163c3.204 0 3.584.012 4.85.07 3.252.148 4.771 1.691 4.919 4.919.058 1.265.069 1.645.069 4.849 0 3.205-.012 3.584-.069 4.849-.149 3.225-1.664 4.771-4.919 4.919-1.266.058-1.644.07-4.85.07-3.204 0-3.584-.012-4.849-.07-3.26-.149-4.771-1.699-4.919-4.92-.058-1.265-.07-1.644-.07-4.849 0-3.204.013-3.583.07-4.849.149-3.227 1.664-4.771 4.919-4.919 1.266-.057 1.645-.069 4.849-.069zM12 0C8.741 0 8.333.014 7.053.072 2.695.272.273 2.69.073 7.052.014 8.333 0 8.741 0 12c0 3.259.014 3.668.072 4.948.2 4.358 2.618 6.78 6.98 6.98C8.333 23.986 8.741 24 12 24c3.259 0 3.668-.014 4.948-.072 4.354-.2 6.782-2.618 6.979-6.98.059-1.28.073-1.689.073-4.948 0-3.259-.014-3.667-.072-4.947-.196-4.354-2.617-6.78-6.979-6.98C15.668.014 15.259 0 12 0zm0 5.838a6.162 6.162 0 100 12.324 6.162 6.162 0 000-12.324zM12 16a4 4 0 110-8 4 4 0 010 8zm6.406-11.845a1.44 1.44 0 100 2.881 1.44 1.44 0 000-2.881z"/></svg>
      @buysell365.pro_tradingsignals
    </a>
  </div>
</section>

<!-- FLOATING TELEGRAM BUTTON -->
<a href="https://t.me/BUYSELL_365_24_7" target="_blank" class="float-telegram" title="Telegram">
  <svg viewBox="0 0 24 24"><path d="M11.944 0A12 12 0 0 0 0 12a12 12 0 0 0 12 12 12 12 0 0 0 12-12A12 12 0 0 0 12 0a12 12 0 0 0-.056 0zm4.962 7.224c.1-.002.321.023.465.14a.506.506 0 0 1 .171.325c.016.093.036.306.02.472-.18 1.898-.962 6.502-1.36 8.627-.168.9-.499 1.201-.82 1.23-.696.065-1.225-.46-1.9-.902-1.056-.693-1.653-1.124-2.678-1.8-1.185-.78-.417-1.21.258-1.91.177-.184 3.247-2.977 3.307-3.23.007-.032.014-.15-.056-.212s-.174-.041-.249-.024c-.106.024-1.793 1.14-5.061 3.345-.479.33-.913.49-1.302.48-.428-.008-1.252-.241-1.865-.44-.752-.245-1.349-.374-1.297-.789.027-.216.325-.437.893-.663 3.498-1.524 5.83-2.529 6.998-3.014 3.332-1.386 4.025-1.627 4.476-1.635z"/></svg>
</a>

<!-- BACK TO TOP -->
<div class="back-to-top" id="backToTop" onclick="window.scrollTo({{top:0,behavior:'smooth'}})">\u2191</div>

<!-- LEGAL / RISK DISCLAIMER (FIX 2026-05-08) -->
<section style="padding:50px 20px;background:linear-gradient(180deg,rgba(248,113,113,0.04),transparent 50%,rgba(0,212,170,0.03));border-top:1px solid rgba(248,113,113,0.2)">
  <div style="max-width:900px;margin:0 auto;text-align:center">
    <div style="display:inline-block;padding:6px 16px;background:rgba(248,113,113,0.12);border:1px solid rgba(248,113,113,0.4);border-radius:50px;margin-bottom:18px">
      <span style="font-size:11px;font-weight:800;color:#ff8a8a;letter-spacing:2px">\u26a0\ufe0f AVISO LEGAL Y DE RIESGO</span>
    </div>
    <h2 style="font-size:1.4rem;font-weight:800;margin-bottom:18px;color:#fff">
      Antes de operar, lee atentamente
    </h2>
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:16px;text-align:left;max-width:880px;margin:0 auto 24px">
      <div style="background:rgba(22,32,53,0.6);border:1px solid rgba(255,255,255,0.08);border-radius:12px;padding:18px">
        <div style="font-size:13px;font-weight:700;color:#fbbf24;margin-bottom:6px">\U0001f4cb No es asesor\u00eda financiera</div>
        <div style="font-size:12px;color:var(--text2);line-height:1.6">Las se\u00f1ales son informativas y educativas. No son recomendaciones personalizadas. Cada decisi\u00f3n de inversi\u00f3n es responsabilidad tuya.</div>
      </div>
      <div style="background:rgba(22,32,53,0.6);border:1px solid rgba(255,255,255,0.08);border-radius:12px;padding:18px">
        <div style="font-size:13px;font-weight:700;color:#ff8a8a;margin-bottom:6px">\u26a0\ufe0f Riesgo de p\u00e9rdida</div>
        <div style="font-size:12px;color:var(--text2);line-height:1.6">Operar en mercados financieros conlleva alto riesgo de p\u00e9rdida de capital. Nunca inviertas dinero que no puedas permitirte perder.</div>
      </div>
      <div style="background:rgba(22,32,53,0.6);border:1px solid rgba(255,255,255,0.08);border-radius:12px;padding:18px">
        <div style="font-size:13px;font-weight:700;color:#00d4aa;margin-bottom:6px">\U0001f4ca El % es estimaci\u00f3n</div>
        <div style="font-size:12px;color:var(--text2);line-height:1.6">El porcentaje de probabilidad es una estimaci\u00f3n estad\u00edstica calculada en tiempo real. NO es una garant\u00eda de resultado. Una se\u00f1al con probabilidad alta puede igualmente terminar en p\u00e9rdida.</div>
      </div>
      <div style="background:rgba(22,32,53,0.6);border:1px solid rgba(255,255,255,0.08);border-radius:12px;padding:18px">
        <div style="font-size:13px;font-weight:700;color:#a78bfa;margin-bottom:6px">\U0001f4c8 Resultados pasados</div>
        <div style="font-size:12px;color:var(--text2);line-height:1.6">Los resultados pasados no garantizan resultados futuros. Los stats publicados reflejan el desempe\u00f1o hist\u00f3rico de las se\u00f1ales del canal VIP, no una proyecci\u00f3n.</div>
      </div>
    </div>
    <div style="margin-top:20px;padding:18px 22px;background:rgba(0,0,0,0.25);border-radius:12px;border:1px solid rgba(255,255,255,0.08);max-width:780px;margin-left:auto;margin-right:auto">
      <p style="font-size:13px;color:var(--text2);line-height:1.7;margin:0">
        Al usar BuySell365 Pro aceptas nuestros
        <a href="/terminos" style="color:#fbbf24;font-weight:700;text-decoration:underline">T\u00e9rminos y Condiciones</a>
        y nuestra
        <a href="/privacidad" style="color:#00d4aa;font-weight:700;text-decoration:underline">Pol\u00edtica de Privacidad</a>.
        L\u00e9elos antes de suscribirte.
      </p>
    </div>
    <div style="margin-top:20px;display:flex;gap:14px;justify-content:center;flex-wrap:wrap">
      <a href="/terminos" style="padding:12px 24px;background:rgba(251,191,36,0.12);border:1px solid rgba(251,191,36,0.4);border-radius:10px;color:#fbbf24;font-weight:700;font-size:13px;text-decoration:none;letter-spacing:0.3px">\U0001f4dc Leer T\u00e9rminos y Condiciones completos</a>
      <a href="/privacidad" style="padding:12px 24px;background:rgba(0,212,170,0.12);border:1px solid rgba(0,212,170,0.4);border-radius:10px;color:#00d4aa;font-weight:700;font-size:13px;text-decoration:none;letter-spacing:0.3px">\U0001f512 Leer Pol\u00edtica de Privacidad</a>
    </div>
  </div>
</section>

<!-- FOOTER -->
<footer class="footer">
  <div class="footer-links">
    <a href="#en-vivo">\U0001f4ca <span data-i18n="footer.dashboard">En Vivo</span></a>
    <a href="/terminos">\U0001f4dc <span data-i18n="footer.terms">T\u00e9rminos</span></a>
    <a href="/privacidad">\U0001f512 <span data-i18n="footer.privacy">Privacidad</span></a>
    <a href="https://t.me/BUYSELL_365_24_7" target="_blank">\U0001f4e2 <span data-i18n="footer.telegram">Telegram</span></a>
    <a href="https://www.instagram.com/buysell365.pro_tradingsignals/" target="_blank" style="display:inline-flex;align-items:center;gap:4px"><svg viewBox="0 0 24 24" width="14" height="14" fill="url(#ig-grad-ft)"><defs><linearGradient id="ig-grad-ft" x1="0%" y1="100%" x2="100%" y2="0%"><stop offset="0%" style="stop-color:#f09433"/><stop offset="50%" style="stop-color:#dc2743"/><stop offset="100%" style="stop-color:#bc1888"/></linearGradient></defs><path d="M12 2.163c3.204 0 3.584.012 4.85.07 3.252.148 4.771 1.691 4.919 4.919.058 1.265.069 1.645.069 4.849 0 3.205-.012 3.584-.069 4.849-.149 3.225-1.664 4.771-4.919 4.919-1.266.058-1.644.07-4.85.07-3.204 0-3.584-.012-4.849-.07-3.26-.149-4.771-1.699-4.919-4.92-.058-1.265-.07-1.644-.07-4.849 0-3.204.013-3.583.07-4.849.149-3.227 1.664-4.771 4.919-4.919 1.266-.057 1.645-.069 4.849-.069zM12 0C8.741 0 8.333.014 7.053.072 2.695.272.273 2.69.073 7.052.014 8.333 0 8.741 0 12c0 3.259.014 3.668.072 4.948.2 4.358 2.618 6.78 6.98 6.98C8.333 23.986 8.741 24 12 24c3.259 0 3.668-.014 4.948-.072 4.354-.2 6.782-2.618 6.979-6.98.059-1.28.073-1.689.073-4.948 0-3.259-.014-3.667-.072-4.947-.196-4.354-2.617-6.78-6.979-6.98C15.668.014 15.259 0 12 0zm0 5.838a6.162 6.162 0 100 12.324 6.162 6.162 0 000-12.324zM12 16a4 4 0 110-8 4 4 0 010 8zm6.406-11.845a1.44 1.44 0 100 2.881 1.44 1.44 0 000-2.881z"/></svg>Instagram</a>
    <a href="mailto:soporte@buysell365.pro">\U0001f4e7 <span data-i18n="footer.email">soporte@buysell365.pro</span></a>
  </div>

  <!-- \u2550\u2550\u2550 XM PARTNER (FIX 2026-04-27) \u2550\u2550\u2550 -->
  <div style="margin:24px auto 20px;padding:18px 16px;max-width:680px;border-top:1px solid rgba(251,191,36,0.25);border-bottom:1px solid rgba(251,191,36,0.25);background:rgba(251,191,36,0.04);border-radius:8px">
    <div style="font-size:0.95rem;font-weight:800;color:#fbbf24;letter-spacing:0.4px;margin-bottom:8px">
      \U0001f91d <span data-i18n="footer.xm_partner_label">Socio Oficial XM Global</span>
    </div>
    <p style="font-size:0.82rem;color:var(--text2);line-height:1.5;margin:0 0 10px">
      <span data-i18n="footer.xm_intro">Operamos con XM Global. Abre tu cuenta real con nuestro c\u00f3digo de afiliado oficial:</span>
      &nbsp;<span data-i18n="footer.xm_code_label">C\u00f3digo</span>: <strong style="color:#00ffcc;letter-spacing:1px">BUYSELL365</strong>
    </p>
    <div style="display:flex;gap:8px;justify-content:center;flex-wrap:wrap;margin:12px 0 10px">
      <a href="https://clicks.pipaffiliates.com/c?c=1197262&amp;l=en&amp;p=1" target="_blank" rel="nofollow noopener"
         style="padding:8px 18px;background:linear-gradient(135deg,#fbbf24,#f59e0b);color:#07091f;border-radius:8px;font-weight:800;font-size:0.82rem;text-decoration:none;letter-spacing:0.3px">
        \U0001f7e2 <span data-i18n="footer.xm_open_btn">Abrir Cuenta</span>
      </a>
      <a href="https://clicks.pipaffiliates.com/c?c=1197262&amp;l=en&amp;p=17" target="_blank" rel="nofollow noopener"
         style="padding:8px 18px;background:transparent;border:1px solid #fbbf24;color:#fbbf24;border-radius:8px;font-weight:700;font-size:0.82rem;text-decoration:none">
        \U0001f4f2 <span data-i18n="footer.xm_app_btn">App XM Global</span>
      </a>
      <a href="https://clicks.pipaffiliates.com/c?c=1197262&amp;l=en&amp;p=6" target="_blank" rel="nofollow noopener"
         style="padding:8px 18px;background:transparent;border:1px solid #00ffcc;color:#00ffcc;border-radius:8px;font-weight:700;font-size:0.82rem;text-decoration:none">
        \U0001f381 <span data-i18n="footer.xm_bonuses_btn">Bonificaciones</span>
      </a>
    </div>
    <p style="font-size:0.7rem;color:#f87171;font-weight:600;margin:6px 0 0">
      \u2139\ufe0f <span data-i18n="footer.xm_disclaimer">Servicio no disponible para residentes de Espa\u00f1a.</span>
    </p>
  </div>

  <div style="text-align:center;margin-top:30px;padding-top:24px;border-top:1px solid rgba(255,255,255,0.06)">
    <p data-i18n="footer.rights" style="font-size:0.85rem;color:var(--text2);margin-bottom:6px">\u00a9 2026 BuySell365 Pro</p>
    <p style="font-size:0.95rem;color:#fbbf24;font-weight:700;letter-spacing:0.3px;margin-bottom:4px" data-i18n="footer.creator">Creador: Emmanuel Diaz</p>
    <p style="font-size:0.7rem;color:#5a6a7a;max-width:520px;margin:8px auto 0;line-height:1.5">
      \u26a0\ufe0f <span data-i18n="footer.disclaimer">Trading con riesgo. Rendimientos pasados no garantizan resultados futuros.</span>
    </p>
  </div>
</footer>

<script>
// ═══════════════════════════════════════════════
//  FLOATING EMOJI PARTICLES — 💰💎🤖📈🎯
// ═══════════════════════════════════════════════
(function(){{
  const c = document.getElementById('particlesCanvas');
  if(!c) return;
  const ctx = c.getContext('2d');
  const emojis = ['\U0001f4b0','\U0001f48e','\U0001f916','\U0001f4c8','\U0001f3af','\U0001f4b5','\U0001f4b8','\U0001f9e0','\u2728','\U0001f680'];
  let W, H, particles = [];
  const MAX = Math.min(50, Math.floor(window.innerWidth / 28));
  function resize(){{ W = c.width = window.innerWidth; H = c.height = window.innerHeight; }}
  resize();
  window.addEventListener('resize', resize);
  function spawn(){{
    return {{
      x: Math.random() * W,
      y: H + 20 + Math.random() * 40,
      vy: -(0.4 + Math.random() * 0.8),
      vx: (Math.random() - 0.5) * 0.4,
      size: 22 + Math.random() * 18,
      emoji: emojis[Math.random() * emojis.length | 0],
      alpha: 0.4 + Math.random() * 0.5,
      rot: Math.random() * 6.28,
      vr: (Math.random() - 0.5) * 0.015
    }};
  }}
  for(let i = 0; i < MAX; i++){{
    const p = spawn();
    p.y = Math.random() * H;
    particles.push(p);
  }}
  function draw(){{
    ctx.clearRect(0, 0, W, H);
    for(let i = particles.length - 1; i >= 0; i--){{
      const p = particles[i];
      p.x += p.vx;
      p.y += p.vy;
      p.rot += p.vr;
      if(p.y < -30){{ particles[i] = spawn(); continue; }}
      ctx.save();
      ctx.translate(p.x, p.y);
      ctx.rotate(p.rot);
      ctx.globalAlpha = p.alpha;
      ctx.font = p.size + 'px serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(p.emoji, 0, 0);
      ctx.restore();
    }}
    requestAnimationFrame(draw);
  }}
  draw();
}})();

// ═══════════════════════════════════════════════
//  i18n ENGINE — BuySell365 Multi-Language System
// ═══════════════════════════════════════════════
(function(){{
  const FLAGS = {{es:'\U0001f1ea\U0001f1f8',en:'\U0001f1fa\U0001f1f8',pt:'\U0001f1e7\U0001f1f7',fr:'\U0001f1eb\U0001f1f7'}};
  const SUPPORTED = ['es','en','pt','fr'];
  let currentLang = 'es';
  let translations = {{}};

  function detectLang(){{
    const saved = localStorage.getItem('buysell365_lang');
    if(saved && SUPPORTED.includes(saved)) return saved;
    const nav = (navigator.language || navigator.userLanguage || 'es').toLowerCase();
    if(nav.startsWith('en')) return 'en';
    if(nav.startsWith('pt')) return 'pt';
    if(nav.startsWith('fr')) return 'fr';
    return 'es';
  }}

  function applyTranslations(tr){{
    document.querySelectorAll('[data-i18n]').forEach(function(el){{
      const key = el.getAttribute('data-i18n');
      if(tr[key]){{
        let text = tr[key];
        const vars = el.getAttribute('data-i18n-vars');
        if(vars){{
          try{{
            const obj = JSON.parse(vars);
            Object.keys(obj).forEach(function(k){{ text = text.replace('{{'+k+'}}', obj[k]); }});
          }}catch(e){{}}
        }}
        if(text.includes('<') && text.includes('>')){{
          el.innerHTML = text;
        }}else{{
          el.textContent = text;
        }}
      }}
    }});
    document.documentElement.lang = currentLang;
    const flagEl = document.getElementById('currentFlag');
    if(flagEl) flagEl.textContent = FLAGS[currentLang] || '\U0001f1ea\U0001f1f8';
    // FIX 2026-05-09: marcar bandera activa en barras desktop+mobile
    document.querySelectorAll('.lang-bar-desktop .lang-flag, .mob-lang-bar .mob-flag').forEach(function(btn){{
      if(btn.getAttribute('data-lang') === currentLang){{
        btn.classList.add('active');
      }}else{{
        btn.classList.remove('active');
      }}
    }});
  }}

  function loadLang(lang, callback){{
    if(!SUPPORTED.includes(lang)) lang = 'es';
    fetch('/i18n/' + lang + '.json')
      .then(function(r){{ return r.json(); }})
      .then(function(data){{
        translations = data;
        window.translations = data;
        currentLang = lang;
        localStorage.setItem('buysell365_lang', lang);
        applyTranslations(data);
        if(callback) callback();
      }})
      .catch(function(err){{
        console.warn('i18n load failed:', err);
      }});
  }}

  window.toggleLangMenu = function(){{
    const menu = document.getElementById('langMenu');
    if(menu) menu.classList.toggle('show');
  }};

  window.setLang = function(lang){{
    loadLang(lang);
    const menu = document.getElementById('langMenu');
    if(menu) menu.classList.remove('show');
  }};

  document.addEventListener('click', function(e){{
    const sel = document.getElementById('langSelector');
    if(sel && !sel.contains(e.target)){{
      const menu = document.getElementById('langMenu');
      if(menu) menu.classList.remove('show');
    }}
  }});

  const lang = detectLang();
  if(lang !== 'es'){{
    loadLang(lang);
  }}else{{
    currentLang = 'es';
    localStorage.setItem('buysell365_lang', 'es');
    const flagEl = document.getElementById('currentFlag');
    if(flagEl) flagEl.textContent = FLAGS['es'];
  }}
}})();

// ═══════════════════════════════════════════════
//  CALCULADORA INTERACTIVA — pips reales del periodo
// ═══════════════════════════════════════════════
(function(){{
  const lotInput = document.getElementById('calcLot');
  const lotDisplay = document.getElementById('calcLotDisplay');
  const result = document.getElementById('calcResult');
  const detail = document.getElementById('calcDetail');
  const btns = document.querySelectorAll('.calcPeriodBtn');
  if(!lotInput || !result) return;

  // Pips reales por periodo (calculados en server-side)
  const pipsByPeriod = {{
    '1': {pips_today_real},
    '7': {pips_week_real},
    '30': {pips_month_real}
  }};

  let currentPeriod = '7';

  function calc(){{
    const lotRaw = parseInt(lotInput.value);
    const lot = lotRaw / 100;
    lotDisplay.textContent = lot.toFixed(2);
    const pips = pipsByPeriod[currentPeriod] || 0;
    // Aprox: 1 pip * 0.10 lote = €1 (caso XAUUSD/forex mayor)
    // Mas realista: pips * lote * 10 (XAU) o pips * lote (forex)
    // Conservador: usar conversion ~1pip * lote * 10 EUR
    const eur = pips * lot * 10;
    const sign = eur >= 0 ? '+' : '';
    result.textContent = sign + '€' + Math.round(eur).toLocaleString('es-ES');
    result.style.color = eur >= 0 ? '#00ffcc' : '#ff6b6b';
    const periodLabel = currentPeriod === '1' ? 'hoy' : (currentPeriod === '7' ? 'esta semana' : 'este mes');
    detail.textContent = sign + Math.round(pips).toLocaleString('es-ES') + ' pips netos ' + periodLabel + ' x ' + lot.toFixed(2) + ' lote';
  }}

  lotInput.addEventListener('input', calc);
  btns.forEach(b => {{
    b.addEventListener('click', function(){{
      btns.forEach(x => {{
        x.classList.remove('active');
        x.style.background = 'rgba(0,212,170,0.15)';
        x.style.borderColor = 'rgba(0,212,170,0.4)';
        x.style.color = '#00d4aa';
      }});
      this.classList.add('active');
      this.style.background = 'rgba(0,212,170,0.4)';
      this.style.borderColor = '#00d4aa';
      this.style.color = '#fff';
      currentPeriod = this.getAttribute('data-days');
      calc();
    }});
  }});
  calc();
}})();

// ═══════════════════════════════════════════════
//  COUNTDOWN — Lanzamiento hasta fin de verano (urgencia honesta)
// ═══════════════════════════════════════════════
(function(){{
  const el = document.getElementById('vipCountdownTimer');
  if(!el) return;
  // Deadline = 31 agosto 2026 23:59 (cierre de oferta de lanzamiento)
  // Si ya paso, rota al fin del trimestre siguiente (3 meses adelante)
  function getDeadline(){{
    const now = new Date();
    let dl = new Date(2026, 7, 31, 23, 59, 0); // mes 7 = Agosto (0-indexed)
    while(dl < now){{
      dl = new Date(dl.getFullYear(), dl.getMonth() + 3, 0, 23, 59, 0);
    }}
    return dl;
  }}
  function tick(){{
    const now = new Date();
    const dl = getDeadline();
    const diff = dl - now;
    const d = Math.floor(diff / 86400000);
    const h = Math.floor((diff % 86400000) / 3600000);
    const m = Math.floor((diff % 3600000) / 60000);
    el.textContent = d + 'd ' + String(h).padStart(2,'0') + 'h ' + String(m).padStart(2,'0') + 'm';
  }}
  tick();
  setInterval(tick, 30000);
}})();

// ═══════════════════════════════════════════════
//  HAMBURGER MOBILE MENU
// ═══════════════════════════════════════════════
window.toggleMobileMenu = function(){{
  const menu = document.getElementById('mobileMenu');
  const btn = document.getElementById('hamburgerBtn');
  if(menu && btn){{
    menu.classList.toggle('show');
    btn.classList.toggle('active');
    document.body.style.overflow = menu.classList.contains('show') ? 'hidden' : '';
  }}
}};
window.closeMobileMenu = function(){{
  const menu = document.getElementById('mobileMenu');
  const btn = document.getElementById('hamburgerBtn');
  if(menu) menu.classList.remove('show');
  if(btn) btn.classList.remove('active');
  document.body.style.overflow = '';
}};

// ═══════════════════════════════════════════════
//  SMOOTH SCROLL
// ═══════════════════════════════════════════════
document.querySelectorAll('a[href^="#"]').forEach(function(a){{
  a.addEventListener('click', function(e){{
    const id = this.getAttribute('href');
    if(id && id.length > 1){{
      const target = document.querySelector(id);
      if(target){{
        e.preventDefault();
        target.scrollIntoView({{ behavior:'smooth', block:'start' }});
      }}
    }}
  }});
}});

// ═══════════════════════════════════════════════
//  FADE-IN ON SCROLL (IntersectionObserver)
// ═══════════════════════════════════════════════
(function(){{
  const observer = new IntersectionObserver(function(entries){{
    entries.forEach(function(entry){{
      if(entry.isIntersecting){{
        entry.target.classList.add('visible');
        observer.unobserve(entry.target);
      }}
    }});
  }}, {{ threshold: 0.02 }});
  document.querySelectorAll('.fade-in').forEach(function(el){{
    observer.observe(el);
  }});
}})();

// ═══════════════════════════════════════════════
//  BACK TO TOP BUTTON + NAV SCROLL
// ═══════════════════════════════════════════════
(function(){{
  const btn = document.getElementById('backToTop');
  const nav = document.querySelector('.nav');
  window.addEventListener('scroll', function(){{
    if(btn){{
      if(window.scrollY > 600) btn.classList.add('show');
      else btn.classList.remove('show');
    }}
    if(nav){{
      if(window.scrollY > 60) nav.classList.add('scrolled');
      else nav.classList.remove('scrolled');
    }}
  }});
}})();

// ═══════════════════════════════════════════════
//  ANIMATED COUNTERS — Stats Bar count-up
// ═══════════════════════════════════════════════
(function(){{
  let fired = false;
  function animateCounter(el, target, suffix){{
    const dur = 1800;
    const start = performance.now();
    function tick(now){{
      const p = Math.min((now - start) / dur, 1);
      const ease = 1 - Math.pow(1 - p, 3);
      const val = Math.round(ease * target);
      if(suffix === '%') el.textContent = val + '%';
      else if(suffix === '+') el.textContent = val.toLocaleString() + '+';
      else el.textContent = (target >= 0 ? '+' : '') + val.toLocaleString();
      if(p < 1) requestAnimationFrame(tick);
    }}
    requestAnimationFrame(tick);
  }}
  const bar = document.getElementById('statsBar');
  if(!bar) return;

  // FIX 2026-05-09: si llegan a la pagina con hash (ej: #whatsapp-alerts) que
  // mueve el viewport mas abajo del statsBar, el IntersectionObserver no dispara
  // y los contadores quedan en "0%". Solucion: si NO esta visible al cargar,
  // setear los valores finales directamente (sin animacion).
  function setFinalValues(){{
    const wr = document.getElementById('counterWr');
    const tot = document.getElementById('counterTotal');
    const pips = document.getElementById('counterPips');
    if(wr){{
      const v = parseInt(wr.getAttribute('data-target'));
      if(!isNaN(v)) wr.textContent = v + '%';
    }}
    if(tot){{
      const v = parseInt(tot.getAttribute('data-target'));
      if(!isNaN(v)) tot.textContent = v.toLocaleString() + '+';
    }}
    if(pips){{
      const v = parseInt((pips.getAttribute('data-target') || '0').replace(/[+,]/g,''));
      if(!isNaN(v)) pips.textContent = (v >= 0 ? '+' : '') + v.toLocaleString();
    }}
  }}

  function fireAnimation(){{
    if(fired) return;
    fired = true;
    const wr = document.getElementById('counterWr');
    const tot = document.getElementById('counterTotal');
    const pips = document.getElementById('counterPips');
    if(wr) animateCounter(wr, parseInt(wr.getAttribute('data-target')), '%');
    if(tot) animateCounter(tot, parseInt(tot.getAttribute('data-target')), '+');
    if(pips){{
      const pv = parseInt(pips.getAttribute('data-target').replace(/[+,]/g,''));
      animateCounter(pips, pv, 'pips');
    }}
  }}

  // Si el statsBar ya esta visible al cargar -> animar. Si no -> setear final.
  const initialRect = bar.getBoundingClientRect();
  const initiallyVisible = initialRect.top < window.innerHeight && initialRect.bottom > 0;
  if(initiallyVisible){{
    fireAnimation();
  }}else{{
    // No visible al cargar -> setear valor final directamente para que cuando
    // el usuario haga scroll hacia arriba vea los numeros correctos sin "0%".
    setFinalValues();
    fired = true;  // marca como ya disparado
  }}

  // Por seguridad, mantener el observer (si por alguna razon initialRect estaba
  // mal calculado, el observer dispara cuando entre en viewport).
  const obs = new IntersectionObserver(function(entries){{
    if(entries[0].isIntersecting && !fired){{
      fireAnimation();
      obs.disconnect();
    }}
  }}, {{threshold: 0.3}});
  obs.observe(bar);
}})();

// countdown removed
</script>

<!-- GDPR Cookie Consent Banner -->
<div id="bs365-cb" style="display:none;position:fixed;bottom:0;left:0;right:0;z-index:99999;background:#0d1117;border-top:2px solid #00e5c5;padding:16px 24px;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap;box-shadow:0 -4px 30px rgba(0,0,0,.8)">
  <div style="flex:1;min-width:240px">
    <p style="margin:0 0 4px;font-weight:700;color:#f0f6ff;font-size:.95rem">🍪 Usamos cookies</p>
    <p style="margin:0;color:#8b9fc4;font-size:.82rem">Usamos Google Analytics para mejorar la experiencia. No vendemos datos personales. <a href="/privacidad" style="color:#00e5c5;text-decoration:underline">Política de privacidad</a></p>
  </div>
  <div style="display:flex;gap:10px;flex-shrink:0;margin-top:4px">
    <button onclick="_declineCookies()" style="padding:10px 18px;border-radius:8px;border:1px solid #2a3045;background:transparent;color:#8b9fc4;cursor:pointer;font-size:.85rem;font-family:inherit">Solo esenciales</button>
    <button onclick="_acceptCookies()" style="padding:10px 22px;border-radius:8px;border:none;background:linear-gradient(135deg,#00e5c5,#00a89d);color:#000;font-weight:800;cursor:pointer;font-size:.85rem;font-family:inherit">✓ Aceptar todo</button>
  </div>
</div>
</body>
</html>"""
    _resp = make_response(_html)
    _resp.headers['Content-Type'] = 'text/html; charset=utf-8'
    _resp.headers['Cache-Control'] = 'public, max-age=300'
    return _resp

# ============================================================
#  DASHBOARD (legacy) — redirige a la seccion #en-vivo en /
# ============================================================
@app.route("/dashboard", methods=["GET"])
def dashboard_visual():
    """Ruta legacy — el dashboard ahora vive como seccion #en-vivo en /.
    Mantenemos la ruta con redirect 301 para preservar SEO y bookmarks.
    """
    return redirect("/#en-vivo", code=301)


# ============================================================
#  LEGAL PAGES
# ============================================================
@app.route("/about")
def pagina_about():
    """Ruta legacy — Quienes Somos ahora forma parte de la landing principal.
    Redirect 301 a / para preservar SEO.
    """
    return redirect("/", code=301)


# ══════════════════════════════════════════════════════════════
#  LANDING PAGE /unete — Optimizada para Google Ads / Meta Ads
# ══════════════════════════════════════════════════════════════
@app.route("/unete")
def pagina_unete():
    """Landing page para campañas de ads — conversión directa."""
    # FIX 2026-04-14: HTML estático sin f-string para evitar errores de escapado en Render
    return """<!DOCTYPE html>
<html lang="es">
<head>
<!-- Google tag (gtag.js) -->
<script async src="https://www.googletagmanager.com/gtag/js?id=AW-18090606337"></script>
<script>
  window.dataLayer = window.dataLayer || [];
  function gtag(){dataLayer.push(arguments);}
  gtag('js', new Date());
  gtag('config', 'AW-18090606337');
</script>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Se&ntilde;ales de Trading con IA H&iacute;brida + Visi&oacute;n &mdash; BuySell365 Pro</title>
<meta name="description" content="Se&ntilde;ales de trading con sistema h&iacute;brido IA + Visi&oacute;n y % de probabilidad por se&ntilde;al. Oro, NASDAQ, S&amp;P 500, Forex. Entry/SL/TP exactos, listos para ejecutar.">
<meta name="keywords" content="se&ntilde;ales trading IA, se&ntilde;ales con probabilidad, sistema h&iacute;brido trading, se&ntilde;ales oro telegram, gold signals AI, BuySell365">
<meta property="og:title" content="Se&ntilde;ales con IA H&iacute;brida + Visi&oacute;n &mdash; % de Probabilidad por Se&ntilde;al">
<meta property="og:description" content="Cada se&ntilde;al pasa por dos capas (an&aacute;lisis t&eacute;cnico + IA con visi&oacute;n) y llega con un % de probabilidad.">
<meta property="og:image" content="https://buysell365.pro/img/og_image.png">
<meta property="og:url" content="https://buysell365.pro/unete">
<meta property="og:type" content="website">
<link rel="canonical" href="https://buysell365.pro/unete">
<link rel="icon" href="/img/bull_bear.png" type="image/png">
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Inter',system-ui,sans-serif;background:#07091f;color:#f0f6ff;min-height:100vh}
.hero{text-align:center;padding:60px 24px 40px;background:linear-gradient(180deg,#0a1628 0%,#07091f 100%)}
.hero h1{font-size:2.4rem;margin-bottom:16px;line-height:1.2}
.hero h1 span{background:linear-gradient(135deg,#00ffcc,#4d9fff);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.hero p{font-size:1.15rem;color:#8b9fc4;max-width:600px;margin:0 auto 32px}
.stats{display:flex;gap:24px;justify-content:center;flex-wrap:wrap;margin:32px 0}
.stat{background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.1);border-radius:12px;padding:16px 24px;min-width:120px}
.stat .num{font-size:1.8rem;font-weight:800;color:#00ffcc}
.stat .label{font-size:0.8rem;color:#8b9fc4;margin-top:4px}
.signal-example{background:rgba(0,255,204,.05);border:1px solid rgba(0,255,204,.15);border-radius:16px;padding:24px;max-width:400px;margin:32px auto;text-align:left;font-family:monospace;font-size:0.95rem;line-height:1.8}
.signal-example .title{color:#00ffcc;font-weight:bold;font-size:1.1rem}
.pares{display:flex;flex-wrap:wrap;gap:10px;justify-content:center;margin:24px auto;max-width:600px}
.par{background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.08);border-radius:8px;padding:8px 16px;font-size:0.85rem;color:#8b9fc4}
.benefits{max-width:600px;margin:40px auto;text-align:left;padding:0 24px}
.benefit{display:flex;align-items:flex-start;gap:12px;margin-bottom:20px}
.benefit .icon{font-size:1.4rem;flex-shrink:0}
.benefit .text h3{font-size:1rem;margin-bottom:4px}
.benefit .text p{font-size:0.9rem;color:#8b9fc4}
.cta-section{text-align:center;padding:40px 24px 60px}
.btn{display:inline-block;padding:16px 40px;border-radius:12px;font-size:1.1rem;font-weight:700;text-decoration:none;margin:8px;transition:transform .2s}
.btn:hover{transform:translateY(-2px)}
.btn-green{background:linear-gradient(135deg,#00ffcc,#00d4aa);color:#07091f}
.btn-blue{background:linear-gradient(135deg,#4d9fff,#2563eb);color:#fff}
.btn-gold{background:linear-gradient(135deg,#fbbf24,#f59e0b);color:#07091f}
.trust{text-align:center;padding:20px;color:#8b9fc4;font-size:0.85rem;border-top:1px solid rgba(255,255,255,.05)}
.note{font-size:0.8rem;color:#555;text-align:center;margin-top:8px}
@media(max-width:640px){
.hero h1{font-size:1.8rem}
.stats{gap:12px}
.stat{min-width:100px;padding:12px 16px}
.stat .num{font-size:1.4rem}
.btn{display:block;margin:8px 24px}
}
</style>
</head>
<body>

<div class="hero">
<h1>Recibe <span>se&ntilde;ales de trading</span> gratis en Telegram</h1>
<p>Oro, Forex, NASDAQ y m&aacute;s &mdash; con Entrada, TP y SL exactos. Nuestro bot de IA analiza el mercado y te env&iacute;a las mejores oportunidades.</p>

<div class="stats">
<div class="stat"><div class="num">24/7</div><div class="label">Monitoreo</div></div>
<div class="stat"><div class="num">+15</div><div class="label">Activos</div></div>
<div class="stat"><div class="num">IA + Visi&oacute;n</div><div class="label">Sistema H&iacute;brido</div></div>
<div class="stat"><div class="num">100%</div><div class="label">Transparente</div></div>
</div>
</div>

<div class="signal-example">
<div class="title">&#x1F7E2; BUY &mdash; ORO</div>
<br>
&#x1F4CD; Entry: 4750.00<br>
&#x1F3AF; TP1: 4760.00<br>
&#x1F3AF; TP2: 4770.00<br>
&#x1F3AF; TP3: 4780.00<br>
&#x1F6E1;&#xFE0F; SL: 4740.00<br>
<br>
<div style="border-top:1px dashed rgba(0,255,204,.3);padding-top:8px;margin-top:4px">
&#x1F4CA; <strong style="color:#00ffcc">Probabilidad: 78%</strong>
</div>
<br>
<span style="color:#8b9fc4;font-size:0.8rem">(Cada se&ntilde;al incluye un % calculado en tiempo real por nuestro sistema h&iacute;brido IA + Visi&oacute;n)</span>
</div>

<h2 style="text-align:center;margin:32px 0 16px;font-size:1.3rem">Pares que operamos</h2>
<div class="pares">
<div class="par">&#x1F947; GOLD</div>
<div class="par">&#x1F4B1; EUR/USD</div>
<div class="par">&#x1F4B1; GBP/USD</div>
<div class="par">&#x1F4B1; USD/JPY</div>
<div class="par">&#x1F4B1; USD/CAD</div>
<div class="par">&#x1F4B1; AUD/USD</div>
<div class="par">&#x1F4B1; GBP/JPY</div>
<div class="par">&#x1F4B1; EUR/JPY</div>
<div class="par">&#x1F4B1; EUR/CHF</div>
<div class="par">&#x1F4C8; NASDAQ</div>
<div class="par">&#x1F4C8; US30</div>
<div class="par">&#x1F4C8; S&amp;P 500</div>
</div>

<div class="benefits">
<div class="benefit">
<div class="icon">&#x1F916;</div>
<div class="text"><h3>Inteligencia Artificial</h3><p>Nuestro bot analiza RSI, MACD, Bollinger, noticias econ&oacute;micas y datos institucionales en tiempo real.</p></div>
</div>
<div class="benefit">
<div class="icon">&#x26A1;</div>
<div class="text"><h3>Alertas instant&aacute;neas</h3><p>Recibes cada se&ntilde;al directo en Telegram con Entrada, TP y SL exactos. Sin retrasos.</p></div>
</div>
<div class="benefit">
<div class="icon">&#x1F4CA;</div>
<div class="text"><h3>Resultados transparentes</h3><p>Dashboard en vivo en buysell365.pro. Cada operaci&oacute;n del canal VIP publicada en tiempo real.</p></div>
</div>
</div>

<div class="cta-section">
<h2 style="font-size:1.5rem;margin-bottom:8px">Empieza ahora &mdash; es gratis</h2>
<p style="color:#8b9fc4;margin-bottom:24px">&Uacute;nete al grupo p&uacute;blico y ve las se&ntilde;ales en acci&oacute;n</p>
<a href="https://t.me/BUYSELL_365_24_7" class="btn btn-green">&#x1F4F1; Unirme al Grupo Gratis</a>
<br>
<a href="https://t.me/BuySell365_bot?start=vip" class="btn btn-blue">&#x1F48E; Canal VIP &mdash; Se&ntilde;ales Premium</a>
<p class="note">Sin tarjeta. Sin compromiso. Cancela cuando quieras.</p>
</div>

<div class="trust">
<p>BuySell365 Pro &mdash; Se&ntilde;ales transparentes, publicadas en tiempo real</p>
<p style="margin-top:8px"><a href="https://buysell365.pro" style="color:#4d9fff;text-decoration:none">buysell365.pro</a> &middot; <a href="https://buysell365.pro/terminos" style="color:#4d9fff;text-decoration:none">T&eacute;rminos</a></p>
</div>

</body>
</html>"""


@app.route("/terminos")
def pagina_terminos():
    return f"""<!DOCTYPE html>
<html lang="es"><head><script>(function(){{var c=localStorage.getItem('bs365_consent');function _loadGA(){{if(window._ga_loaded)return;window._ga_loaded=true;var s=document.createElement('script');s.async=true;s.src='https://www.googletagmanager.com/gtag/js?id=G-L514BL7E83';document.head.appendChild(s);window.dataLayer=window.dataLayer||[];window.gtag=function(){{dataLayer.push(arguments);}};gtag('js',new Date());gtag('config','G-L514BL7E83');}}if(c==='accepted')_loadGA();}})();</script><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>T&eacute;rminos y Condiciones — BuySell365 Pro</title>
<style>*{{margin:0;padding:0;box-sizing:border-box}}body{{font-family:'Inter',sans-serif;background:#0d1117;color:#c9d1d9;line-height:1.7;padding:20px}}.container{{max-width:800px;margin:0 auto}}h1{{color:#f0b90b;font-size:1.8rem;margin-bottom:10px}}h2{{color:#58a6ff;font-size:1.2rem;margin-top:25px;margin-bottom:8px}}p,li{{font-size:0.95rem;margin-bottom:8px}}ul{{padding-left:20px}}.date{{color:#8b949e;font-size:0.85rem;margin-bottom:20px}}a{{color:#58a6ff}}.back{{display:inline-block;margin-top:30px;padding:10px 20px;background:#f0b90b;color:#000;border-radius:8px;text-decoration:none;font-weight:bold}}</style></head>
<body><div class="container">
<h1>&#128221; T&eacute;rminos y Condiciones</h1><p class="date">&Uacute;ltima actualizaci&oacute;n: 8 de mayo de 2026</p>
<h2>1. Qu&eacute; es BuySell365 Pro</h2><p>BuySell365 Pro es un servicio automatizado que genera alertas informativas sobre activos financieros mediante un sistema h&iacute;brido propietario de Inteligencia Artificial. Cada se&ntilde;al pasa por dos capas independientes de an&aacute;lisis: indicadores t&eacute;cnicos en vivo sobre datos de mercado, y un modelo de visi&oacute;n por computadora basado en Claude Sonnet 4.6 (Anthropic) que analiza la gr&aacute;fica del activo en el momento de la entrada. El resultado es un porcentaje de probabilidad estad&iacute;stica que acompa&ntilde;a a cada se&ntilde;al. Las alertas son de car&aacute;cter informativo y educativo &mdash; no son recomendaciones de inversi&oacute;n personalizadas.</p>
<h2>2. No es Asesor&iacute;a Financiera</h2><p>BuySell365 Pro NO proporciona asesor&iacute;a financiera personalizada. Cada usuario es responsable de sus propias decisiones de inversi&oacute;n. El % de probabilidad mostrado es una estimaci&oacute;n estad&iacute;stica calculada en tiempo real, NO una garant&iacute;a de resultado. Consulta a un profesional financiero antes de operar con dinero real.</p>
<h2>3. Riesgo de Inversi&oacute;n</h2><p>Operar en mercados financieros conlleva un alto riesgo de p&eacute;rdida de capital. Los resultados pasados no garantizan resultados futuros. Una se&ntilde;al con probabilidad alta puede igualmente terminar en p&eacute;rdida. Nunca inviertas dinero que no puedas permitirte perder.</p>
<h2>4. Suscripci&oacute;n VIP</h2><ul><li>Precio: &euro;149/mes (equivalente en USDT TRC20), pagado manualmente.</li><li>Incluye: se&ntilde;ales del canal VIP con sistema h&iacute;brido IA + Visi&oacute;n, % de probabilidad por se&ntilde;al, an&aacute;lisis bajo petici&oacute;n y soporte directo.</li><li>Sin cobros autom&aacute;ticos ni recurrentes. T&uacute; decides cu&aacute;ndo renovar.</li><li>Cancela cuando quieras: simplemente no renuevas y el acceso expira al final del periodo pagado.</li><li>No se ofrecen reembolsos una vez procesado el pago, ya que el acceso al canal VIP se activa de forma inmediata.</li></ul>
<h2>5. Sistema de Probabilidad</h2><p>El porcentaje de probabilidad que aparece en cada se&ntilde;al se calcula combinando dos capas: (a) an&aacute;lisis t&eacute;cnico cuantitativo en tiempo real (EMA, RSI, ATR, R:R, tendencia M15/H1) y (b) an&aacute;lisis cualitativo de la gr&aacute;fica mediante un modelo de visi&oacute;n IA. El servicio nunca filtra ni descarta se&ntilde;ales en base al %; todas las se&ntilde;ales se publican y la decisi&oacute;n final de operar corresponde al usuario. Si el sistema de probabilidad falla por causas t&eacute;cnicas (API, conectividad), la se&ntilde;al se publica sin la l&iacute;nea de probabilidad.</p>
<h2>6. Uso Aceptable</h2><ul><li>No redistribuir ni revender las se&ntilde;ales del canal VIP.</li><li>No usar bots ni scrapers para extraer contenido del canal.</li><li>No enviar spam en el grupo de Telegram.</li><li>Respetar a los dem&aacute;s miembros de la comunidad.</li></ul>
<h2>7. Limitaci&oacute;n de Responsabilidad</h2><p>BuySell365 Pro y su equipo no ser&aacute;n responsables de p&eacute;rdidas financieras derivadas del uso del servicio. Al usar el servicio aceptas que operas bajo tu propia responsabilidad.</p>
<h2>8. Disponibilidad</h2><p>Trabajamos para mantener el servicio operativo 24/7, pero no garantizamos disponibilidad ininterrumpida. Puede haber pausas por mantenimiento, actualizaciones o dependencias externas (proveedor de IA, broker, Telegram).</p>
<h2>9. Modificaciones</h2><p>Podemos actualizar estos t&eacute;rminos en cualquier momento. Los cambios se publican en esta p&aacute;gina. El uso continuado del servicio implica la aceptaci&oacute;n de los cambios.</p>
<h2>10. Contacto</h2><p><a href="https://t.me/BuySell365Traiding">@BuySell365Traiding</a> en Telegram.</p>
<a href="/" class="back">&larr; Volver</a>
<p style="margin-top:30px;font-size:0.75rem;color:#8b949e;text-align:center">&copy; 2026 BuySell365 Pro &mdash; Creador: Emmanuel Diaz</p>
</div></body></html>"""

@app.route("/privacidad")
def pagina_privacidad():
    return """<!DOCTYPE html>
<html lang="es"><head><script>(function(){var c=localStorage.getItem('bs365_consent');function _loadGA(){if(window._ga_loaded)return;window._ga_loaded=true;var s=document.createElement('script');s.async=true;s.src='https://www.googletagmanager.com/gtag/js?id=G-L514BL7E83';document.head.appendChild(s);window.dataLayer=window.dataLayer||[];window.gtag=function(){dataLayer.push(arguments);};gtag('js',new Date());gtag('config','G-L514BL7E83');}if(c==='accepted')_loadGA();})();</script><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Pol&iacute;tica de Privacidad — BuySell365 Pro</title>
<style>*{margin:0;padding:0;box-sizing:border-box}body{font-family:'Inter',sans-serif;background:#0d1117;color:#c9d1d9;line-height:1.7;padding:20px}.container{max-width:800px;margin:0 auto}h1{color:#f0b90b;font-size:1.8rem;margin-bottom:10px}h2{color:#58a6ff;font-size:1.2rem;margin-top:25px;margin-bottom:8px}p,li{font-size:0.95rem;margin-bottom:8px}ul{padding-left:20px}.date{color:#8b949e;font-size:0.85rem;margin-bottom:20px}a{color:#58a6ff}.back{display:inline-block;margin-top:30px;padding:10px 20px;background:#f0b90b;color:#000;border-radius:8px;text-decoration:none;font-weight:bold}</style></head>
<body><div class="container">
<h1>&#128274; Pol&iacute;tica de Privacidad</h1><p class="date">&Uacute;ltima actualizaci&oacute;n: 8 de mayo de 2026</p>
<h2>1. Datos que Recopilamos</h2><p>Solo datos de Telegram: ID de usuario, nombre de usuario, nombre de pila y estado VIP (activo o expirado). NO recopilamos email, tel&eacute;fono, ubicaci&oacute;n ni datos bancarios.</p>
<h2>2. C&oacute;mo Usamos los Datos</h2><ul><li>Gestionar suscripciones VIP (activaci&oacute;n, expiraci&oacute;n y verificaci&oacute;n de pago).</li><li>Enviarte se&ntilde;ales y notificaciones del servicio.</li><li>Mejorar el servicio mediante estad&iacute;sticas an&oacute;nimas.</li></ul>
<h2>3. Verificaci&oacute;n de Pagos</h2><p>Pagos en USDT TRC20 verificados autom&aacute;ticamente via API de Binance. No almacenamos datos financieros ni n&uacute;meros de wallet.</p>
<h2>4. Servicios de IA de Terceros (Anthropic)</h2><p>Para calcular el % de probabilidad de cada se&ntilde;al, BuySell365 Pro env&iacute;a a la API de Anthropic (Claude Sonnet 4.6 con visi&oacute;n) los siguientes datos: una imagen anonimizada de la gr&aacute;fica del activo financiero analizado y los par&aacute;metros num&eacute;ricos de la se&ntilde;al (Entry, SL, TP, par). NO se env&iacute;a ning&uacute;n dato personal del suscriptor (ID, nombre, username) en estas peticiones. Las pol&iacute;ticas de uso de datos de Anthropic est&aacute;n disponibles en <a href="https://www.anthropic.com/legal/privacy" target="_blank">anthropic.com/legal/privacy</a>.</p>
<h2>5. Almacenamiento y Seguridad</h2><ul><li>Servidor privado con acceso restringido.</li><li>Comunicaci&oacute;n web cifrada con HTTPS.</li><li>Datos en formato JSON en el servidor, sin base de datos externa.</li><li>No compartimos tus datos personales con terceros (excepto los datos t&eacute;cnicos de mercado descritos en la secci&oacute;n 4).</li></ul>
<h2>6. Tus Derechos</h2><p>Tienes derecho a acceso, rectificaci&oacute;n, eliminaci&oacute;n y portabilidad de tus datos. Contacta: <a href="https://t.me/BuySell365Traiding">@BuySell365Traiding</a></p>
<h2>7. Cookies y Analytics</h2><p>Esta web usa Google Analytics (gtag.js) para medir el tr&aacute;fico de forma an&oacute;nima. Google Analytics puede usar cookies propias. No usamos tracking de publicidad ni vendemos datos. Puedes desactivar Google Analytics desde la configuraci&oacute;n de tu navegador.</p>
<a href="/" class="back">&larr; Volver</a>
<p style="margin-top:30px;font-size:0.75rem;color:#8b949e;text-align:center">&copy; 2026 BuySell365 Pro &mdash; Creador: Emmanuel Diaz</p>
</div></body></html>"""


# ══════════════════════════════════════════════════════════════
#  /resultados — Track record completo (FIX 2026-05-08)
# ══════════════════════════════════════════════════════════════
@app.route("/resultados")
def pagina_resultados():
    """Página de track record verificable con stats por mes/par/sesión."""
    from datetime import datetime as _dtr, timedelta as _tdr
    from collections import defaultdict

    with _lock:
        all_trades = list(_copier_trades) if _copier_trades else []

    # Excluir hoy
    try:
        _hoy_r = (_dtr.utcnow() + _tdr(hours=1)).strftime("%d/%m/%Y")
    except Exception:
        _hoy_r = ""
    trades = [t for t in all_trades if t.get("fecha", "") != _hoy_r]

    WIN_R = ("tp", "close_half", "close_partial", "full_close")

    # Stats globales
    total_signals = len(trades)
    wins_total = sum(1 for t in trades if t.get("result") in WIN_R and float(t.get("pips", 0) or 0) > 0)
    losses_total = sum(1 for t in trades if t.get("result") == "sl")
    closed_total = wins_total + losses_total
    wr_total = round(wins_total / closed_total * 100, 1) if closed_total > 0 else 0
    pips_won = round(sum(float(t.get("pips", 0) or 0) for t in trades
                          if t.get("result") in WIN_R and float(t.get("pips", 0) or 0) > 0), 1)
    pips_lost = round(sum(float(t.get("pips", 0) or 0) for t in trades
                           if t.get("result") == "sl"), 1)
    net_total = round(pips_won - pips_lost, 1)

    # Stats por mes
    by_month = defaultdict(lambda: {"wins": 0, "losses": 0, "pips": 0.0})
    for t in trades:
        f = t.get("fecha", "")
        if len(f) != 10:
            continue
        mm_yy = f[3:10]  # MM/YYYY
        p = float(t.get("pips", 0) or 0)
        if t.get("result") in WIN_R and p > 0:
            by_month[mm_yy]["wins"] += 1
            by_month[mm_yy]["pips"] += p
        elif t.get("result") == "sl":
            by_month[mm_yy]["losses"] += 1
            by_month[mm_yy]["pips"] -= p
    months_sorted = sorted(by_month.items(), key=lambda x: (x[0][3:7], x[0][0:2]), reverse=True)

    # Stats por par (top 8)
    by_pair = defaultdict(lambda: {"wins": 0, "losses": 0, "pips": 0.0})
    for t in trades:
        pair = t.get("pair_display") or t.get("pair", "?")
        p = float(t.get("pips", 0) or 0)
        if t.get("result") in WIN_R and p > 0:
            by_pair[pair]["wins"] += 1
            by_pair[pair]["pips"] += p
        elif t.get("result") == "sl":
            by_pair[pair]["losses"] += 1
            by_pair[pair]["pips"] -= p
    pairs_sorted = sorted(by_pair.items(), key=lambda x: -x[1]["pips"])[:8]

    # Render
    months_html = ""
    for mm, st in months_sorted:
        n = st["wins"] + st["losses"]
        wr_m = round(st["wins"] / n * 100, 1) if n > 0 else 0
        pips_color = "#00e676" if st["pips"] >= 0 else "#ff6b35"
        sign = "+" if st["pips"] >= 0 else ""
        months_html += (
            f'<tr style="border-bottom:1px solid rgba(255,255,255,.06)">'
            f'<td style="padding:14px 12px;font-weight:700;color:#fff">{_e(mm)}</td>'
            f'<td style="padding:14px 12px;color:#8b9fc4">{n}</td>'
            f'<td style="padding:14px 12px;color:#00e676">{st["wins"]}W</td>'
            f'<td style="padding:14px 12px;color:#ff6b35">{st["losses"]}L</td>'
            f'<td style="padding:14px 12px;color:#fff;font-weight:700">{wr_m}%</td>'
            f'<td style="padding:14px 12px;text-align:right;color:{pips_color};font-weight:800;font-family:monospace">{sign}{st["pips"]:.1f}</td>'
            f'</tr>'
        )
    if not months_html:
        months_html = '<tr><td colspan="6" style="padding:30px;text-align:center;color:#8b9fc4">Sin datos por ahora</td></tr>'

    pairs_html = ""
    for pair, st in pairs_sorted:
        n = st["wins"] + st["losses"]
        wr_p = round(st["wins"] / n * 100, 1) if n > 0 else 0
        sign = "+" if st["pips"] >= 0 else ""
        bar_w = min(100, int(abs(st["pips"]) / max([abs(s["pips"]) for _, s in pairs_sorted] + [1]) * 100))
        bar_color = "#00e676" if st["pips"] >= 0 else "#ff6b35"
        pairs_html += (
            f'<div style="margin-bottom:14px">'
            f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">'
            f'<span style="font-weight:700;color:#fff;font-size:14px">{_e(pair)}</span>'
            f'<span style="font-family:monospace;font-size:13px"><span style="color:#8b9fc4">{n} ops · {wr_p}% WR</span> · <span style="color:{bar_color};font-weight:800">{sign}{st["pips"]:.0f} pips</span></span>'
            f'</div>'
            f'<div style="height:6px;background:rgba(255,255,255,.06);border-radius:3px;overflow:hidden">'
            f'<div style="width:{bar_w}%;height:100%;background:{bar_color};border-radius:3px"></div>'
            f'</div>'
            f'</div>'
        )

    return f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Track Record Completo — BuySell365 Pro</title>
<meta name="description" content="Historial completo de señales del Canal VIP de BuySell365 Pro. Stats por mes, por par. Resultados publicados en tiempo real.">
<link rel="canonical" href="https://buysell365.pro/resultados">
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:'Inter',system-ui,sans-serif;background:#07091f;color:#f0f6ff;line-height:1.6;min-height:100vh}}
.container{{max-width:1100px;margin:0 auto;padding:32px 20px}}
.back{{display:inline-flex;align-items:center;gap:6px;color:#00d4aa;text-decoration:none;font-size:.9rem;margin-bottom:24px}}
h1{{font-size:clamp(1.8rem,5vw,2.6rem);font-weight:900;margin-bottom:8px;background:linear-gradient(135deg,#fff 30%,#00ffcc 70%);-webkit-background-clip:text;-webkit-text-fill-color:transparent}}
.subtitle{{color:#8b9fc4;font-size:1rem;margin-bottom:28px}}
.kpi-row{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:14px;margin-bottom:32px}}
.kpi{{background:linear-gradient(145deg,rgba(22,32,53,0.95),rgba(14,22,40,0.85));border:1px solid rgba(0,212,170,.2);border-radius:14px;padding:20px;text-align:center}}
.kpi-num{{font-size:1.8rem;font-weight:900;color:#00ffcc}}
.kpi-label{{font-size:.7rem;color:#8b9fc4;text-transform:uppercase;letter-spacing:1.5px;margin-top:4px}}
.section{{background:linear-gradient(145deg,rgba(22,32,53,0.95),rgba(14,22,40,0.85));border:1px solid rgba(255,255,255,.08);border-radius:16px;padding:28px 24px;margin-bottom:24px}}
.section-title{{font-size:1.2rem;font-weight:800;color:#fff;margin-bottom:18px;display:flex;align-items:center;gap:8px}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th{{padding:12px;text-align:left;color:#8b9fc4;font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:1px;border-bottom:1px solid rgba(255,255,255,.1)}}
@media(max-width:600px){{
  table thead{{display:none}}
  table tr{{display:flex;flex-wrap:wrap;border:1px solid rgba(255,255,255,.08);border-radius:10px;margin-bottom:8px;padding:10px;background:rgba(22,32,53,0.6)}}
  table td{{padding:4px 6px!important;flex:1 1 33%;font-size:12px;border:none!important}}
  table td:first-child{{flex:1 1 100%;font-size:14px!important;color:#fff!important}}
}}
</style></head><body>
<div class="container">
<a href="/" class="back">&larr; Volver al inicio</a>
<h1>📊 Track Record Completo</h1>
<p class="subtitle">Historial verificable del Canal VIP. Excluyendo el día actual hasta cierre de jornada.</p>

<div class="kpi-row">
  <div class="kpi"><div class="kpi-num" style="color:#00ffcc">{closed_total}</div><div class="kpi-label">Señales Cerradas</div></div>
  <div class="kpi"><div class="kpi-num" style="color:#00d4aa">{wr_total}%</div><div class="kpi-label">Win Rate</div></div>
  <div class="kpi"><div class="kpi-num" style="color:#fbbf24">+{int(net_total):,}</div><div class="kpi-label">Pips Netos</div></div>
  <div class="kpi"><div class="kpi-num" style="color:#00e676">{wins_total}</div><div class="kpi-label">Ganadoras</div></div>
  <div class="kpi"><div class="kpi-num" style="color:#ff6b35">{losses_total}</div><div class="kpi-label">Perdedoras</div></div>
</div>

<div class="section">
  <div class="section-title">📅 Rendimiento por Mes</div>
  <table>
    <thead><tr><th>Mes</th><th>Total</th><th>Wins</th><th>Losses</th><th>WR</th><th style="text-align:right">Pips Netos</th></tr></thead>
    <tbody>{months_html}</tbody>
  </table>
</div>

<div class="section">
  <div class="section-title">🥇 Top Activos por Rendimiento</div>
  {pairs_html or '<p style="color:#8b9fc4">Sin datos por ahora</p>'}
</div>

<div class="section" style="text-align:center;border:1px solid rgba(0,212,170,.4)">
  <div style="font-size:1.1rem;font-weight:700;color:#fff;margin-bottom:8px">¿Quieres recibir estas señales en tiempo real?</div>
  <p style="color:#8b9fc4;font-size:.9rem;margin-bottom:18px">Cada señal con análisis IA híbrida + % de probabilidad antes de publicar.</p>
  <a href="/#pricing" style="display:inline-block;padding:14px 32px;background:linear-gradient(135deg,#00ffcc,#00d4aa);color:#07091f;border-radius:12px;font-weight:800;text-decoration:none">💎 Ver Planes VIP</a>
</div>

<p style="text-align:center;font-size:.7rem;color:#5a6a7a;margin-top:30px">⚠️ Resultados pasados no garantizan resultados futuros. Operar con riesgo.</p>
</div></body></html>"""


@app.route("/login")
def redirect_to_home():
    return redirect("/")

# ============================================================
#  SEO & PWA ROUTES
# ============================================================
@app.route("/sitemap.xml")
def sitemap():
    base = "https://buysell365.pro"
    urls = [
        {"loc": f"{base}/", "changefreq": "daily", "priority": "1.0"},
        {"loc": f"{base}/terminos", "changefreq": "monthly", "priority": "0.4"},
        {"loc": f"{base}/privacidad", "changefreq": "monthly", "priority": "0.4"},
    ]
    xml_parts = ['<?xml version="1.0" encoding="UTF-8"?>',
                 '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for u in urls:
        xml_parts.append(f'  <url><loc>{u["loc"]}</loc><changefreq>{u["changefreq"]}</changefreq><priority>{u["priority"]}</priority></url>')
    xml_parts.append('</urlset>')
    resp = make_response('\n'.join(xml_parts))
    resp.headers['Content-Type'] = 'application/xml; charset=utf-8'
    resp.headers['Cache-Control'] = 'public, max-age=86400'
    return resp

@app.route("/robots.txt")
def robots():
    content = (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /api/\n"
        "Disallow: /logs\n\n"
        "Sitemap: https://buysell365.pro/sitemap.xml\n"
    )
    resp = make_response(content)
    resp.headers['Content-Type'] = 'text/plain; charset=utf-8'
    resp.headers['Cache-Control'] = 'public, max-age=86400'
    return resp

@app.route("/sw.js")
def service_worker():
    sw_content = """const CACHE_NAME = 'buysell365-v1';
const CACHE_URLS = ['/'];

self.addEventListener('install', function(event) {
  event.waitUntil(
    caches.open(CACHE_NAME).then(function(cache) {
      return cache.addAll(CACHE_URLS);
    }).catch(function() {})
  );
  self.skipWaiting();
});

self.addEventListener('activate', function(event) {
  event.waitUntil(
    caches.keys().then(function(keys) {
      return Promise.all(
        keys.filter(function(k) { return k !== CACHE_NAME; }).map(function(k) { return caches.delete(k); })
      );
    })
  );
  self.clients.claim();
});

self.addEventListener('fetch', function(event) {
  if (event.request.method !== 'GET') return;
  const url = new URL(event.request.url);
  if (url.pathname.startsWith('/api/')) return;
  event.respondWith(
    caches.match(event.request).then(function(cached) {
      const networkFetch = fetch(event.request).then(function(response) {
        if (response && response.status === 200 && response.type === 'basic') {
          const clone = response.clone();
          caches.open(CACHE_NAME).then(function(cache) { cache.put(event.request, clone); });
        }
        return response;
      }).catch(function() { return cached; });
      return cached || networkFetch;
    })
  );
});
"""
    resp = make_response(sw_content)
    resp.headers['Content-Type'] = 'application/javascript; charset=utf-8'
    resp.headers['Cache-Control'] = 'no-cache'
    resp.headers['Service-Worker-Allowed'] = '/'
    return resp

# ============================================================
#  MAIN
# ============================================================
if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port, debug=False)

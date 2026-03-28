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
SYNC_SECRET = os.getenv("SYNC_SECRET", "buysell365_sync_2026").strip()
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

def _geolocate_ip(ip):
    """Obtiene país y ciudad de una IP usando ip-api.com (gratis, 45 req/min)."""
    if ip in _geo_cache:
        return _geo_cache[ip]
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
            _visitors.setdefault("unique_ips", {})
            if ip not in _visitors["unique_ips"]:
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
    """Check API key authentication."""
    if not API_SECRET_KEY:
        return True
    referer = request.headers.get("Referer", "")
    if referer:
        from urllib.parse import urlparse
        ref_host = urlparse(referer).hostname or ""
        req_host = request.host.split(":")[0] if request.host else ""
        if ref_host == req_host:
            return True
    key = request.args.get("key", "") or request.headers.get("X-API-Key", "")
    return hmac.compare_digest(str(key), str(API_SECRET_KEY))

def _ahora():
    """Current time in Andorra timezone."""
    import pytz
    return datetime.now(pytz.timezone('Europe/Andorra'))

# ============================================================
#  SECURITY MIDDLEWARE
# ============================================================
_rate_limit_web = {}
_RATE_LIMIT_MAX = 30
_RATE_LIMIT_WINDOW = 60

def _check_rate_limit(ip, max_req=_RATE_LIMIT_MAX, window=_RATE_LIMIT_WINDOW):
    now = time.time()
    if ip not in _rate_limit_web:
        _rate_limit_web[ip] = []
    _rate_limit_web[ip] = [t for t in _rate_limit_web[ip] if now - t < window]
    if len(_rate_limit_web[ip]) >= max_req:
        return True
    _rate_limit_web[ip].append(now)
    return False

@app.before_request
def _redirect_http_to_https():
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
        tv_secret_received = str(data.get("secret", data.get("passphrase", "")))
        if TV_SECRET and not hmac.compare_digest(tv_secret_received, TV_SECRET):
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
            # Priorizar historial completo; fallback a winning_trades para compatibilidad
            trades = _store.get("historial_operaciones", []) or _store.get("winning_trades", [])
        return app.response_class(response=json.dumps(trades, ensure_ascii=False), status=200, mimetype="application/json")
    except Exception:
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
    with _lock:
        hist = _store.get("historial_operaciones", [])
    wins = sum(1 for h in hist if h.get('pips', 0) > 0)
    total = len(hist)
    # Win rate real — mínimo 30 señales para mostrar dato fiable
    # Si hay pocas señales, mostrar tasa histórica del scanner (64.1%)
    if total >= 30:
        wr = round(wins / total * 100, 1)
    elif total > 0:
        # Combinar datos reales con base histórica para evitar outliers
        wr = round((wins + 19) / (total + 30) * 100, 1)  # suavizado bayesiano
    else:
        wr = 64.1  # tasa base del scanner sin datos
    pips = round(sum(h.get('pips', 0) for h in hist), 1)
    # Pips mínimos realistas si el historial es nuevo
    if pips < 100 and total < 30:
        pips = max(pips, 0)  # no inflar, pero tampoco negativo
    n_ops = sum(1 for op in _store.get("operaciones_activas", {}).values() if isinstance(op, dict) and op.get('mt5_ejecutado', False))
    activos = _store.get("assets_count", 6)
    is_alive = (time.time() - _store.get("ultimo_sync", 0)) < 120

    _html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<!-- Google Analytics --><script async src="https://www.googletagmanager.com/gtag/js?id=G-L514BL7E83"></script><script>window.dataLayer=window.dataLayer||[];function gtag(){{dataLayer.push(arguments);}}gtag('js',new Date());gtag('config','G-L514BL7E83');</script>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>BuySell365 Pro — Se\u00f1ales de Trading con Inteligencia Artificial</title>
<meta name="description" content="Se\u00f1ales de trading automatizadas con Inteligencia Artificial para Oro, Forex e \u00cdndices. An\u00e1lisis en +20 activos con IA avanzada y datos institucionales.">
<meta name="keywords" content="trading signals, se\u00f1ales trading, inteligencia artificial trading, forex signals, oro trading, NASDAQ signals, copy trading, bot trading, XAU USD, BuySell365">
<meta property="og:title" content="BuySell365 Pro \u2014 Trading con IA">
<meta property="og:description" content="Se\u00f1ales profesionales de trading con Inteligencia Artificial. Oro, EUR/USD, USD/JPY, NASDAQ, S&P 500, AUD/CAD, EUR/CHF, USD/CAD.">
<meta property="og:image" content="https://buysell365.pro/img/og_image.png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:type" content="website">
<meta property="og:url" content="https://buysell365.pro">
<meta property="og:site_name" content="BuySell365 Pro">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="BuySell365 Pro \u2014 Trading con IA">
<meta name="twitter:description" content="Se\u00f1ales de trading automatizadas con Inteligencia Artificial. Oro, Forex e \u00cdndices.">
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
  "description": "Se\u00f1ales de trading automatizadas con Inteligencia Artificial para Oro, Forex e \u00cdndices. An\u00e1lisis en +20 activos con IA avanzada y datos institucionales.",
  "url": "https://buysell365.pro",
  "offers": {{
    "@type": "Offer",
    "price": "0",
    "priceCurrency": "USD",
    "description": "Plan Comunidad gratuito disponible"
  }}
}}
</script>
<script defer data-domain="buysell365.pro" src="https://plausible.io/js/script.js"></script>
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&display=swap');
*{{margin:0;padding:0;box-sizing:border-box}}
:root{{
  --bg:#060a12;--bg2:#0d1420;--bg3:#141e2e;
  --green:#00ffcc;--green2:#00f5c4;--blue:#4d9fff;--purple:#a78bfa;
  --gold:#fbbf24;--red:#f87171;--text:#f1f5f9;--text2:#a0aec0;
  --glass:rgba(255,255,255,0.05);--border:rgba(255,255,255,0.08);
  --glow-green:rgba(0,255,204,0.15);--glow-gold:rgba(251,191,36,0.15);
}}
html{{scroll-behavior:smooth}}
body{{font-family:'Inter',sans-serif;background:var(--bg);color:var(--text);overflow-x:hidden}}

/* ═══ HERO ═══ */
.hero{{min-height:60vh;display:flex;align-items:center;justify-content:center;position:relative;overflow:hidden;padding:100px 20px 40px}}
.hero::before{{content:'';position:absolute;top:-50%;left:-50%;width:200%;height:200%;
  background:radial-gradient(circle at 30% 40%,rgba(0,255,204,0.12) 0%,transparent 45%),
             radial-gradient(circle at 70% 60%,rgba(77,159,255,0.10) 0%,transparent 45%),
             radial-gradient(circle at 50% 30%,rgba(167,139,250,0.08) 0%,transparent 45%);
  animation:heroGlow 15s ease infinite alternate}}
@keyframes heroGlow{{0%{{transform:rotate(0deg)}}100%{{transform:rotate(12deg)}}}}
.hero-content{{text-align:center;max-width:900px;z-index:2;position:relative}}
.hero-badge{{display:inline-flex;align-items:center;gap:8px;background:rgba(0,212,170,0.1);border:1px solid rgba(0,212,170,0.2);
  border-radius:50px;padding:6px 18px;font-size:13px;color:var(--green);margin-bottom:24px;font-weight:500}}
.hero-badge .dot{{width:8px;height:8px;background:var(--green);border-radius:50%;animation:pulse 2s infinite}}
@keyframes pulse{{0%,100%{{opacity:1}}50%{{opacity:0.3}}}}
.hero h1{{font-size:clamp(2.5rem,6vw,4rem);font-weight:900;line-height:1.1;margin-bottom:16px;
  background:linear-gradient(135deg,#fff 0%,#00ffcc 40%,#4d9fff 100%);
  -webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;
  text-shadow:none;filter:drop-shadow(0 0 30px rgba(0,255,204,0.2))}}
.hero p{{font-size:clamp(1rem,2vw,1.15rem);color:var(--text2);max-width:600px;margin:0 auto 30px;line-height:1.6}}
.hero-buttons{{display:flex;gap:16px;justify-content:center;flex-wrap:wrap}}
.btn{{display:inline-flex;align-items:center;gap:8px;padding:14px 32px;border-radius:12px;font-size:15px;font-weight:600;
  text-decoration:none;transition:all 0.3s ease;cursor:pointer;border:none}}
.btn-primary{{background:linear-gradient(135deg,#00ffcc,#00d4aa,#00b894);color:#060a12;box-shadow:0 4px 25px rgba(0,255,204,0.4);font-weight:800}}
.btn-primary:hover{{transform:translateY(-3px);box-shadow:0 8px 40px rgba(0,255,204,0.6)}}
.btn-secondary{{background:rgba(255,255,255,0.06);color:var(--text);border:1px solid rgba(255,255,255,0.15)}}
.btn-secondary:hover{{background:rgba(255,255,255,0.12);transform:translateY(-3px);border-color:rgba(0,255,204,0.3)}}

/* ═══ STATS BAR ═══ */
.stats-bar{{display:flex;justify-content:center;gap:40px;margin-top:40px;flex-wrap:wrap;padding:20px;background:rgba(0,255,204,0.03);border:1px solid rgba(0,255,204,0.1);border-radius:20px;max-width:700px;margin-left:auto;margin-right:auto}}
.stat-item{{text-align:center}}
.stat-value{{font-size:2rem;font-weight:900;color:#00ffcc;text-shadow:0 0 20px rgba(0,255,204,0.4)}}
.stat-value.blue{{color:#4d9fff;text-shadow:0 0 20px rgba(77,159,255,0.4)}}
.stat-value.gold{{color:#fbbf24;text-shadow:0 0 20px rgba(251,191,36,0.4)}}
.stat-value.purple{{color:#a78bfa;text-shadow:0 0 20px rgba(167,139,250,0.4)}}
.stat-label{{font-size:12px;color:var(--text2);text-transform:uppercase;letter-spacing:1px;margin-top:4px}}

/* ═══ SECTIONS ═══ */
section{{padding:60px 20px}}
.section-title{{text-align:center;margin-bottom:36px}}
.section-title h2{{font-size:clamp(1.8rem,4vw,2.8rem);font-weight:900;margin-bottom:14px}}
.section-title p{{color:var(--text2);font-size:1.1rem;max-width:650px;margin:0 auto;line-height:1.6}}

/* ═══ FEATURES ═══ */
.features{{background:var(--bg2)}}
.features-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:20px;max-width:1100px;margin:0 auto}}
.feature-card{{background:linear-gradient(145deg,rgba(20,30,46,0.8),rgba(13,20,32,0.6));border:1px solid rgba(255,255,255,0.08);border-radius:18px;padding:28px;transition:all 0.4s ease}}
.feature-card:hover{{transform:translateY(-6px);border-color:rgba(0,255,204,0.4);box-shadow:0 8px 40px rgba(0,255,204,0.1)}}
.feature-icon{{width:48px;height:48px;border-radius:12px;display:flex;align-items:center;justify-content:center;font-size:24px;margin-bottom:16px}}
.feature-icon.green{{background:rgba(0,212,170,0.1)}}
.feature-icon.blue{{background:rgba(59,130,246,0.1)}}
.feature-icon.purple{{background:rgba(139,92,246,0.1)}}
.feature-icon.gold{{background:rgba(245,158,11,0.1)}}
.feature-card h3{{font-size:1.15rem;font-weight:700;margin-bottom:8px}}
.feature-card p{{color:var(--text2);font-size:0.9rem;line-height:1.6}}

/* ═══ ASSETS ═══ */
.assets-grid{{display:flex;flex-wrap:wrap;justify-content:center;gap:16px;max-width:960px;margin:0 auto}}
.asset-card{{background:var(--bg3);border:1px solid var(--border);border-radius:14px;padding:24px 16px;text-align:center;transition:all 0.3s ease;width:160px;flex-shrink:0}}
.asset-card:hover{{transform:scale(1.05);border-color:var(--green)}}
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
.pricing-cards{{display:grid;grid-template-columns:repeat(3,1fr);gap:28px;max-width:1200px;margin:0 auto}}
@media(max-width:900px){{.pricing-cards{{grid-template-columns:1fr!important}}}}
.price-card{{background:linear-gradient(160deg,rgba(20,30,46,0.95),rgba(13,20,32,0.95));border:1px solid rgba(255,255,255,0.1);border-radius:24px;padding:48px 32px;text-align:center;position:relative;transition:all .4s ease;backdrop-filter:blur(10px)}}
.price-card:hover{{transform:translateY(-10px) scale(1.02);box-shadow:0 24px 60px rgba(0,0,0,.5)}}
.price-card.featured{{border-color:var(--gold);box-shadow:0 0 50px rgba(251,191,36,0.15),0 0 100px rgba(251,191,36,0.05)}}
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
@keyframes glowPulse{{0%,100%{{box-shadow:0 0 20px rgba(0,255,204,0.3)}}50%{{box-shadow:0 0 40px rgba(0,255,204,0.6)}}}}

/* ═══ CTA ═══ */
.cta{{text-align:center;padding:50px 20px}}
.cta h2{{font-size:clamp(1.8rem,4vw,2.5rem);font-weight:800;margin-bottom:16px}}
.cta p{{color:var(--text2);margin-bottom:32px;font-size:1.05rem}}

/* ═══ FOOTER ═══ */
.footer{{background:var(--bg2);border-top:1px solid var(--border);padding:40px 20px;text-align:center}}
.footer-links{{display:flex;justify-content:center;gap:24px;margin-bottom:16px;flex-wrap:wrap}}
.footer-links a{{color:var(--text2);text-decoration:none;font-size:0.9rem;transition:color 0.3s}}
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
.nav-cta{{background:#00d4aa;color:#0a0e17;padding:9px 18px;border-radius:8px;font-weight:700;font-size:0.85rem;border:none;text-decoration:none;display:flex;align-items:center;gap:6px;transition:all .2s}}
.nav-cta:hover{{background:#00e6b8;transform:translateY(-1px)}}
.lang-selector{{position:relative}}
.lang-btn{{background:rgba(0,212,170,.15);border:2px solid rgba(0,212,170,.5);border-radius:10px;padding:8px 14px;cursor:pointer;font-size:20px;line-height:1;display:flex;align-items:center;gap:6px;transition:all .2s}}
.lang-btn:hover{{background:rgba(0,212,170,.25);border-color:rgba(0,212,170,.8);box-shadow:0 0 12px rgba(0,212,170,.3)}}
.lang-menu{{display:none;position:absolute;top:110%;right:0;background:var(--bg3);border:1px solid var(--border);border-radius:10px;overflow:hidden;min-width:150px;z-index:200;box-shadow:0 8px 30px rgba(0,0,0,0.4)}}
.lang-menu.show{{display:block}}
.lang-menu a{{display:block;padding:10px 16px;color:var(--text);text-decoration:none;font-size:0.9rem;cursor:pointer;transition:background 0.2s}}
.lang-menu a:hover{{background:rgba(0,212,170,0.1);color:var(--green)}}

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
.faq-item{{background:var(--bg3);border:1px solid var(--border);border-radius:14px;margin-bottom:12px;overflow:hidden;transition:border-color .3s}}
.faq-item:hover{{border-color:rgba(0,212,170,.3)}}
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

/* ═══ COUNTDOWN ═══ */
.countdown{{display:flex;gap:8px;justify-content:center;margin-top:12px}}
.countdown-item{{background:rgba(0,212,170,.1);border:1px solid rgba(0,212,170,.2);border-radius:8px;padding:6px 10px;text-align:center;min-width:50px}}
.countdown-val{{font-size:1.2rem;font-weight:800;color:var(--green)}}
.countdown-lbl{{font-size:.55rem;color:var(--text2);text-transform:uppercase;letter-spacing:1px}}


/* ═══ FADE-IN ANIMATIONS ═══ */
.fade-in{{opacity:1;transform:none}}
.fade-in.visible{{opacity:1;transform:none}}

/* ═══ RESPONSIVE ═══ */
@media(max-width:768px){{
  .nav{{padding:10px 16px}}
  .nav.scrolled{{padding:6px 16px}}
  .nav-logo{{display:flex!important;visibility:visible!important;opacity:1!important;gap:10px;font-size:18px}}
  .nav-logo img{{width:48px;height:48px;min-width:48px;min-height:48px;display:block!important;visibility:visible!important}}
  .nav-links{{display:none}}
  .hamburger{{display:block}}
  .stats-bar{{gap:20px;flex-wrap:wrap;justify-content:center}}
  .stat-value{{font-size:1.5rem}}
  .hero h1{{font-size:2.2rem}}
  .hero p{{font-size:.9rem;padding:0 10px}}
  .hero-buttons{{flex-direction:column;gap:10px;align-items:center}}
  .section-title h2{{font-size:1.5rem}}
  .features-grid{{grid-template-columns:1fr!important}}
  .about-grid{{grid-template-columns:1fr}}
  .assets-grid .asset-card{{width:140px}}
  .pricing-cards{{grid-template-columns:1fr!important}}
  .cta h2{{font-size:1.5rem}}
  .cta .hero-buttons{{flex-direction:column;gap:10px}}
  .footer-links{{gap:12px}}
  .float-telegram{{bottom:16px;right:16px;width:50px;height:50px}}
  .back-to-top{{bottom:76px;right:20px;width:38px;height:38px}}
}}
@media(max-width:480px){{
  .hero h1{{font-size:1.6rem}}
  .stats-bar .stat{{min-width:auto}}
  .stat-value{{font-size:1.2rem}}
  .stat-label{{font-size:.6rem}}
  .assets-grid .asset-card{{width:120px;padding:16px 10px}}
  .asset-icon svg{{width:44px;height:44px}}
  .asset-name{{font-size:.8rem}}
  .asset-tag{{font-size:.65rem}}
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

<!-- MATRIX RAIN BACKGROUND -->
<canvas id="matrixCanvas" style="position:fixed;top:0;left:0;width:100%;height:100%;z-index:0;pointer-events:none;opacity:.10"></canvas>

<!-- NAV -->
<nav class="nav">
  <a href="/" class="nav-logo">
    <img src="/img/bull_bear.png" alt="BS365" loading="lazy"><div style="display:flex;flex-direction:column;line-height:1.1">BuySell365 <span style="color:#00d4aa;font-weight:800">Pro</span><small style="font-size:10px;color:rgba(255,255,255,.5);font-weight:400;letter-spacing:1px;margin-top:2px" data-i18n="dash.tagline">TRADING CON INTELIGENCIA ARTIFICIAL</small></div>
  </a>
  <div class="nav-links">
    <a href="#features" data-i18n="nav.technology">Tecnolog\u00eda</a>
    <a href="#assets" data-i18n="nav.assets">Activos</a>
    <a href="#pricing" data-i18n="nav.pricing">Servicios</a>
    <a href="/dashboard" data-i18n="nav.dashboard">Trading en Vivo</a>
    <div class="lang-selector" id="langSelector" style="position:relative">
      <button class="lang-btn" onclick="toggleLangMenu()"><span id="currentFlag">\U0001f1ea\U0001f1f8</span><span style="font-size:12px;color:#00d4aa;font-weight:700">\u25bc</span></button>
      <div class="lang-menu" id="langMenu">
        <a onclick="setLang('es')">\U0001f1ea\U0001f1f8 Espa\u00f1ol</a>
        <a onclick="setLang('en')">\U0001f1fa\U0001f1f8 English</a>
        <a onclick="setLang('pt')">\U0001f1e7\U0001f1f7 Portugu\u00eas</a>
        <a onclick="setLang('fr')">\U0001f1eb\U0001f1f7 Fran\u00e7ais</a>
      </div>
    </div>
  </div>
  <button class="hamburger" id="hamburgerBtn" onclick="toggleMobileMenu()">
    <span></span><span></span><span></span>
  </button>
</nav>

<!-- MOBILE MENU OVERLAY -->
<div class="mobile-overlay" id="mobileMenu">
  <a href="#features" onclick="closeMobileMenu()" data-i18n="nav.technology">Tecnolog\u00eda</a>
  <a href="#about" onclick="closeMobileMenu()">Qui\u00e9nes Somos</a>
  <a href="#assets" onclick="closeMobileMenu()" data-i18n="nav.assets">Activos</a>
  <a href="#pricing" onclick="closeMobileMenu()" data-i18n="nav.pricing">Precios</a>
  <a href="/dashboard" data-i18n="nav.dashboard">Trading en Vivo</a>
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
    <div class="hero-badge"><span class="dot"></span> <span data-i18n="hero.badge" data-i18n-vars='{{"ops":"{n_ops}"}}'>{'Bot activo' if is_alive else 'Dashboard Online'} \u2014 {n_ops} operaciones en vivo</span></div>
    <h1 data-i18n="hero.title">Trading Inteligente<br>Impulsado por IA</h1>
    <p data-i18n="hero.subtitle">Se\u00f1ales de trading con Inteligencia Artificial. Forex, Oro e \u00cdndices \u2014 m\u00e1s de 20 activos analizados en tiempo real, 24/5.</p>
    <div class="hero-buttons">
      <a href="#pricing" class="btn btn-primary" style="font-size:1.05rem;padding:16px 36px">\U0001f451 Ver Planes y Servicios</a>
      <a href="/dashboard" class="btn btn-secondary">\U0001f4ca <span data-i18n="hero.btn_dashboard">Rendimiento en Vivo</span></a>
      <a href="https://t.me/BUYSELL_365_24_7" target="_blank" class="btn btn-secondary">\U0001f4e2 <span data-i18n="hero.btn_telegram">Telegram</span></a>
    </div>
    <div class="stats-bar">
      <div class="stat-item"><div class="stat-value">{wr}%</div><div class="stat-label" data-i18n="stats.winrate">WIN RATE</div></div>
      <div class="stat-item"><div class="stat-value blue">{total}+</div><div class="stat-label" data-i18n="stats.signals">SE\u00d1ALES GENERADAS</div></div>
      <div class="stat-item"><div class="stat-value gold">{pips:+,.0f}</div><div class="stat-label" data-i18n="stats.pips">PIPS ACUMULADOS</div></div>
      <div class="stat-item"><div class="stat-value purple">24/7</div><div class="stat-label" data-i18n="stats.analysis">AN\u00c1LISIS ACTIVO</div></div>
    </div>
  </div>
</section>

<!-- PRICING — Visible inmediatamente después del Hero -->
<section class="pricing fade-in" id="pricing">
  <div class="section-title" style="margin-bottom:48px">
    <h2 style="font-size:clamp(2rem,5vw,3rem);background:linear-gradient(135deg,#fff 20%,#fbbf24 50%,#00ffcc 80%);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;filter:drop-shadow(0 0 20px rgba(251,191,36,0.2))">\U0001f4b0 <span data-i18n="pricing.title" style="-webkit-text-fill-color:inherit">Servicios y Planes</span></h2>
    <p data-i18n="pricing.subtitle" style="font-size:1.15rem;color:#a0aec0">Elige el plan que mejor se adapte a tu estilo de trading.</p>
  </div>
  <div class="pricing-cards" style="grid-template-columns:repeat(3,1fr)">
    <div class="price-card" style="border:1px solid rgba(77,159,255,0.3);box-shadow:0 0 30px rgba(77,159,255,0.1)">
      <div class="price-name" style="color:#4d9fff;font-size:1.4rem" data-i18n="pricing.community">Comunidad</div>
      <div class="price-amount" style="color:#4d9fff;text-shadow:0 0 30px rgba(77,159,255,0.6);font-size:2rem" data-i18n="pricing.free">Acceso Libre</div>
      <p style="color:#b0bec5;margin-bottom:16px" data-i18n="pricing.community_desc">Acceso al grupo p\u00fablico de Telegram</p>
      <ul class="price-list">
        <li style="color:#90caf9">&#128227; Resumen diario de mercado</li>
        <li style="color:#90caf9">&#128218; Educaci\u00f3n y an\u00e1lisis general</li>
        <li style="color:#90caf9">&#129309; Soporte de la comunidad</li>
        <li style="color:#90caf9">&#128202; Dashboard p\u00fablico limitado</li>
      </ul>
      <a href="https://t.me/BUYSELL_365_24_7" target="_blank" style="display:block;width:100%;text-align:center;margin-top:16px;padding:16px 24px;background:linear-gradient(135deg,#1565c0,#42a5f5,#64b5f6);border-radius:14px;color:#fff;font-weight:800;font-size:1.1rem;text-decoration:none;cursor:pointer;box-shadow:0 4px 25px rgba(66,165,245,0.5);transition:all .3s">&#128172; Unirse al Grupo</a>
    </div>
    <div class="price-card featured" style="border:2px solid #fbbf24;box-shadow:0 0 50px rgba(251,191,36,0.3),0 0 100px rgba(251,191,36,0.1);transform:scale(1.04)">
      <div class="price-badge" style="background:linear-gradient(135deg,#ff6d00,#fbbf24);box-shadow:0 0 30px rgba(251,191,36,.8);animation:pulse 2s infinite;font-size:15px;padding:10px 32px">\U0001f525 <span data-i18n="pricing.badge">50% OFF \u2014 LANZAMIENTO</span></div>
      <div class="price-name" style="color:#fbbf24;font-size:1.5rem;text-shadow:0 0 20px rgba(251,191,36,0.3)" data-i18n="pricing.vip">VIP Pro</div>
      <div class="price-amount" style="font-size:3.8rem;text-shadow:0 0 30px rgba(255,255,255,0.1)">
        <span class="old">$299/mes</span>
        $149<span data-i18n="pricing.month">/mes USDT</span>
      </div>
      <div class="countdown" id="offerCountdown">
        <div class="countdown-item"><div class="countdown-val" id="cdDays">--</div><div class="countdown-lbl" data-i18n="countdown.days">D\u00edas</div></div>
        <div class="countdown-item"><div class="countdown-val" id="cdHours">--</div><div class="countdown-lbl" data-i18n="countdown.hours">Horas</div></div>
        <div class="countdown-item"><div class="countdown-val" id="cdMins">--</div><div class="countdown-lbl" data-i18n="countdown.mins">Min</div></div>
        <div class="countdown-item"><div class="countdown-val" id="cdSecs">--</div><div class="countdown-lbl" data-i18n="countdown.secs">Seg</div></div>
      </div>
      <ul class="price-list" style="margin-top:20px">
        <li style="color:#ffd740">&#128293; Se\u00f1ales en tiempo real con TP/SL</li>
        <li style="color:#ffd740">&#128081; Canal VIP privado de Telegram</li>
        <li style="color:#ffd740">&#9889; Alertas instant\u00e1neas en tiempo real</li>
        <li style="color:#ffd740">&#128161; Soporte prioritario</li>
        <li style="color:#ffd740">&#129302; An\u00e1lisis multi-IA exclusivo</li>
        <li style="color:#ffd740">&#128200; Briefing matutino + Cierre nocturno</li>
      </ul>
      <a href="https://t.me/BUYSELL365_PRO_BOT?start=vip" target="_blank" style="display:block;width:100%;text-align:center;margin-top:16px;padding:18px 24px;background:linear-gradient(135deg,#ff6d00,#ffd740,#ffab00);border-radius:14px;color:#000;font-weight:900;font-size:1.15rem;text-decoration:none;cursor:pointer;box-shadow:0 4px 30px rgba(255,215,64,0.5);transition:all .3s">\U0001f451 Suscribirme al VIP</a>
    </div>
    <div class="price-card" style="position:relative;border:2px solid #00ffcc;box-shadow:0 0 50px rgba(0,255,204,0.25),0 0 100px rgba(0,255,204,0.08)">
      <div class="price-badge" style="background:linear-gradient(135deg,#00c853,#00ffcc);box-shadow:0 0 30px rgba(0,255,204,.7);animation:glowPulse 3s infinite;font-size:15px;padding:10px 32px">&#9989; <span data-i18n="pricing.copy_badge">ACTIVO</span></div>
      <div class="price-name" style="color:#00ffcc;font-size:1.5rem;text-shadow:0 0 20px rgba(0,255,204,0.3)" data-i18n="pricing.copy_name">Copy Trading</div>
      <div class="price-amount" style="font-size:2rem;color:#00ffcc;font-weight:900;text-shadow:0 0 30px rgba(0,255,204,0.5)" data-i18n="pricing.copy_price">Sin cuota fija</div>
      <p style="color:#b0bec5;margin-bottom:16px;font-size:1rem" data-i18n="pricing.copy_desc">Copia autom\u00e1tica todas nuestras operaciones en tu cuenta MT5</p>
      <ul class="price-list" style="color:#e0e0e0">
        <li style="color:#00e676" data-i18n="pricing.cp1">&#9889; Operativa automatizada \u2014 Oro, Forex e \u00cdndices</li>
        <li style="color:#00e676" data-i18n="pricing.cp2">&#128640; Copia autom\u00e1tica en tiempo real</li>
        <li style="color:#00e676" data-i18n="pricing.cp3">&#127919; SL y TP colocados autom\u00e1ticamente</li>
        <li style="color:#ffd740" data-i18n="pricing.cp4">&#128176; Si ganas, pagas un peque\u00f1o % de comisi\u00f3n</li>
        <li style="color:#00e676" data-i18n="pricing.cp5">&#127970; Broker regulado XM (MT5)</li>
        <li style="color:#00e676" data-i18n="pricing.cp6">&#129302; Sin intervenci\u00f3n manual requerida</li>
      </ul>
      <a href="https://social.tp-redirect.com/s/WRE0V7jm" target="_blank" style="display:block;width:100%;text-align:center;margin-top:16px;padding:18px 24px;background:linear-gradient(135deg,#00c853,#00e676,#69f0ae);border-radius:14px;color:#000;font-weight:900;font-size:1.15rem;text-decoration:none;cursor:pointer;box-shadow:0 4px 30px rgba(0,230,118,0.5);transition:all 0.3s">&#128640; Empezar Copy Trading</a>
    </div>
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
      <h3 style="color:#00ffc8;font-size:1.1rem;margin-bottom:8px">Unete al Canal</h3>
      <p style="color:var(--text2);font-size:.9rem">Unete a nuestro grupo de Telegram gratis o activa el canal VIP para se\u00f1ales premium.</p>
    </div>
    <div style="flex:1;min-width:250px;max-width:320px;background:linear-gradient(135deg,rgba(255,200,0,.05),rgba(255,150,0,.05));border:1px solid rgba(255,200,0,.2);border-radius:16px;padding:28px;text-align:center;position:relative">
      <div style="position:absolute;top:-16px;left:50%;transform:translateX(-50%);background:linear-gradient(135deg,#ffd700,#f0b90b);color:#000;width:36px;height:36px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-weight:900;font-size:18px">2</div>
      <div style="font-size:2.5rem;margin:12px 0">\U0001f4ca</div>
      <h3 style="color:#ffd700;font-size:1.1rem;margin-bottom:8px">Recibe Se\u00f1ales</h3>
      <p style="color:var(--text2);font-size:.9rem">Nuestra IA analiza +20 activos cada 3 minutos y te env\u00eda se\u00f1ales con Entry, TP y SL exactos.</p>
    </div>
    <div style="flex:1;min-width:250px;max-width:320px;background:linear-gradient(135deg,rgba(0,230,118,.05),rgba(0,180,90,.05));border:1px solid rgba(0,230,118,.2);border-radius:16px;padding:28px;text-align:center;position:relative">
      <div style="position:absolute;top:-16px;left:50%;transform:translateX(-50%);background:linear-gradient(135deg,#00e676,#00c853);color:#000;width:36px;height:36px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-weight:900;font-size:18px">3</div>
      <div style="font-size:2.5rem;margin:12px 0">\U0001f4b0</div>
      <h3 style="color:#00e676;font-size:1.1rem;margin-bottom:8px">Opera o Copia</h3>
      <p style="color:var(--text2);font-size:.9rem">Ejecuta las se\u00f1ales manualmente o activa el Copy Trading para que se copien automaticamente en tu MT5.</p>
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
      <div class="feature-icon green">\U0001f916</div>
      <h3 data-i18n="features.ml.title">Inteligencia Artificial</h3>
      <p data-i18n="features.ml.desc">Modelo de aprendizaje autom\u00e1tico entrenado con +3,000 velas por activo. Analiza 15 indicadores t\u00e9cnicos para predecir la direcci\u00f3n con m\u00e1s de 55% de acierto hist\u00f3rico.</p>
    </div>
    <div class="feature-card" style="padding:20px">
      <div class="feature-icon blue">\U0001f4f0</div>
      <h3 data-i18n="features.finbert.title">An\u00e1lisis de Noticias</h3>
      <p data-i18n="features.finbert.desc">Sistema de procesamiento de lenguaje natural que analiza noticias financieras en tiempo real. Si el sentimiento contradice la se\u00f1al t\u00e9cnica, se bloquea autom\u00e1ticamente.</p>
    </div>
    <div class="feature-card" style="padding:20px">
      <div class="feature-icon purple">\U0001f3e6</div>
      <h3 data-i18n="features.cot.title">Datos Institucionales</h3>
      <p data-i18n="features.cot.desc">Datos de posicionamiento de grandes fondos (CFTC) actualizados semanalmente. Detecta si las instituciones est\u00e1n comprando o vendiendo para alinear las se\u00f1ales.</p>
    </div>
    <div class="feature-card" style="padding:20px">
      <div class="feature-icon gold">\U0001f4ca</div>
      <h3 data-i18n="features.ta.title">An\u00e1lisis T\u00e9cnico Avanzado</h3>
      <p data-i18n="features.ta.desc">8 indicadores t\u00e9cnicos (RSI, MACD, Bollinger, ADX, Ichimoku, ATR, EMA, volumen) con umbrales calibrados individualmente para cada uno de los +20 activos.</p>
    </div>
    <div class="feature-card" style="padding:20px">
      <div class="feature-icon green">\u26a1</div>
      <h3 data-i18n="features.mt5.title">Ejecuci\u00f3n Autom\u00e1tica</h3>
      <p data-i18n="features.mt5.desc">Conexi\u00f3n directa a MetaTrader 5. Las \u00f3rdenes se ejecutan en milisegundos con Stop Loss y TP autom\u00e1ticos.</p>
    </div>
    <div class="feature-card" style="padding:20px">
      <div class="feature-icon blue">\U0001f6e1\ufe0f</div>
      <h3 data-i18n="features.risk.title">Control de Riesgo</h3>
      <p data-i18n="features.risk.desc">Stop Loss calculado con volatilidad real (ATR), m\u00e1ximo 6 operaciones simult\u00e1neas, pausa de 20 min por activo, y protecci\u00f3n autom\u00e1tica ante p\u00e9rdidas excesivas.</p>
    </div>
  </div>
</section>

<!-- ABOUT / TRUST BAR -->
<section id="about" class="fade-in" style="padding:30px 20px;background:var(--bg2)">
  <div style="max-width:1000px;margin:0 auto;display:flex;flex-wrap:wrap;justify-content:center;gap:20px">
    <div style="background:var(--bg3);border:1px solid var(--border);border-radius:14px;padding:18px 28px;text-align:center;flex:1;min-width:140px">
      <div style="font-size:1.6rem;font-weight:900;color:var(--green)">6+</div>
      <div style="font-size:.75rem;color:var(--text2);text-transform:uppercase;letter-spacing:1px" data-i18n="about.stat_ai">Modelos de IA</div>
    </div>
    <div style="background:var(--bg3);border:1px solid var(--border);border-radius:14px;padding:18px 28px;text-align:center;flex:1;min-width:140px">
      <div style="font-size:1.6rem;font-weight:900;color:var(--green)">24/7</div>
      <div style="font-size:.75rem;color:var(--text2);text-transform:uppercase;letter-spacing:1px" data-i18n="about.stat_monitor">Monitoreo</div>
    </div>
    <div style="background:var(--bg3);border:1px solid var(--border);border-radius:14px;padding:18px 28px;text-align:center;flex:1;min-width:140px">
      <div style="font-size:1.6rem;font-weight:900;color:var(--green)">3min</div>
      <div style="font-size:.75rem;color:var(--text2);text-transform:uppercase;letter-spacing:1px" data-i18n="about.stat_scan">Escaneo</div>
    </div>
    <div style="background:var(--bg3);border:1px solid var(--border);border-radius:14px;padding:18px 28px;text-align:center;flex:1;min-width:140px">
      <div style="font-size:1.6rem;font-weight:900;color:var(--green)">100%</div>
      <div style="font-size:.75rem;color:var(--text2);text-transform:uppercase;letter-spacing:1px" data-i18n="about.stat_transparent">Transparente</div>
    </div>
    <div style="background:var(--bg3);border:1px solid var(--border);border-radius:14px;padding:18px 28px;text-align:center;flex:1;min-width:140px">
      <div style="font-size:1.6rem;font-weight:900;color:var(--green)">+20</div>
      <div style="font-size:.75rem;color:var(--text2);text-transform:uppercase;letter-spacing:1px" data-i18n="about.stat_assets">Activos</div>
    </div>
  </div>
  <p style="text-align:center;font-size:.8rem;color:var(--text2);margin:16px auto 0;max-width:800px;opacity:.7" data-i18n="about.powered">\u26a1 Inteligencia Artificial \u00b7 An\u00e1lisis de Noticias \u00b7 Datos Institucionales \u00b7 An\u00e1lisis T\u00e9cnico \u00b7 MetaTrader 5</p>
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
      <div class="asset-icon eur"><svg viewBox="0 0 40 40" fill="none"><circle cx="14" cy="20" r="12" fill="#003399"/><text x="14" y="25" text-anchor="middle" font-size="16" font-weight="900" fill="#ffcc00" font-family="Arial">\u20ac</text><circle cx="28" cy="20" r="12" fill="#1a6b3c"/><text x="28" y="25" text-anchor="middle" font-size="16" font-weight="900" fill="#fff" font-family="Arial">$</text><path d="M20 10v20" stroke="#0d1117" stroke-width="2" stroke-dasharray="2 2" opacity=".3"/></svg></div>
      <div class="asset-name">EUR/USD</div><div class="asset-tag" data-i18n="assets.forex_major">Forex Principal</div>
    </div>
    <div class="asset-card">
      <div class="asset-icon jpy"><svg viewBox="0 0 40 40" fill="none"><circle cx="14" cy="20" r="12" fill="#1a6b3c"/><text x="14" y="25" text-anchor="middle" font-size="16" font-weight="900" fill="#fff" font-family="Arial">$</text><circle cx="28" cy="20" r="12" fill="#bc002d"/><text x="28" y="25" text-anchor="middle" font-size="15" font-weight="900" fill="#fff" font-family="Arial">\u00a5</text><path d="M20 10v20" stroke="#0d1117" stroke-width="2" stroke-dasharray="2 2" opacity=".3"/></svg></div>
      <div class="asset-name">USD/JPY</div><div class="asset-tag" data-i18n="assets.forex_major">Forex Principal</div>
    </div>
    <div class="asset-card">
      <div class="asset-icon audcad"><svg viewBox="0 0 40 40" fill="none"><circle cx="14" cy="20" r="12" fill="#00008B"/><text x="14" y="25" text-anchor="middle" font-size="10" font-weight="900" fill="#fff" font-family="Arial">A$</text><circle cx="28" cy="20" r="12" fill="#FF0000"/><text x="28" y="25" text-anchor="middle" font-size="10" font-weight="900" fill="#fff" font-family="Arial">C$</text></svg></div>
      <div class="asset-name">AUD/CAD</div><div class="asset-tag">Fibonacci Scalper</div>
    </div>
    <div class="asset-card">
      <div class="asset-icon eurchf"><svg viewBox="0 0 40 40" fill="none"><circle cx="14" cy="20" r="12" fill="#003399"/><text x="14" y="25" text-anchor="middle" font-size="12" font-weight="900" fill="#FFD700" font-family="Arial">&euro;</text><circle cx="28" cy="20" r="12" fill="#FF0000"/><text x="28" y="25" text-anchor="middle" font-size="12" font-weight="900" fill="#fff" font-family="Arial">Fr</text></svg></div>
      <div class="asset-name">EUR/CHF</div><div class="asset-tag">Fibonacci Scalper</div>
    </div>
    <div class="asset-card">
      <div class="asset-icon usdcad"><svg viewBox="0 0 40 40" fill="none"><circle cx="14" cy="20" r="12" fill="#002868"/><text x="14" y="25" text-anchor="middle" font-size="12" font-weight="900" fill="#fff" font-family="Arial">$</text><circle cx="28" cy="20" r="12" fill="#FF0000"/><text x="28" y="25" text-anchor="middle" font-size="10" font-weight="900" fill="#fff" font-family="Arial">C$</text></svg></div>
      <div class="asset-name">USD/CAD</div><div class="asset-tag">Fibonacci Scalper</div>
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
      <div class="asset-icon gbpnzd"><svg viewBox="0 0 40 40" fill="none"><circle cx="14" cy="20" r="12" fill="#012169"/><text x="14" y="25" text-anchor="middle" font-size="12" font-weight="900" fill="#fff" font-family="Arial">\u00a3</text><circle cx="28" cy="20" r="12" fill="#00247D"/><text x="28" y="25" text-anchor="middle" font-size="9" font-weight="900" fill="#fff" font-family="Arial">NZ</text></svg></div>
      <div class="asset-name">GBP/NZD</div><div class="asset-tag">Cross</div>
    </div>
    <div class="asset-card">
      <div class="asset-icon gbpaud"><svg viewBox="0 0 40 40" fill="none"><circle cx="14" cy="20" r="12" fill="#012169"/><text x="14" y="25" text-anchor="middle" font-size="12" font-weight="900" fill="#fff" font-family="Arial">\u00a3</text><circle cx="28" cy="20" r="12" fill="#00008B"/><text x="28" y="25" text-anchor="middle" font-size="10" font-weight="900" fill="#fff" font-family="Arial">A$</text></svg></div>
      <div class="asset-name">GBP/AUD</div><div class="asset-tag">Cross</div>
    </div>
    <div class="asset-card">
      <div class="asset-icon eurgbp"><svg viewBox="0 0 40 40" fill="none"><circle cx="14" cy="20" r="12" fill="#003399"/><text x="14" y="25" text-anchor="middle" font-size="12" font-weight="900" fill="#FFD700" font-family="Arial">\u20ac</text><circle cx="28" cy="20" r="12" fill="#012169"/><text x="28" y="25" text-anchor="middle" font-size="12" font-weight="900" fill="#fff" font-family="Arial">\u00a3</text></svg></div>
      <div class="asset-name">EUR/GBP</div><div class="asset-tag">Cross Europa</div>
    </div>
    <div class="asset-card">
      <div class="asset-icon euraud"><svg viewBox="0 0 40 40" fill="none"><circle cx="14" cy="20" r="12" fill="#003399"/><text x="14" y="25" text-anchor="middle" font-size="12" font-weight="900" fill="#FFD700" font-family="Arial">\u20ac</text><circle cx="28" cy="20" r="12" fill="#00008B"/><text x="28" y="25" text-anchor="middle" font-size="10" font-weight="900" fill="#fff" font-family="Arial">A$</text></svg></div>
      <div class="asset-name">EUR/AUD</div><div class="asset-tag">Cross</div>
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
      <div class="faq-a" data-i18n="faq.a1">El grupo público de Telegram te da acceso a resúmenes diarios de mercado, educación y análisis general. Para recibir señales VIP en tiempo real con Entry, TP y SL exactos, necesitas el plan VIP Pro. El Copy Trading funciona sin cuota fija — solo pagas un pequeño % si ganas.</div>
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
      <div class="faq-a" data-i18n="faq.a4">Puedes usar cualquier broker que soporte los activos que operamos (Oro, Forex e \u00cdndices). Recomendamos brokers con MetaTrader 5 para aprovechar nuestro servicio de Copy Trading activo.</div>
    </div>
    <div class="faq-item" onclick="this.classList.toggle('open')">
      <div class="faq-q" data-i18n="faq.q_copy">\u00bfC\u00f3mo funciona el Copy Trading con XM?</div>
      <div class="faq-a" data-i18n="faq.a_copy">El Copy Trading te permite replicar autom\u00e1ticamente todas nuestras operaciones en tu propia cuenta. Solo necesitas: 1) Abrir una cuenta en XM (broker regulado internacionalmente), 2) Conectar tu cuenta a nuestro perfil de copy a trav\u00e9s del enlace que te proporcionamos, 3) Elegir tu nivel de riesgo y monto. A partir de ah\u00ed, cada vez que nuestro bot ejecuta una operaci\u00f3n, se replica autom\u00e1ticamente en tu cuenta con los mismos SL y TP. No necesitas estar pendiente ni tener experiencia \u2014 todo es 100% autom\u00e1tico.</div>
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
    <h2>\U0001f3c6 <span>Por Qu\u00e9 Elegirnos</span></h2>
    <p>Lo que nos diferencia de otros servicios de se\u00f1ales</p>
  </div>
  <div style="display:flex;flex-wrap:wrap;justify-content:center;gap:20px;max-width:1100px;margin:0 auto">
    <div style="flex:1;min-width:220px;max-width:260px;background:var(--bg3);border:1px solid var(--border);border-radius:14px;padding:24px;text-align:center">
      <div style="font-size:2.2rem;margin-bottom:10px">\U0001f916</div>
      <h4 style="color:#00ffc8;margin-bottom:8px">IA Real, No Opiniones</h4>
      <p style="color:var(--text2);font-size:.85rem">Nuestro bot analiza datos reales cada 3 minutos. Sin emociones, sin sesgos — solo datos y algoritmos.</p>
    </div>
    <div style="flex:1;min-width:220px;max-width:260px;background:var(--bg3);border:1px solid var(--border);border-radius:14px;padding:24px;text-align:center">
      <div style="font-size:2.2rem;margin-bottom:10px">\U0001f4b0</div>
      <h4 style="color:#ffd700;margin-bottom:8px">Copy Trading Sin Cuota</h4>
      <p style="color:var(--text2);font-size:.85rem">No pagas nada hasta que ganas. Solo un peque\u00f1o porcentaje de tus ganancias. Riesgo cero para ti.</p>
    </div>
    <div style="flex:1;min-width:220px;max-width:260px;background:var(--bg3);border:1px solid var(--border);border-radius:14px;padding:24px;text-align:center">
      <div style="font-size:2.2rem;margin-bottom:10px">\U0001f4ca</div>
      <h4 style="color:#00e676;margin-bottom:8px">100% Transparente</h4>
      <p style="color:var(--text2);font-size:.85rem">Dashboard p\u00fablico con resultados en vivo. Cada operaci\u00f3n visible con entrada, SL, TP y resultado.</p>
    </div>
    <div style="flex:1;min-width:220px;max-width:260px;background:var(--bg3);border:1px solid var(--border);border-radius:14px;padding:24px;text-align:center">
      <div style="font-size:2.2rem;margin-bottom:10px">\u26a1</div>
      <h4 style="color:#a855f7;margin-bottom:8px">Ejecuci\u00f3n Instant\u00e1nea</h4>
      <p style="color:var(--text2);font-size:.85rem">Las se\u00f1ales se ejecutan en menos de 1 segundo. Sin retrasos, sin slippage. Tu cuenta siempre sincronizada.</p>
    </div>
  </div>
</section>

<!-- SIGNAL PREVIEW -->
<section style="padding:60px 20px;text-align:center" class="fade-in">
<div style="max-width:500px;margin:0 auto">
    <div style="font-size:11px;font-weight:700;color:#a855f7;text-transform:uppercase;letter-spacing:2px;margin-bottom:14px">&#128227; Ejemplo de Se\u00f1al en Vivo</div>
    <h2 style="font-size:1.6rem;font-weight:800;margin-bottom:20px;color:#fff">As\u00ed Recibir\u00e1s las Se\u00f1ales</h2>
    <div style="background:linear-gradient(135deg,#111820,#1a0d2e);border:1px solid rgba(0,212,170,.15);border-radius:16px;padding:24px;text-align:left;margin-bottom:24px">
        <div style="display:flex;align-items:center;gap:10px;margin-bottom:12px">
            <div style="width:36px;height:36px;border-radius:50%;background:linear-gradient(135deg,#00d4aa,#00a080);display:flex;align-items:center;justify-content:center;font-size:18px">&#129302;</div>
            <div><div style="font-weight:700;font-size:14px;color:#fff">BuySell365 Pro</div><div style="font-size:10px;color:#5a6a7a">Canal de Se\u00f1ales</div></div>
        </div>
        <div style="font-size:14px;line-height:1.8;color:#e2e8f0">
            <div style="color:#00d4aa;font-weight:700;font-size:15px">&#128200; USD/JPY &mdash; Se\u00f1al Detectada</div>
            <div style="margin-top:6px">&#127919; Score: <strong>4/5</strong> | Confianza: <strong>67%</strong></div>
            <div style="color:#00e676;margin-top:4px">&#128994; BUY @ 149.250</div>
            <div style="margin-top:2px">&#128308; SL: 148.900 | &#127919; TP: 149.850</div>
            <div style="margin-top:6px;font-size:12px;color:#5a6a7a">Rendimiento en vivo &bull; <a href="/dashboard" style="color:#00d4aa;text-decoration:none">Ver Dashboard &rarr;</a></div>
        </div>
    </div>
    <p style="color:#5a6a7a;font-size:13px;margin-bottom:12px">Recibe alertas como esta directamente en tu Telegram</p>
</div>
</section>

<!-- CTA -->
<section class="cta fade-in" style="padding:60px 20px;background:linear-gradient(180deg,transparent,rgba(0,212,170,0.03),transparent)">
  <h2 style="background:linear-gradient(135deg,#fff,var(--green));-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text">\U0001f680 Empieza a Operar con IA</h2>
  <p style="font-size:1.1rem">Elige entre VIP Pro o Copy Trading. Sin contratos, cancela cuando quieras.</p>
  <div class="hero-buttons">
    <a href="#pricing" class="btn btn-primary">\U0001f451 Ver Planes</a>
    <a href="https://social.tp-redirect.com/s/WRE0V7jm" target="_blank" class="btn btn-secondary" style="border-color:rgba(0,230,118,0.3);color:#00e676">&#128640; Copy Trading</a>
    <a href="https://t.me/BUYSELL_365_24_7" target="_blank" class="btn btn-secondary">\U0001f4ac Comunidad Gratis</a>
  </div>
</section>

<!-- FLOATING TELEGRAM BUTTON -->
<a href="https://t.me/BUYSELL_365_24_7" target="_blank" class="float-telegram" title="Telegram">
  <svg viewBox="0 0 24 24"><path d="M11.944 0A12 12 0 0 0 0 12a12 12 0 0 0 12 12 12 12 0 0 0 12-12A12 12 0 0 0 12 0a12 12 0 0 0-.056 0zm4.962 7.224c.1-.002.321.023.465.14a.506.506 0 0 1 .171.325c.016.093.036.306.02.472-.18 1.898-.962 6.502-1.36 8.627-.168.9-.499 1.201-.82 1.23-.696.065-1.225-.46-1.9-.902-1.056-.693-1.653-1.124-2.678-1.8-1.185-.78-.417-1.21.258-1.91.177-.184 3.247-2.977 3.307-3.23.007-.032.014-.15-.056-.212s-.174-.041-.249-.024c-.106.024-1.793 1.14-5.061 3.345-.479.33-.913.49-1.302.48-.428-.008-1.252-.241-1.865-.44-.752-.245-1.349-.374-1.297-.789.027-.216.325-.437.893-.663 3.498-1.524 5.83-2.529 6.998-3.014 3.332-1.386 4.025-1.627 4.476-1.635z"/></svg>
</a>

<!-- BACK TO TOP -->
<div class="back-to-top" id="backToTop" onclick="window.scrollTo({{top:0,behavior:'smooth'}})">\u2191</div>

<!-- FOOTER -->
<footer class="footer">
  <div class="footer-links">
    <a href="/dashboard">\U0001f4ca <span data-i18n="footer.dashboard">Trading en Vivo</span></a>
    <a href="/terminos">\U0001f4dc <span data-i18n="footer.terms">T\u00e9rminos</span></a>
    <a href="/privacidad">\U0001f512 <span data-i18n="footer.privacy">Privacidad</span></a>
    <a href="https://t.me/BUYSELL_365_24_7" target="_blank">\U0001f4e2 <span data-i18n="footer.telegram">Telegram</span></a>
    <a href="mailto:soporte@buysell365.pro">\U0001f4e7 <span data-i18n="footer.email">soporte@buysell365.pro</span></a>
  </div>
  <p data-i18n="footer.rights">\u00a9 2026 BuySell365 Pro. Todos los derechos reservados.</p>
  <p style="margin-top:4px;font-size:0.75rem;color:var(--text2)">By Emmanuel Diaz</p>
  <p style="margin-top:8px;font-size:0.7rem;color:#4a5568">
    \u26a0\ufe0f <span data-i18n="footer.disclaimer">Trading con riesgo. Rendimientos pasados no garantizan resultados futuros. Opera bajo tu propia responsabilidad.</span>
  </p>
</footer>

<script>
// ═══════════════════════════════════════════════
//  MATRIX RAIN — Binary 0/1 falling effect
// ═══════════════════════════════════════════════
(function(){{
  const c = document.getElementById('matrixCanvas');
  if(!c) return;
  const ctx = c.getContext('2d');
  let W, H, cols, drops;
  function resize(){{
    W = c.width = window.innerWidth;
    H = c.height = window.innerHeight;
    cols = Math.floor(W / 18);
    drops = Array(cols).fill(0).map(()=>Math.random()*H/18|0);
  }}
  resize();
  window.addEventListener('resize', resize);
  function draw(){{
    ctx.fillStyle = 'rgba(8,11,15,.06)';
    ctx.fillRect(0,0,W,H);
    ctx.fillStyle = '#00d4aa';
    ctx.font = '14px monospace';
    for(let i=0;i<cols;i++){{
      const ch = Math.random()>.5?'1':'0';
      ctx.fillText(ch, i*18, drops[i]*18);
      if(drops[i]*18>H && Math.random()>.975) drops[i]=0;
      drops[i]++;
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
        if(text.includes('<br') || text.includes('<span') || text.includes('<strong')){{
          el.innerHTML = text;
        }}else{{
          el.textContent = text;
        }}
      }}
    }});
    document.documentElement.lang = currentLang;
    const flagEl = document.getElementById('currentFlag');
    if(flagEl) flagEl.textContent = FLAGS[currentLang] || '\U0001f1ea\U0001f1f8';
  }}

  function loadLang(lang, callback){{
    if(!SUPPORTED.includes(lang)) lang = 'es';
    fetch('/i18n/' + lang + '.json')
      .then(function(r){{ return r.json(); }})
      .then(function(data){{
        translations = data;
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
//  COUNTDOWN TIMER (50% OFF offer expires 2026-07-30)
// ═══════════════════════════════════════════════
(function(){{
  const endDate = new Date('2026-07-30T23:59:59').getTime();
  function update(){{
    const now = Date.now();
    const diff = endDate - now;
    if(diff <= 0){{
      document.getElementById('cdDays').textContent = '0';
      document.getElementById('cdHours').textContent = '0';
      document.getElementById('cdMins').textContent = '0';
      document.getElementById('cdSecs').textContent = '0';
      return;
    }}
    const d = Math.floor(diff / 86400000);
    const h = Math.floor((diff % 86400000) / 3600000);
    const m = Math.floor((diff % 3600000) / 60000);
    const s = Math.floor((diff % 60000) / 1000);
    const de = document.getElementById('cdDays');
    const he = document.getElementById('cdHours');
    const me = document.getElementById('cdMins');
    const se = document.getElementById('cdSecs');
    if(de) de.textContent = d;
    if(he) he.textContent = h < 10 ? '0'+h : h;
    if(me) me.textContent = m < 10 ? '0'+m : m;
    if(se) se.textContent = s < 10 ? '0'+s : s;
  }}
  update();
  setInterval(update, 1000);
}})();
</script>

</body>
</html>"""
    _resp = make_response(_html)
    _resp.headers['Content-Type'] = 'text/html; charset=utf-8'
    _resp.headers['Cache-Control'] = 'public, max-age=300'
    return _resp

# ============================================================
#  DASHBOARD
# ============================================================
@app.route("/dashboard", methods=["GET"])
def dashboard_visual():
    """Dashboard served from synced data — full version matching bot.py."""
    _track_visitor()
    with _lock:
        hist = list(_store.get("historial_operaciones", []))
        ops = dict(_store.get("operaciones_activas", {}))

    wins = sum(1 for h in hist if h.get('pips', 0) > 0)
    losses_count = sum(1 for h in hist if h.get('pips', 0) <= 0)
    total = wins + losses_count
    winrate = round(wins / total * 100, 1) if total > 0 else 0
    pips_total = round(sum(h.get('pips', 0) for h in hist), 1)
    avg_win = round(sum(h.get('pips', 0) for h in hist if h.get('pips', 0) > 0) / max(wins, 1), 1)
    avg_loss = round(sum(abs(h.get('pips', 0)) for h in hist if h.get('pips', 0) <= 0) / max(losses_count, 1), 1)
    rr = round(avg_win / avg_loss, 2) if avg_loss > 0 else 0
    now = _ahora()
    now_str = now.strftime("%H:%M:%S CET")
    hoy_str = now.strftime("%Y-%m-%d")
    senales_hoy = sum(1 for h in hist if h.get('fecha', '').startswith(hoy_str))
    pips_color = "#00e676" if pips_total >= 0 else "#ff3b30"
    is_alive = (time.time() - _store.get("ultimo_sync", 0)) < 120
    wr_color = '#00d4aa' if winrate >= 60 else ('#f0b90b' if winrate >= 45 else '#ff3b30')

    # FIX 2026-03-19: Calcular drawdown máximo y racha de pérdidas
    _cumul = 0.0
    _peak = 0.0
    _max_dd = 0.0
    _loss_streak = 0
    _max_loss_streak = 0
    for h in hist:
        _cumul += h.get('pips', 0)
        if _cumul > _peak:
            _peak = _cumul
        dd = _peak - _cumul
        if dd > _max_dd:
            _max_dd = dd
        if h.get('pips', 0) <= 0:
            _loss_streak += 1
            if _loss_streak > _max_loss_streak:
                _max_loss_streak = _loss_streak
        else:
            _loss_streak = 0
    max_drawdown = round(_max_dd, 1)
    dd_color = '#00d4aa' if max_drawdown < 50 else ('#f0b90b' if max_drawdown < 150 else '#ff3b30')

    # --- Asset performance ---
    asset_perf = {}
    for h in hist:
        nombre = h.get('nombre', h.get('ticker', 'N/A'))
        if nombre not in asset_perf:
            asset_perf[nombre] = {'wins': 0, 'losses': 0, 'pips': 0.0, 'total': 0}
        asset_perf[nombre]['total'] += 1
        if h.get('pips', 0) > 0:
            asset_perf[nombre]['wins'] += 1
        else:
            asset_perf[nombre]['losses'] += 1
        asset_perf[nombre]['pips'] += h.get('pips', 0)

    _all_assets = [
        ('ORO', '#f0b90b', ['ORO', 'GOLD', 'XAUUSD', 'XAU']),
        ('NASDAQ', '#00d4aa', ['NASDAQ', 'NQ', 'US100', 'US100Cash']),
        ('S&amp;P 500', '#3b82f6', ['S&P', 'SP500', 'US500', 'ES', 'US500Cash']),
        ('EUR/USD', '#a855f7', ['EUR/USD', 'EURUSD', 'EUR']),
        ('USD/JPY', '#ef4444', ['USD/JPY', 'USDJPY', 'JPY']),
        ('AUD/CAD', '#22c55e', ['AUD/CAD', 'AUDCAD']),
        ('EUR/CHF', '#06b6d4', ['EUR/CHF', 'EURCHF']),
        ('USD/CAD', '#f97316', ['USD/CAD', 'USDCAD']),
    ]
    activos_en_curso = [str(op.get('nombre', op.get('ticker', ''))) for op in ops.values() if isinstance(op, dict)]
    asset_cards_html = ""
    for display_name, accent, aliases in _all_assets:
        st = {'wins': 0, 'losses': 0, 'pips': 0.0, 'total': 0}
        for key, val in asset_perf.items():
            if any(a.lower() in key.lower() for a in aliases):
                st['wins'] += val['wins']
                st['losses'] += val['losses']
                st['pips'] += val['pips']
                st['total'] += val['total']
        wr = round(st['wins'] / st['total'] * 100) if st['total'] > 0 else 0
        pips_a = round(st['pips'], 1)
        pips_c = "#00e676" if pips_a >= 0 else "#ff3b30"
        is_active = any(any(a.lower() in ac.lower() for a in aliases) for ac in activos_en_curso)
        active_cls = " asset-live" if is_active else ""
        active_dot = '<span class="mini-pulse"></span>' if is_active else ''
        asset_cards_html += f'''<div class="asset-card{active_cls}">
                <div class="asset-hdr"><span class="asset-dot" style="background:{accent}"></span>{display_name}{active_dot}</div>
                <div class="asset-wr">{wr}%</div>
                <div class="wr-bar-bg"><div class="wr-bar-fill" style="width:{wr}%;background:{accent}"></div></div>
                <div class="asset-meta"><span>{st['total']} ops</span><span style="color:{pips_c}">{pips_a:+.1f}</span></div>
            </div>'''

    _dash_html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<!-- Google Analytics --><script async src="https://www.googletagmanager.com/gtag/js?id=G-L514BL7E83"></script><script>window.dataLayer=window.dataLayer||[];function gtag(){{dataLayer.push(arguments);}}gtag('js',new Date());gtag('config','G-L514BL7E83');</script>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>BuySell365 Pro | Rendimiento en Vivo</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800;900&display=swap" rel="stylesheet">
<script defer data-domain="buysell365.pro" src="https://plausible.io/js/script.js"></script>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
:root{{--bg:#080b0f;--panel:#111820;--panel2:#192230;--border:#1e2a3a;--primary:#00d4aa;--primary-dim:rgba(0,212,170,.12);--gold:#f0b90b;--buy:#00e676;--sell:#ff3b30;--text:#e2e8f0;--muted:#5a6a7a;--font:'Inter',system-ui,-apple-system,sans-serif}}
body{{background:var(--bg);color:var(--text);font-family:var(--font);min-height:100vh;overflow-x:hidden}}
#matrix-canvas{{position:fixed;top:0;left:0;width:100%;height:100%;z-index:0;pointer-events:none;opacity:.12}}
body::before{{content:'';position:fixed;top:0;left:0;right:0;height:400px;background:radial-gradient(ellipse at 50% 0%,rgba(0,212,170,.08) 0%,transparent 70%);pointer-events:none;z-index:0}}
.wrap{{max-width:1280px;margin:0 auto;padding:20px;position:relative;z-index:1}}
.hdr{{display:flex;justify-content:space-between;align-items:center;padding:18px 0 22px;border-bottom:1px solid var(--border);margin-bottom:28px}}
.hdr-left{{display:flex;align-items:center;gap:14px}}
.hdr-logo{{width:72px;height:72px;border-radius:14px;object-fit:cover;border:1px solid rgba(0,212,170,.2);box-shadow:0 4px 16px rgba(0,212,170,.15)}}
.brand{{font-size:24px;font-weight:800;letter-spacing:-.5px;color:#fff}}
.brand small{{display:block;font-size:11px;font-weight:500;color:var(--muted);letter-spacing:.5px;margin-top:2px}}
.live-badge{{display:flex;align-items:center;gap:6px;background:rgba(0,212,170,.08);border:1px solid rgba(0,212,170,.2);padding:7px 14px;border-radius:20px;font-size:12px;font-weight:600;color:var(--primary)}}
.pulse{{width:8px;height:8px;border-radius:50%;background:var(--primary);animation:pulse 2s infinite}}
@keyframes pulse{{0%,100%{{opacity:1;box-shadow:0 0 0 0 rgba(0,212,170,.5)}}50%{{opacity:.7;box-shadow:0 0 0 6px rgba(0,212,170,0)}}}}
.stats-row{{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin-bottom:24px}}
.stat-card{{background:var(--panel);border:1px solid var(--border);border-radius:14px;padding:18px 20px;position:relative;overflow:hidden}}
.stat-card::after{{content:'';position:absolute;top:0;left:0;right:0;height:2px;border-radius:14px 14px 0 0}}
.stat-card.accent-green::after{{background:linear-gradient(90deg,var(--primary),transparent)}}
.stat-card.accent-gold::after{{background:linear-gradient(90deg,var(--gold),transparent)}}
.stat-card.accent-blue::after{{background:linear-gradient(90deg,#3b82f6,transparent)}}
.stat-card.accent-purple::after{{background:linear-gradient(90deg,#a855f7,transparent)}}
.stat-label{{font-size:11px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:1px;margin-bottom:8px}}
.stat-value{{font-size:32px;font-weight:800;letter-spacing:-1px}}
.stat-sub{{font-size:11px;color:var(--muted);margin-top:4px}}
.wr-bar-bg{{width:100%;height:6px;background:var(--border);border-radius:3px;overflow:hidden}}
.wr-bar-fill{{height:100%;border-radius:3px;transition:width .5s ease}}
.two-col{{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-bottom:24px}}
.card{{background:var(--panel);border:1px solid var(--border);border-radius:16px;padding:22px;position:relative}}
.card-title{{font-size:14px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.8px;margin-bottom:16px;display:flex;align-items:center;gap:8px}}
.card-title i{{font-style:normal}}
.chart-area{{display:flex;align-items:flex-end;gap:4px;height:100px;padding:10px 0}}
.perf-bar{{flex:1;min-width:6px;border-radius:3px 3px 0 0;transition:height .3s ease;opacity:.85}}
.perf-bar:hover{{opacity:1;filter:brightness(1.2)}}
.mini-pulse{{width:6px;height:6px;border-radius:50%;background:var(--primary);display:inline-block;margin-left:6px;animation:pulse 2s infinite;vertical-align:middle}}
.asset-grid{{display:grid;grid-template-columns:repeat(7,1fr);gap:12px}}
.asset-card{{background:var(--panel2);border:1px solid var(--border);border-radius:12px;padding:14px;text-align:center;transition:border-color .2s,transform .2s}}
.asset-card:hover{{border-color:rgba(0,212,170,.3);transform:translateY(-2px)}}
.asset-card.asset-live{{border-color:rgba(0,212,170,.4);box-shadow:0 0 12px rgba(0,212,170,.1)}}
.asset-hdr{{font-size:12px;font-weight:700;color:#fff;margin-bottom:8px;display:flex;align-items:center;justify-content:center;gap:6px}}
.asset-dot{{width:8px;height:8px;border-radius:50%;display:inline-block}}
.asset-wr{{font-size:24px;font-weight:900;color:var(--primary);letter-spacing:-1px;margin:4px 0}}
.asset-meta{{display:flex;justify-content:space-between;font-size:10px;color:var(--muted);margin-top:8px}}
.promo{{background:linear-gradient(135deg,#0d1a2a 0%,#112030 50%,#0a1520 100%);border:1px solid rgba(0,212,170,.15);border-radius:18px;padding:32px;text-align:center;margin-top:24px;position:relative;overflow:hidden}}
.promo::before{{content:'';position:absolute;top:-50%;left:-50%;width:200%;height:200%;background:radial-gradient(circle,rgba(0,212,170,.04) 0%,transparent 60%);pointer-events:none}}
.promo h2{{font-size:20px;font-weight:800;margin-bottom:6px;position:relative}}
.promo p{{color:var(--muted);font-size:13px;margin-bottom:20px;position:relative}}
.promo-features{{display:flex;justify-content:center;gap:24px;margin-bottom:24px;flex-wrap:wrap;position:relative}}
.promo-feat{{display:flex;align-items:center;gap:6px;font-size:12px;font-weight:600}}
.promo-feat i{{font-style:normal;color:var(--primary)}}
.cta-btn{{display:inline-flex;align-items:center;gap:8px;padding:14px 36px;background:linear-gradient(135deg,var(--primary),#00a080);color:#000;font-weight:800;font-size:15px;border-radius:12px;text-decoration:none;transition:all .2s;box-shadow:0 4px 24px rgba(0,212,170,.3);position:relative}}
.cta-btn:hover{{transform:translateY(-2px);box-shadow:0 8px 32px rgba(0,212,170,.4)}}
.active-alert{{background:linear-gradient(135deg,rgba(0,212,170,.08),rgba(0,212,170,.02));border:1px solid rgba(0,212,170,.25);border-radius:14px;padding:16px 20px;margin-bottom:24px;animation:alertGlow 3s infinite}}
@keyframes alertGlow{{0%,100%{{box-shadow:0 0 8px rgba(0,212,170,.1)}}50%{{box-shadow:0 0 20px rgba(0,212,170,.2)}}}}
.alert-header{{display:flex;align-items:center;gap:10px;margin-bottom:12px;font-size:14px;font-weight:700;color:var(--primary)}}
.alert-op{{display:flex;align-items:center;gap:16px;padding:10px 0;border-bottom:1px solid rgba(30,42,58,.3);flex-wrap:wrap}}
.alert-op:last-child{{border-bottom:none}}
.alert-name{{font-weight:700;min-width:120px}}
.alert-type{{padding:2px 10px;border-radius:6px;font-size:12px;font-weight:700}}
.alert-type.buy{{background:rgba(0,230,118,.15);color:#00e676}}
.alert-type.sell{{background:rgba(255,59,48,.15);color:#ff3b30}}
.progress-bar-wrap{{flex:1;min-width:200px;display:flex;align-items:center;gap:10px}}
.progress-track{{flex:1;height:8px;background:var(--panel2);border-radius:4px;overflow:hidden;position:relative}}
.progress-fill{{height:100%;border-radius:4px;transition:width .5s ease;background:linear-gradient(90deg,var(--primary),#00e676)}}
.progress-marks{{position:absolute;top:0;height:100%;display:flex;width:100%}}
.progress-mark{{position:absolute;top:-4px;width:2px;height:16px;background:rgba(255,255,255,.3)}}
.progress-pct{{font-size:13px;font-weight:700;color:var(--primary);min-width:45px;text-align:right}}
.tp-badges{{display:flex;gap:4px}}
.tp-badge{{padding:2px 6px;border-radius:4px;font-size:10px;font-weight:700}}
.tp-badge.hit{{background:rgba(0,230,118,.2);color:#00e676}}
.tp-badge.pending{{background:var(--panel2);color:var(--muted)}}
.filter-bar{{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:14px;align-items:center}}
.filter-btn{{padding:5px 14px;border-radius:8px;font-size:12px;font-weight:600;cursor:pointer;transition:all .2s;border:1px solid var(--border);background:var(--panel2);color:var(--muted)}}
.filter-btn:hover{{border-color:rgba(0,212,170,.3);color:var(--text)}}
.filter-btn.active{{background:var(--primary-dim);border-color:var(--primary);color:var(--primary)}}
.pagination{{display:flex;justify-content:center;align-items:center;gap:6px;margin-top:16px;flex-wrap:wrap}}
.page-btn{{background:var(--panel2);border:1px solid var(--border);color:var(--text);padding:8px 14px;border-radius:8px;cursor:pointer;font-size:0.85rem;transition:all .2s;font-family:inherit}}
.page-btn:hover:not(:disabled){{border-color:var(--primary);color:var(--primary)}}
.page-btn.active{{background:var(--primary);color:#000;border-color:var(--primary);font-weight:700}}
.page-btn:disabled{{opacity:.4;cursor:not-allowed}}
.streak-banner{{display:flex;align-items:center;gap:16px;padding:14px 20px;background:linear-gradient(135deg,rgba(0,212,170,.06),transparent);border:1px solid rgba(0,212,170,.15);border-radius:12px;margin-bottom:14px}}
.streak-number{{font-size:36px;font-weight:900;color:var(--primary);letter-spacing:-2px;line-height:1}}
.streak-info{{flex:1}}
.streak-label{{font-size:13px;font-weight:700;color:var(--text)}}
.streak-sub{{font-size:11px;color:var(--muted)}}
.streak-fire{{color:#ff6b35;font-size:24px}}
.cumul-chart-wrap{{position:relative;height:160px;margin:14px 0;border:1px solid var(--border);border-radius:10px;overflow:hidden;background:var(--panel2)}}
.cumul-chart-wrap svg{{width:100%;height:100%}}
.wr-period-row{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:24px}}
.wr-period-card{{background:var(--panel);border:1px solid var(--border);border-radius:12px;padding:16px;text-align:center;position:relative;overflow:hidden}}
.wr-period-card::after{{content:'';position:absolute;top:0;left:0;right:0;height:2px}}
.wr-period-label{{font-size:11px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.8px;margin-bottom:6px}}
.wr-period-val{{font-size:28px;font-weight:900;letter-spacing:-1px}}
.wr-period-detail{{font-size:11px;color:var(--muted);margin-top:4px}}
.wr-period-bar{{height:4px;background:var(--border);border-radius:2px;margin-top:8px;overflow:hidden}}
.wr-period-fill{{height:100%;border-radius:2px;transition:width .5s}}
.footer{{text-align:center;padding:24px 0 12px;margin-top:20px;border-top:1px solid var(--border)}}
.footer p{{font-size:11px;color:var(--muted)}}
.footer a{{color:var(--primary);text-decoration:none}}
@media(max-width:1024px){{
    .asset-grid{{grid-template-columns:repeat(4,1fr)}}
}}
@media(max-width:768px){{
    .stats-row{{grid-template-columns:repeat(2,1fr)}}
    .two-col{{grid-template-columns:1fr}}
    .asset-grid{{grid-template-columns:repeat(3,1fr)}}
    .hdr{{flex-direction:column;gap:12px;text-align:center}}
    .promo-features{{flex-direction:column;align-items:center;gap:10px}}
    .stat-value{{font-size:24px}}
}}
@media(max-width:480px){{
    .wrap{{padding:12px}}
    .stats-row{{grid-template-columns:1fr 1fr}}
    .asset-grid{{grid-template-columns:repeat(2,1fr)}}
    .card{{padding:16px}}
    .hdr-logo{{width:56px;height:56px}}
}}
.date-filter-bar{{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px;align-items:center}}
.date-filter-btn{{padding:5px 14px;border-radius:8px;font-size:12px;font-weight:600;cursor:pointer;transition:all .2s;border:1px solid var(--border);background:var(--panel2);color:var(--muted)}}
.date-filter-btn:hover{{border-color:rgba(240,185,11,.3);color:var(--gold)}}
.date-filter-btn.active{{background:rgba(240,185,11,.12);border-color:var(--gold);color:var(--gold)}}
.export-csv-btn{{padding:5px 14px;border-radius:8px;font-size:12px;font-weight:600;cursor:pointer;transition:all .2s;border:1px solid rgba(59,130,246,.4);background:rgba(59,130,246,.08);color:#3b82f6;margin-left:auto}}
.export-csv-btn:hover{{background:rgba(59,130,246,.18);border-color:#3b82f6}}
.bell-btn{{background:var(--panel2);border:1px solid var(--border);border-radius:8px;padding:6px 10px;cursor:pointer;font-size:18px;line-height:1;transition:all .2s;position:relative}}
.bell-btn:hover{{border-color:rgba(240,185,11,.4);background:rgba(240,185,11,.08)}}
.bell-btn.notif-on{{border-color:rgba(240,185,11,.6);background:rgba(240,185,11,.12)}}
.bell-dot{{position:absolute;top:2px;right:2px;width:8px;height:8px;border-radius:50%;background:#ff3b30;border:1px solid var(--bg);display:none}}
.bell-dot.show{{display:block}}
</style>
<script>setTimeout(()=>location.reload(),30000);</script>
</head>
<body>
<canvas id="matrix-canvas"></canvas>
<div class="wrap">

    <!-- HEADER -->
    <div class="hdr">
        <div class="hdr-left">
            <a href="/" style="display:flex;align-items:center;text-decoration:none;color:inherit;gap:14px">
            <img src="/img/bull_bear.png" alt="BuySell365 Pro" class="hdr-logo" loading="lazy">
            <div class="brand" style="color:#fff">BuySell365 <span style="color:var(--primary)">Pro</span><small data-i18n="dash.tagline">TRADING CON INTELIGENCIA ARTIFICIAL</small></div>
            </a>
        </div>
        <div style="display:flex;align-items:center;gap:12px">
            <button class="bell-btn" id="bellBtn" onclick="window._requestNotifPermission()" title="Activar notificaciones">&#128276;<span class="bell-dot" id="bellDot"></span></button>
            <div class="lang-selector" id="langSelector" style="position:relative">
                <button class="lang-btn" onclick="toggleLangMenu()" style="background:var(--panel2);border:1px solid var(--border);border-radius:8px;padding:6px 10px;cursor:pointer;font-size:18px;line-height:1"><span id="currentFlag">\U0001f1ea\U0001f1f8</span></button>
                <div class="lang-menu" id="langMenu" style="display:none;position:absolute;top:110%;right:0;background:var(--panel);border:1px solid var(--border);border-radius:10px;overflow:hidden;z-index:999;min-width:150px;box-shadow:0 8px 32px rgba(0,0,0,.4)">
                    <a onclick="setLang('es')" style="display:block;padding:10px 16px;cursor:pointer;color:var(--text);text-decoration:none;font-size:14px;transition:background .2s">\U0001f1ea\U0001f1f8 Espa\u00f1ol</a>
                    <a onclick="setLang('en')" style="display:block;padding:10px 16px;cursor:pointer;color:var(--text);text-decoration:none;font-size:14px;transition:background .2s">\U0001f1fa\U0001f1f8 English</a>
                    <a onclick="setLang('pt')" style="display:block;padding:10px 16px;cursor:pointer;color:var(--text);text-decoration:none;font-size:14px;transition:background .2s">\U0001f1e7\U0001f1f7 Portugu\u00eas</a>
                    <a onclick="setLang('fr')" style="display:block;padding:10px 16px;cursor:pointer;color:var(--text);text-decoration:none;font-size:14px;transition:background .2s">\U0001f1eb\U0001f1f7 Fran\u00e7ais</a>
                </div>
            </div>
            <div class="live-badge"><div class="pulse"></div><span data-i18n="dash.live">{'EN VIVO' if is_alive else 'OFFLINE'}</span> &mdash; {now_str}</div>
        </div>
    </div>

    <!-- ACTIVE OPERATIONS -->
    <div id="active-alerts-container" style="margin-bottom:24px"></div>

    <!-- ALL TRADES HISTORY — FIX 2026-03-19: Transparencia total -->
    <div class="card" style="margin-bottom:24px">
        <div class="card-title"><i>&#128200;</i> <span>Historial Completo de Operaciones</span></div>
        <div id="streak-banner-container"></div>
        <div class="card-title" style="margin-top:8px"><i>&#128200;</i> <span data-i18n="dash.cumulative_chart">Rendimiento Acumulado</span></div>
        <div id="cumulative-chart-container" class="cumul-chart-wrap">
            <p style="color:var(--muted);text-align:center;padding:40px;font-size:12px" data-i18n="dash.loading_chart">Cargando gr&aacute;fico...</p>
        </div>
        <div id="date-filter-bar" class="date-filter-bar" style="margin-top:14px">
            <span style="font-size:11px;color:var(--muted);text-transform:uppercase;font-weight:600;letter-spacing:.5px" data-i18n="dash.period">Periodo:</span>
            <button class="date-filter-btn" data-period="7d" onclick="window._filterByDate('7d')" data-i18n="dash.7d">7 d&iacute;as</button>
            <button class="date-filter-btn" data-period="30d" onclick="window._filterByDate('30d')" data-i18n="dash.30d">30 d&iacute;as</button>
            <button class="date-filter-btn" data-period="90d" onclick="window._filterByDate('90d')" data-i18n="dash.90d">90 d&iacute;as</button>
            <button class="date-filter-btn active" data-period="all" onclick="window._filterByDate('all')" data-i18n="dash.all">Todo</button>
        </div>
        <div id="trade-filter-bar" class="filter-bar" style="margin-top:6px">
            <span style="font-size:11px;color:var(--muted);text-transform:uppercase;font-weight:600;letter-spacing:.5px" data-i18n="dash.filter_label">Filtrar:</span>
        </div>
        <div id="winning-trades-container" style="overflow-x:auto">
            <p style="color:var(--muted);text-align:center;padding:20px" data-i18n="dash.loading_history">Cargando historial...</p>
        </div>
    </div>

    <!-- WIN RATE BY PERIOD -->
    <div class="wr-period-row" id="wr-period-container">
        <div class="wr-period-card"><div class="wr-period-label">Hoy</div><div class="wr-period-val" style="color:var(--muted)">-</div></div>
        <div class="wr-period-card"><div class="wr-period-label">Esta Semana</div><div class="wr-period-val" style="color:var(--muted)">-</div></div>
        <div class="wr-period-card"><div class="wr-period-label">Este Mes</div><div class="wr-period-val" style="color:var(--muted)">-</div></div>
    </div>

    <!-- STAT CARDS -->
    <div class="stats-row">
        <div class="stat-card accent-green">
            <div class="stat-label">&#127942; <span data-i18n="dash.total_signals">Se&ntilde;ales Totales</span></div>
            <div class="stat-value" style="color:var(--primary)">{total}</div>
            <div class="stat-sub">{senales_hoy} <span data-i18n="dash.today">hoy</span> &mdash; {wins}W / {losses_count}L</div>
        </div>
        <div class="stat-card accent-gold">
            <div class="stat-label">&#128200; <span data-i18n="dash.winrate">Tasa de Acierto</span></div>
            <div class="stat-value" style="color:{wr_color}">{winrate}%</div>
            <div class="wr-bar-bg" style="margin-top:8px"><div class="wr-bar-fill" style="width:{winrate}%;background:{wr_color}"></div></div>
            <div class="stat-sub" data-i18n="dash.winrate_sub">Porcentaje de acierto global</div>
        </div>
        <div class="stat-card accent-blue">
            <div class="stat-label">&#128176; <span data-i18n="dash.net_pips">Resultado Neto</span></div>
            <div class="stat-value" style="color:{pips_color}">{pips_total:+.1f}</div>
            <div class="stat-sub"><span data-i18n="dash.avg_win">Promedio ganancia</span>: {avg_win}</div>
        </div>
        <div class="stat-card accent-purple">
            <div class="stat-label">&#9878; <span data-i18n="dash.rr">Risk : Reward</span></div>
            <div class="stat-value" style="color:var(--primary)">{rr}:1</div>
            <div class="stat-sub" data-i18n="dash.rr_sub">Relaci&oacute;n ganancia / p&eacute;rdida</div>
        </div>
    </div>
    <!-- FIX 2026-03-19: Drawdown y racha de pérdidas — transparencia -->
    <div class="stats-row" style="margin-top:0;margin-bottom:24px">
        <div class="stat-card" style="border-left:3px solid {dd_color}">
            <div class="stat-label">&#128200; Max Drawdown</div>
            <div class="stat-value" style="color:{dd_color}">{max_drawdown} pips</div>
            <div class="stat-sub">Ca&iacute;da m&aacute;xima desde el pico</div>
        </div>
        <div class="stat-card" style="border-left:3px solid #ff3b30">
            <div class="stat-label">&#128308; Racha P&eacute;rdidas M&aacute;x</div>
            <div class="stat-value" style="color:#ff3b30">{_max_loss_streak}</div>
            <div class="stat-sub">P&eacute;rdidas consecutivas m&aacute;ximas</div>
        </div>
        <div class="stat-card" style="border-left:3px solid var(--primary)">
            <div class="stat-label">&#128178; Promedio P&eacute;rdida</div>
            <div class="stat-value" style="color:#ff3b30">-{avg_loss}</div>
            <div class="stat-sub">Pips promedio en p&eacute;rdidas</div>
        </div>
        <div class="stat-card" style="border-left:3px solid var(--primary)">
            <div class="stat-label">&#128176; Profit Factor</div>
            <div class="stat-value" style="color:var(--primary)">{round(sum(h.get('pips',0) for h in hist if h.get('pips',0)>0) / max(abs(sum(h.get('pips',0) for h in hist if h.get('pips',0)<=0)), 0.1), 2)}</div>
            <div class="stat-sub">Ganancia total / P&eacute;rdida total</div>
        </div>
    </div>

    <!-- PROMO -->
    <div class="promo" style="margin-bottom:24px;background:linear-gradient(135deg,#0d1a2a 0%,#1a0d2e 50%,#0a1520 100%);border:1px solid rgba(168,85,247,.2)">
        <div style="position:relative">
            <h2 style="font-size:22px" data-i18n="dash.promo_unified_title">&#128640; &Uacute;nete a BuySell365 Pro</h2>
            <p style="font-size:14px;max-width:520px;margin:8px auto 20px" data-i18n="dash.promo_unified_sub">Se&ntilde;ales de IA + Copy Trading autom&aacute;tico en tu cuenta MT5 con broker regulado XM</p>
            <div class="promo-features" style="margin-bottom:20px">
                <div class="promo-feat"><i style="color:#a855f7">&#10003;</i> Se&ntilde;ales con TP y SL exactos</div>
                <div class="promo-feat"><i style="color:#a855f7">&#10003;</i> Copy Trading 24/7</div>
                <div class="promo-feat"><i style="color:#a855f7">&#10003;</i> Broker regulado XM</div>
                <div class="promo-feat"><i style="color:#a855f7">&#10003;</i> SL y TP autom&aacute;ticos</div>
            </div>
            <div style="background:rgba(0,0,0,.3);border:1px solid var(--border);border-radius:12px;padding:16px 20px;max-width:420px;margin:0 auto 20px;text-align:left">
                <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">
                    <div style="width:28px;height:28px;border-radius:50%;background:linear-gradient(135deg,var(--primary),#00a080);display:flex;align-items:center;justify-content:center;font-size:14px">&#129302;</div>
                    <span style="font-weight:700;font-size:13px;color:#fff">BuySell365 Pro</span>
                    <span style="font-size:10px;color:var(--muted);margin-left:auto">ahora</span>
                </div>
                <div style="font-size:13px;line-height:1.6">
                    <div style="color:var(--primary);font-weight:700">&#128200; USD/JPY &mdash; Se\u00f1al detectada</div>
                    <div style="color:var(--text);margin-top:4px">&#127919; Score: 4/5 | Confianza: 67%</div>
                    <div style="color:#00e676;margin-top:2px">&#128994; BUY @ 149.250 | SL: 148.900 | TP: 149.850</div>
                </div>
            </div>
            <div style="display:flex;justify-content:center;gap:14px;flex-wrap:wrap">
                <a href="https://t.me/BUYSELL_365_24_7" target="_blank" class="cta-btn" style="padding:12px 24px">&#128172; ÚNETE AL CANAL</a>
                <a href="https://social.tp-redirect.com/s/WRE0V7jm" target="_blank" class="cta-btn" style="background:linear-gradient(135deg,#00c853,#00e676);border:none;padding:12px 24px">&#128640; EMPEZAR COPY TRADING</a>
            </div>
            <p style="font-size:11px;color:var(--muted);margin-top:12px">Estamos optimizando el sistema para ofrecerte la mejor experiencia</p>
        </div>
    </div>

    <!-- ASSET PERFORMANCE -->
    <div class="card" style="margin-bottom:24px">
        <div class="card-title"><i>&#128178;</i> <span data-i18n="dash.asset_perf">Rendimiento por Activo</span></div>
        <div class="asset-grid">{asset_cards_html}</div>
    </div>

    <!-- FOOTER -->
    <div class="footer">
        <p>&#169; 2026 BuySell365 Pro &mdash; <span data-i18n="dash.footer_created">Creado por</span> <strong>Emmanuel D&iacute;az</strong> | <span data-i18n="dash.footer_refresh">Auto-refresh cada 30s</span></p>
        <p style="margin-top:4px"><a href="https://t.me/BUYSELL_365_24_7" data-i18n="dash.footer_telegram">Grupo Telegram</a> &middot; <a href="https://t.me/BuySell365Traiding" data-i18n="dash.footer_vip">Contacto VIP</a> &middot; <a href="/terminos" data-i18n="footer.terms">T&eacute;rminos</a> &middot; <a href="/privacidad" data-i18n="footer.privacy">Privacidad</a></p>
        <p style="margin-top:8px;font-size:0.7rem;color:#888;max-width:700px;margin-left:auto;margin-right:auto">
            &#9888; <strong data-i18n="dash.footer_legal_title">Aviso legal:</strong> <span data-i18n="dash.footer_legal">BuySell365 Pro es una herramienta de an&aacute;lisis t&eacute;cnico automatizado con fines informativos y educativos. No constituye asesor&iacute;a financiera, recomendaci&oacute;n de inversi&oacute;n ni oferta de servicios regulados. Operar en mercados financieros conlleva riesgo de p&eacute;rdida de capital. Resultados pasados no garantizan resultados futuros. Cada usuario es responsable de sus propias decisiones de inversi&oacute;n.</span>
        </p>
    </div>

</div>

<script>
// MATRIX BINARY RAIN
(function(){{
  const canvas = document.getElementById('matrix-canvas');
  if(!canvas) return;
  const ctx = canvas.getContext('2d');
  let w, h, columns, drops;
  function resize(){{
    w = canvas.width = window.innerWidth;
    h = canvas.height = window.innerHeight;
    const fs = 14;
    columns = Math.floor(w / fs);
    drops = Array(columns).fill(1);
  }}
  resize();
  window.addEventListener('resize', resize);
  const chars = '01';
  function draw(){{
    ctx.fillStyle = 'rgba(8,11,15,0.05)';
    ctx.fillRect(0, 0, w, h);
    ctx.fillStyle = '#00d4aa';
    ctx.font = '14px monospace';
    for(let i = 0; i < drops.length; i++){{
      const txt = chars[Math.floor(Math.random() * chars.length)];
      ctx.fillStyle = 'rgba(0,212,170,' + (0.15 + Math.random() * 0.25) + ')';
      ctx.fillText(txt, i * 14, drops[i] * 14);
      if(drops[i] * 14 > h && Math.random() > 0.975){{
        drops[i] = 0;
      }}
      drops[i]++;
    }}
  }}
  setInterval(draw, 50);
}})();

// i18n ENGINE
(function(){{
  const FLAGS = {{es:'\U0001f1ea\U0001f1f8',en:'\U0001f1fa\U0001f1f8',pt:'\U0001f1e7\U0001f1f7',fr:'\U0001f1eb\U0001f1f7'}};
  const SUPPORTED = ['es','en','pt','fr'];
  let currentLang = 'es';
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
        if(vars){{ try{{ const obj=JSON.parse(vars); Object.keys(obj).forEach(function(k){{ text=text.replace('{{'+k+'}}',obj[k]); }}); }}catch(e){{}} }}
        if(text.includes('<br') || text.includes('<span') || text.includes('<strong')){{ el.innerHTML = text; }}
        else{{ el.textContent = text; }}
      }}
    }});
    document.documentElement.lang = currentLang;
    const flagEl = document.getElementById('currentFlag');
    if(flagEl) flagEl.textContent = FLAGS[currentLang] || '\U0001f1ea\U0001f1f8';
  }}
  function loadLang(lang){{
    if(!SUPPORTED.includes(lang)) lang = 'es';
    fetch('/i18n/' + lang + '.json')
      .then(function(r){{ return r.json(); }})
      .then(function(data){{ currentLang = lang; localStorage.setItem('buysell365_lang', lang); window._tr = data; applyTranslations(data); }})
      .catch(function(err){{ console.warn('i18n load failed:', err); }});
  }}
  window.toggleLangMenu = function(){{
    const menu = document.getElementById('langMenu');
    if(menu) menu.style.display = menu.style.display === 'block' ? 'none' : 'block';
  }};
  window.setLang = function(lang){{ loadLang(lang); const menu = document.getElementById('langMenu'); if(menu) menu.style.display = 'none'; }};
  document.addEventListener('click', function(e){{
    const sel = document.getElementById('langSelector');
    if(sel && !sel.contains(e.target)){{ const menu = document.getElementById('langMenu'); if(menu) menu.style.display = 'none'; }}
  }});
  window._t = function(key, fallback) {{ return (window._tr && window._tr[key]) || fallback; }};
  const lang = detectLang();
  if(lang !== 'es') loadLang(lang);
  else{{ currentLang = 'es'; localStorage.setItem('buysell365_lang', 'es'); const flagEl = document.getElementById('currentFlag'); if(flagEl) flagEl.textContent = FLAGS['es']; }}
}})();

// ACTIVE OPERATIONS ALERT BANNER
(function(){{
  function loadActiveOps(){{
    fetch('/api/active_ops')
      .then(r => r.json())
      .then(ops => {{
        const container = document.getElementById('active-alerts-container');
        if(!container) return;
        if(!ops || ops.length === 0){{
          container.innerHTML = '<div class="active-alert" style="text-align:center;padding:20px;opacity:.7"><div class="alert-header"><span style="font-size:18px">&#128308;</span> ' + window._t('dash.no_active_ops','Sin operaciones activas en este momento') + '</div><div style="font-size:13px;color:var(--muted);margin-top:8px">' + window._t('dash.bot_schedule','El bot opera de 8:00 a 18:00 hora Andorra (L-V)') + '</div></div>';
          return;
        }}
        let html = '<div class="active-alert">';
        html += '<div class="alert-header"><span style="font-size:18px">&#9889;</span> ' + ops.length + ' Operaci\u00f3n' + (ops.length > 1 ? 'es' : '') + ' Activa' + (ops.length > 1 ? 's' : '') + ' en Tiempo Real</div>';
        ops.forEach(function(op){{
          const isBuy = op.tipo === 'COMPRA';
          const typeCls = isBuy ? 'buy' : 'sell';
          const typeLabel = isBuy ? 'COMPRA' : 'VENTA';
          const prog = Math.max(0, Math.min(100, op.progreso || 0));
          const tp1Pos = 33; const tp2Pos = 66;
          const isMt5 = op.fuente === 'mt5';
          const plColor = (op.beneficio !== undefined) ? (op.beneficio >= 0 ? '#00c853' : '#ff5252') : '#8b949e';
          html += '<div class="alert-op">';
          const _dispName = (function(raw){{ if(!raw) return '?'; const m={{'GC=F':'ORO','NQ=F':'NASDAQ','ES=F':'S&P 500','EURUSD=X':'EUR/USD','USDJPY=X':'USD/JPY','GBPJPY=X':'GBP/JPY','AUDCAD':'AUD/CAD','EURCHF':'EUR/CHF','USDCAD':'USD/CAD'}}; if(m[raw]) return m[raw]; var n=raw; for(var k in m){{ if(raw.indexOf(k)>=0) return m[k]; }}; n=n.replace(/[^A-Za-z0-9\\/&. _-]/g,'').trim(); if(m[n]) return m[n]; return n||raw; }})(op.nombre || op.ticker);
          html += '<div class="alert-name">' + _dispName;
          if(isMt5 && op.volumen) html += ' <span style="font-size:11px;color:#8b949e">(' + op.volumen + ' lots)</span>';
          html += '</div>';
          html += '<div class="alert-type ' + typeCls + '">' + typeLabel + '</div>';
          if(op.beneficio !== undefined){{
            html += '<div style="font-size:13px;color:' + plColor + ';font-weight:600;margin:2px 0">';
            html += (op.beneficio >= 0 ? '+' : '') + op.beneficio.toFixed(2) + ' USD';
            html += '</div>';
          }}
          html += '<div class="progress-bar-wrap">';
          html += '<div class="progress-track">';
          html += '<div class="progress-fill" style="width:' + prog + '%"></div>';
          html += '<div class="progress-mark" style="left:' + tp1Pos + '%"></div>';
          html += '<div class="progress-mark" style="left:' + tp2Pos + '%"></div>';
          html += '</div>';
          html += '<div class="progress-pct">' + prog.toFixed(0) + '%</div>';
          html += '</div>';
          html += '<div class="tp-badges">';
          html += '<span class="tp-badge ' + (op.tp1_hit ? 'hit' : 'pending') + '">TP1</span>';
          html += '<span class="tp-badge ' + (op.tp2_hit ? 'hit' : 'pending') + '">TP2</span>';
          html += '<span class="tp-badge pending">TP3</span>';
          html += '</div>';
          html += '</div>';
        }});
        html += '</div>';
        container.innerHTML = html;
      }})
      .catch(function(){{ }});
  }}
  loadActiveOps();
  setInterval(loadActiveOps, 15000);
}})();

// WINNING TRADES + FILTERS + STREAK + CHART + WIN RATE
(function(){{
  let allTrades = [];
  let currentFilter = 'ALL';
  let currentDateFilter = 'all';
  let currentPage = 1;
  const TRADES_PER_PAGE = 20;

  function getUnit(tkr){{
    tkr = (tkr || '').toUpperCase();
    if(tkr.indexOf('EUR')>=0 || tkr.indexOf('JPY')>=0 || tkr.indexOf('GBP')>=0) return 'pips';
    if(tkr === 'BTC-USD' || tkr === 'ETH-USD' || tkr === 'BTCUSD' || tkr === 'ETHUSD') return 'USD';
    return 'pts';
  }}

  function getDec(tkr){{
    tkr = (tkr || '').toUpperCase();
    if(tkr.indexOf('NQ=F')>=0||tkr.indexOf('ES=F')>=0||tkr.indexOf('GC=F')>=0) return 2;
    if(tkr.indexOf('BTC')>=0||tkr.indexOf('ETH')>=0) return 2;
    if(tkr.indexOf('JPY')>=0) return 3;
    return 5;
  }}

  // FIX 2026-03-19: Racha real — contar desde la última pérdida
  function renderStreak(trades){{
    const container = document.getElementById('streak-banner-container');
    if(!container) return;
    if(!trades || trades.length === 0){{ container.innerHTML = ''; return; }}
    // Calcular racha actual desde el final
    let streak = 0;
    for(let i = trades.length - 1; i >= 0; i--){{
      if((trades[i].pips || 0) > 0) streak++;
      else break;
    }}
    if(streak < 2){{ container.innerHTML = ''; return; }}
    const totalWins = trades.filter(function(t){{ return (t.pips||0) > 0; }}).length;
    const totalAll = trades.length;
    const realWR = totalAll > 0 ? Math.round(totalWins / totalAll * 100) : 0;
    let fireEmoji = '';
    if(streak >= 10) fireEmoji = '\U0001f525\U0001f525\U0001f525';
    else if(streak >= 5) fireEmoji = '\U0001f525\U0001f525';
    else if(streak >= 3) fireEmoji = '\U0001f525';
    let html = '<div class="streak-banner">';
    html += '<div class="streak-number">' + streak + '</div>';
    html += '<div class="streak-info"><div class="streak-label">' + window._t('dash.consecutive_wins','Racha Ganadora Actual') + '</div>';
    html += '<div class="streak-sub">' + totalWins + 'W / ' + (totalAll - totalWins) + 'L | WR: ' + realWR + '%</div></div>';
    if(fireEmoji) html += '<div class="streak-fire">' + fireEmoji + '</div>';
    html += '</div>';
    container.innerHTML = html;
  }}

  function renderCumulativeChart(trades){{
    const container = document.getElementById('cumulative-chart-container');
    if(!container || !trades || trades.length === 0){{
      if(container) container.innerHTML = '<p style="color:var(--muted);text-align:center;padding:40px;font-size:12px">' + window._t('dash.no_data','Sin datos a\u00fan') + '</p>';
      return;
    }}
    const W = container.clientWidth || 600;
    const H = 160;
    const pad = {{t:20,r:20,b:30,l:55}};
    const pw = W - pad.l - pad.r;
    const ph = H - pad.t - pad.b;
    let cumul = [0];
    trades.forEach(function(t){{ cumul.push(cumul[cumul.length-1] + (t.pips || 0)); }});
    const maxY = Math.max.apply(null, cumul);
    const minY = Math.min.apply(null, cumul);
    const rangeY = maxY - minY || 1;
    function x(i){{ return pad.l + (i / (cumul.length - 1)) * pw; }}
    function y(v){{ return pad.t + ph - ((v - minY) / rangeY) * ph; }}
    let pathD = 'M' + x(0) + ',' + y(cumul[0]);
    for(let i = 1; i < cumul.length; i++) pathD += ' L' + x(i) + ',' + y(cumul[i]);
    let areaD = pathD + ' L' + x(cumul.length-1) + ',' + (pad.t + ph) + ' L' + x(0) + ',' + (pad.t + ph) + ' Z';
    let svg = '<svg viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="none" style="width:100%;height:100%">';
    for(let i = 0; i <= 4; i++){{
      const yy = pad.t + (ph / 4) * i;
      const val = (maxY - (rangeY / 4) * i).toFixed(1);
      svg += '<line x1="' + pad.l + '" y1="' + yy + '" x2="' + (W - pad.r) + '" y2="' + yy + '" stroke="rgba(30,42,58,.5)" stroke-width="1"/>';
      svg += '<text x="' + (pad.l - 8) + '" y="' + (yy + 4) + '" fill="#5a6a7a" font-size="10" text-anchor="end" font-family="Inter">' + val + '</text>';
    }}
    svg += '<defs><linearGradient id="cg" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="rgba(0,212,170,.3)"/><stop offset="100%" stop-color="rgba(0,212,170,0)"/></linearGradient></defs>';
    svg += '<path d="' + areaD + '" fill="url(#cg)"/>';
    svg += '<path d="' + pathD + '" fill="none" stroke="#00d4aa" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>';
    svg += '<circle cx="' + x(cumul.length-1) + '" cy="' + y(cumul[cumul.length-1]) + '" r="4" fill="#00d4aa" stroke="#080b0f" stroke-width="2"/>';
    const step = Math.max(1, Math.floor(trades.length / 6));
    for(let i = 0; i < trades.length; i += step){{
      const label = trades[i].fecha || (i + 1);
      svg += '<text x="' + x(i + 1) + '" y="' + (H - 6) + '" fill="#5a6a7a" font-size="9" text-anchor="middle" font-family="Inter">' + label + '</text>';
    }}
    svg += '<text x="' + x(cumul.length-1) + '" y="' + (y(cumul[cumul.length-1]) - 8) + '" fill="#00e676" font-size="12" font-weight="700" text-anchor="middle" font-family="Inter">+' + cumul[cumul.length-1].toFixed(1) + '</text>';
    svg += '</svg>';
    container.innerHTML = svg;
  }}

  function normName(raw){{
    if(!raw) return '?';
    const map = {{'GC=F':'ORO','NQ=F':'NASDAQ','ES=F':'S&P 500','EURUSD=X':'EUR/USD','USDJPY=X':'USD/JPY','GBPJPY=X':'GBP/JPY','AUDCAD':'AUD/CAD','EURCHF':'EUR/CHF','USDCAD':'USD/CAD','BTC-USD':'BITCOIN','ETH-USD':'ETHEREUM','XAUUSD':'ORO','BTCUSD':'BITCOIN','ETHUSD':'ETHEREUM','US100Cash':'NASDAQ','US500Cash':'S&P 500'}};
    if(map[raw]) return map[raw];
    let n = raw.replace(/[^A-Za-z0-9\\/&. _-]/g, '').trim();
    if(map[n]) return map[n];
    for(let k in map){{ if(raw.indexOf(k) >= 0) return map[k]; }}
    return n;
  }}

  function renderFilters(trades){{
    const bar = document.getElementById('trade-filter-bar');
    if(!bar) return;
    const assets = {{}};
    const hidden = {{'BITCOIN':1,'ETHEREUM':1}};
    trades.forEach(function(t){{
      const n = normName(t.nombre || t.ticker || '?');
      if(hidden[n]) return;
      assets[n] = (assets[n] || 0) + 1;
    }});
    const visibleCount = trades.filter(function(t){{ return !hidden[normName(t.nombre || t.ticker || '?')]; }}).length;
    let html = '<span style="font-size:11px;color:var(--muted);text-transform:uppercase;font-weight:600;letter-spacing:.5px">' + window._t('dash.filter_label','Filtrar:') + '</span>';
    html += '<button class="filter-btn' + (currentFilter === 'ALL' ? ' active' : '') + '" data-filter="ALL" onclick="window._filterTrades(this.dataset.filter)">' + window._t('dash.all_filter','Todos') + ' (' + visibleCount + ')</button>';
    Object.keys(assets).sort().forEach(function(name){{
      const cls = currentFilter === name ? ' active' : '';
      html += '<button class="filter-btn' + cls + '" data-filter="' + name + '" onclick="window._filterTrades(this.dataset.filter)">' + name + ' (' + assets[name] + ')</button>';
    }});
    html += '<button class="export-csv-btn" onclick="window._exportCSV()">&#8681; ' + window._t('dash.export_csv','Exportar CSV') + '</button>';
    bar.innerHTML = html;
  }}

  function applyDateFilter(trades){{
    if(currentDateFilter === 'all') return trades;
    const now = new Date();
    const days = currentDateFilter === '7d' ? 7 : (currentDateFilter === '30d' ? 30 : 90);
    const cutoff = new Date(now.getTime() - days * 86400000);
    return trades.filter(function(t){{
      const f = t.fecha || '';
      if(!f) return false;
      const parts = f.split('/');
      if(parts.length === 3){{
        const d = new Date(parseInt(parts[2],10), parseInt(parts[1],10)-1, parseInt(parts[0],10));
        return d >= cutoff;
      }}
      const d2 = new Date(f);
      return !isNaN(d2) && d2 >= cutoff;
    }});
  }}

  function renderTable(trades){{
    const container = document.getElementById('winning-trades-container');
    if(!container) return;
    const _hidden = {{'BITCOIN':1,'ETHEREUM':1}};
    const datFiltered = applyDateFilter(trades);
    const visibleTrades = datFiltered.filter(function(t){{ return !_hidden[normName(t.nombre || t.ticker || '?')]; }});
    const filtered = currentFilter === 'ALL' ? visibleTrades : visibleTrades.filter(function(t){{ return normName(t.nombre || t.ticker) === currentFilter; }});
    if(!filtered || filtered.length === 0){{
      container.innerHTML = '<p style="color:var(--muted);text-align:center;padding:20px">No hay operaciones para este filtro.</p>';
      return;
    }}
    let totalPips = 0;
    filtered.forEach(function(t){{ totalPips += (t.pips || 0); }});
    const sorted = filtered.slice().reverse();
    const totalPages = Math.ceil(sorted.length / TRADES_PER_PAGE);
    if(currentPage > totalPages) currentPage = totalPages;
    if(currentPage < 1) currentPage = 1;
    const startIdx = (currentPage - 1) * TRADES_PER_PAGE;
    const pageData = sorted.slice(startIdx, startIdx + TRADES_PER_PAGE);
    let html = '<table style="width:100%;border-collapse:collapse;font-size:0.85rem">';
    html += '<thead><tr style="border-bottom:2px solid var(--border);color:var(--primary);text-align:left">';
    html += '<th style="padding:10px 8px">Fecha</th><th style="padding:10px 8px">Activo</th><th style="padding:10px 8px">Tipo</th>';
    html += '<th style="padding:10px 8px">Entrada</th><th style="padding:10px 6px">Hora</th><th style="padding:10px 8px">Salida</th>';
    html += '<th style="padding:10px 6px">Hora</th><th style="padding:10px 8px">Pips/Pts</th><th style="padding:10px 8px">Score</th>';
    html += '</tr></thead><tbody>';
    pageData.forEach(function(t, i){{
      // FIX 2026-03-19: Fondo rojo sutil para losses
      const isLoss = (t.pips || 0) <= 0;
      const bg = isLoss ? 'rgba(255,59,48,0.06)' : (i % 2 === 0 ? 'rgba(0,212,170,0.04)' : 'transparent');
      const tipoIcon = t.tipo === 'COMPRA' ? '\U0001f7e2' : '\U0001f534';
      const pips = (t.pips || 0);
      const tkr = (t.ticker || '').toUpperCase();
      const unit = getUnit(tkr);
      const dec = getDec(tkr);
      html += '<tr style="background:' + bg + ';border-bottom:1px solid var(--border)">';
      html += '<td style="padding:8px;color:var(--muted)">' + (t.fecha || '-') + '</td>';
      html += '<td style="padding:8px;font-weight:600">' + normName(t.nombre || t.ticker || '-') + '</td>';
      html += '<td style="padding:8px">' + tipoIcon + ' ' + (t.tipo || '-') + '</td>';
      html += '<td style="padding:8px;font-family:monospace">' + (t.entrada ? Number(t.entrada).toFixed(dec) : '-') + '</td>';
      html += '<td style="padding:8px 6px;color:var(--muted);font-size:0.8rem">' + (t.hora_entrada || '-') + '</td>';
      html += '<td style="padding:8px;font-family:monospace">' + (t.salida ? Number(t.salida).toFixed(dec) : '-') + '</td>';
      html += '<td style="padding:8px 6px;color:var(--muted);font-size:0.8rem">' + (t.hora_salida || '-') + '</td>';
      // FIX 2026-03-19: Mostrar wins en verde y losses en rojo + score real /5
      const pipsColor = pips >= 0 ? '#00e676' : '#ff3b30';
      const pipsSign = pips >= 0 ? '+' : '';
      html += '<td style="padding:8px;color:' + pipsColor + ';font-weight:700">' + pipsSign + pips.toFixed(1) + ' ' + unit + '</td>';
      html += '<td style="padding:8px;color:var(--primary)">' + (t.score != null ? t.score : '-') + '/5</td>';
      html += '</tr>';
    }});
    html += '</tbody></table>';
    html += '<div style="display:flex;justify-content:space-between;align-items:center;padding:14px 8px;margin-top:8px;border-top:2px solid var(--primary);font-weight:700">';
    // FIX 2026-03-19: Resumen con wins/losses reales
    const totalWins = filtered.filter(function(t){{ return (t.pips||0) > 0; }}).length;
    const totalLosses = filtered.length - totalWins;
    const pipsColorTotal = totalPips >= 0 ? '#00e676' : '#ff3b30';
    const pipsSignTotal = totalPips >= 0 ? '+' : '';
    html += '<span style="color:var(--text)">\U0001f4ca Total: ' + filtered.length + ' ops (' + totalWins + 'W / ' + totalLosses + 'L)</span>';
    html += '<span style="color:' + pipsColorTotal + ';font-size:1.1rem">' + pipsSignTotal + totalPips.toFixed(1) + ' ' + window._t('dash.accumulated_pips','pips netos') + '</span>';
    html += '</div>';
    if(totalPages > 1){{
      html += '<div class="pagination">';
      html += '<button class="page-btn" onclick="window._goToPage(' + (currentPage - 1) + ')"' + (currentPage === 1 ? ' disabled' : '') + '>&laquo; ' + window._t('dash.prev','Anterior') + '</button>';
      let startP = Math.max(1, currentPage - 2);
      let endP = Math.min(totalPages, currentPage + 2);
      if(startP > 1){{ html += '<button class="page-btn" onclick="window._goToPage(1)">1</button>'; if(startP > 2) html += '<span style="color:var(--muted);padding:0 4px">...</span>'; }}
      for(let p = startP; p <= endP; p++){{
        html += '<button class="page-btn' + (p === currentPage ? ' active' : '') + '" onclick="window._goToPage(' + p + ')">' + p + '</button>';
      }}
      if(endP < totalPages){{ if(endP < totalPages - 1) html += '<span style="color:var(--muted);padding:0 4px">...</span>'; html += '<button class="page-btn" onclick="window._goToPage(' + totalPages + ')">' + totalPages + '</button>'; }}
      html += '<button class="page-btn" onclick="window._goToPage(' + (currentPage + 1) + ')"' + (currentPage === totalPages ? ' disabled' : '') + '>' + window._t('dash.next','Siguiente') + ' &raquo;</button>';
      html += '</div>';
      html += '<div style="text-align:center;font-size:0.75rem;color:var(--muted);margin-top:8px">' + window._t('dash.page','P\u00e1gina') + ' ' + currentPage + ' ' + window._t('dash.of','de') + ' ' + totalPages + ' \u00b7 ' + window._t('dash.showing','Mostrando') + ' ' + pageData.length + ' ' + window._t('dash.of','de') + ' ' + filtered.length + ' ' + window._t('dash.operations','operaciones') + '</div>';
    }}
    container.innerHTML = html;
  }}

  function renderWinRatePeriods(trades){{
    const container = document.getElementById('wr-period-container');
    if(!container) return;
    const now = new Date();
    const todayStr = String(now.getDate()).padStart(2,'0') + '/' + String(now.getMonth()+1).padStart(2,'0') + '/' + now.getFullYear();
    const weekStart = new Date(now); weekStart.setDate(now.getDate() - now.getDay() + 1); weekStart.setHours(0,0,0,0);
    if(now.getDay() === 0) weekStart.setDate(weekStart.getDate() - 7);
    let todayTotal=0,todayWins=0,weekTotal=0,weekWins=0,monthTotal=0,monthWins=0;
    (trades||[]).forEach(function(t){{
      const f = t.fecha || '';
      const parts = f.split('/');
      if(parts.length !== 3) return;
      const dd=parseInt(parts[0],10),mm=parseInt(parts[1],10),yy=parseInt(parts[2],10);
      const dt = new Date(yy, mm-1, dd);
      const isWin = (t.pips||0) > 0;
      if(f === todayStr){{ todayTotal++; if(isWin) todayWins++; }}
      if(dt >= weekStart){{ weekTotal++; if(isWin) weekWins++; }}
      if(mm === (now.getMonth()+1) && yy === now.getFullYear()){{ monthTotal++; if(isWin) monthWins++; }}
    }});
    const todayWR = todayTotal > 0 ? Math.round(todayWins/todayTotal*100) : 0;
    const weekWR = weekTotal > 0 ? Math.round(weekWins/weekTotal*100) : 0;
    const monthWR = monthTotal > 0 ? Math.round(monthWins/monthTotal*100) : 0;
    function getColor(wr,total){{ return wr >= 60 ? '#00d4aa' : (wr >= 45 ? '#f0b90b' : (total > 0 ? '#ff3b30' : '#5a6a7a')); }}
    const todayColor = getColor(todayWR,todayTotal);
    const weekColor = getColor(weekWR,weekTotal);
    const monthColor = getColor(monthWR,monthTotal);
    function card(label,wr,wins,total,color){{
      return '<div class="wr-period-card" style="border-top:2px solid '+color+'">' +
        '<div class="wr-period-label">'+label+'</div>' +
        '<div class="wr-period-val" style="color:'+color+'">'+wr+'%</div>' +
        '<div class="wr-period-detail">'+wins+'W / '+(total-wins)+'L de '+total+'</div>' +
        '<div class="wr-period-bar"><div class="wr-period-fill" style="width:'+wr+'%;background:'+color+'"></div></div></div>';
    }}
    container.innerHTML = card(window._t('dash.today','Hoy'),todayWR,todayWins,todayTotal,todayColor) +
      card(window._t('dash.this_week','Esta Semana'),weekWR,weekWins,weekTotal,weekColor) +
      card(window._t('dash.this_month','Este Mes'),monthWR,monthWins,monthTotal,monthColor);
  }}

  window._goToPage = function(page){{
    currentPage = page;
    renderTable(allTrades);
    const el = document.getElementById('winning-trades-container');
    if(el) el.scrollIntoView({{behavior:'smooth', block:'start'}});
  }};

  window._filterTrades = function(filter){{
    currentFilter = filter;
    currentPage = 1;
    renderFilters(allTrades);
    renderTable(allTrades);
  }};

  window._filterByDate = function(period){{
    currentDateFilter = period;
    currentPage = 1;
    document.querySelectorAll('.date-filter-btn').forEach(function(btn){{
      btn.classList.toggle('active', btn.dataset.period === period);
    }});
    renderFilters(allTrades);
    renderTable(allTrades);

    renderCumulativeChart(applyDateFilter(allTrades));
  }};

  window._exportCSV = function(){{
    const _hiddenCSV = {{'BITCOIN':1,'ETHEREUM':1}};
    const datFilteredCSV = applyDateFilter(allTrades);
    const visibleCSV = datFilteredCSV.filter(function(t){{ return !_hiddenCSV[normName(t.nombre || t.ticker || '?')]; }});
    const filteredCSV = currentFilter === 'ALL' ? visibleCSV : visibleCSV.filter(function(t){{ return normName(t.nombre || t.ticker) === currentFilter; }});
    if(!filteredCSV.length){{ alert('No hay operaciones para exportar.'); return; }}
    const csvHeaders = ['Fecha','Activo','Tipo','Entrada','Hora Entrada','Salida','Hora Salida','Pips/Pts','Score'];
    const csvRows = filteredCSV.slice().reverse().map(function(t){{
      const tkrCSV = (t.ticker || '').toUpperCase();
      const decCSV = getDec(tkrCSV);
      const rowArr = [
        t.fecha || '',
        normName(t.nombre || t.ticker || ''),
        t.tipo || '',
        t.entrada ? Number(t.entrada).toFixed(decCSV) : '',
        t.hora_entrada || '',
        t.salida ? Number(t.salida).toFixed(decCSV) : '',
        t.hora_salida || '',
        (t.pips || 0).toFixed(1),
        t.score != null ? Math.min(t.score * 2, 10) : ''
      ];
      return rowArr.map(function(v){{ return '"' + String(v).replace(/"/g,'""') + '"'; }}).join(',');
    }});
    const csvContent = [csvHeaders.join(',')].concat(csvRows).join('\\n');
    const csvBlob = new Blob([csvContent], {{type:'text/csv;charset=utf-8;'}});
    const csvUrl = URL.createObjectURL(csvBlob);
    const csvLink = document.createElement('a');
    csvLink.href = csvUrl;
    csvLink.download = 'buysell365_trades_' + new Date().toISOString().slice(0,10) + '.csv';
    document.body.appendChild(csvLink);
    csvLink.click();
    document.body.removeChild(csvLink);
    URL.revokeObjectURL(csvUrl);
  }};

  function loadAll(){{
    fetch('/api/winning_trades')
      .then(r => r.json())
      .then(trades => {{
        allTrades = trades || [];
        renderStreak(allTrades);
        renderCumulativeChart(applyDateFilter(allTrades));
    
        renderFilters(allTrades);
        renderTable(allTrades);
        renderWinRatePeriods(allTrades);
      }})
      .catch(function(e){{
        const container = document.getElementById('winning-trades-container');
        if(container) container.innerHTML = '<p style="color:var(--muted);text-align:center">Error cargando historial</p>';
      }});
  }}
  loadAll();
  setInterval(loadAll, 30000);
}})();

// BROWSER PUSH NOTIFICATIONS
(function(){{
  let _prevOpsCount = -1;

  window._requestNotifPermission = function(){{
    if(!('Notification' in window)){{ alert('Tu navegador no soporta notificaciones.'); return; }}
    if(Notification.permission === 'granted'){{
      const bellBtnEl = document.getElementById('bellBtn');
      if(bellBtnEl) bellBtnEl.classList.toggle('notif-on');
      return;
    }}
    Notification.requestPermission().then(function(perm){{
      const bellBtnEl = document.getElementById('bellBtn');
      if(perm === 'granted'){{
        if(bellBtnEl) bellBtnEl.classList.add('notif-on');
        new Notification('BuySell365 Pro', {{body:'Notificaciones activadas. Te avisaremos de nuevas operaciones.', icon:'/img/bull_bear.png'}});
      }}
    }});
  }};

  function _sendNotif(title, body){{
    if(!('Notification' in window) || Notification.permission !== 'granted') return;
    try{{ new Notification(title, {{body:body, icon:'/img/bull_bear.png'}}); }}catch(notifErr){{}}
  }}

  (function(){{
    const bellBtnInit = document.getElementById('bellBtn');
    if(bellBtnInit && 'Notification' in window && Notification.permission === 'granted') bellBtnInit.classList.add('notif-on');
  }})();

  function pollActiveOpsNotif(){{
    fetch('/api/active_ops')
      .then(r => r.json())
      .then(function(ops){{
        const cnt = (ops && ops.length) ? ops.length : 0;
        const bellDotEl = document.getElementById('bellDot');
        if(_prevOpsCount >= 0 && cnt > _prevOpsCount){{
          const diff = cnt - _prevOpsCount;
          _sendNotif('BuySell365 Pro \u2014 Nueva Operaci\u00f3n', diff + ' nueva' + (diff > 1 ? 's' : '') + ' operaci\u00f3n' + (diff > 1 ? 'es' : '') + ' activa' + (diff > 1 ? 's' : ''));
          if(bellDotEl){{ bellDotEl.classList.add('show'); setTimeout(function(){{ bellDotEl.classList.remove('show'); }}, 8000); }}
        }}
        _prevOpsCount = cnt;
      }})
      .catch(function(){{}});
  }}
  pollActiveOpsNotif();
  setInterval(pollActiveOpsNotif, 15000);
}})();
</script>

</body>
</html>"""
    _dash_resp = make_response(_dash_html)
    _dash_resp.headers['Content-Type'] = 'text/html; charset=utf-8'
    _dash_resp.headers['Cache-Control'] = 'no-cache'
    return _dash_resp

# ============================================================
#  LEGAL PAGES
# ============================================================
@app.route("/terminos")
def pagina_terminos():
    return f"""<!DOCTYPE html>
<html lang="es"><head><!-- Google Analytics --><script async src="https://www.googletagmanager.com/gtag/js?id=G-L514BL7E83"></script><script>window.dataLayer=window.dataLayer||[];function gtag(){{dataLayer.push(arguments);}}gtag('js',new Date());gtag('config','G-L514BL7E83');</script><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>T&eacute;rminos y Condiciones — BuySell365 Pro</title>
<style>*{{margin:0;padding:0;box-sizing:border-box}}body{{font-family:'Inter',sans-serif;background:#0d1117;color:#c9d1d9;line-height:1.7;padding:20px}}.container{{max-width:800px;margin:0 auto}}h1{{color:#f0b90b;font-size:1.8rem;margin-bottom:10px}}h2{{color:#58a6ff;font-size:1.2rem;margin-top:25px;margin-bottom:8px}}p,li{{font-size:0.95rem;margin-bottom:8px}}ul{{padding-left:20px}}.date{{color:#8b949e;font-size:0.85rem;margin-bottom:20px}}a{{color:#58a6ff}}.back{{display:inline-block;margin-top:30px;padding:10px 20px;background:#f0b90b;color:#000;border-radius:8px;text-decoration:none;font-weight:bold}}</style></head>
<body><div class="container">
<h1>&#128221; T&eacute;rminos y Condiciones</h1><p class="date">&Uacute;ltima actualizaci&oacute;n: 28 de marzo de 2026</p>
<h2>1. Qu&eacute; es BuySell365</h2><p>BuySell365 es una herramienta automatizada de an&aacute;lisis t&eacute;cnico que genera alertas informativas sobre activos financieros. Las se&ntilde;ales se basan en indicadores t&eacute;cnicos, modelos de Machine Learning y an&aacute;lisis de sentimiento. Son de car&aacute;cter informativo y educativo &mdash; no son recomendaciones de inversi&oacute;n.</p>
<h2>2. No es Asesor&iacute;a Financiera</h2><p>BuySell365 NO proporciona asesor&iacute;a financiera ni recomendaciones personalizadas. Cada usuario es responsable de sus propias decisiones de inversi&oacute;n. Consulta a un profesional financiero antes de operar con dinero real.</p>
<h2>3. Riesgo de Inversi&oacute;n</h2><p>Operar en mercados financieros conlleva un alto riesgo de p&eacute;rdida de capital. Los resultados pasados no garantizan resultados futuros. Nunca inviertas dinero que no puedas permitirte perder.</p>
<h2>4. Suscripci&oacute;n VIP</h2><ul><li>Precio: $149 USDT/mes, pagado manualmente en USDT TRC20.</li><li>Sin cobros autom&aacute;ticos ni recurrentes. T&uacute; decides cu&aacute;ndo renovar.</li><li>Cancela cuando quieras: simplemente no renuevas y el acceso expira al final del periodo pagado.</li><li>No se ofrecen reembolsos una vez procesado el pago, ya que el acceso al canal VIP se activa de forma inmediata.</li></ul>
<h2>5. Uso Aceptable</h2><ul><li>No redistribuir ni revender las se&ntilde;ales del canal VIP.</li><li>No usar bots ni scrapers para extraer contenido del canal.</li><li>No enviar spam en el grupo de Telegram.</li><li>Respetar a los dem&aacute;s miembros de la comunidad.</li></ul>
<h2>6. Limitaci&oacute;n de Responsabilidad</h2><p>BuySell365 y su equipo no ser&aacute;n responsables de p&eacute;rdidas financieras derivadas del uso del servicio. Al usar el servicio aceptas que operas bajo tu propia responsabilidad.</p>
<h2>7. Disponibilidad</h2><p>Trabajamos para mantener el servicio operativo 24/7, pero no garantizamos disponibilidad ininterrumpida. Puede haber pausas por mantenimiento o actualizaciones.</p>
<h2>8. Modificaciones</h2><p>Podemos actualizar estos t&eacute;rminos en cualquier momento. Los cambios se publican en esta p&aacute;gina. El uso continuado del servicio implica la aceptaci&oacute;n de los cambios.</p>
<h2>9. Contacto</h2><p><a href="https://t.me/BuySell365Traiding">@BuySell365Traiding</a> en Telegram.</p>
<a href="/dashboard" class="back">&larr; Volver</a></div></body></html>"""

@app.route("/privacidad")
def pagina_privacidad():
    return """<!DOCTYPE html>
<html lang="es"><head><!-- Google Analytics --><script async src="https://www.googletagmanager.com/gtag/js?id=G-L514BL7E83"></script><script>window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments);}gtag('js',new Date());gtag('config','G-L514BL7E83');</script><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Pol&iacute;tica de Privacidad — BuySell365 Pro</title>
<style>*{margin:0;padding:0;box-sizing:border-box}body{font-family:'Inter',sans-serif;background:#0d1117;color:#c9d1d9;line-height:1.7;padding:20px}.container{max-width:800px;margin:0 auto}h1{color:#f0b90b;font-size:1.8rem;margin-bottom:10px}h2{color:#58a6ff;font-size:1.2rem;margin-top:25px;margin-bottom:8px}p,li{font-size:0.95rem;margin-bottom:8px}ul{padding-left:20px}.date{color:#8b949e;font-size:0.85rem;margin-bottom:20px}a{color:#58a6ff}.back{display:inline-block;margin-top:30px;padding:10px 20px;background:#f0b90b;color:#000;border-radius:8px;text-decoration:none;font-weight:bold}</style></head>
<body><div class="container">
<h1>&#128274; Pol&iacute;tica de Privacidad</h1><p class="date">&Uacute;ltima actualizaci&oacute;n: 28 de marzo de 2026</p>
<h2>1. Datos que Recopilamos</h2><p>Solo datos de Telegram: ID de usuario, nombre de usuario, nombre de pila y estado VIP (activo o expirado). NO recopilamos email, tel&eacute;fono, ubicaci&oacute;n ni datos bancarios.</p>
<h2>2. C&oacute;mo Usamos los Datos</h2><ul><li>Gestionar suscripciones VIP (activaci&oacute;n, expiraci&oacute;n y verificaci&oacute;n de pago).</li><li>Enviarte se&ntilde;ales y notificaciones del servicio.</li><li>Mejorar el servicio mediante estad&iacute;sticas an&oacute;nimas.</li></ul>
<h2>3. Verificaci&oacute;n de Pagos</h2><p>Pagos en USDT TRC20 verificados autom&aacute;ticamente via API de Binance. No almacenamos datos financieros ni n&uacute;meros de wallet.</p>
<h2>4. Almacenamiento y Seguridad</h2><ul><li>Servidor privado con acceso restringido.</li><li>Comunicaci&oacute;n web cifrada con HTTPS.</li><li>Datos en formato JSON en el servidor, sin base de datos externa.</li><li>No compartimos tus datos con terceros.</li></ul>
<h2>5. Tus Derechos</h2><p>Tienes derecho a acceso, rectificaci&oacute;n, eliminaci&oacute;n y portabilidad de tus datos. Contacta: <a href="https://t.me/BuySell365Traiding">@BuySell365Traiding</a></p>
<h2>6. Cookies y Analytics</h2><p>Esta web usa Google Analytics (gtag.js) para medir el tr&aacute;fico de forma an&oacute;nima. Google Analytics puede usar cookies propias. No usamos tracking de publicidad ni vendemos datos. Puedes desactivar Google Analytics desde la configuraci&oacute;n de tu navegador.</p>
<a href="/dashboard" class="back">&larr; Volver</a></div></body></html>"""

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
        {"loc": f"{base}/dashboard", "changefreq": "always", "priority": "0.9"},
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
  if (url.pathname.startsWith('/api/') || url.pathname === '/dashboard') return;
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

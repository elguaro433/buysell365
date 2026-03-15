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
from flask import Flask, jsonify, request, redirect, send_from_directory, send_file

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

# Bot state — pushed by the bot every ~30 seconds
_store = {
    "operaciones_activas": {},
    "historial_operaciones": [],
    "estadisticas_diarias": {"ganadas": 0, "perdidas": 0, "pips_ganados": 0.0, "pips_perdidos": 0.0, "senales_hoy": 0},
    "winning_trades": [],
    "bot_active": False,
    "auto_trading": True,
    "ultimo_sync": 0,
    "assets_count": 6,
    "capital_usuario": 640.0,
    "mt5_status": "DESCONECTADO",
    "active_ops_detail": [],  # Pre-computed by bot for /api/active_ops
}

# Pending webhooks queue for the bot to pick up
_pending_signals = []
_signals_lock = threading.Lock()

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
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    if request.is_secure or request.headers.get("X-Forwarded-Proto") == "https":
        response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    return response

@app.before_request
def _enforce_rate_limit():
    path = request.path
    if path.startswith('/logs') or path.startswith('/api/'):
        ip = request.headers.get("X-Forwarded-For", request.remote_addr) or "unknown"
        if path.startswith('/logs'):
            if _check_rate_limit(f"logs_{ip}", max_req=10, window=60):
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
            for key in ("operaciones_activas", "historial_operaciones", "estadisticas_diarias",
                        "winning_trades", "bot_active", "auto_trading", "assets_count",
                        "capital_usuario", "mt5_status", "active_ops_detail"):
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
    static_dir = os.path.join(BASE_DIR, "static")
    try:
        return send_from_directory(static_dir, "manifest.json", mimetype="application/json")
    except Exception:
        return "Not found", 404

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

@app.route("/api/winning_trades")
def api_winning_trades():
    try:
        with _lock:
            trades = _store.get("winning_trades", [])
        return app.response_class(response=json.dumps(trades, ensure_ascii=False), status=200, mimetype="application/json")
    except Exception:
        return "[]", 200, {"Content-Type": "application/json"}

@app.route("/api/active_ops")
def api_active_ops():
    if not _check_api_auth():
        return jsonify({"error": "unauthorized"}), 401
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

# ============================================================
#  LANDING PAGE
# ============================================================
@app.route("/", methods=["GET", "POST"])
def index_web():
    if request.method == "POST":
        return route_tv_signal()

    with _lock:
        hist = _store.get("historial_operaciones", [])
    wins = sum(1 for h in hist if h.get('pips', 0) > 0)
    total = len(hist)
    wr = round(wins / total * 100, 1) if total > 0 else 78.5
    pips = round(sum(h.get('pips', 0) for h in hist), 1)
    n_ops = sum(1 for op in _store.get("operaciones_activas", {}).values() if isinstance(op, dict) and op.get('mt5_ejecutado', False))
    activos = _store.get("assets_count", 6)
    is_alive = (time.time() - _store.get("ultimo_sync", 0)) < 120

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>BuySell365 Pro — Se\u00f1ales de Trading con Inteligencia Artificial</title>
<meta name="description" content="Se\u00f1ales de trading automatizadas con IA para Oro, Forex e \u00cdndices.">
<meta property="og:title" content="BuySell365 Pro — Trading con IA">
<meta property="og:description" content="Se\u00f1ales profesionales de trading con IA. Oro, EUR/USD, USD/JPY, GBP/JPY, NASDAQ, S&P 500.">
<meta property="og:image" content="/img/og_image.png">
<meta property="og:type" content="website">
<meta name="twitter:card" content="summary_large_image">
<link rel="icon" href="/img/bull_bear.png" type="image/png">
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&display=swap');
*{{margin:0;padding:0;box-sizing:border-box}}
:root{{--bg:#0a0e17;--bg2:#111827;--bg3:#1a2332;--green:#00d4aa;--green2:#00f5c4;--blue:#3b82f6;--purple:#8b5cf6;--gold:#f59e0b;--red:#ef4444;--text:#e2e8f0;--text2:#94a3b8;--glass:rgba(255,255,255,0.03);--border:rgba(255,255,255,0.06)}}
html{{scroll-behavior:smooth}}
body{{font-family:'Inter',sans-serif;background:var(--bg);color:var(--text);overflow-x:hidden}}
.hero{{min-height:100vh;display:flex;align-items:center;justify-content:center;position:relative;overflow:hidden;padding:20px}}
.hero::before{{content:'';position:absolute;top:-50%;left:-50%;width:200%;height:200%;background:radial-gradient(circle at 30% 40%,rgba(0,212,170,0.08) 0%,transparent 50%),radial-gradient(circle at 70% 60%,rgba(59,130,246,0.06) 0%,transparent 50%);animation:heroGlow 20s ease infinite alternate}}
@keyframes heroGlow{{0%{{transform:rotate(0deg)}}100%{{transform:rotate(15deg)}}}}
.hero-content{{text-align:center;max-width:900px;z-index:2;position:relative}}
.hero-badge{{display:inline-flex;align-items:center;gap:8px;background:rgba(0,212,170,0.1);border:1px solid rgba(0,212,170,0.2);border-radius:50px;padding:6px 18px;font-size:13px;color:var(--green);margin-bottom:24px;font-weight:500}}
.hero-badge .dot{{width:8px;height:8px;background:var(--green);border-radius:50%;animation:pulse 2s infinite}}
@keyframes pulse{{0%,100%{{opacity:1}}50%{{opacity:0.3}}}}
.hero h1{{font-size:clamp(2.5rem,6vw,4.5rem);font-weight:900;line-height:1.1;margin-bottom:20px;background:linear-gradient(135deg,#fff 0%,var(--green) 50%,var(--blue) 100%);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text}}
.hero p{{font-size:clamp(1rem,2vw,1.25rem);color:var(--text2);max-width:650px;margin:0 auto 40px;line-height:1.7}}
.hero-buttons{{display:flex;gap:16px;justify-content:center;flex-wrap:wrap}}
.btn{{display:inline-flex;align-items:center;gap:8px;padding:14px 32px;border-radius:12px;font-size:15px;font-weight:600;text-decoration:none;transition:all 0.3s ease;cursor:pointer;border:none}}
.btn-primary{{background:linear-gradient(135deg,var(--green),#00b894);color:#0a0e17;box-shadow:0 4px 20px rgba(0,212,170,0.3)}}
.btn-primary:hover{{transform:translateY(-2px);box-shadow:0 8px 30px rgba(0,212,170,0.4)}}
.btn-secondary{{background:var(--glass);color:var(--text);border:1px solid var(--border)}}
.btn-secondary:hover{{background:rgba(255,255,255,0.08);transform:translateY(-2px)}}
.stats-bar{{display:flex;justify-content:center;gap:40px;margin-top:60px;flex-wrap:wrap}}
.stat-item{{text-align:center}}
.stat-value{{font-size:2rem;font-weight:800;color:var(--green)}}
.stat-value.blue{{color:var(--blue)}}
.stat-value.gold{{color:var(--gold)}}
.stat-value.purple{{color:var(--purple)}}
.stat-label{{font-size:12px;color:var(--text2);text-transform:uppercase;letter-spacing:1px;margin-top:4px}}
section{{padding:50px 20px}}
.section-title{{text-align:center;margin-bottom:30px}}
.section-title h2{{font-size:clamp(1.8rem,4vw,2.8rem);font-weight:800;margin-bottom:12px}}
.section-title p{{color:var(--text2);font-size:1.05rem;max-width:600px;margin:0 auto}}
.features{{background:var(--bg2)}}
.features-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;max-width:1000px;margin:0 auto}}
.feature-card{{background:var(--bg3);border:1px solid var(--border);border-radius:14px;padding:20px;transition:all 0.3s ease}}
.feature-card:hover{{transform:translateY(-2px);border-color:rgba(0,212,170,0.2)}}
.feature-icon{{width:48px;height:48px;border-radius:12px;display:flex;align-items:center;justify-content:center;font-size:24px;margin-bottom:16px}}
.feature-icon.green{{background:rgba(0,212,170,0.1)}}
.feature-icon.blue{{background:rgba(59,130,246,0.1)}}
.feature-icon.purple{{background:rgba(139,92,246,0.1)}}
.feature-icon.gold{{background:rgba(245,158,11,0.1)}}
.feature-card h3{{font-size:1.15rem;font-weight:700;margin-bottom:8px}}
.feature-card p{{color:var(--text2);font-size:0.9rem;line-height:1.6}}
.assets-grid{{display:flex;flex-wrap:wrap;justify-content:center;gap:16px;max-width:960px;margin:0 auto}}
.asset-card{{background:var(--bg3);border:1px solid var(--border);border-radius:14px;padding:24px 16px;text-align:center;transition:all 0.3s ease;width:160px}}
.asset-card:hover{{transform:scale(1.05);border-color:var(--green)}}
.asset-emoji{{font-size:2.5rem;margin-bottom:8px}}
.cta-section{{text-align:center;padding:60px 20px}}
.cta-btn-big{{display:inline-flex;align-items:center;gap:10px;padding:18px 48px;background:linear-gradient(135deg,var(--green),#00b894);color:#0a0e17;font-weight:800;font-size:18px;border-radius:16px;text-decoration:none;box-shadow:0 8px 30px rgba(0,212,170,0.3)}}
.cta-btn-big:hover{{transform:translateY(-3px);box-shadow:0 12px 40px rgba(0,212,170,0.4)}}
.footer-main{{text-align:center;padding:30px 20px;border-top:1px solid var(--border);font-size:12px;color:var(--text2)}}
.footer-main a{{color:var(--green);text-decoration:none}}
@media(max-width:768px){{.features-grid{{grid-template-columns:1fr}}.stats-bar{{gap:20px}}.hero h1{{font-size:2rem}}}}
</style>
</head>
<body>
<section class="hero">
<div class="hero-content">
    <div class="hero-badge"><span class="dot"></span> {'Bot Activo' if is_alive else 'Dashboard Online'}</div>
    <h1>Trading Inteligente con IA Avanzada</h1>
    <p>Se\u00f1ales profesionales de trading generadas por Inteligencia Artificial. An\u00e1lisis en tiempo real de Oro, Forex e \u00cdndices con datos institucionales.</p>
    <div class="hero-buttons">
        <a href="https://t.me/BUYSELL_365_24_7" target="_blank" class="btn btn-primary">Unirse al Canal</a>
        <a href="/dashboard" class="btn btn-secondary">Ver Dashboard</a>
    </div>
    <div class="stats-bar">
        <div class="stat-item"><div class="stat-value">{wr}%</div><div class="stat-label">Win Rate</div></div>
        <div class="stat-item"><div class="stat-value blue">{total}</div><div class="stat-label">Se\u00f1ales</div></div>
        <div class="stat-item"><div class="stat-value gold">{pips:+.0f}</div><div class="stat-label">Pips Netos</div></div>
        <div class="stat-item"><div class="stat-value purple">{activos}</div><div class="stat-label">Activos</div></div>
    </div>
</div>
</section>

<section class="features">
<div class="section-title"><h2>Tecnolog\u00eda de Trading Profesional</h2><p>Combinamos m\u00faltiples fuentes de datos para generar se\u00f1ales de alta precisi\u00f3n</p></div>
<div class="features-grid">
    <div class="feature-card"><div class="feature-icon green">&#129302;</div><h3>IA Avanzada</h3><p>An\u00e1lisis con FinBERT, COT Reports y m\u00e1s de 15 indicadores t\u00e9cnicos combinados.</p></div>
    <div class="feature-card"><div class="feature-icon blue">&#128202;</div><h3>6 Activos Premium</h3><p>Oro, EUR/USD, USD/JPY, GBP/JPY, NASDAQ y S&P 500 analizados 24/5.</p></div>
    <div class="feature-card"><div class="feature-icon purple">&#9889;</div><h3>Ejecuci\u00f3n Autom\u00e1tica</h3><p>Copy Trading directo en MT5 con SL y TP autom\u00e1ticos. Sin intervenci\u00f3n manual.</p></div>
    <div class="feature-card"><div class="feature-icon gold">&#128176;</div><h3>Gesti\u00f3n de Riesgo</h3><p>Control de capital profesional: 2.5% por trade, kill switch diario, breakeven autom\u00e1tico.</p></div>
    <div class="feature-card"><div class="feature-icon green">&#128640;</div><h3>Datos Institucionales</h3><p>COT Reports semanales del CFTC integrados para detectar posicionamiento de grandes fondos.</p></div>
    <div class="feature-card"><div class="feature-icon blue">&#128274;</div><h3>Seguridad Total</h3><p>HTTPS, rate limiting, autenticaci\u00f3n por API key y protecci\u00f3n contra ataques.</p></div>
</div>
</section>

<section class="cta-section">
    <h2 style="font-size:2rem;font-weight:800;margin-bottom:16px">Empieza a Operar con IA</h2>
    <p style="color:var(--text2);margin-bottom:30px;max-width:500px;margin-left:auto;margin-right:auto">\u00danete al canal gratuito de Telegram y recibe se\u00f1ales en tiempo real.</p>
    <a href="https://t.me/BUYSELL_365_24_7" target="_blank" class="cta-btn-big">&#128172; Unirse Gratis</a>
</section>

<footer class="footer-main">
    <p>&copy; 2026 BuySell365 Pro | By Emmanuel Diaz | <a href="/terminos">T\u00e9rminos</a> | <a href="/privacidad">Privacidad</a></p>
    <p style="margin-top:8px;font-size:11px;color:#666">Herramienta informativa/educativa. No es asesor\u00eda financiera. Opera bajo tu propio riesgo.</p>
</footer>
</body>
</html>"""

# ============================================================
#  DASHBOARD
# ============================================================
@app.route("/dashboard", methods=["GET"])
def dashboard_visual():
    """Dashboard served from synced data."""
    # Read the dashboard template
    tpl_path = os.path.join(BASE_DIR, "templates", "dashboard.html")
    if os.path.exists(tpl_path):
        with open(tpl_path, "r", encoding="utf-8") as f:
            return f.read()

    # Fallback: compute from synced data
    with _lock:
        hist = list(_store.get("historial_operaciones", []))
        ops = dict(_store.get("operaciones_activas", {}))

    wins = sum(1 for h in hist if h.get('pips', 0) > 0)
    losses_count = sum(1 for h in hist if h.get('pips', 0) <= 0)
    total = wins + losses_count
    winrate = round(wins / total * 100, 1) if total > 0 else 0
    pips = round(sum(h.get('pips', 0) for h in hist), 1)
    avg_win = round(sum(h.get('pips', 0) for h in hist if h.get('pips', 0) > 0) / max(wins, 1), 1)
    avg_loss = round(sum(abs(h.get('pips', 0)) for h in hist if h.get('pips', 0) <= 0) / max(losses_count, 1), 1)
    rr = round(avg_win / avg_loss, 2) if avg_loss > 0 else 0
    n_activas = len(ops)
    now = _ahora()
    now_str = now.strftime("%H:%M:%S CET")
    hoy_str = now.strftime("%Y-%m-%d")
    senales_hoy = sum(1 for h in hist if h.get('fecha', '').startswith(hoy_str))
    pips_color = "#00e676" if pips >= 0 else "#ff3b30"
    is_alive = (time.time() - _store.get("ultimo_sync", 0)) < 120

    # Last 20 bars
    last_20 = hist[-20:] if hist else []
    max_pips = max((abs(h.get('pips', 0)) for h in last_20), default=1) or 1
    bars_html = ""
    for h in last_20:
        p = h.get('pips', 0)
        pct = min(abs(p) / max_pips * 100, 100)
        color = "#00e676" if p > 0 else "#ff3b30"
        bars_html += f'<div style="flex:1;min-width:6px;height:{max(pct,10):.0f}%;background:{color};border-radius:3px 3px 0 0;opacity:.85"></div>'
    if not bars_html:
        bars_html = '<div style="color:#5a6a7a;text-align:center;width:100%;padding:30px 0;font-size:13px">Primeras se&ntilde;ales en camino...</div>'

    # Streak
    racha = 0
    racha_tipo = ""
    for h in reversed(hist):
        is_w = h.get('pips', 0) > 0
        if racha == 0:
            racha = 1
            racha_tipo = "W" if is_w else "L"
        elif (is_w and racha_tipo == "W") or (not is_w and racha_tipo == "L"):
            racha += 1
        else:
            break
    racha_txt = f"{racha}{racha_tipo}" if racha > 0 else "--"
    racha_color = "#00e676" if racha_tipo == "W" else "#ff3b30" if racha_tipo == "L" else "#5a6a7a"

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>BuySell365 Pro | Rendimiento en Vivo</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800;900&display=swap" rel="stylesheet">
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
:root{{--bg:#080b0f;--panel:#111820;--panel2:#192230;--border:#1e2a3a;--primary:#00d4aa;--primary-dim:rgba(0,212,170,.12);--gold:#f0b90b;--buy:#00e676;--sell:#ff3b30;--text:#e2e8f0;--muted:#5a6a7a;--font:'Inter',system-ui,sans-serif}}
body{{background:var(--bg);color:var(--text);font-family:var(--font);min-height:100vh;overflow-x:hidden}}
.wrap{{max-width:1280px;margin:0 auto;padding:20px;position:relative;z-index:1}}
.hdr{{display:flex;justify-content:space-between;align-items:center;padding:18px 0 22px;border-bottom:1px solid var(--border);margin-bottom:28px}}
.hdr-left{{display:flex;align-items:center;gap:14px}}
.hdr-logo{{width:72px;height:72px;border-radius:14px;object-fit:cover;border:1px solid rgba(0,212,170,.2)}}
.brand{{font-size:24px;font-weight:800;color:#fff}}
.brand small{{display:block;font-size:11px;font-weight:500;color:var(--muted);margin-top:2px}}
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
.card{{background:var(--panel);border:1px solid var(--border);border-radius:16px;padding:22px;margin-bottom:24px}}
.card-title{{font-size:14px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.8px;margin-bottom:16px}}
.chart-area{{display:flex;align-items:flex-end;gap:4px;height:100px;padding:10px 0}}
.promo{{background:linear-gradient(135deg,#0d1a2a,#1a0d2e,#0a1520);border:1px solid rgba(168,85,247,.2);border-radius:18px;padding:32px;text-align:center;margin-bottom:24px}}
.cta-btn{{display:inline-flex;align-items:center;gap:8px;padding:12px 24px;background:linear-gradient(135deg,var(--primary),#00a080);color:#000;font-weight:800;font-size:15px;border-radius:12px;text-decoration:none;box-shadow:0 4px 24px rgba(0,212,170,.3)}}
.cta-btn:hover{{transform:translateY(-2px)}}
.footer{{text-align:center;padding:24px 0 12px;margin-top:20px;border-top:1px solid var(--border)}}
.footer p{{font-size:11px;color:var(--muted)}}
.footer a{{color:var(--primary);text-decoration:none}}
@media(max-width:768px){{.stats-row{{grid-template-columns:repeat(2,1fr)}}.hdr{{flex-direction:column;gap:12px;text-align:center}}}}
@media(max-width:480px){{.wrap{{padding:12px}}.stats-row{{grid-template-columns:1fr 1fr}}.stat-value{{font-size:24px}}}}
</style>
<script>setTimeout(()=>location.reload(),30000);</script>
</head>
<body>
<div class="wrap">
    <div class="hdr">
        <div class="hdr-left">
            <a href="/" style="display:flex;align-items:center;text-decoration:none;color:inherit;gap:14px">
            <img src="/img/bull_bear.png" alt="BuySell365" class="hdr-logo">
            <div class="brand">BuySell365 <span style="color:var(--primary)">Pro</span><small>TRADING CON IA</small></div>
            </a>
        </div>
        <div class="live-badge"><div class="pulse"></div> {'EN VIVO' if is_alive else 'OFFLINE'} &mdash; {now_str}</div>
    </div>

    <div id="active-alerts-container" style="margin-bottom:24px"></div>

    <div class="card">
        <div class="card-title">&#127942; Historial de Operaciones</div>
        <div id="winning-trades-container"><p style="color:var(--muted);text-align:center;padding:20px">Cargando...</p></div>
    </div>

    <div class="stats-row">
        <div class="stat-card accent-green"><div class="stat-label">Se\u00f1ales Totales</div><div class="stat-value" style="color:var(--primary)">{total}</div><div class="stat-sub">{senales_hoy} hoy &mdash; {wins}W / {losses_count}L</div></div>
        <div class="stat-card accent-gold"><div class="stat-label">Tasa de Acierto</div><div class="stat-value" style="color:{'#00d4aa' if winrate >= 60 else '#f0b90b'}">{winrate}%</div><div class="stat-sub">Racha: <span style="color:{racha_color}">{racha_txt}</span></div></div>
        <div class="stat-card accent-blue"><div class="stat-label">Resultado Neto</div><div class="stat-value" style="color:{pips_color}">{pips:+.1f}</div><div class="stat-sub">Avg win: {avg_win}</div></div>
        <div class="stat-card accent-purple"><div class="stat-label">Risk : Reward</div><div class="stat-value" style="color:var(--primary)">{rr}:1</div><div class="stat-sub">Ganancia / p\u00e9rdida</div></div>
    </div>

    <div class="card">
        <div class="card-title">&#128200; \u00daltimas 20 Se\u00f1ales</div>
        <div class="chart-area">{bars_html}</div>
    </div>

    <div class="promo">
        <h2 style="font-size:22px;font-weight:800;margin-bottom:12px">&#128640; \u00danete a BuySell365 Pro</h2>
        <p style="color:var(--muted);font-size:14px;margin-bottom:20px">Se\u00f1ales de IA + Copy Trading autom\u00e1tico en MT5</p>
        <div style="display:flex;justify-content:center;gap:14px;flex-wrap:wrap">
            <a href="https://t.me/BUYSELL_365_24_7" target="_blank" class="cta-btn">&#128172; TELEGRAM GRATIS</a>
            <a href="https://social.tp-redirect.com/s/WRE0V7jm" target="_blank" class="cta-btn" style="background:linear-gradient(135deg,#a855f7,#6366f1)">&#128640; COPY TRADING</a>
        </div>
    </div>

    <div class="footer">
        <p>&copy; 2026 BuySell365 Pro | <a href="/terminos">T\u00e9rminos</a> | <a href="/privacidad">Privacidad</a></p>
        <p style="margin-top:6px;font-size:10px">Herramienta informativa. No es asesor\u00eda financiera.</p>
    </div>
</div>

<script>
// Fetch active ops and winning trades
(function(){{
  fetch('/api/active_ops').then(r=>r.json()).then(ops=>{{
    if(!ops||!ops.length)return;
    let h='<div style="background:linear-gradient(135deg,rgba(0,212,170,.08),rgba(0,212,170,.02));border:1px solid rgba(0,212,170,.25);border-radius:14px;padding:16px 20px">';
    h+='<div style="display:flex;align-items:center;gap:10px;margin-bottom:12px;font-size:14px;font-weight:700;color:#00d4aa"><div class="pulse"></div>'+ops.length+' Operaci\u00f3n(es) Activa(s)</div>';
    ops.forEach(op=>{{
      const color=op.tipo.toUpperCase().includes('COMPRA')||op.tipo.toUpperCase().includes('BUY')?'#00e676':'#ff3b30';
      h+='<div style="display:flex;align-items:center;gap:16px;padding:10px 0;border-bottom:1px solid rgba(30,42,58,.3);flex-wrap:wrap">';
      h+='<span style="font-weight:700;min-width:120px">'+op.nombre+'</span>';
      h+='<span style="padding:2px 10px;border-radius:6px;font-size:12px;font-weight:700;background:rgba('+( op.tipo.includes('COMPRA')?'0,230,118':'255,59,48' )+',0.15);color:'+color+'">'+op.tipo+'</span>';
      h+='<div style="flex:1;min-width:200px;display:flex;align-items:center;gap:10px"><div style="flex:1;height:8px;background:#192230;border-radius:4px;overflow:hidden"><div style="height:100%;width:'+op.progreso+'%;background:linear-gradient(90deg,#00d4aa,#00e676);border-radius:4px"></div></div><span style="font-size:13px;font-weight:700;color:#00d4aa">'+op.progreso.toFixed(1)+'%</span></div>';
      h+='</div>';
    }});
    h+='</div>';
    document.getElementById('active-alerts-container').innerHTML=h;
  }}).catch(()=>{{}});

  fetch('/api/winning_trades').then(r=>r.json()).then(trades=>{{
    if(!trades||!trades.length){{document.getElementById('winning-trades-container').innerHTML='<p style="color:#5a6a7a;text-align:center;padding:20px">Sin operaciones registradas a\u00fan.</p>';return}}
    let rows='';
    trades.slice(-30).reverse().forEach(t=>{{
      const color=t.pips>0?'#00e676':'#ff3b30';
      const icon=t.pips>0?'&#9989;':'&#10060;';
      rows+=`<tr><td>${{t.fecha||''}}</td><td>${{t.nombre||''}}</td><td>${{t.tipo||''}}</td><td style="color:${{color}};font-weight:700">${{t.pips>0?'+':''}}${{(t.pips||0).toFixed(1)}}</td><td>${{icon}}</td></tr>`;
    }});
    document.getElementById('winning-trades-container').innerHTML=`<table style="width:100%;border-collapse:collapse;font-size:13px"><thead><tr style="border-bottom:1px solid #1e2a3a;color:#5a6a7a;font-size:11px;text-transform:uppercase"><th style="padding:8px;text-align:left">Fecha</th><th style="text-align:left">Activo</th><th style="text-align:left">Tipo</th><th style="text-align:left">Pips</th><th></th></tr></thead><tbody>${{rows}}</tbody></table>`;
  }}).catch(()=>{{}});
}})();
</script>
</body>
</html>"""

# ============================================================
#  LEGAL PAGES
# ============================================================
@app.route("/terminos")
def pagina_terminos():
    return f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>T&eacute;rminos y Condiciones — BuySell365 Pro</title>
<style>*{{margin:0;padding:0;box-sizing:border-box}}body{{font-family:'Inter',sans-serif;background:#0d1117;color:#c9d1d9;line-height:1.7;padding:20px}}.container{{max-width:800px;margin:0 auto}}h1{{color:#f0b90b;font-size:1.8rem;margin-bottom:10px}}h2{{color:#58a6ff;font-size:1.2rem;margin-top:25px;margin-bottom:8px}}p,li{{font-size:0.95rem;margin-bottom:8px}}ul{{padding-left:20px}}.date{{color:#8b949e;font-size:0.85rem;margin-bottom:20px}}a{{color:#58a6ff}}.back{{display:inline-block;margin-top:30px;padding:10px 20px;background:#f0b90b;color:#000;border-radius:8px;text-decoration:none;font-weight:bold}}</style></head>
<body><div class="container">
<h1>&#128221; T&eacute;rminos y Condiciones</h1><p class="date">&Uacute;ltima actualizaci&oacute;n: 12 de marzo de 2026</p>
<h2>1. Descripci&oacute;n del Servicio</h2><p>BuySell365 es una herramienta automatizada de an&aacute;lisis t&eacute;cnico que genera alertas informativas sobre activos financieros mediante indicadores t&eacute;cnicos, modelos de IA y datos institucionales.</p>
<h2>2. No es Asesor&iacute;a Financiera</h2><p>BuySell365 NO proporciona asesor&iacute;a financiera. Las se&ntilde;ales son an&aacute;lisis t&eacute;cnicos automatizados con fines informativos y educativos.</p>
<h2>3. Riesgo de Inversi&oacute;n</h2><p>Operar en mercados financieros conlleva un alto riesgo de p&eacute;rdida de capital. Los resultados pasados no garantizan resultados futuros.</p>
<h2>4. Suscripci&oacute;n VIP</h2><ul><li>Periodo de prueba: 5 d&iacute;as h&aacute;biles gratuitos.</li><li>Precio: {VIP_PRECIO_EUR} {VIP_MONEDA}/mes (USDT TRC20).</li><li>Renovaci&oacute;n manual. Sin cobros autom&aacute;ticos.</li><li>No se ofrecen reembolsos una vez procesado el pago.</li></ul>
<h2>5. Uso Aceptable</h2><ul><li>No redistribuir las se&ntilde;ales VIP.</li><li>No usar bots para extraer contenido.</li><li>Respetar a los miembros de la comunidad.</li></ul>
<h2>6. Limitaci&oacute;n de Responsabilidad</h2><p>BuySell365 no ser&aacute; responsable de p&eacute;rdidas financieras derivadas del uso de las se&ntilde;ales.</p>
<h2>7. Contacto</h2><p><a href="https://t.me/BuySell365Traiding">@BuySell365Traiding</a> en Telegram.</p>
<a href="/dashboard" class="back">&larr; Volver</a></div></body></html>"""

@app.route("/privacidad")
def pagina_privacidad():
    return """<!DOCTYPE html>
<html lang="es"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Pol&iacute;tica de Privacidad — BuySell365 Pro</title>
<style>*{margin:0;padding:0;box-sizing:border-box}body{font-family:'Inter',sans-serif;background:#0d1117;color:#c9d1d9;line-height:1.7;padding:20px}.container{max-width:800px;margin:0 auto}h1{color:#f0b90b;font-size:1.8rem;margin-bottom:10px}h2{color:#58a6ff;font-size:1.2rem;margin-top:25px;margin-bottom:8px}p,li{font-size:0.95rem;margin-bottom:8px}ul{padding-left:20px}.date{color:#8b949e;font-size:0.85rem;margin-bottom:20px}a{color:#58a6ff}.back{display:inline-block;margin-top:30px;padding:10px 20px;background:#f0b90b;color:#000;border-radius:8px;text-decoration:none;font-weight:bold}</style></head>
<body><div class="container">
<h1>&#128274; Pol&iacute;tica de Privacidad</h1><p class="date">&Uacute;ltima actualizaci&oacute;n: 12 de marzo de 2026</p>
<h2>1. Datos que Recopilamos</h2><p>Solo datos de Telegram: ID, nombre de usuario, nombre y estado VIP. NO recopilamos email, tel&eacute;fono ni datos bancarios.</p>
<h2>2. C&oacute;mo Usamos los Datos</h2><ul><li>Gestionar suscripciones VIP.</li><li>Enviar se&ntilde;ales y notificaciones.</li><li>Mejorar el servicio (estad&iacute;sticas an&oacute;nimas).</li></ul>
<h2>3. Verificaci&oacute;n de Pagos</h2><p>Pagos en USDT TRC20 verificados autom&aacute;ticamente via API de Binance. No almacenamos datos de wallets.</p>
<h2>4. Seguridad</h2><ul><li>Servidor privado con acceso restringido.</li><li>Comunicaci&oacute;n cifrada con HTTPS.</li><li>No compartimos datos con terceros.</li></ul>
<h2>5. Tus Derechos</h2><p>Acceso, rectificaci&oacute;n, eliminaci&oacute;n y portabilidad. Contacta: <a href="https://t.me/BuySell365Traiding">@BuySell365Traiding</a></p>
<h2>6. Cookies</h2><p>No usamos cookies, analytics ni tracking de terceros.</p>
<a href="/dashboard" class="back">&larr; Volver</a></div></body></html>"""

@app.route("/login")
def redirect_to_home():
    return redirect("/")

# ============================================================
#  MAIN
# ============================================================
if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port, debug=False)

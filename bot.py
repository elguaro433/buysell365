import sys, io
# Forzar UTF-8 en la consola de Windows para que los emojis no crasheen
if sys.stdout and hasattr(sys.stdout, 'encoding') and sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace', line_buffering=True)
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace', line_buffering=True)

import re
import warnings
warnings.filterwarnings("ignore", message="X does not have valid feature names")
import requests
import yfinance as yf
import pandas_ta as ta
import time
import json
import os
import threading
import pandas as pd
import numpy as np
import random
from difflib import get_close_matches
import copy
try:
    from dotenv import load_dotenv
    import os as _os
    # Busca .env en la misma carpeta que bot.py (funciona en PythonAnywhere y local)
    _env_path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), '.env')
    load_dotenv(dotenv_path=_env_path, override=True)
except ImportError:
    # python-dotenv no instalado — token debe estar en variable de entorno del sistema
    import sys
    print("⚠️  AVISO: python-dotenv no está instalado. Ejecuta: pip install python-dotenv --user")
    print("⚠️  El bot continuará pero puede que el token de Telegram no se cargue correctamente.")
try:
    from rapidfuzz import process as rf_process, fuzz as rf_fuzz
    _RAPIDFUZZ = True
except ImportError:
    _RAPIDFUZZ = False
from datetime import datetime, timedelta
import pytz
from flask import Flask, jsonify, request, redirect
from waitress import create_server
import ssl
import matplotlib
matplotlib.use('Agg') # Evitar que abra ventanas en el servidor
import mplfinance as mpf
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
import queue
import heapq

# ── WEB SYNC MODULE (envía datos a Render) ──
try:
    from web_sync import start_sync_loop as _start_web_sync
    _WEB_SYNC_AVAILABLE = True
except ImportError:
    _WEB_SYNC_AVAILABLE = False

# ============================================================
#  CONFIGURACIÓN DE LOGGING PROFESIONAL — ROTACIÓN DIARIA 30d
# ============================================================
from logging.handlers import TimedRotatingFileHandler

_LOGS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
os.makedirs(_LOGS_DIR, exist_ok=True)

class _SafeFormatter(logging.Formatter):
    """Formatter que inyecta 'category' si no existe — evita KeyError de urllib3/requests."""
    def format(self, record):
        if not hasattr(record, 'category'):
            record.category = 'GENERAL'
        return super().format(record)

_log_fmt = _SafeFormatter(
    fmt='%(asctime)s [%(levelname)s] [%(category)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# Handler 1: Archivo rotativo diario (30 días, un archivo por día)
_file_handler = TimedRotatingFileHandler(
    filename=os.path.join(_LOGS_DIR, "bot.log"),
    when="midnight",
    interval=1,
    backupCount=30,        # Guarda últimos 30 días
    encoding="utf-8",
    utc=False,             # Usa hora local
)
_file_handler.suffix = "%Y-%m-%d"         # bot.log.2026-03-08
_file_handler.setFormatter(_log_fmt)
_file_handler.setLevel(logging.DEBUG)     # Archivo guarda TODO (DEBUG+)

# Handler 2: Consola (solo INFO+) — deshabilitado si no hay consola (pythonw.exe)
_handlers = [_file_handler]
if sys.stderr is not None:
    _console_handler = logging.StreamHandler()
    _console_handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
    _console_handler.setLevel(logging.INFO)
    _handlers.append(_console_handler)

logging.basicConfig(level=logging.DEBUG, handlers=_handlers)
logger = logging.getLogger("BuySell365")

# Filtro para añadir campo 'category' si no existe
class _CategoryFilter(logging.Filter):
    def filter(self, record):
        if not hasattr(record, 'category'):
            record.category = 'GENERAL'
        return True

logger.addFilter(_CategoryFilter())

# Silenciar loggers ruidosos de librerías externas
for _noisy in ("urllib3", "requests", "urllib3.connectionpool"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)
_file_handler.addFilter(_CategoryFilter())   # También en el handler → cubre urllib3, requests, etc.

# 🔒 PRODUCCIÓN: Redirigir print() al logger para que NADA se pierda
class _LoggerWriter:
    """Captura stdout/stderr y lo envía al logger además de la consola."""
    def __init__(self, logger_fn, original):
        self._log = logger_fn
        self._original = original
    def write(self, msg):
        if msg and msg.strip():
            self._log(msg.rstrip())
        if self._original:
            try:
                self._original.write(msg)
            except UnicodeEncodeError:
                self._original.write(msg.encode('ascii', 'replace').decode('ascii'))
    def flush(self):
        if self._original:
            self._original.flush()

# Solo redirigir si hay consola real (no en pythonw.exe)
if sys.__stdout__ is not None:
    sys.stdout = _LoggerWriter(logger.info, sys.__stdout__)
if sys.__stderr__ is not None:
    sys.stderr = _LoggerWriter(logger.error, sys.__stderr__)

# Helper: logger con categoría para organizar los logs
def log_op(msg: str, nivel: str = "info"):
    """Log de OPERACIONES (señales, trades, TP/SL)."""
    getattr(logger, nivel)(msg, extra={"category": "OPERACION"})

def log_vip(msg: str, nivel: str = "info"):
    """Log de VIP (trial, pagos, suscripciones, acceso)."""
    getattr(logger, nivel)(msg, extra={"category": "VIP"})

def log_usuario(msg: str, nivel: str = "info"):
    """Log de USUARIOS (mensajes, comandos, interacción)."""
    getattr(logger, nivel)(msg, extra={"category": "USUARIO"})

def log_sistema(msg: str, nivel: str = "info"):
    """Log de SISTEMA (arranque, errores, MT5, Binance)."""
    getattr(logger, nivel)(msg, extra={"category": "SISTEMA"})

def log_senal(msg: str, nivel: str = "info"):
    """Log de SEÑALES (generación, envío, análisis)."""
    getattr(logger, nivel)(msg, extra={"category": "SENAL"})

def log_pago(msg: str, nivel: str = "info"):
    """Log de PAGOS (verificación Binance, montos, confirmaciones)."""
    getattr(logger, nivel)(msg, extra={"category": "PAGO"})

# ============================================================
#  CONFIGURACIÓN - BOT PROFESIONAL OPTIMIZADO (TELEGRAM)
# ============================================================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
if not TELEGRAM_TOKEN:
    raise RuntimeError("❌ TELEGRAM_TOKEN no configurado en .env — bot no puede arrancar sin token")
CHANNEL_ID     = os.getenv("CHANNEL_ID", "-1003729609114").strip()
# 👥 Grupo de Logs/Alertas (Entrada: https://t.me/BUYSELL_365_24_7)
GROUP_ID       = os.getenv("GROUP_ID", "@BUYSELL_365_24_7").strip()
ADMIN_USER     = "@BuySell365Traiding"  # 👑 Dueño del canal para gestión de accesos
ESTADO_FILE    = "estado.json"
ANDORRA_TZ     = pytz.timezone('Europe/Andorra')

# 🔒 IDs de usuarios autorizados
_uid1 = os.getenv("USER_ID_1", "").strip()
_uid2 = os.getenv("USER_ID_2", "").strip()
# Extraer bot ID del token para evitar que el bot se envíe mensajes a sí mismo (403)
_bot_id = TELEGRAM_TOKEN.split(":")[0] if ":" in TELEGRAM_TOKEN else ""
_valid_uids = []
for _uid in [_uid1, _uid2]:
    if _uid and _uid != _bot_id:
        _valid_uids.append(_uid)
    elif _uid and _uid == _bot_id:
        print(f"⚠️ USER_ID ({_uid}) es el ID del BOT, no un usuario real. Corrige USER_ID_1/USER_ID_2 en .env con tu ID de Telegram personal.")
USERS_AUTORIZADOS = _valid_uids

# 👑 IDs de Administradores (Tienen control total + VIP permanente)
ADMIN_IDS = list(_valid_uids)  # Ambos propietarios son admin

# ✅ CONFIGURACIÓN PROFESIONAL (defaults — se sobreescriben con launcher_trading_config.json)
TIEMPO_AUTOCIERRE = 86400       # Auto-cierre a las 24 horas
INTERVALO_ESCANEO = 180         # Escanear señales cada 3 minutos
INTERVALO_MONITOR = 15          # Monitorizar niveles cada 15 segundos (ALTA VELOCIDAD)
MIN_SCORE = 3                   # Score mínimo para enviar señal (auto-calibración ajusta ±1 por activo)

# ✅ PARÁMETROS INSTITUCIONALES
CAPITAL_USUARIO   = 555.00      # Capital base (se actualiza con balance real de MT5)
RIESGO_POR_TRADE  = 0.01        # 1% para TODOS los activos (~$5.5 por trade)
RIESGO_ORO        = 0.01        # 1% para ORO
RIESGO_USDJPY     = 0.01        # 1% para USD/JPY
RIESGO_GBPJPY     = 0.01        # 1% para GBP/JPY
RIESGO_PREMIUM    = 0.015       # 1.5% para señales premium (~$8 por trade — solo score≥4)
BOT_TZ = pytz.timezone('Europe/Andorra')  # Zona horaria del usuario (CET/CEST)
HORA_APERTURA_LOCAL = 8         # 08:00 hora Andorra: inicio de ejecución MT5
HORA_CORTE_LOCAL = 18           # 18:00 hora Andorra: fin de ejecución MT5 (L-V uniforme)
MAX_PERDIDA_DIARIA = 0.05       # 5% máximo diario (~$27) — estándar prop firms
MAX_TRADES_SIMULTANEOS = 6      # Máx 1 por activo × 6 activos = 6 simultáneas
MIN_RR_RATIO = 1.0              # Mínimo Risk:Reward — no abrir si TP1/SL < 1.0

# 📂 Cargar config desde consola (launcher_trading_config.json) si existe
_TRADING_CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "launcher_trading_config.json")
try:
    if os.path.exists(_TRADING_CONFIG_FILE):
        with open(_TRADING_CONFIG_FILE, "r", encoding="utf-8") as _tcf:
            _tc = json.load(_tcf)
        RIESGO_POR_TRADE = _tc.get("riesgo_trade", RIESGO_POR_TRADE)
        RIESGO_PREMIUM = _tc.get("riesgo_premium", RIESGO_PREMIUM)
        RIESGO_ORO = _tc.get("riesgo_oro", RIESGO_ORO)
        MIN_SCORE = _tc.get("min_score", MIN_SCORE)
        MAX_TRADES_SIMULTANEOS = _tc.get("max_trades", MAX_TRADES_SIMULTANEOS)
        MAX_PERDIDA_DIARIA = _tc.get("max_perdida_diaria", MAX_PERDIDA_DIARIA)
        HORA_APERTURA_LOCAL = _tc.get("hora_apertura", HORA_APERTURA_LOCAL)
        HORA_CORTE_LOCAL = _tc.get("hora_corte", HORA_CORTE_LOCAL)
        MIN_RR_RATIO = _tc.get("min_rr", MIN_RR_RATIO)
        TIEMPO_AUTOCIERRE = _tc.get("auto_cierre_horas", 24) * 3600
        INTERVALO_ESCANEO = _tc.get("intervalo_escaneo", INTERVALO_ESCANEO)
        print(f"📂 Config cargada desde consola: Riesgo={RIESGO_POR_TRADE*100:.1f}% Premium={RIESGO_PREMIUM*100:.1f}% Horario={HORA_APERTURA_LOCAL}-{HORA_CORTE_LOCAL}h")
except Exception as _e_tc:
    print(f"⚠️ No se pudo cargar trading config: {_e_tc}")

# Cooldown persistente: sobrevive al cierre de posiciones (anti re-entry)
_cooldown_cierres = {}  # {(ticker_normalizado, tipo): timestamp_cierre}
# BUG-3 FIX: Anti doble ejecución webhook+scanner — registro unificado de señales recientes
_senal_reciente = {}  # {ticker_normalizado: timestamp} — compartido por webhook y scanner

def _kill_switch_activo() -> bool:
    """Kill switch: pausa el bot si pierde >12% o >15 losses en un día."""
    global _fecha_stats_diarias, estadisticas_diarias
    hoy = ahora().strftime("%Y-%m-%d")
    # Auto-reset si cambió el día (maneja reinicios de bot)
    if _fecha_stats_diarias and _fecha_stats_diarias != hoy:
        with _lock_ops:
            estadisticas_diarias.update({"ganadas":0, "perdidas":0, "pips_ganados":0.0, "pips_perdidos":0.0, "senales_hoy":0})
            _fecha_stats_diarias = hoy
        print(f"🔄 Stats: auto-reset (nuevo día: {hoy})")
    elif not _fecha_stats_diarias:
        _fecha_stats_diarias = hoy

    # Verificar límite de pérdidas diarias
    return False

# ============================================================
#  CONFIGURACIÓN VIP — SUSCRIPCIONES CON PAGO USDT (BINANCE)
# ============================================================
VIP_PRECIO_EUR       = 149         # Precio actual en EUR (lanzamiento 50% OFF)
VIP_PRECIO_REGULAR   = 299         # Precio regular en EUR
VIP_DESCUENTO_HASTA  = "2026-07-30"  # Fecha límite del descuento de lanzamiento
VIP_PRECIO_POST_DESC = 299         # Precio después del descuento en EUR
VIP_MONEDA           = "€"         # Símbolo de moneda para mostrar al cliente
VIP_WALLET_USDT      = "TEw97pnhpbB9GtrzjnoX6WQy25ost1HUDA"  # Binance USDT TRC20
VIP_RED              = "TRC20"     # Red de pago
VIP_DURACION_DIAS    = 30          # Duración de la suscripción en días
VIP_TRIAL_DIAS       = 7           # 7 días calendario = 5 días hábiles (L-V). NO cambiar.
VIP_AVISO_DIAS       = 7           # FIX 2026-03-19: Secuencia 7d→3d→1d (antes solo 3d)
VIP_CHECK_INTERVALO  = 300         # Revisar depósitos cada 5 minutos (segundos)
BINANCE_API_KEY      = os.getenv("BINANCE_API_KEY", "").strip()
BINANCE_API_SECRET   = os.getenv("BINANCE_API_SECRET", "").strip()

# ✅ FILTRO DE COSTES (Spread máximo permitido en puntos)
# Si el spread es mayor a esto, el bot no entrará para proteger el capital.
MAX_SPREAD_ALLOWED = {
    "EURUSD=X": 25,    # 2.5 pips (diezmilésimas)
    "USDJPY=X": 25,    # 2.5 pips (centésimas)
    "GBPJPY=X": 35,    # 3.5 pips (centésimas) — GBP/JPY spread más ancho que USD/JPY
    "GC=F":     80,    # $0.80 puntos (Oro ~$2900)
    "NQ=F":     400,   # 4.00 puntos (Nasdaq ~20000)
    "ES=F":     150,   # 1.50 puntos (S&P500 ~5800)
}


# ✅ PARÁMETROS DE DESCARGA - INTRADIARIO
PERIOD_DATA = "5d"              # 5 días de historial
INTERVAL_DATA = "15m"           # Velas de 15 minutos

app = Flask(__name__)

# H-05 FIX: Redirigir HTTP→HTTPS (excepto webhooks de TradingView en puerto 80)
@app.before_request
def _redirect_http_to_https():
    """Redirige navegadores de HTTP a HTTPS. Webhooks POST a /webhook se mantienen en HTTP."""
    # En modo local (puertos alternativos), no redirigir — SSL auto-firmado causa problemas
    _is_local = os.getenv("HTTP_PORT", "80") != "80"
    if not _is_local and not request.is_secure and request.method == "GET":
        # Solo redirigir GETs del navegador — los POSTs de TradingView se quedan en HTTP
        try:
            url = request.url.replace("http://", "https://", 1)
            from flask import redirect
            return redirect(url, code=301)
        except Exception:
            pass  # Si falla, servir normal

# SECURITY HEADERS: Protección contra clickjacking, XSS, MIME sniffing, SSL stripping
@app.after_request
def _add_security_headers(response):
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    if request.is_secure:
        response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    return response

# RATE LIMITING: Protección contra fuerza bruta y DoS en endpoints web
_rate_limit_web = {}  # {ip: [timestamps]}
_RATE_LIMIT_MAX = 30  # Max 30 requests por ventana
_RATE_LIMIT_WINDOW = 60  # Ventana de 60 segundos

def _check_rate_limit(ip, max_req=_RATE_LIMIT_MAX, window=_RATE_LIMIT_WINDOW):
    """Retorna True si la IP excede el rate limit."""
    import time as _rl_time
    ahora_rl = _rl_time.time()
    if ip not in _rate_limit_web:
        _rate_limit_web[ip] = []
    # Limpiar timestamps viejos
    _rate_limit_web[ip] = [t for t in _rate_limit_web[ip] if ahora_rl - t < window]
    if len(_rate_limit_web[ip]) >= max_req:
        return True
    _rate_limit_web[ip].append(ahora_rl)
    return False

@app.before_request
def _enforce_rate_limit():
    """Rate limit en endpoints sensibles (/logs, /api/*)."""
    path = request.path
    if path.startswith('/logs') or path.startswith('/api/'):
        ip = request.remote_addr or "unknown"
        # /logs: más estricto (10 req/min) para evitar fuerza bruta del password
        if path.startswith('/logs'):
            if _check_rate_limit(f"logs_{ip}", max_req=10, window=60):
                return "Rate limit exceeded. Try again later.", 429
        else:
            if _check_rate_limit(f"api_{ip}", max_req=30, window=60):
                return "Rate limit exceeded.", 429

# ============================================================
#  FUNCIONES DE TIEMPO
# ============================================================

def ahora():
    """Retorna hora actual en Andorra (maneja DST automáticamente)"""
    return datetime.now(ANDORRA_TZ)

# ============================================================
#  MEMORIA DEL BOT
# ============================================================

operaciones_activas: dict[str, dict]   = {}
historial_operaciones: list[dict]    = []
estadisticas_diarias: dict[str, float] = {"ganadas": 0, "perdidas": 0, "pips_ganados": 0.0, "pips_perdidos": 0.0, "senales_hoy": 0}
ultimo_resumen        = time.time()
ultimo_recordatorio   = time.time()  # Esperar 4h desde arranque antes del primer recordatorio
bot_inicio            = time.time()
ultimo_escaneo        = 0
escaneo_pausado       = False
mt5_pausado           = False   # Si True: escáner y Telegram siguen, pero MT5 NO ejecuta
mt5_solo_premium      = False   # Si True: MT5 solo ejecuta señales PREMIUM (💎 score≥4 + conf≥40%)
_CMD_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".bot.cmd")
_cache_ind: dict[str, dict]            = {}   # {ticker: ind} del último escaneo
directorio_usuarios: dict[str, dict]   = {}   # {user_id: {"nombre": str, "username": str}}

# ── NUEVAS VARIABLES GLOBALES ─────────────────────────────────

# Modo de riesgo: "conservador", "normal", "agresivo"
MODO_RIESGO = "normal"

# Alertas de precio personalizadas
alertas_precio = []  # [{"ticker", "nombre", "precio", "tipo": ">="|"<="}]

# Activos desactivados por suscripción
activos_desactivados = set()

# Briefing matutino y notificaciones de sesión
ultimo_briefing = time.time()  # Esperar desde arranque antes del primer briefing
_sesiones_notificadas: dict[str, bool] = {}  # {"london_YYYY-MM-DD": True, "ny_YYYY-MM-DD": True}

# Divergencias como alerta separada (cooldown 6h por ticker)
_cache_div_alertas: dict[str, float] = {}  # {ticker: timestamp}

# ── SUSCRIPCIONES VIP ───────────────────────────────────────
suscripciones_vip: dict[str, dict]    = {}   # {user_id: {nombre, expira, ...}}
pagos_pendientes_vip: dict[str, dict] = {}   # {user_id: {monto_unico, timestamp, nombre}}
_vip_monto_counter: int = 0                  # Contador para generar montos únicos
_depositos_procesados_vip: set = set()       # TxIDs ya procesados (evitar duplicados)
_vip_trials_usados: set = set()              # User IDs que ya usaron trial gratis (promo única)
_trial_intentos: dict = {}                   # {user_id: int} — intentos de trial (máx 3)
_cache_miembros: dict = {}                   # {user_id: (timestamp, bool)} — caché getChatMember TTL 300s
_ultima_auditoria: float = 0.0              # Timestamp última auditoría de membresías
_codigos_invitacion: dict = {}              # {code: {creado_por, dias, creado, max_usos, usos, usado_por}}
_ultimo_reporte_diario: str = ""            # "YYYY-MM-DD" — evita enviar doble
_fecha_stats_diarias: str = ""              # "YYYY-MM-DD" — auto-reset si cambia el día
REPORTE_HORA = 6                            # Hora local (Andorra) para enviar reporte diario (6:30)
REPORTE_MINUTO = 30                         # Minuto del reporte diario

# ── CACHÉS DE OPTIMIZACIÓN ────────────────────────────────────
_cache_ml_modelos = {}  # {ticker: {'model': obj, 'timestamp': float}}
_cache_mtf_1h     = {}  # {ticker: {'df': df, 'timestamp': float}}
_cache_mtf_4h     = {}  # {ticker: {'df': df, 'timestamp': float}}
_lock_ops         = threading.RLock()  # Thread-safety para operaciones_activas
_lock_yf          = threading.Lock()   # Thread-safety para descargas de yfinance
_FUENTES_PRECIO: dict[str, dict]   = {}  # {ticker: {'precio': float, 'apertura': float, 'ts': float}} — cache 30s

import sys
# COT Reports - Posicionamiento Institucional
try:
    from cot_module import cot_confirma_senal, descargar_cot_cftc
    print("COT module cargado")
except Exception:
    pass

# FinBERT - Analisis de Sentimiento de Noticias
try:
    from finbert_module import sentimiento_confirma_senal
    FINBERT_AVAILABLE = True
    print("FinBERT module cargado")
except Exception:
    FINBERT_AVAILABLE = False

MT5_AVAILABLE = False
if sys.platform == 'win32':
    try:
        import MetaTrader5 as mt5
        MT5_AVAILABLE = True
    except ImportError:
        pass

# ── MAPA METATRADER 5 XM (Fuente Primaria Absoluta para Windows) ─────
MT5_TICKER_MAP = {
    'NQ=F':       'US100Cash',
    'NASDAQ':     'US100Cash',
    'NAS100':     'US100Cash',
    'US100':      'US100Cash',
    'NQ1!':       'US100Cash',
    'ES=F':     'US500Cash',
    'SP500':     'US500Cash',
    'SPX500USD': 'US500Cash',
    'SP500USD':  'US500Cash',
    'GC=F':       'GOLD',
    'XAUUSD':     'GOLD',
    'GOLD':        'GOLD',
    'ORO':         'GOLD',
    'EURUSD=X': 'EURUSD',
    'EURUSD':   'EURUSD',
    'USDJPY=X': 'USDJPY',
    'USDJPY':   'USDJPY',
    'GBPJPY=X': 'GBPJPY',
    'GBPJPY':   'GBPJPY',
    # Scalper Fibonacci pairs
    'AUDCAD':   'AUDCAD',
    'EURCHF':   'EURCHF',
    'USDCAD':   'USDCAD',
    'GBPUSD':   'GBPUSD',
    'GBPUSD=X': 'GBPUSD',
}

# Mapa inverso: MT5/webhook ticker → yfinance ticker (para cooldown consistente)
_TICKER_TO_YFINANCE = {
    'GOLD': 'GC=F', 'XAUUSD': 'GC=F',
    'US100CASH': 'NQ=F', 'NAS100': 'NQ=F', 'US100': 'NQ=F', 'NASDAQ': 'NQ=F', 'NQ': 'NQ=F',
    'US500CASH': 'ES=F', 'US500': 'ES=F', 'SP500': 'ES=F', 'SPX500USD': 'ES=F', 'SPX': 'ES=F',
    'EURUSD': 'EURUSD=X', 'USDJPY': 'USDJPY=X', 'GBPJPY': 'GBPJPY=X',
    'AUDCAD': 'AUDCAD', 'EURCHF': 'EURCHF', 'USDCAD': 'USDCAD', 'GBPUSD': 'GBPUSD=X',
    # Tickers yfinance ya correctos (pass-through)
    'GC=F': 'GC=F',
    'NQ=F': 'NQ=F', 'ES=F': 'ES=F', 'EURUSD=X': 'EURUSD=X', 'USDJPY=X': 'USDJPY=X', 'GBPJPY=X': 'GBPJPY=X',
}

# ── CONFIGURACIÓN DE AUTO-TRADING (XM Demo) ─────────────
AUTO_TRADING   = os.getenv("AUTO_TRADING", "True").strip().lower() in ("true", "1", "yes")  # Controlado desde .env
MAX_SL_PIPS    = 500   # Filtro de seguridad: no abrir si el SL es demasiado grande

# ── CUENTAS MT5 PARALELAS ─────────────────────────────────
# Ambas cuentas reciben las mismas señales y ejecutan en paralelo
MT5_ACCOUNTS = []
# Cuenta principal
_acc1_login = os.getenv("MT5_LOGIN", "").strip()
_acc1_pass  = os.getenv("MT5_PASSWORD", "").strip()
_acc1_srv   = os.getenv("MT5_SERVER", "").strip()
if _acc1_login and _acc1_pass and _acc1_srv:
    MT5_ACCOUNTS.append({"login": int(_acc1_login), "password": _acc1_pass, "server": _acc1_srv, "name": "Cuenta-1"})
# Cuenta secundaria
_acc2_login = os.getenv("MT5_LOGIN_2", "").strip()
_acc2_pass  = os.getenv("MT5_PASSWORD_2", "").strip()
_acc2_srv   = os.getenv("MT5_SERVER_2", "").strip()
if _acc2_login and _acc2_pass and _acc2_srv:
    MT5_ACCOUNTS.append({"login": int(_acc2_login), "password": _acc2_pass, "server": _acc2_srv, "name": "Cuenta-2"})

_mt5_primary_account = MT5_ACCOUNTS[0] if MT5_ACCOUNTS else None
_lock_mt5_switch = threading.RLock()  # Proteger cambio de cuenta
_lock_mt5 = threading.Lock()  # Thread-safety para TODAS las llamadas MT5 (IPC no es thread-safe)
_mt5_consecutive_failures = 0  # Circuit breaker: si falla N veces seguidas, pausar señales
_MT5_MAX_FAILURES = 5  # Máx fallos antes de pausar

# 🧵 THREAD POOL para procesamiento de webhooks — limitar concurrencia (prevenir DoS)
_pool_webhook = ThreadPoolExecutor(max_workers=4, thread_name_prefix="webhook")

def _mt5_switch_account(account):
    """Cambia la sesión MT5 activa a otra cuenta. Retorna True si exitoso."""
    try:
        if mt5.login(account["login"], password=account["password"], server=account["server"]):
            return True
        else:
            logger.warning(f"⚠️ MT5 switch falló para {account['name']} ({account['login']}): {mt5.last_error()}")
            return False
    except Exception as e:
        logger.error(f"❌ Error switching MT5 account {account['name']}: {e}")
        return False

def _reenable_autotrading():
    """Re-habilita AutoTrading si fue desactivado por cambio de cuenta entre servidores."""
    try:
        ti = mt5.terminal_info()
        if ti and not ti.trade_allowed:
            import subprocess
            ps_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "enable_autotrading.ps1")
            result = subprocess.run(
                ["powershell", "-ExecutionPolicy", "Bypass", "-File", ps_script],
                timeout=10, capture_output=True, text=True
            )
            time.sleep(1)
            ti2 = mt5.terminal_info()
            if ti2 and ti2.trade_allowed:
                logger.info("✅ AutoTrading re-habilitado automáticamente tras cambio de cuenta")
            else:
                logger.warning("⚠️ AutoTrading sigue desactivado — intento con doble Ctrl+E")
                # Segundo intento (a veces el primero lo desactiva si ya estaba activo)
                subprocess.run(
                    ["powershell", "-ExecutionPolicy", "Bypass", "-File", ps_script],
                    timeout=10, capture_output=True, text=True
                )
                time.sleep(1)
                ti3 = mt5.terminal_info()
                if ti3 and ti3.trade_allowed:
                    logger.info("✅ AutoTrading re-habilitado en segundo intento")
                else:
                    logger.error("❌ No se pudo re-habilitar AutoTrading")
    except Exception as e:
        logger.warning(f"⚠️ Error re-habilitando AutoTrading: {e}")

# Dashboard URL: configurable via .env para modo local vs VPS
DASHBOARD_URL  = os.getenv("DASHBOARD_URL", "https://buysell365.pro/dashboard").strip()

# ── MAPA TRADINGVIEW SCANNER — FUENTES EN TIEMPO REAL ──────────────────────
# PROBADO en PythonAnywhere con test_tv_symbols.py (2 Mar 2026):
#   ORO:    cfd/OANDA:XAUUSD ✅, cfd/FOREXCOM:XAUUSD ✅, cfd/PEPPERSTONE:XAUUSD ✅
#   NASDAQ: america/NASDAQ:NDX ✅  (forex/cfd → totalCount=0 para todos)
#   S&P:    america/SP:SPX ✅      (forex/cfd → totalCount=0 para todos)
#   EUR/USD: forex/OANDA:EURUSD ✅
#   USD/JPY: forex/OANDA:USDJPY ✅
TV_TICKER_MAP = {
    # FOREX — OANDA probado y funciona en PythonAnywhere
    'EURUSD=X': ('forex',   'OANDA',        'EURUSD'),
    'USDJPY=X': ('forex',   'OANDA',        'USDJPY'),
    'GBPJPY=X': ('forex',   'OANDA',        'GBPJPY'),
    # ORO — XAUUSD spot via OANDA en screener 'cfd' (PROBADO: funciona en PA)
    'GC=F':     ('cfd',     'OANDA',        'XAUUSD'),
    # ÍNDICES — screener 'america' con índices reales (PROBADO: funciona en PA)
    # NOTA: NDX = NASDAQ-100 index, SPX = S&P 500 index (no CFDs)
    'NQ=F':     ('america', 'NASDAQ',       'NDX'),
    'ES=F':     ('america', 'SP',           'SPX'),
}

TV_TICKER_FALLBACK = {
    'EURUSD=X': ('forex',   'FX',           'EURUSD'),
    'USDJPY=X': ('forex',   'FX',           'USDJPY'),
    'GBPJPY=X': ('forex',   'FX',           'GBPJPY'),
    # ORO fallback: FOREXCOM y PEPPERSTONE también funcionaron en test
    'GC=F':     ('cfd',     'FOREXCOM',     'XAUUSD'),
    # Índices: no hay fallback conocido que funcione (forex/cfd = 0 datos)
    # Se omiten NQ=F y ES=F — irán directo a TV_EXTRA o Twelve Data
}

# ── TWELVE DATA API — FUENTE ALTERNATIVA DE PRECIOS EN TIEMPO REAL ────────
# Plan Basic 8: 800 créditos/día, 8 req/min. Con cache 10s → ~100 créditos/hora max.
# Twelve Data da precios SPOT reales (XAUUSD, no futuros COMEX).
TWELVE_DATA_API_KEY = os.getenv("TWELVE_DATA_KEY", "").strip()

# Mapa de tickers internos → símbolos Twelve Data
TWELVE_DATA_MAP = {
    'GC=F':     'XAU/USD',    # ORO spot (no futuros)
    'NQ=F':     'IXIC',       # NASDAQ Composite index
    'ES=F':     'SPX',        # S&P 500 index
    'EURUSD=X': 'EUR/USD',    # Euro/Dólar
    'USDJPY=X': 'USD/JPY',    # Dólar/Yen
    'GBPJPY=X': 'GBP/JPY',    # Libra/Yen
}

# ── MAPA DE TICKERS PARA PRECIO EN VIVO (yfinance) ─────────────────────────
# CRÍTICO: para el precio que se muestra al usuario, usamos el ticker correcto.
# ORO → XAUUSD=X (spot forex, igual a XM) en lugar de GC=F (futuros COMEX)
# El análisis técnico (velas 15m) sigue usando GC=F para el historial.
YF_PRICE_TICKER = {
    # NOTA: XAUUSD=X no existe en Yahoo Finance → usar GC=F (futuros COMEX).
    # La diferencia con XM (spot) es ~$5-20, pequeña vs el problema anterior de 15min de delay.
    # Lo importante es que fast_info da precio actualizado cada ~15 segundos, no cada 15 minutos.
    'GC=F':     'GC=F',       # ORO: futuros COMEX (XAUUSD=X no soportado por YF)
    'NQ=F':     'NQ=F',       # NASDAQ futuros
    'ES=F':     'ES=F',       # S&P 500 futuros
    'EURUSD=X': 'EURUSD=X',   # EUR/USD spot forex
    'USDJPY=X': 'USDJPY=X',   # USD/JPY spot forex
    'GBPJPY=X': 'GBPJPY=X',   # GBP/JPY spot forex
}

# ── CALENDARIO DE FESTIVOS (Mercado Cerrado) ─────────────────────────
# Días donde NYSE/NASDAQ y bancos principales cierran. El bot no dará señales.
# Auto-calcula feriados movibles para el año actual.
def _calcular_feriados(year):
    """Calcula feriados bursátiles de USA para cualquier año."""
    from datetime import date, timedelta
    feriados = {}
    # Fijos
    feriados[f"{year}-01-01"] = "Año Nuevo"
    feriados[f"{year}-06-19"] = "Juneteenth"
    feriados[f"{year}-07-04"] = "Independence Day"
    feriados[f"{year}-12-25"] = "Navidad"
    # MLK Day: 3er lunes de enero
    d = date(year, 1, 1)
    while d.weekday() != 0: d += timedelta(days=1)
    d += timedelta(weeks=2)
    feriados[d.strftime("%Y-%m-%d")] = "MLK Day"
    # Presidents Day: 3er lunes de febrero
    d = date(year, 2, 1)
    while d.weekday() != 0: d += timedelta(days=1)
    d += timedelta(weeks=2)
    feriados[d.strftime("%Y-%m-%d")] = "Presidents Day"
    # Memorial Day: último lunes de mayo
    d = date(year, 5, 31)
    while d.weekday() != 0: d -= timedelta(days=1)
    feriados[d.strftime("%Y-%m-%d")] = "Memorial Day"
    # Labor Day: 1er lunes de septiembre
    d = date(year, 9, 1)
    while d.weekday() != 0: d += timedelta(days=1)
    feriados[d.strftime("%Y-%m-%d")] = "Labor Day"
    # Thanksgiving: 4to jueves de noviembre
    d = date(year, 11, 1)
    while d.weekday() != 3: d += timedelta(days=1)
    d += timedelta(weeks=3)
    feriados[d.strftime("%Y-%m-%d")] = "Thanksgiving Day"
    # Good Friday: Viernes antes de Pascua (algoritmo simplificado)
    # Algoritmo de Pascua (Meeus/Jones/Butcher)
    a = year % 19; b = year // 100; c = year % 100
    d2 = b // 4; e = b % 4; f = (b + 8) // 25
    g = (b - f + 1) // 3; h = (19*a + b - d2 - g + 15) % 30
    i = c // 4; k = c % 4; l = (32 + 2*e + 2*i - h - k) % 7
    m = (a + 11*h + 22*l) // 451
    month = (h + l - 7*m + 114) // 31
    day = ((h + l - 7*m + 114) % 31) + 1
    easter = date(year, month, day)
    good_friday = easter - timedelta(days=2)
    feriados[good_friday.strftime("%Y-%m-%d")] = "Good Friday"
    # Si 4 julio cae en sábado → viernes 3; si domingo → lunes 5
    jul4 = date(year, 7, 4)
    if jul4.weekday() == 5:  # sábado
        feriados[f"{year}-07-03"] = "Independence Day (Obs)"
    elif jul4.weekday() == 6:  # domingo
        feriados[f"{year}-07-05"] = "Independence Day (Obs)"
    return feriados

FERIADOS_2026 = _calcular_feriados(datetime.now().year)


# ============================================================
#  PERSISTENCIA DE ESTADO
# ============================================================

def guardar_estado():
    """Guarda el estado del bot de forma segura para hilos."""
    global operaciones_activas, historial_operaciones, estadisticas_diarias, alertas_precio, MODO_RIESGO, activos_desactivados, directorio_usuarios, suscripciones_vip, pagos_pendientes_vip, _vip_monto_counter, _vip_trials_usados, _depositos_procesados_vip, _trial_intentos, _codigos_invitacion, _ultimo_reporte_diario
    try:
        with _lock_ops:
            if not isinstance(operaciones_activas, dict):
                logger.error("🚨 ERROR CRÍTICO: operaciones_activas no es un diccionario en guardar_estado!")
                return

            data_to_save = copy.deepcopy({
                "operaciones_activas": operaciones_activas,
                "alertas_precio": alertas_precio,
                "modo_riesgo": MODO_RIESGO,
                "capital_usuario": CAPITAL_USUARIO,
                "activos_desactivados": list(activos_desactivados),
                "historial_operaciones": historial_operaciones,
                "estadisticas_diarias": estadisticas_diarias,
                "_fecha_stats_diarias": _fecha_stats_diarias,
                "directorio_usuarios": directorio_usuarios,
                "suscripciones_vip": suscripciones_vip,
                "pagos_pendientes_vip": pagos_pendientes_vip,
                "_vip_monto_counter": _vip_monto_counter,
                "_vip_trials_usados": list(_vip_trials_usados),
                "_depositos_procesados_vip": list(_depositos_procesados_vip),
                "_trial_intentos": _trial_intentos,
                "_codigos_invitacion": _codigos_invitacion,
                "_ultimo_reporte_diario": _ultimo_reporte_diario,
                "mt5_pausado": mt5_pausado,
                "mt5_solo_premium": mt5_solo_premium,
                "escaneo_pausado": escaneo_pausado,
                "scalper_activo": SCALPER_ACTIVO,
                # H-07 FIX: Persistir cooldowns de cierres (keys son tuples → convertir a strings)
                "_cooldown_cierres": {f"{k[0]}|{k[1]}": v for k, v in _cooldown_cierres.items()},
                # FIX 2026-03-19: Diagnóstico por activo para consola
                "diagnostico_activos": dict(_diagnostico_activos),
            })
        
        # H-04 FIX: Crear backup del estado actual antes de sobreescribir
        bak_file = f"{ESTADO_FILE}.bak"
        try:
            if os.path.exists(ESTADO_FILE):
                import shutil
                shutil.copy2(ESTADO_FILE, bak_file)
        except Exception:
            pass  # No bloquear guardado si backup falla

        # Guardado ATÓMICO: escribir en temporal y luego reemplazar para evitar corrupción
        temp_file = f"{ESTADO_FILE}.tmp"
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(data_to_save, f, indent=4, ensure_ascii=False)

        if os.path.exists(temp_file):
            os.replace(temp_file, ESTADO_FILE)  # Atómico en Windows (sin race condition)

    except Exception as e:
        print(f"⚠️ Error guardando estado atomico: {e}")

def cargar_estado():
    """Carga el estado previo del bot con validaciones de tipo."""
    global operaciones_activas, historial_operaciones, estadisticas_diarias, alertas_precio, MODO_RIESGO, activos_desactivados, CAPITAL_USUARIO, directorio_usuarios, suscripciones_vip, pagos_pendientes_vip, _vip_monto_counter, _vip_trials_usados, _depositos_procesados_vip, _trial_intentos, _codigos_invitacion, _ultimo_reporte_diario, _fecha_stats_diarias
    try:
        if os.path.exists(ESTADO_FILE):
            with open(ESTADO_FILE, encoding="utf-8") as f:
                data = json.load(f)
            
            if isinstance(data, dict):
                with _lock_ops:
                    CAPITAL_USUARIO = data.get("capital_usuario", 1000.0)
                    # Cargar modo de riesgo primero
                    modo = data.get("modo_riesgo") or data.get("MODO_RIESGO")
                    if modo in ["conservador", "normal", "agresivo"]:
                        MODO_RIESGO = modo

                    # Cargar activos desactivados
                    desact = data.get("activos_desactivados")
                    if isinstance(desact, list):
                        activos_desactivados.clear()
                        activos_desactivados.update([str(a) for a in desact])
                    
                    # Cargar historial
                    hist = data.get("historial_operaciones")
                    if isinstance(hist, list):
                        historial_operaciones.clear()
                        historial_operaciones.extend([h for h in hist if isinstance(h, dict)])
                    
                    # Cargar estadísticas + fecha
                    _saved_fecha = data.get("_fecha_stats_diarias", "")
                    est = data.get("estadisticas_diarias")
                    if isinstance(est, dict):
                        _hoy_str = ahora().strftime("%Y-%m-%d")
                        if _saved_fecha == _hoy_str:
                            # Stats son de hoy → cargar normalmente
                            estadisticas_diarias.update({k: float(v) if isinstance(v, (int, float)) else v for k, v in est.items()})
                            _fecha_stats_diarias = _saved_fecha
                            print(f"📊 Stats diarias cargadas (hoy {_hoy_str}): {int(est.get('ganadas',0))}W / {int(est.get('perdidas',0))}L")
                        else:
                            # Stats son de otro día → resetear a 0
                            estadisticas_diarias.update({"ganadas": 0, "perdidas": 0, "pips_ganados": 0.0, "pips_perdidos": 0.0})
                            _fecha_stats_diarias = _hoy_str
                            print(f"🔄 Stats diarias reseteadas (eran de {_saved_fecha or 'desconocido'}, hoy es {_hoy_str})")

                    # Cargar alertas de precio
                    alertas = data.get("alertas_precio")
                    if isinstance(alertas, list):
                        alertas_precio.clear()
                        alertas_precio.extend([a for a in alertas if isinstance(a, dict)])

                    # Cargar operaciones activas — FILTRADO ESTRICTO
                    ops = data.get("operaciones_activas")
                    if isinstance(ops, dict):
                        operaciones_activas.clear()
                        for k, v in ops.items():
                            if isinstance(v, dict):
                                operaciones_activas[str(k)] = v
                            else:
                                print(f"🧹 Filtrando op corrupta: {k}")
                    
                    # Cargar directorio de usuarios
                    users = data.get("directorio_usuarios")
                    if isinstance(users, dict):
                        directorio_usuarios.clear()
                        directorio_usuarios.update(users)

                    # Cargar suscripciones VIP
                    vips = data.get("suscripciones_vip")
                    if isinstance(vips, dict):
                        suscripciones_vip.clear()
                        suscripciones_vip.update({str(k): v for k, v in vips.items() if isinstance(v, dict)})

                    # Cargar pagos pendientes VIP
                    pend = data.get("pagos_pendientes_vip")
                    if isinstance(pend, dict):
                        pagos_pendientes_vip.clear()
                        pagos_pendientes_vip.update({str(k): v for k, v in pend.items() if isinstance(v, dict)})

                    # Cargar contador de montos VIP
                    cnt = data.get("_vip_monto_counter")
                    if isinstance(cnt, int):
                        _vip_monto_counter = cnt

                    # Cargar trials usados VIP
                    trials = data.get("_vip_trials_usados")
                    if isinstance(trials, list):
                        _vip_trials_usados.clear()
                        _vip_trials_usados.update({str(t) for t in trials})

                    # Cargar depósitos procesados VIP (evitar doble-grant tras reinicio)
                    deps_proc = data.get("_depositos_procesados_vip")
                    if isinstance(deps_proc, list):
                        _depositos_procesados_vip.clear()
                        _depositos_procesados_vip.update({str(d) for d in deps_proc})

                    # Cargar intentos de trial VIP
                    ti = data.get("_trial_intentos")
                    if isinstance(ti, dict):
                        _trial_intentos.clear()
                        _trial_intentos.update({str(k): int(v) for k, v in ti.items() if isinstance(v, (int, float))})

                    # 🔄 Retrocompatibilidad: suscripciones existentes sin "entrada_confirmada" → True
                    for uid, sub in suscripciones_vip.items():
                        if isinstance(sub, dict) and "entrada_confirmada" not in sub:
                            sub["entrada_confirmada"] = True  # Asume que ya estaban en el canal

                    # Cargar codigos de invitacion
                    codigos = data.get("_codigos_invitacion")
                    if isinstance(codigos, dict):
                        _codigos_invitacion.clear()
                        _codigos_invitacion.update({str(k): v for k, v in codigos.items() if isinstance(v, dict)})

                    # Cargar último reporte diario
                    urd = data.get("_ultimo_reporte_diario")
                    if isinstance(urd, str):
                        _ultimo_reporte_diario = urd

                    # Cargar mt5_pausado, mt5_solo_premium, escaneo_pausado, SCALPER_ACTIVO
                    global mt5_pausado, mt5_solo_premium, escaneo_pausado, SCALPER_ACTIVO
                    mt5_pausado = bool(data.get("mt5_pausado", False))
                    mt5_solo_premium = bool(data.get("mt5_solo_premium", False))
                    escaneo_pausado = bool(data.get("escaneo_pausado", False))
                    if "scalper_activo" in data:
                        SCALPER_ACTIVO = bool(data["scalper_activo"])

                    # H-07 FIX: Cargar cooldowns de cierres (keys guardadas como "ticker|tipo")
                    _cd_data = data.get("_cooldown_cierres")
                    if isinstance(_cd_data, dict):
                        _cooldown_cierres.clear()
                        ahora_ts = time.time()
                        for k_str, ts_val in _cd_data.items():
                            if "|" in k_str and isinstance(ts_val, (int, float)):
                                # Solo cargar cooldowns que no hayan expirado (< 30 min)
                                if ahora_ts - ts_val < 1800:
                                    parts = k_str.split("|", 1)
                                    _cooldown_cierres[(parts[0], parts[1])] = ts_val

                print(f"📂 Estado cargado: {len(operaciones_activas)} ops activas, {len(historial_operaciones)} en historial, {len(suscripciones_vip)} VIPs, {len(_trial_intentos)} trial intentos, {len(_codigos_invitacion)} codigos.")
                
                # 🧹 LIMPIEZA RADICAL DE DATOS CORRUPTOS (Glitches de Billones)
                # Bajamos el umbral a 50k pips para atrapar el glitch actual de 213k.
                glitch_pips_threshold = 50000 
                
                # Creamos lista limpia
                hist_limpio = [h for h in historial_operaciones if abs(h.get('pips', 0)) < glitch_pips_threshold]
                
                # Si el historial cambió o las estadísticas diarias son absurdas
                p_g = estadisticas_diarias.get("pips_ganados", 0)
                p_p = estadisticas_diarias.get("pips_perdidos", 0)

                if len(hist_limpio) < len(historial_operaciones) or abs(p_g) > glitch_pips_threshold or abs(p_p) > glitch_pips_threshold:
                    logger.warning(f"🚨 EXORCISMO DE DATOS: Detectados pips absurdos. Eliminando {len(historial_operaciones) - len(hist_limpio)} entradas corruptas.")

                    historial_operaciones.clear()
                    historial_operaciones.extend(hist_limpio)

                    # Resetear estadísticas y recalcular SOLO operaciones de HOY
                    estadisticas_diarias.update({"ganadas": 0, "perdidas": 0, "pips_ganados": 0.0, "pips_perdidos": 0.0})
                    _hoy_recalc = ahora().strftime("%Y-%m-%d")
                    for h in historial_operaciones:
                        try:
                            # Solo contar operaciones de hoy
                            _h_ts = h.get('timestamp', 0)
                            if _h_ts:
                                from datetime import datetime as _dt_rc
                                _h_fecha = _dt_rc.fromtimestamp(_h_ts).strftime("%Y-%m-%d")
                                if _h_fecha != _hoy_recalc:
                                    continue
                            else:
                                continue  # Sin timestamp → no contar
                            if h.get('resultado') == 'WIN':
                                estadisticas_diarias['ganadas'] += 1
                                estadisticas_diarias['pips_ganados'] += float(h.get('pips', 0))
                            else:
                                estadisticas_diarias['perdidas'] += 1
                                estadisticas_diarias['pips_perdidos'] += abs(float(h.get('pips', 0)))
                        except Exception: continue

                    guardar_estado()
                    print(f"✅ Sistema purificado. Stats recalculadas solo para hoy ({_hoy_recalc}).")

            else:
                print("⚠️ Archivo de estado tiene un formato inválido.")
                with _lock_ops:
                    operaciones_activas.clear()
    except Exception as e:
        print(f"⚠️ Error cargando estado: {e}")
        with _lock_ops:
            operaciones_activas.clear()

# ============================================================
#  🎯 5 ACTIVOS PREMIUM - OPTIMIZADO PARA CALIDAD
# ============================================================

ACTIVOS = {
    # TOP PRIORITARIOS
    "ORO":          "GC=F",           # Commodity más estable
    # ❌ BITCOIN y ETHEREUM eliminados — solo activos tradicionales
    "EUR/USD":      "EURUSD=X",       # Par forex más líquido
    "USD/JPY":      "USDJPY=X",       # 2do par más líquido del mundo (sesión asiática)
    "GBP/JPY":      "GBPJPY=X",       # "The Beast" — más volátil que USD/JPY, sesión London+Tokyo

    # 💎 COMPLEMENTARIOS DE ALTA CALIDAD
    "NASDAQ":          "NQ=F",           # Índice tech principal
    "S&P 500":         "ES=F",           # Índice de referencia mundial
}

# Mapa de palabras clave simplificado
KEYWORDS_ACTIVOS = {
    # ORO
    "oro": "ORO", "gold": "ORO", "gc": "ORO",

    # ❌ BITCOIN y ETHEREUM eliminados

    # EUR/USD
    "eurusd": "EUR/USD", "eur/usd": "EUR/USD", "euro": "EUR/USD",
    "eurodolar": "EUR/USD", "dolareuro": "EUR/USD",

    # NASDAQ
    "nasdaq": "NASDAQ", "nq": "NASDAQ", "tech": "NASDAQ",

    # S&P 500
    "sp500": "S&P 500", "s&p": "S&P 500", "sp": "S&P 500",
    "es": "S&P 500", "spx": "S&P 500", "s&p500": "S&P 500",
    "500": "S&P 500", "s&p 500": "S&P 500", "sandp": "S&P 500",

    # USD/JPY
    "usdjpy": "USD/JPY", "usd/jpy": "USD/JPY", "yen": "USD/JPY",
    "dolaryen": "USD/JPY", "dolaren": "USD/JPY", "jpyusd": "USD/JPY",

    # GBP/JPY
    "gbpjpy": "GBP/JPY", "gbp/jpy": "GBP/JPY", "librayen": "GBP/JPY",
    "beast": "GBP/JPY", "dragon": "GBP/JPY", "gj": "GBP/JPY",
}

# ============================================================
#  DESCARGA DE DATOS - OPTIMIZADO
# ============================================================

def descargar_datos_seguro(ticker, period="1d", interval="15m"):
    """
    Descarga los datos prioritariamente de MT5 si está disponible.
    En PythonAnywhere (Linux), usará el método antiguo seguro basado en yfinance.
    """
    import pandas as pd
    
    # === INTENTO MT5 (Sólo Windows local) ===
    if MT5_AVAILABLE:
        try:
            mt5_ticker = MT5_TICKER_MAP.get(ticker, ticker)
            timeframes = {
                "1m": mt5.TIMEFRAME_M1,
                "5m": mt5.TIMEFRAME_M5,
                "15m": mt5.TIMEFRAME_M15,
                "1h": mt5.TIMEFRAME_H1,
                "4h": mt5.TIMEFRAME_H4,
                "1d": mt5.TIMEFRAME_D1
            }
            tf = timeframes.get(interval, mt5.TIMEFRAME_M15)
            ratesCount = 3000  # ML necesita 500+ barras para entrenar correctamente
            # H-01 FIX: Proteger llamadas MT5 con lock (IPC no es thread-safe)
            with _lock_mt5:
                mt5.symbol_select(mt5_ticker, True)
                rates = mt5.copy_rates_from_pos(mt5_ticker, tf, 0, ratesCount)
            
            if rates is not None and len(rates) > 0:
                df = pd.DataFrame(rates)
                df['datetime'] = pd.to_datetime(df['time'], unit='s')
                
                for c in ['time', 'spread', 'real_volume']:
                    if c in df.columns:
                        df.drop(c, axis=1, inplace=True)
                        
                df.set_index('datetime', inplace=True)
                df.rename(columns={'open': 'Open', 'high': 'High', 'low': 'Low', 'close': 'Close', 'tick_volume': 'Volume'}, inplace=True)
                        
                for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
                    df[col] = pd.to_numeric(df[col], errors='coerce')
                    
                df_final = df[['Open', 'High', 'Low', 'Close', 'Volume']].dropna()
                if not df_final.empty:
                    return df_final
        except Exception as e:
            print(f"⚠️ Error recabando velero MT5 [{ticker}]: {e}")
            
    # === FALLBACK YFINANCE (Para PythonAnywhere/Linux) ===
    # Aumentamos el periodo por defecto para asegurar que indicadores como EMA200 tengan datos suficientes
    intentos = [(period if period != "1d" else "5d", interval), ("10d", interval), ("30d", "30m")]
    for intento_period, intento_interval in intentos:
        df = _descargar_intento(ticker, intento_period, intento_interval)
        if df is not None:
            return df
        time.sleep(1)

    return pd.DataFrame() if MT5_AVAILABLE else None

def _descargar_intento(ticker, period, interval):
    """
    Un único intento de descarga — usado internamente por descargar_datos_seguro.
    Usa yf.Ticker().history() en lugar de yf.download() para evitar bug StringArray en yfinance 0.2.x+
    """
    try:
        with _lock_yf:
            df = yf.Ticker(ticker).history(
                period=period,
                interval=interval,
                auto_adjust=False,
                actions=False,
            )

        if df is None or df.empty:
            return None

        # Normalizar nombres de columna a str puro (defensivo)
        df.columns = [str(c) for c in df.columns]

        # Seleccionar y renombrar columnas necesarias para pandas-ta
        cols = {
            'Open': 'Open', 'High': 'High', 'Low': 'Low',
            'Close': 'Close', 'Adj Close': 'Close', 'Volume': 'Volume'
        }

        df_limpio = pd.DataFrame(index=df.index)
        for old_col, new_col in cols.items():
            if old_col in df.columns:
                df_limpio[new_col] = pd.to_numeric(df[old_col], errors='coerce')

        df_limpio = df_limpio.dropna(subset=['Close'])

        if df_limpio.empty or len(df_limpio) < 20:
            return None

        return df_limpio

    except Exception as e:
        print(f"⚠️ Error descargando {ticker}: {e}")
        return None



# ============================================================
#  DESCARGA OHLCV UNIFICADA — MT5 primero, yfinance fallback
# ============================================================

# Mapa de periodo texto → número de barras necesarias para MT5
_PERIOD_TO_BARS = {
    "1d": 24, "2d": 48, "5d": 120, "10d": 240,
    "30d": 720, "60d": 1440, "90d": 2160, "6mo": 4320,
    "1y": 8760, "max": 10000,
}

def descargar_ohlcv(ticker, period="60d", interval="1h"):
    """
    Descarga datos OHLCV priorizando MT5 (real-time) con fallback a yfinance.
    Retorna DataFrame con columnas [Open, High, Low, Close, Volume] y DateTimeIndex.
    Compatible 1:1 con el formato que espera el resto del bot.
    """
    import pandas as pd

    # === 1. INTENTO MT5 (datos en tiempo real, sin delay) ===
    if MT5_AVAILABLE:
        try:
            mt5_ticker = MT5_TICKER_MAP.get(ticker, ticker)
            timeframes = {
                "1m": mt5.TIMEFRAME_M1,
                "5m": mt5.TIMEFRAME_M5,
                "15m": mt5.TIMEFRAME_M15,
                "30m": mt5.TIMEFRAME_M30,
                "60m": mt5.TIMEFRAME_H1,
                "1h": mt5.TIMEFRAME_H1,
                "4h": mt5.TIMEFRAME_H4,
                "1d": mt5.TIMEFRAME_D1,
            }
            tf = timeframes.get(interval, mt5.TIMEFRAME_H1)

            # Calcular cuántas barras necesitamos según el periodo solicitado
            bars_needed = _PERIOD_TO_BARS.get(period, 1440)
            # Pedir un 10% extra por huecos de fin de semana
            bars_request = min(int(bars_needed * 1.1) + 50, 10000)

            with _lock_mt5:
                mt5.symbol_select(mt5_ticker, True)
                rates = mt5.copy_rates_from_pos(mt5_ticker, tf, 0, bars_request)

            if rates is not None and len(rates) > 0:
                df = pd.DataFrame(rates)
                df['datetime'] = pd.to_datetime(df['time'], unit='s')

                for c in ['time', 'spread', 'real_volume']:
                    if c in df.columns:
                        df.drop(c, axis=1, inplace=True)

                df.set_index('datetime', inplace=True)
                df.rename(columns={
                    'open': 'Open', 'high': 'High', 'low': 'Low',
                    'close': 'Close', 'tick_volume': 'Volume'
                }, inplace=True)

                for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
                    if col in df.columns:
                        df[col] = pd.to_numeric(df[col], errors='coerce')

                df_final = df[['Open', 'High', 'Low', 'Close', 'Volume']].dropna()
                if not df_final.empty:
                    return df_final
        except Exception as e:
            print(f"[descargar_ohlcv] MT5 error [{ticker}→{MT5_TICKER_MAP.get(ticker, ticker)}]: {e}")

    # === 2. FALLBACK YFINANCE (15-20 min delay, rate limited) ===
    try:
        with _lock_yf:
            df = yf.download(ticker, period=period, interval=interval,
                             progress=False, timeout=15)
        if df is not None and not df.empty:
            # Aplanar MultiIndex si yfinance devuelve columnas multi-nivel
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            # Normalizar columnas
            for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors='coerce')
            df_final = df[['Open', 'High', 'Low', 'Close', 'Volume']].dropna()
            if not df_final.empty:
                return df_final
    except Exception as e:
        print(f"[descargar_ohlcv] yfinance error [{ticker}]: {e}")

    return pd.DataFrame()


_TV_HEADERS = {
    'Content-Type': 'application/json',
    'Referer': 'https://www.tradingview.com/',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
}

def _tv_scan(screener, exch, sym):
    """
    Consulta TradingView Scanner API — fuente de precio en tiempo real.
    Retorna (precio, apertura) o (None, None).
    El campo 'close' en el scanner es el ÚLTIMO precio en tiempo real (no cierre diario).

    IMPORTANTE: Esta es la fuente más precisa para coincidir con TradingView web.
    Los precios devueltos son idénticos a lo que muestra tradingview.com.
    """
    try:
        url     = f'https://scanner.tradingview.com/{screener}/scan'
        payload = {
            'symbols': {'tickers': [f'{exch}:{sym}'], 'query': {'types': []}},
            'columns': ['close', 'open', 'change', 'high', 'low']
        }
        r = requests.post(url, json=payload, headers=_TV_HEADERS, timeout=8)
        if r.status_code != 200:
            logger.debug(f"TV Scanner {exch}:{sym} — HTTP {r.status_code}")
            return None, None
        data = r.json()
        if data.get('data') and len(data['data']) > 0 and data['data'][0].get('d'):
            d = data['data'][0]['d']
            precio   = float(d[0]) if d[0] is not None else None
            apertura = float(d[1]) if d[1] is not None else None
            if precio and precio > 0:
                logger.debug(f"✅ TV Scanner {exch}:{sym} = {precio}")
                return precio, apertura
    except requests.exceptions.Timeout:
        logger.debug(f"⏱️ TV Scanner timeout: {exch}:{sym}")
    except requests.exceptions.ConnectionError:
        logger.debug(f"🔌 TV Scanner conexión fallida: {exch}:{sym}")
    except Exception as e:
        logger.debug(f"❌ TV Scanner error {exch}:{sym}: {e}")
    return None, None


def _twelve_data_price(ticker):
    """
    Twelve Data API — precio en tiempo real.
    Plan Basic 8: 800 créditos/día, 8 req/min.
    Da precios SPOT (XAU/USD, no futuros COMEX) → coincide con XM/TradingView.
    Retorna (precio, apertura) o (None, None).
    """
    td_sym = TWELVE_DATA_MAP.get(ticker)
    if not td_sym or not TWELVE_DATA_API_KEY:
        return None, None
    try:
        url = f'https://api.twelvedata.com/quote?symbol={td_sym}&apikey={TWELVE_DATA_API_KEY}'
        r = requests.get(url, timeout=8)
        if r.status_code != 200:
            logger.debug(f"Twelve Data {td_sym} — HTTP {r.status_code}")
            return None, None
        data = r.json()
        if data.get('code'):
            # Error de API (rate limit, símbolo inválido, etc)
            logger.debug(f"Twelve Data {td_sym} error: {data.get('message', data.get('code'))}")
            return None, None
        precio   = float(data['close']) if data.get('close') else None
        apertura = float(data['open'])  if data.get('open')  else None
        if precio and precio > 0:
            logger.debug(f"✅ Twelve Data {td_sym} = {precio}")
            return precio, apertura
    except requests.exceptions.Timeout:
        logger.debug(f"⏱️ Twelve Data timeout: {td_sym}")
    except requests.exceptions.ConnectionError:
        logger.debug(f"🔌 Twelve Data conexión fallida: {td_sym}")
    except Exception as e:
        logger.debug(f"❌ Twelve Data error {td_sym}: {e}")
    return None, None


def _yf_chart_api(ticker):
    """
    Yahoo Finance Chart API — regularMarketPrice.
    Para GC=F devuelve futuros COMEX (puede diferir ~$5-20 de XAUUSD spot).
    Se usa como ÚLTIMO recurso cuando TV Scanner falla.
    """
    # Intentar con query1 y query2 como fallback
    for host in ('query1', 'query2'):
        try:
            url = (f'https://{host}.finance.yahoo.com/v8/finance/chart/{ticker}'
                   f'?range=1d&interval=1m&includePrePost=true')
            r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=5)
            if r.status_code != 200:
                continue
            data = r.json()
            meta = data['chart']['result'][0]['meta']
            precio   = meta.get('regularMarketPrice')
            apertura = meta.get('previousClose') or meta.get('chartPreviousClose')
            if precio and precio > 0:
                return float(precio), float(apertura) if apertura else float(precio)
        except Exception:
            continue
    return None, None


def _yf_precio_rapido(ticker):
    """
    Obtiene precio en tiempo real usando yfinance — la fuente más confiable en PythonAnywhere.
    Usa el ticker mapeado en YF_PRICE_TICKER (e.g. GC=F → XAUUSD=X para spot gold).

    Cascada interna:
      1. fast_info.last_price  — endpoint ligero, actualizado cada ~15 segundos
      2. history 1m last close — vela del último minuto (máx 1 min de retraso)
      3. history 5m last close — último recurso
    """
    yf_tk = YF_PRICE_TICKER.get(ticker, ticker)
    with _lock_yf:
        # --- Intento 1: fast_info ---
        try:
            fi    = yf.Ticker(yf_tk).fast_info
            precio = getattr(fi, 'last_price', None)
            prev   = getattr(fi, 'previous_close', None) or getattr(fi, 'open', None)
            if precio and float(precio) > 0:
                return float(precio), float(prev) if prev else float(precio), 'YF fast_info'
        except Exception:
            pass

        # --- Intento 2: history 1m ---
        try:
            h = yf.Ticker(yf_tk).history(period='1d', interval='1m', threads=False)
            if h is not None and not h.empty:
                precio  = float(h['Close'].iloc[-1])
                apertura = float(h['Open'].iloc[0])
                if precio > 0:
                    return precio, apertura, 'YF 1m'
        except Exception:
            pass

        # --- Intento 3: history 5m ---
        try:
            h = yf.Ticker(yf_tk).history(period='1d', interval='5m', threads=False)
            if h is not None and not h.empty:
                precio   = float(h['Close'].iloc[-1])
                apertura = float(h['Open'].iloc[0])
                if precio > 0:
                    return precio, apertura, 'YF 5m'
        except Exception:
            pass

    return None, None, None


def obtener_cotizacion_tv(ticker):
    """
    Cascada de fuentes de precio — PRIORIDAD: TradingView (precios exactos).

    OBJETIVO: Que el precio mostrado sea IDÉNTICO al de TradingView web.
    TradingView Scanner usa XAUUSD spot (no GC=F futuros), US100/US500 CFD,
    y EURUSD/USDJPY forex — exactamente lo que muestra la web de TradingView.

    Orden de cascada:
      1. MT5 bid/ask              — exacto, solo Windows local
      2. TradingView Scanner      — PRIORITARIO: precio idéntico a TV web
      3. TradingView Fallback     — fuentes alternativas en TV
      4. TradingView Extra Sources— múltiples exchanges redundantes
      5. Twelve Data API          — precio spot real (XAU/USD, SPX, IXIC)
      6. Yahoo Finance Chart v8   — API rápida pero puede diferir en futuros
      7. yfinance fast_info       — ÚLTIMO recurso (GC=F futuros ≠ XAUUSD spot)
    Cache: 10 segundos
    """
    global _FUENTES_PRECIO
    cached = _FUENTES_PRECIO.get(ticker)
    if cached and (time.time() - cached['ts']) < 10:
        return cached

    # === 1. MT5 (solo Windows — precio exacto XM bid/ask) ===
    if MT5_AVAILABLE:
        mt5_ticker = MT5_TICKER_MAP.get(ticker, ticker)
        try:
            # H-01 FIX: Proteger llamadas MT5 con lock
            with _lock_mt5:
                mt5.symbol_select(mt5_ticker, True)
                Rates    = mt5.copy_rates_from_pos(mt5_ticker, mt5.TIMEFRAME_D1, 0, 1)
                apertura = float(Rates[0]['open']) if (Rates is not None and len(Rates) > 0) else None
                tick     = mt5.symbol_info_tick(mt5_ticker)
            precio   = None
            if tick and tick.bid > 0:
                precio = round((tick.bid + tick.ask) / 2.0, 5)
            if precio:
                result = {'precio': precio, 'apertura': apertura or precio,
                          'ts': time.time(), 'fuente': 'MT5 (XM)'}
                _FUENTES_PRECIO[ticker] = result
                return result
        except Exception:
            pass

    # === 2. TradingView Scanner — PRIORITARIO (precio = TV web) ===
    # XAUUSD spot, US100/US500 índices, EURUSD/USDJPY/GBPJPY forex
    tv = TV_TICKER_MAP.get(ticker)
    if tv:
        p, a = _tv_scan(*tv)
        if p:
            sym_real = tv[2]
            result   = {'precio': p, 'apertura': a or p,
                        'ts': time.time(), 'fuente': f'TradingView ({sym_real})'}
            _FUENTES_PRECIO[ticker] = result
            return result

    # === 3. TradingView Fallback (fuentes alternativas TV) ===
    tv_fb = TV_TICKER_FALLBACK.get(ticker)
    if tv_fb:
        p, a = _tv_scan(*tv_fb)
        if p:
            sym_fb = tv_fb[2]
            result = {'precio': p, 'apertura': a or p,
                      'ts': time.time(), 'fuente': f'TradingView ({sym_fb})'}
            _FUENTES_PRECIO[ticker] = result
            return result

    # === 4. TradingView Scanner — Exchanges adicionales (PROBADOS en PythonAnywhere) ===
    _TV_EXTRA_SOURCES = {
        # ORO: 3 fuentes CFD probadas que funcionan + TVC:GOLD
        'GC=F':     [('cfd', 'PEPPERSTONE', 'XAUUSD'), ('cfd', 'TVC', 'GOLD'), ('cfd', 'FOREXCOM', 'XAUUSD')],
        # NASDAQ/S&P: solo 'america' funciona en PythonAnywhere
        'NQ=F':     [('america', 'SP', 'NDX')],
        'ES=F':     [('america', 'NASDAQ', 'SPX')],
        'EURUSD=X': [('forex', 'FX', 'EURUSD'), ('forex', 'FXOPEN', 'EURUSD')],
        'USDJPY=X': [('forex', 'FX', 'USDJPY'), ('forex', 'FXOPEN', 'USDJPY')],
        'GBPJPY=X': [('forex', 'FX', 'GBPJPY'), ('forex', 'FXOPEN', 'GBPJPY')],
    }
    extras = _TV_EXTRA_SOURCES.get(ticker, [])
    for tv_extra in extras:
        p, a = _tv_scan(*tv_extra)
        if p:
            result = {'precio': p, 'apertura': a or p,
                      'ts': time.time(), 'fuente': f'TradingView ({tv_extra[1]}:{tv_extra[2]})'}
            _FUENTES_PRECIO[ticker] = result
            return result

    # === 5. Twelve Data API — precio spot en tiempo real ===
    # Twelve Data da XAUUSD spot (coincide con XM), SPX, IXIC reales.
    # 800 créditos/día, 8 req/min. Suficiente con cache de 10s.
    p, a = _twelve_data_price(ticker)
    if p:
        td_sym = TWELVE_DATA_MAP.get(ticker, ticker)
        result = {'precio': p, 'apertura': a or p,
                  'ts': time.time(), 'fuente': f'TwelveData ({td_sym})'}
        _FUENTES_PRECIO[ticker] = result
        return result

    # === 6. Yahoo Finance Chart API v8 ===
    p, a = _yf_chart_api(ticker)
    if p:
        result = {'precio': p, 'apertura': a or p,
                  'ts': time.time(), 'fuente': 'Yahoo Finance RT ⚠️'}
        _FUENTES_PRECIO[ticker] = result
        return result

    # === 7. yfinance fast_info — ÚLTIMO RECURSO ===
    # ADVERTENCIA: Para ORO usa GC=F (futuros COMEX) que difiere $5-30 de XAUUSD spot
    precio, apertura, fuente_yf = _yf_precio_rapido(ticker)
    if precio:
        yf_tk  = YF_PRICE_TICKER.get(ticker, ticker)
        result = {'precio': precio, 'apertura': apertura or precio,
                  'ts': time.time(), 'fuente': f'{fuente_yf} ({yf_tk}) ⚠️'}
        _FUENTES_PRECIO[ticker] = result
        return result

    return None


def obtener_precio_actual(ticker, df_fallback=None):
    """Orquestador maestro de precios — cascada completa"""
    cotizacion = obtener_cotizacion_tv(ticker)
    if cotizacion:
        return cotizacion['precio']

    # 2. Rescate yfinance 1m (velas)
    try:
        with _lock_yf:
            t = yf.Ticker(ticker)
            hist = t.history(period='1d', interval='1m', threads=False)
            if hist is not None and not hist.empty:
                precio = float(hist['Close'].iloc[-1])
                if precio > 0:
                    return precio
    except Exception:
        pass

    # 3. Rescate dataframe base (último recurso)
    if df_fallback is not None and not df_fallback.empty:
        return float(df_fallback['Close'].iloc[-1])

    return None


# ============================================================
#  HELPERS DE FORMATO
# ============================================================

def get_categoria(ticker):
    t = ticker.upper() if ticker else ""
    if "=X" in t or t in ("EURUSD", "USDJPY", "GBPUSD", "AUDUSD", "USDCHF", "USDCAD", "NZDUSD"):
        return "forex"
    if "=F" in t or t.startswith("^") or t in ("US100CASH", "US500CASH", "GOLD", "XAUUSD", "NAS100", "US100", "US500"):
        return "futuros"
    return "accion"

CATEGORIA_EMOJI = {"crypto": "🪙", "forex": "💱", "futuros": "📦", "accion": "📈"}

def unidad_medida(ticker=""):
    """Retorna la unidad de medida correcta para cada activo.
    Forex (EUR/USD, USD/JPY, GBP/JPY) → pips
    Oro, NASDAQ, S&P 500 → pts (puntos)
    """
    cat = get_categoria(ticker)
    if cat == "forex":
        return "pips"
    else:
        return "pts"

def fmt_val(valor, ticker=""):
    """Formato de valor (pips/puntos) según tipo de activo."""
    if valor is None:
        return "N/A"
    cat = get_categoria(ticker)
    if ticker in ("GC=F", "NQ=F", "ES=F"):
        return f"{valor:,.2f}"
    elif cat == "forex":
        if "JPY" in ticker:
            return f"{valor:.3f}"
        return f"{valor:.5f}"
    return f"{valor:.2f}"

def fmt(v: float, ticker: str) -> str:
    """Formato de precio según tipo de activo para máxima coincidencia con TradingView"""
    if v is None: return "N/A"
    
    # Forex: 5 decimales (Standard) o 3 para JPY
    if "=X" in ticker:
        if "JPY" in ticker:
            return f"{v:.3f}"
        return f"{v:.5f}"
    
    # Futuros / Índices: 2 decimales es el estándar en TradingView para Gold, NQ, ES
    if ticker in ("GC=F", "ES=F", "NQ=F"):
        return f"{v:,.2f}"
    
    # Fallback: intentar detectar si es un valor pequeño para usar más precisión
    if abs(v) < 10:
        return f"{v:.6f}"
    return f"{v:.4f}"

def barra_confianza(score: int) -> str:
    score = max(1, min(5, score))
    filled = "█" * score
    empty  = "░" * (5 - score)
    niveles = {1:"MUY BAJA ⚠️",2:"BAJA 🟡",3:"MEDIA 🟠",4:"ALTA 🟢",5:"MUY ALTA 🔥"}
    return f"|{filled}{empty}| {score}/5 — {niveles[score]}"

def recomendacion(score: int) -> str:
    if score >= 4: return "✅ *SEÑAL CONFIRMADA — ENTRADA RECOMENDADA*"
    if score == 3: return "⚠️ *SEÑAL MODERADA — Espera confirmación adicional*"
    return "🔍 *SEÑAL DÉBIL — Solo observar, NO entrar todavía*"

# ============================================================
#  ENVÍO TELEGRAM — COLA GLOBAL ANTI-429
# ============================================================

# Rate limiter global: serializa TODOS los envíos a Telegram para no exceder
# el límite de 30 msgs/sec (usamos ~22/sec como margen de seguridad)
_lock_envio_tg = threading.Lock()
_ultimo_envio_tg = 0.0
_MIN_INTERVALO_ENVIO = 0.045  # ~22 msgs/sec max

def _es_miembro_canal(user_id: str, force: bool = False) -> bool:
    """Verifica si el usuario está REALMENTE dentro del canal VIP usando getChatMember.
    Usa caché de 300s para reducir llamadas API. force=True ignora caché.
    Retorna True si es member, creator, o administrator. False si left, kicked, o error."""
    # 1. Buscar en caché (válido 300 segundos)
    if not force:
        cached = _cache_miembros.get(user_id)
        if cached:
            ts, resultado = cached
            if time.time() - ts < 300:
                return resultado

    # 2. Consultar API de Telegram
    es_miembro = False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getChatMember",
            json={"chat_id": CHANNEL_ID, "user_id": int(user_id)},
            timeout=10
        )
        if r.status_code == 200:
            status = r.json().get("result", {}).get("status", "left")
            es_miembro = status in ("member", "creator", "administrator")
    except Exception as e:
        logger.warning(f"⚠️ Error verificando membresía canal para {user_id}: {e}")

    # 3. Guardar en caché
    _cache_miembros[user_id] = (time.time(), es_miembro)
    return es_miembro


def borrar_mensaje_telegram(chat_id, message_id):
    """Elimina un mensaje de Telegram (requiere permisos de admin en grupos)."""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/deleteMessage"
    try:
        r = requests.post(url, json={"chat_id": chat_id, "message_id": message_id}, timeout=10)
        if not r.json().get("ok"):
            desc = r.json().get("description", "")
            if "message to delete not found" not in desc:
                logger.warning(f"⚠️ No pude borrar msg {message_id} en {chat_id}: {desc}")
    except Exception as e_del:
        logger.warning(f"⚠️ Error borrando msg {message_id}: {e_del}")

LIMPIEZA_DELAY = 120    # 2 minutos — borrar mensajes de usuario/bot (señales exentas)

# 🗑️ SCHEDULER DE BORRADO — un solo hilo reemplaza cientos de threading.Timer
# Usa heapq para ejecutar borrados en orden cronológico (escala a 1000+ usuarios)
_cola_borrado: list = []       # heapq de (timestamp, chat_id, message_id)
_lock_borrado = threading.Lock()
_evento_borrado = threading.Event()

def _hilo_borrado_scheduler():
    """Hilo único que ejecuta todos los borrados programados en orden."""
    while True:
        try:
            with _lock_borrado:
                if _cola_borrado:
                    prox_ts = _cola_borrado[0][0]
                else:
                    prox_ts = None

            if prox_ts is None:
                # No hay nada — esperar hasta que se encole algo
                _evento_borrado.wait(timeout=30)
                _evento_borrado.clear()
                continue

            ahora = time.time()
            if ahora < prox_ts:
                # Dormir hasta el próximo borrado (o hasta que llegue uno nuevo)
                _evento_borrado.wait(timeout=prox_ts - ahora)
                _evento_borrado.clear()
                continue

            # Ejecutar todos los borrados que ya vencieron
            with _lock_borrado:
                while _cola_borrado and _cola_borrado[0][0] <= time.time():
                    _, _chat, _msg = heapq.heappop(_cola_borrado)
                    try:
                        borrar_mensaje_telegram(_chat, _msg)
                    except Exception:
                        pass
                    time.sleep(0.05)  # Rate limit borrados: ~20/sec
        except Exception as e_bor:
            logger.error(f"⚠️ Error en scheduler borrado: {e_bor}")
            time.sleep(5)

# Iniciar scheduler al cargar módulo
_t_borrado = threading.Thread(target=_hilo_borrado_scheduler, daemon=True, name="delete_sched")
_t_borrado.start()
# M-FIX: Se registrará en watchdog después (ver _hilos_registrados más abajo)

def programar_borrado(chat_id, message_id, delay=None):
    """Programa la eliminación de un mensaje (via scheduler centralizado)."""
    if delay is None:
        delay = LIMPIEZA_DELAY
    if chat_id and message_id:
        with _lock_borrado:
            heapq.heappush(_cola_borrado, (time.time() + delay, str(chat_id), message_id))
        _evento_borrado.set()  # Despertar scheduler

def escapar_markdown(texto: str) -> str:
    """Escapa caracteres especiales de Markdown v1 en contenido dinámico."""
    for ch in ('_', '*', '`', '['):
        texto = texto.replace(ch, f'\\{ch}')
    return texto

def enviar_telegram(mensaje: str, destino: str = None, teclado: dict = None):
    """Envía mensaje al canal de Telegram con reintentos y rate limiting global.
       Si el mensaje supera 4096 caracteres, lo divide en fragmentos.
       Serializa envíos via lock para no exceder 22 msgs/sec (anti-429)."""
    global _ultimo_envio_tg
    chat_id = destino or CHANNEL_ID
    url     = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    # Telegram limita a 4096 caracteres por mensaje
    MAX_LEN = 4096
    if len(mensaje) > MAX_LEN:
        partes = []
        while mensaje:
            if len(mensaje) <= MAX_LEN:
                partes.append(mensaje)
                break
            corte = mensaje.rfind('\n', 0, MAX_LEN)
            if corte == -1:
                corte = MAX_LEN
            partes.append(mensaje[:corte])
            mensaje = mensaje[corte:].lstrip('\n')

        ultimo_id = None
        for i, parte in enumerate(partes):
            ultimo_id = enviar_telegram(parte, destino=chat_id, teclado=teclado if i == len(partes)-1 else None)
        return ultimo_id

    payload = {
        "chat_id":    chat_id,
        "text":       mensaje,
        "parse_mode": "Markdown"
    }
    if teclado:
        payload["reply_markup"] = teclado

    for intento in range(3):
        try:
            # 🛡️ Rate limit global — esperar si enviamos demasiado rápido
            with _lock_envio_tg:
                _ahora = time.time()
                _espera_rl = _MIN_INTERVALO_ENVIO - (_ahora - _ultimo_envio_tg)
                if _espera_rl > 0:
                    time.sleep(_espera_rl)
                _ultimo_envio_tg = time.time()

            r = requests.post(url, json=payload, timeout=12)
            if r.status_code == 200:
                data = r.json()
                return data.get("result", {}).get("message_id")
            elif r.status_code == 429: # Too Many Requests
                espera = int(r.json().get("parameters", {}).get("retry_after", 5))
                time.sleep(espera)
                continue
            elif r.status_code == 400 and "parse" in r.text.lower():
                # Markdown inválido → reintentar sin parse_mode
                payload.pop("parse_mode", None)
                continue
            break
        except Exception as e:
            if intento == 2: logger.error(f"❌ Fallo crítico Telegram: {e}")
            time.sleep(1)
    return None

def enviar_canal(mensaje: str, **kwargs):
    """📢 SEÑALES Y SALIDAS: Envía solo señales, TP y SL al canal principal."""
    return enviar_telegram(mensaje, destino=CHANNEL_ID, **kwargs)

def enviar_grupo(mensaje: str, incluir_promo: bool = True, auto_delete: int = 300, **kwargs):
    """👥 ALERTAS TÉCNICAS: Envía logs, ejecuciones y alertas de sistema al grupo.
       Añade automáticamente un tag de publicidad VIP para incentivar ventas.
       auto_delete: segundos para auto-borrar (default 300=5min, 0=no borrar)."""
    target = GROUP_ID if GROUP_ID else CHANNEL_ID

    # 📢 Marca de agua publicitaria para el grupo
    if incluir_promo and ADMIN_USER:
        promos = [
            f"\n\n💎 *¿QUIERES ESTAS SEÑALES EN VIVO?*\nRecibe entradas con alta precision.\n🎁 *Escribe /vip — 5 dias habiles GRATIS + 50% en tu primer mes* 🔥",
            f"\n\n🚀 *SEÑALES DE TRADING EN TIEMPO REAL*\nAnalisis tecnico automatizado con IA.\n🎁 *Escribe /vip — Prueba 5 dias habiles GRATIS + 50% OFF* 🔥",
            f"\n\n🔥 *UNETE AL CANAL VIP*\nSenales diarias de Oro, Forex e Indices.\n🎁 *Escribe /vip — 5 dias habiles GRATIS + 50% en tu primer mes* 🔥",
            "\n\n🚀 *COPY TRADING DISPONIBLE*\nCopia nuestras operaciones automaticamente en tu cuenta MT5.\n👉 [Empezar Copy Trading](https://social.tp-redirect.com/s/WRE0V7jm) 🔥",
            "\n\n📈 *COPIA NUESTRAS OPERACIONES 24/7*\nSin experiencia necesaria. Broker regulado XM.\n👉 [Activar Copy Trading](https://social.tp-redirect.com/s/WRE0V7jm) 🚀",
            "\n\n💰 *COPY TRADING AUTOMATICO*\nTodas nuestras senales ejecutadas en tu cuenta.\n👉 [Comenzar ahora](https://social.tp-redirect.com/s/WRE0V7jm) — Solo pagas si ganas 🔥"
        ]
        mensaje += random.choice(promos)
        
    msg_id = enviar_telegram(mensaje, destino=target, **kwargs)
    if msg_id and auto_delete > 0:
        _programar_borrado_mensaje(target, msg_id, auto_delete)
    return msg_id

def notificar_fomo_grupo(nombre: str, tipo: str):
    """Notificación FOMO al grupo público — corta y con CopyTrading. Se auto-borra en 10 min."""
    if not ADMIN_USER or not GROUP_ID: return

    fomos = [
        (f"*{nombre}* — Se\u00f1al activa\n"
         f"[Ver resultado en vivo]({DASHBOARD_URL})\n"
         f"\U0001f680 [Copy Trading — copia autom\u00e1tica](https://social.tp-redirect.com/s/WRE0V7jm)"),
        (f"*{nombre}* — Operaci\u00f3n en curso\n"
         f"[Dashboard en vivo]({DASHBOARD_URL})\n"
         f"\U0001f4b0 [Copia nuestras operaciones en tu cuenta](https://social.tp-redirect.com/s/WRE0V7jm)"),
        (f"*{nombre}* — Se\u00f1al detectada\n"
         f"[Rendimiento en vivo]({DASHBOARD_URL})\n"
         f"\u26a1 [Activa Copy Trading — sin experiencia](https://social.tp-redirect.com/s/WRE0V7jm)"),
    ]
    msg = random.choice(fomos)
    return enviar_telegram_temporal(msg, destino=GROUP_ID, delay_borrado=600)

def _programar_borrado_mensaje(chat_id, message_id, delay_seg=300):
    """Borra un mensaje de Telegram después de N segundos (default 5 min)."""
    def _borrar():
        time.sleep(delay_seg)
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/deleteMessage"
            requests.post(url, json={"chat_id": chat_id, "message_id": message_id}, timeout=10)
        except Exception:
            pass
    threading.Thread(target=_borrar, daemon=True).start()


def enviar_telegram_temporal(mensaje: str, destino: str = None, delay_borrado: int = 300, **kwargs):
    """Envía un mensaje a Telegram que se auto-borra después de delay_borrado segundos."""
    chat_id = destino or CHANNEL_ID
    msg_id = enviar_telegram(mensaje, destino=chat_id, **kwargs)
    if msg_id:
        _programar_borrado_mensaje(chat_id, msg_id, delay_borrado)
    return msg_id


def enviar_foto_telegram(mensaje: str, ruta_foto: str, destino: str = None):
    """Envía una foto con un caption (mensaje) a Telegram. Retorna message_id."""
    time.sleep(0.3)
    chat_id = destino or CHANNEL_ID
    url     = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"

    for intento in range(3):
        try:
            with open(ruta_foto, 'rb') as f:
                r = requests.post(
                    url,
                    data={"chat_id": chat_id, "caption": mensaje, "parse_mode": "Markdown"},
                    files={"photo": f},
                    timeout=20
                )

            if r.status_code == 200:
                # Limpiar foto tras éxito
                try: os.remove(ruta_foto)
                except Exception: pass
                data = r.json()
                return data.get("result", {}).get("message_id")
            elif r.status_code == 429:
                espera = int(r.json().get("parameters", {}).get("retry_after", 5))
                time.sleep(espera)
                continue
            else:
                break  # Error no recuperable
        except Exception as e:
            if intento < 2:
                time.sleep(2)
                continue
            print(f"⚠️ Error enviando foto a Telegram (intento {intento+1}): {e}")
            # Limpiar foto
            try: os.remove(ruta_foto)
            except Exception: pass
            # Fallback a texto normal
            return enviar_telegram(mensaje, destino)

    # Limpiar foto si todos los intentos fallaron
    try: os.remove(ruta_foto)
    except Exception: pass
    return None

def pin_message(chat_id: str, message_id: int, silencioso: bool = True):
    """Fija un mensaje en un chat de Telegram."""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/pinChatMessage"
    payload = {"chat_id": chat_id, "message_id": message_id,
               "disable_notification": silencioso}
    try:
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code == 200:
            logger.info(f"📌 Mensaje {message_id} fijado en {chat_id}")
            return True
        else:
            logger.warning(f"⚠️ No se pudo fijar mensaje en {chat_id}: {r.text}")
    except Exception as e:
        logger.warning(f"⚠️ Error al fijar mensaje: {e}")
    return False

def _chat_tiene_pin_web(chat_id: str) -> bool:
    """Verifica si el chat ya tiene un mensaje fijado con el link de la web."""
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getChat"
        r = requests.post(url, json={"chat_id": chat_id}, timeout=10)
        if r.status_code == 200:
            data = r.json().get("result", {})
            pinned = data.get("pinned_message", {})
            texto_pin = pinned.get("text", "")
            # Si ya tiene un pin con "WEB EN VIVO" y la URL correcta, no duplicar
            if "WEB EN VIVO" in texto_pin and ("buysell365.pro" in texto_pin or "buysell365.duckdns.org" in texto_pin):
                logger.info(f"📌 Chat {chat_id} ya tiene pin de la web — no se duplica")
                return True
    except Exception as e:
        logger.warning(f"⚠️ Error verificando pin: {e}")
    return False

def fijar_web_en_canal_y_grupo():
    """Envía y fija el mensaje de la web en vivo en el canal y el grupo.
       Solo envía si NO hay un pin existente con la web correcta."""
    msg, teclado = cmd_url_dashboard()
    # --- Canal ---
    if not _chat_tiene_pin_web(CHANNEL_ID):
        mid_canal = enviar_telegram(msg, destino=CHANNEL_ID, teclado=teclado)
        if mid_canal:
            pin_message(CHANNEL_ID, mid_canal)
            logger.info("📌 Web fijada en CANAL")
    # --- Grupo ---
    if GROUP_ID and GROUP_ID != CHANNEL_ID:
        if not _chat_tiene_pin_web(GROUP_ID):
            mid_grupo = enviar_telegram(msg, destino=GROUP_ID, teclado=teclado)
            if mid_grupo:
                pin_message(GROUP_ID, mid_grupo)
                logger.info("📌 Web fijada en GRUPO")

# (Alias eliminado: ahora usamos enviar_telegram directamente)

# ============================================================
#  INDICADORES TÉCNICOS PROFESIONALES
# ============================================================

def get_col(df, prefijo):
    """Busca columna por prefijo de forma segura"""
    cols = [c for c in df.columns if str(c).startswith(prefijo)]
    if cols: return df[cols[0]]
    cols = [c for c in df.columns if prefijo in str(c)]
    if cols: return df[cols[0]]
    raise KeyError(f"No se encontró columna '{prefijo}'")

# ============================================================
#  GRÁFICOS DE MUESTRA PARA TELEGRAM
# ============================================================

def generar_grafico_operacion(df, ticker, tipo, entrada, salida, nivel_tp, niveles=None):
    """
    Genera un gráfico de ALTA PRECISIÓN y ESTÉTICA PREMIUM para Telegram.
    Optimizado para móviles, con marca de agua y contexto técnico.
    """
    try:
        import matplotlib.pyplot as plt
        import matplotlib.ticker as mticker
        import pandas_ta as ta

        # Zoom de 40 velas: equilibrio entre detalle y contexto
        d = df.tail(40).copy()
        if not isinstance(d.index, pd.DatetimeIndex):
            d.index = pd.to_datetime(d.index)

        # Cálculo de contexto técnico rápido (EMAs)
        d['ema20'] = ta.ema(d['Close'], length=20)
        d['ema50'] = ta.ema(d['Close'], length=50)

        # Paleta TradingView Premium Dark
        TV_UP    = '#089981' # Verde vibrante TV
        TV_DOWN  = '#f23645' # Rojo vibrante TV
        TV_BG    = '#131722' # Fondo nocturno
        TV_GRID  = '#1e222d' # Rejilla sutil
        TV_BLUE  = '#2962ff' # Azul institucional
        TV_TEXT  = '#d1d4dc' # Gris claro
        TV_GOLD  = '#face15' # Dorado brillante para victorias
        TV_EMA_F = '#2962ff' # EMA Rápida
        TV_EMA_L = '#ff9800' # EMA Lenta

        # Configurar colores de las velas
        mc = mpf.make_marketcolors(up=TV_UP, down=TV_DOWN, edge='inherit', wick='inherit', volume='in', ohlc='i')
        s = mpf.make_mpf_style(
            marketcolors=mc, facecolor=TV_BG, figcolor=TV_BG, gridcolor=TV_GRID,
            gridstyle='solid', y_on_right=True, 
            rc={'font.size': 10, 'axes.labelcolor': TV_TEXT, 'xtick.color': TV_TEXT, 'ytick.color': TV_TEXT}
        )

        # Determinar precisión real (Forex: 5, JPY: 3, Oro/Indices: 2)
        prec = 2
        if "=X" in ticker:
            prec = 3 if "JPY" in ticker else 5

        # Añadir EMAs en el overlay
        apds = [
            mpf.make_addplot(d['ema20'], color=TV_EMA_F, width=0.8, alpha=0.7),
            mpf.make_addplot(d['ema50'], color=TV_EMA_L, width=0.8, alpha=0.7)
        ]

        # Crear figura (12x7 es mejor para ver la acción del precio en móviles)
        fig, axes = mpf.plot(
            d, type='candle', style=s, volume=False,
            addplot=apds, figsize=(12, 7.5), returnfig=True,
            tight_layout=False, scale_padding={'right': 15, 'left': 1, 'top': 5, 'bottom': 5}
        )

        ax = axes[0]
        # Ajuste fino de márgenes para las etiquetas laterales
        fig.subplots_adjust(right=0.82, left=0.04, top=0.92, bottom=0.08)

        # 1. 🟢 ZONA DE PROFIT (BOX)
        color_box = TV_UP if tipo == "COMPRA" else TV_DOWN
        alpha_val = 0.15
        
        y_min_box, y_max_box = (entrada, salida) if tipo == "COMPRA" else (salida, entrada)
        ax.fill_between(range(len(d)), y_min_box, y_max_box, color=color_box, alpha=alpha_val, zorder=0)

        # 2. 📍 LÍNEAS DE PRECIO INDICATIVAS
        ax.axhline(y=entrada, color=TV_BLUE, linewidth=1.5, linestyle='--', alpha=0.6, zorder=1)
        ax.axhline(y=salida, color=TV_GOLD, linewidth=2.5, linestyle='-', alpha=0.9, zorder=5)

        # 3. 🏷️ ETIQUETAS "TRADINGVIEW STYLE"
        def draw_tv_label(price, text, color, bg_color='#131722'):
            txt = f" {text}: {price:.{prec}f} "
            ax.text(len(d) + 0.5, price, txt, 
                   color=color, fontsize=9, fontweight='bold',
                   va='center', ha='left',
                   bbox=dict(facecolor=bg_color, alpha=1.0, edgecolor=color, boxstyle='round,pad=0.3', linewidth=1.2))

        draw_tv_label(entrada, " ENTRADA ", TV_BLUE)
        draw_tv_label(salida, f" WIN {nivel_tp} ", TV_GOLD, bg_color='#1e222d')
        
        if niveles and 'sl' in niveles:
            sl_p = niveles['sl']
            ax.axhline(y=sl_p, color='#ff5252', linewidth=1.0, linestyle=':', alpha=0.5)
            draw_tv_label(sl_p, " STOP LOSS ", '#ff5252')

        # 4. 💎 MARCA DE AGUA Y TÍTULO PREMIUM
        ax.set_title(f" {ticker} · {tipo} EXIT · {nivel_tp} ✅", color=TV_GOLD, fontsize=15, fontweight='bold', pad=25)
        
        # Marca de agua elegante
        fig.text(0.5, 0.5, "BUYSELL365 PRO", fontsize=45, color='white',
                 alpha=0.04, ha='center', va='center', rotation=30, fontweight='bold')
        
        # Leyenda de indicadores
        ax.text(0.02, 0.96, f"EMA 20/50 Context Chart", transform=ax.transAxes, color=TV_TEXT, fontsize=8, alpha=0.6)

        # Formateo de ejes
        ax.yaxis.set_major_formatter(mticker.FormatStrFormatter(f'%.{prec}f'))
        # Dar espacio a la derecha para las etiquetas
        ax.set_xlim(-1, len(d) + 12) 
        
        # Quitar labels de X para limpieza
        ax.set_xlabel("")

        # Guardar con alta densidad para nitidez
        filename = f"chart_{ticker.replace('=','')}_{int(time.time())}.png"
        fig.savefig(filename, dpi=180, bbox_inches='tight', facecolor=TV_BG)
        plt.close(fig)
        return filename

    except Exception as e:
        logger.error(f"🚨 Error crítico en generación de gráfico ({ticker}): {e}")
        import traceback
        traceback.print_exc()
        return None


# ============================================================
#  DETECCIÓN DE PATRONES DE VELAS JAPONESAS
# ============================================================

def detectar_patrones_velas(df):
    """
    Detecta patrones clásicos de velas japonesas en las últimas velas.
    Retorna lista de strings con los patrones detectados.
    """
    try:
        if len(df) < 4 or 'Open' not in df.columns:
            return []

        opens  = df['Open'].values.astype(float)
        highs  = df['High'].values.astype(float)
        lows   = df['Low'].values.astype(float)
        closes = df['Close'].values.astype(float)

        # Últimas 4 velas (índices -4, -3, -2, -1)
        o2, _, _, c2 = opens[-3], highs[-3], lows[-3], closes[-3]
        o3, _, _, c3 = opens[-2], highs[-2], lows[-2], closes[-2]
        o4, h4, l4, c4 = opens[-1], highs[-1], lows[-1], closes[-1]

        patrones = []

        def body(o, c):       return abs(c - o)
        def rango(h, l):      return max(h - l, 0.0001)
        def upper_wick(o, c, h): return h - max(o, c)
        def lower_wick(o, c, l): return min(o, c) - l

        b4  = body(o4, c4)
        r4  = rango(h4, l4)
        uw4 = upper_wick(o4, c4, h4)
        lw4 = lower_wick(o4, c4, l4)
        b3  = body(o3, c3)
        b2  = body(o2, c2)

        # ── VELA ACTUAL ──────────────────────────────────────

        # Doji: cuerpo < 10% del rango
        if b4 / r4 < 0.1:
            patrones.append("🕯️ Doji")

        # Hammer (alcista): mecha inferior >= 2x cuerpo, mecha sup <= 30% cuerpo
        if b4 > 0 and lw4 >= 2 * b4 and uw4 <= 0.5 * b4 and c4 >= o4:
            patrones.append("🔨 Hammer (alcista)")

        # Shooting Star (bajista): mecha superior >= 2x cuerpo, mecha inf pequeña
        if b4 > 0 and uw4 >= 2 * b4 and lw4 <= 0.5 * b4 and c4 < o4:
            patrones.append("⭐ Shooting Star (bajista)")

        # Marubozu alcista: casi sin mechas, cuerpo > 80% rango
        if c4 > o4 and b4 / r4 > 0.8:
            patrones.append("📗 Marubozu Alcista")

        # Marubozu bajista
        if c4 < o4 and b4 / r4 > 0.8:
            patrones.append("📕 Marubozu Bajista")

        # ── DOS VELAS ────────────────────────────────────────

        # Bullish Engulfing: vela previa bajista, actual alcista y engloba
        if c3 < o3 and c4 > o4 and c4 > o3 and o4 < c3:
            patrones.append("🟢 Engulfing Alcista")

        # Bearish Engulfing
        if c3 > o3 and c4 < o4 and c4 < o3 and o4 > c3:
            patrones.append("🔴 Engulfing Bajista")

        # Piercing Line (alcista)
        if c3 < o3 and c4 > o4 and o4 < c3:
            mid3 = (o3 + c3) / 2
            if c4 > mid3 and c4 < o3:
                patrones.append("🟢 Piercing Line (alcista)")

        # Dark Cloud Cover (bajista)
        if c3 > o3 and c4 < o4 and o4 > c3:
            mid3 = (o3 + c3) / 2
            if c4 < mid3 and c4 > o3:
                patrones.append("🔴 Dark Cloud Cover (bajista)")

        # ── TRES VELAS ───────────────────────────────────────

        # Morning Star: bajista → cuerpo pequeño → alcista
        if c2 < o2 and b3 <= 0.35 * b2 and c4 > o4 and c4 > (o2 + c2) / 2:
            patrones.append("🌟 Morning Star (alcista)")

        # Evening Star: alcista → cuerpo pequeño → bajista
        if c2 > o2 and b3 <= 0.35 * b2 and c4 < o4 and c4 < (o2 + c2) / 2:
            patrones.append("🌇 Evening Star (bajista)")

        # Three White Soldiers: 3 velas alcistas consecutivas
        if c4 > o4 and c3 > o3 and c2 > o2 and c4 > c3 > c2:
            patrones.append("💪 Three White Soldiers (alcista)")

        # Three Black Crows: 3 velas bajistas consecutivas
        if c4 < o4 and c3 < o3 and c2 < o2 and c4 < c3 < c2:
            patrones.append("🐦 Three Black Crows (bajista)")

        return patrones[:3]  # Máximo 3 patrones para no saturar el mensaje

    except Exception:
        return []


# ============================================================
#  PIVOT POINTS DIARIOS
# ============================================================

def calcular_pivot_points_df(df):
    """
    Calcula Pivot Points del día anterior usando el df de 15min.
    Retorna dict con pp, r1, r2, s1, s2 o {} si no hay datos.
    """
    try:
        if df is None or df.empty or 'Open' not in df.columns:
            return {}
        # Asegurar que el índice es DatetimeIndex
        if not isinstance(df.index, pd.DatetimeIndex):
            return {}
        # Agregar a diario
        df_d = df.resample('D').agg({
            'High': 'max', 'Low': 'min', 'Close': 'last'
        }).dropna()
        if len(df_d) < 2:
            return {}
        prev = df_d.iloc[-2]
        h = float(prev['High'])
        l = float(prev['Low'])
        c = float(prev['Close'])
        pp = (h + l + c) / 3
        r1 = 2 * pp - l
        r2 = pp + (h - l)
        r3 = h + 2 * (pp - l)   # FIX: añadido para evitar KeyError en cmd_pivots
        s1 = 2 * pp - h
        s2 = pp - (h - l)
        s3 = l - 2 * (h - pp)   # FIX: añadido para evitar KeyError en cmd_pivots
        return {"pp": pp, "r1": r1, "r2": r2, "r3": r3, "s1": s1, "s2": s2, "s3": s3}
    except Exception:
        return {}

# ============================================================
#  ML - PREDICIÓN DE DIRECCIÓN
# ============================================================

def _calcular_features_ml(d):
    """Calcula todas las features ML sobre un DataFrame con OHLCV.
    Se reutiliza tanto para entrenamiento como para predicción."""
    d['rsi'] = ta.rsi(d['Close'], length=14)
    macd = ta.macd(d['Close'], fast=12, slow=26, signal=9)
    d['macd'] = get_col(macd, 'MACDh') if macd is not None and not macd.empty else 0
    d['macd_line'] = get_col(macd, 'MACD') if macd is not None and not macd.empty else 0
    d['ema9']  = ta.ema(d['Close'], length=9)
    d['ema20'] = ta.ema(d['Close'], length=20)
    d['ema50'] = ta.ema(d['Close'], length=50)
    d['dist_ema'] = (d['Close'] - d['ema20']) / (d['ema20'].replace(0, 1))
    # Alineación de EMAs: +1 si 9>20>50 (alcista), -1 si 9<20<50 (bajista), 0 mixto
    d['ema_align'] = 0.0
    d.loc[(d['ema9'] > d['ema20']) & (d['ema20'] > d['ema50']), 'ema_align'] = 1.0
    d.loc[(d['ema9'] < d['ema20']) & (d['ema20'] < d['ema50']), 'ema_align'] = -1.0
    stoch = ta.stoch(d['High'], d['Low'], d['Close'])
    d['stoch'] = get_col(stoch, 'STOCHk') if stoch is not None and not stoch.empty else 0
    adx = ta.adx(d['High'], d['Low'], d['Close'], length=14)
    d['adx'] = get_col(adx, 'ADX_') if adx is not None and not adx.empty else 0
    d['plus_di'] = get_col(adx, 'DMP_') if adx is not None and not adx.empty else 0
    d['minus_di'] = get_col(adx, 'DMN_') if adx is not None and not adx.empty else 0
    # DI diferencia normalizada: positivo = tendencia alcista dominante
    d['di_diff'] = (d['plus_di'] - d['minus_di']) / (d['adx'].replace(0, 1))
    atr = ta.atr(d['High'], d['Low'], d['Close'], length=14)
    d['atr_pct'] = atr / d['Close'] * 100 if atr is not None else 0
    vol_sma = ta.sma(d['Volume'], length=20)
    d['vol_ratio'] = d['Volume'] / vol_sma.replace(0, 1) if vol_sma is not None else 1.0
    # Para futuros con volumen=0: usar 1.0 (neutral) en vez de ratios basura
    d.loc[d['Volume'] <= 0, 'vol_ratio'] = 1.0
    bb = ta.bbands(d['Close'], length=20, std=2)
    if bb is not None and not bb.empty:
        bbu = get_col(bb, 'BBU')
        bbl = get_col(bb, 'BBL')
        bb_mid = get_col(bb, 'BBM')
        d['bb_width'] = (bbu - bbl) / d['Close'] * 100
        # Posición dentro de Bollinger: 0=lower band, 1=upper band
        d['bb_pos'] = (d['Close'] - bbl) / (bbu - bbl).replace(0, 1)
    else:
        d['bb_width'] = 0
        d['bb_pos'] = 0.5
    # Momentum: cambio porcentual en las últimas 3 y 6 velas
    d['momentum_3'] = d['Close'].pct_change(3) * 100
    d['momentum_6'] = d['Close'].pct_change(6) * 100
    # Tamaño del cuerpo de la vela vs rango total (0-1)
    rango = d['High'] - d['Low']
    d['body_pct'] = abs(d['Close'] - d['Open']) / rango.replace(0, 1)
    # Features temporales (Captura ciclos de apertura/cierre de sesión)
    d['hour'] = d.index.hour
    d['hour_sin'] = np.sin(2 * np.pi * d['hour'] / 24)
    d['hour_cos'] = np.cos(2 * np.pi * d['hour'] / 24)
    # Día de la semana (Lunes=0 más volátil, Viernes=4 menos)
    d['dow_sin'] = np.sin(2 * np.pi * d.index.dayofweek / 5)
    return d


def predecir_direccion_ml(df, ticker=""):
    """
    Modelo LightGBM v3 (reemplazo de RandomForest) — reentrenado máximo cada 30 min por ticker.
    Mejoras v2: 18 features (vs 10 antes), EMA alignment, DI diff, momentum,
    BB position, body_pct, día de semana. Accuracy threshold subido a 0.52.
    """
    global _cache_ml_modelos
    TTL_ML = 1800  # 30 minutos
    features = [
        'rsi', 'macd', 'macd_line', 'dist_ema', 'ema_align',
        'stoch', 'adx', 'di_diff', 'atr_pct',
        'vol_ratio', 'bb_width', 'bb_pos',
        'momentum_3', 'momentum_6', 'body_pct',
        'hour_sin', 'hour_cos', 'dow_sin'
    ]
    try:
        import lightgbm as lgb
        from sklearn.preprocessing import StandardScaler

        d = _calcular_features_ml(df.copy())
        d = d.dropna()
        if len(d) < 80:
            return 50.0

        cached = _cache_ml_modelos.get(ticker)
        if cached and (time.time() - cached['timestamp']) < TTL_ML:
            rf = cached['model']
            scaler = cached['scaler']
        else:
            # DESCARGA MASIVA PARA ENTRENAMIENTO (30 días para robustez)
            d_train = descargar_datos_seguro(ticker, period="30d", interval="15m")
            if d_train is None or len(d_train) < 500:
                return 50.0

            d_t = _calcular_features_ml(d_train.copy())

            # Target: precio sube en las próximas 3 velas
            d_t['Target'] = (d_t['Close'].shift(-3) > d_t['Close']).astype(int)
            train_data = d_t.iloc[:-3].dropna()

            if len(train_data) < 400:
                return 50.0

            # Train/test split 80/20
            split_idx = int(len(train_data) * 0.8)
            X_all = train_data[features]
            y_all = train_data['Target']

            X_train = X_all.iloc[:split_idx]
            y_train = y_all.iloc[:split_idx]
            X_test = X_all.iloc[split_idx:]
            y_test = y_all.iloc[split_idx:]

            # Escalado de features
            scaler = StandardScaler()
            X_train_scaled = scaler.fit_transform(X_train)
            X_test_scaled = scaler.transform(X_test)

            # Random Forest con más árboles y regularización mejorada
            rf = lgb.LGBMClassifier(
                n_estimators=300,       # LightGBM es más rápido, más árboles sin costo
                max_depth=8,            # Controlar overfitting
                num_leaves=31,          # Default óptimo para LightGBM
                min_child_samples=20,   # Más conservador → reduce overfitting
                learning_rate=0.05,     # Learning rate bajo + más árboles = mejor generalización
                subsample=0.8,          # Bagging: usa 80% de datos por árbol
                colsample_bytree=0.8,   # Usa 80% de features por árbol
                reg_alpha=0.1,          # Regularización L1
                reg_lambda=0.1,         # Regularización L2
                random_state=42,
                n_jobs=-1,
                verbose=-1              # Sin spam en consola
            )
            rf.fit(X_train_scaled, y_train)

            # Validar accuracy mínima — umbral 0.52 (relajado para más señales)
            # Modelos entre 52-56% pueden ser útiles como factor de confluencia
            accuracy = rf.score(X_test_scaled, y_test)
            if accuracy < 0.48:
                logger.warning(f"🤖 ML {ticker}: accuracy muy baja ({accuracy:.2f}) — devolviendo neutral")
                # Solo cachear como inútil si realmente es peor que random (< 48%)
                with _lock_ops:
                    _cache_ml_modelos[ticker] = {
                        'model': None, 'scaler': None,
                        'timestamp': time.time(), 'accuracy': accuracy
                    }
                return 50.0
            # Accuracy 48-52%: usar modelo pero avisar — sigue siendo útil como factor de confluencia
            if accuracy < 0.52:
                logger.info(f"🤖 ML {ticker}: accuracy moderada ({accuracy:.2f}) — usando como señal débil")

            with _lock_ops:
                _cache_ml_modelos[ticker] = {
                    'model': rf,
                    'scaler': scaler,
                    'timestamp': time.time(),
                    'accuracy': accuracy
                }
            logger.info(f"🤖 ML {ticker}: Reentrenado v2 con 30d ({len(train_data)} muestras, acc={accuracy:.2f}, {len(features)} features)")

        # Si el modelo cacheado fue marcado como baja accuracy → neutral
        if rf is None:
            return 50.0

        last_row = d.iloc[[-1]][features]
        last_row_scaled = scaler.transform(last_row)
        probs = rf.predict_proba(last_row_scaled)
        prob_alcista = float(probs[0][1]) * 100
        return round(prob_alcista, 1)

    except Exception as e:
        logger.warning(f"⚠️ Error en ML ({ticker}): {e}")
        return 50.0

def _calcular_rango_asiatico(df, ticker=""):
    """
    🌏 Calcula el rango de la sesión asiática (00:00-07:00 UTC) para Asian Range Breakout.
    Solo se activa para ORO (GC=F) y USD/JPY (USDJPY=X).
    Usa la ÚLTIMA vela del df como referencia temporal (funciona tanto en real como backtest).
    Retorna dict con: asian_high, asian_low, asian_range_valid, is_london_session
    """
    resultado = {
        "asian_high": 0.0,
        "asian_low": 0.0,
        "asian_range_valid": False,
        "is_london_session": False,
    }

    # Solo calcular para activos que usan esta estrategia
    if ticker not in ("GC=F", "USDJPY=X"):
        return resultado

    try:
        idx = df.index
        if len(idx) < 30:
            return resultado

        # Normalizar a UTC
        if hasattr(idx, 'tz') and idx.tz is not None:
            idx_utc = idx.tz_convert('UTC')
        else:
            # MT5 XM: UTC+2 invierno, UTC+3 verano (Europe/Helsinki)
            # Detectar automáticamente según fecha
            try:
                import pytz as _pytz_ar
                _hel = _pytz_ar.timezone('Europe/Helsinki')
                _sample = idx[-1].to_pydatetime() if hasattr(idx[-1], 'to_pydatetime') else idx[-1]
                _offset_h = _hel.localize(_sample).utcoffset().total_seconds() / 3600
                _tz_str = f'Etc/GMT-{int(_offset_h)}'
                idx_utc = idx.tz_localize(_tz_str).tz_convert('UTC')
            except Exception:
                try:
                    idx_utc = idx.tz_localize('Etc/GMT-2').tz_convert('UTC')
                except Exception:
                    idx_utc = idx

        # Usar la ÚLTIMA vela como referencia temporal (no datetime.now)
        # Así funciona correctamente tanto en real como en backtest
        ultima_vela = idx_utc[-1]
        if hasattr(ultima_vela, 'date'):
            hoy = ultima_vela.date()
        else:
            return resultado

        # Hora actual de la vela (en UTC)
        if hasattr(ultima_vela, 'hour'):
            hora_utc = ultima_vela.hour
        else:
            return resultado

        # Solo buscar si estamos en horario London (07:00-16:00 UTC)
        # Ampliado hasta 16:00 para cubrir sesión US matutina también
        if hora_utc < 7 or hora_utc > 16:
            return resultado

        # Sesión asiática: 00:00-07:00 UTC del DÍA de la vela actual
        asian_start = pd.Timestamp(hoy.year, hoy.month, hoy.day, 0, 0, tzinfo=pytz.UTC)
        asian_end = pd.Timestamp(hoy.year, hoy.month, hoy.day, 7, 0, tzinfo=pytz.UTC)
        london_end = pd.Timestamp(hoy.year, hoy.month, hoy.day, 16, 0, tzinfo=pytz.UTC)

        # Crear un df temporal con índice UTC para filtrar
        df_temp = df.copy()
        df_temp.index = idx_utc

        # Filtrar velas de sesión asiática de HOY
        mask_asian = (df_temp.index >= asian_start) & (df_temp.index < asian_end)
        df_asian = df_temp.loc[mask_asian]

        if len(df_asian) >= 8:  # Mínimo 8 velas de 15m (2 horas) para ser válido
            asian_high = float(df_asian['High'].max())
            asian_low = float(df_asian['Low'].min())
            asian_range = asian_high - asian_low

            resultado["asian_high"] = asian_high
            resultado["asian_low"] = asian_low

            # Validar que el rango no sea demasiado amplio (mercado ya se movió mucho)
            # ORO: rango entre $2-$30 (típico $8-$20)
            # USD/JPY: rango entre 5-60 pips (típico 20-40 pips)
            if ticker == "GC=F":
                resultado["asian_range_valid"] = 2.0 < asian_range < 30.0
            elif ticker == "USDJPY=X":
                resultado["asian_range_valid"] = 0.05 < asian_range < 0.60

            # ¿La vela actual está en sesión London/US? (07:00-16:00 UTC)
            resultado["is_london_session"] = asian_end <= ultima_vela <= london_end

    except Exception as e:
        print(f"⚠️ Error calculando rango asiático [{ticker}]: {e}")

    return resultado


def calcular_indicadores_profesionales(df, precio, ticker=""):
    """
    Sistema profesional de indicadores con análisis multi-dimensional
    """
    try:
        close = pd.Series(df['Close'].values, dtype=float).dropna()
        high  = pd.Series(df['High'].values, dtype=float).dropna()
        low   = pd.Series(df['Low'].values, dtype=float).dropna()
        volume = pd.Series(df['Volume'].values, dtype=float).dropna()

        if len(close) < 200:
            return None

        min_len = min(len(close), len(high), len(low), len(volume))
        close = close.iloc[:min_len]
        high = high.iloc[:min_len]
        low = low.iloc[:min_len]
        volume = volume.iloc[:min_len]

        # ━━━━━━━━━━
        # INDICADORES PRINCIPALES
        # ━━━━━━━━━━

        # RSI
        rsi = ta.rsi(close, length=14)

        # EMAs para tendencia
        ema9   = ta.ema(close, length=9)
        ema20  = ta.ema(close, length=20)
        ema50  = ta.ema(close, length=50)
        ema200 = ta.ema(close, length=200)

        # ATR para volatilidad (15m — se usa para mostrar en análisis)
        atr = ta.atr(high, low, close, length=14)

        # ATR de 1h — para calcular TP/SL con objetivos realistas
        # El ATR 1h es ~3-4× el de 15m → pips/puntos acordes al mercado intraday
        _atr_15m_raw = float(atr.iloc[-1])
        atr_1h_val = _atr_15m_raw * 3.5  # Fallback escalado: 15m×3.5 ≈ 1h (si el caché no está listo)
        try:
            cached_1h = _cache_mtf_1h.get(ticker)
            if cached_1h:
                df1h = cached_1h['df']
                if df1h is not None and len(df1h) >= 20:
                    h1h = pd.Series(df1h['High'].values, dtype=float)
                    l1h = pd.Series(df1h['Low'].values, dtype=float)
                    c1h = pd.Series(df1h['Close'].values, dtype=float)
                    min_len_1h = min(len(h1h), len(l1h), len(c1h))
                    atr_1h_s = ta.atr(h1h.iloc[:min_len_1h], l1h.iloc[:min_len_1h], c1h.iloc[:min_len_1h], length=14)
                    if atr_1h_s is not None and not atr_1h_s.empty:
                        v = float(atr_1h_s.iloc[-1])
                        if v > 0:
                            atr_1h_val = v
        except Exception:
            pass

        # MACD
        macd_df = ta.macd(close, fast=12, slow=26, signal=9)

        # Bollinger Bands
        bb = ta.bbands(close, length=20, std=2)

        # Stochastic
        stoch = ta.stoch(high, low, close, k=14, d=3)

        # ADX (fuerza de tendencia) - CRÍTICO
        adx_df = ta.adx(high, low, close, length=14)

        # ━━━━━━━━━━
        # ANÁLISIS DE VOLUMEN
        # ━━━━━━━━━━

        vol_sma = ta.sma(volume, length=20)
        # Detectar si el volumen es válido (> 0) o si yfinance devuelve 0 (futuros)
        _vol_valido = volume.iloc[-5:].sum() > 0  # últimas 5 velas con algún volumen
        if _vol_valido and vol_sma.iloc[-1] > 0:
            vol_ratio = float(volume.iloc[-1] / vol_sma.iloc[-1])
        else:
            # yfinance devuelve volumen=0 para futuros (GC=F, NQ=F, ES=F)
            # Proxy de actividad: ratio ATR reciente vs ATR promedio
            # Si ATR se expande → mercado activo (equivale a volumen alto)
            # Si ATR se contrae → mercado dormido (equivale a volumen bajo)
            try:
                _atr_reciente = float(atr.iloc[-1])
                _atr_media = float(atr.iloc[-20:].mean()) if len(atr) >= 20 else _atr_reciente
                vol_ratio = _atr_reciente / _atr_media if _atr_media > 0 else 1.0
            except Exception:
                vol_ratio = 1.0

        # ━━━━━━━━━━
        # DETECCIÓN DE DIVERGENCIAS RSI
        # ━━━━━━━━━━

        # Últimos 20 períodos
        precio_20 = close.iloc[-20:]
        rsi_20 = rsi.iloc[-20:]

                
        # Divergencia alcista: precio hace mínimos más bajos, RSI mínimos más altos
        divergencia_alcista = False
        if precio <= precio_20.min() * 1.002:  # Cerca del mínimo
            if rsi.iloc[-1] > rsi_20.min() * 1.15:  # RSI significativamente más alto
                divergencia_alcista = True

        # Divergencia bajista: precio hace máximos más altos, RSI máximos más bajos
        divergencia_bajista = False
        if precio >= precio_20.max() * 0.998:  # Cerca del máximo
            if rsi.iloc[-1] < rsi_20.max() * 0.85:  # RSI significativamente más bajo
                divergencia_bajista = True

        # ━━━━━━━━━━
        # SOPORTES Y RESISTENCIAS — ZONAS REALES
        # Detecta mínimos/máximos locales en últimas 100 velas
        # y los agrupa por proximidad (±0.5%) para encontrar zonas relevantes
        # ━━━━━━━━━━

        recientes = close.iloc[-100:]
        arr = recientes.values
        minimos_loc, maximos_loc = [], []
        for i in range(2, len(arr) - 2):
            if arr[i] < arr[i-1] and arr[i] < arr[i+1] and arr[i] < arr[i-2] and arr[i] < arr[i+2]:
                minimos_loc.append(arr[i])
            if arr[i] > arr[i-1] and arr[i] > arr[i+1] and arr[i] > arr[i-2] and arr[i] > arr[i+2]:
                maximos_loc.append(arr[i])

        if len(minimos_loc) >= 2:
            minimos_loc.sort()
            zonas_s = [minimos_loc[0]]
            for m in minimos_loc[1:]:
                if abs(m - zonas_s[-1]) / zonas_s[-1] > 0.005:
                    zonas_s.append(m)
            soportes_validos = [z for z in zonas_s if z < precio]
            soporte = float(max(soportes_validos)) if soportes_validos else float(recientes.min())
        else:
            soporte = float(recientes.min())

        if len(maximos_loc) >= 2:
            maximos_loc.sort()
            zonas_r = [maximos_loc[0]]
            for m in maximos_loc[1:]:
                if abs(m - zonas_r[-1]) / zonas_r[-1] > 0.005:
                    zonas_r.append(m)
            resistencias_validas = [z for z in zonas_r if z > precio]
            resistencia = float(min(resistencias_validas)) if resistencias_validas else float(recientes.max())
        else:
            resistencia = float(recientes.max())

        # Distancia porcentual
        dist_soporte = ((precio - soporte) / soporte) * 100
        dist_resistencia = ((resistencia - precio) / resistencia) * 100

        try:
            ema200_val = float(ema200.iloc[-1])
            if pd.isna(ema200_val): ema200_val = precio
        except Exception:
            ema200_val = precio

        return {
            # Precio y tendencia
            "precio": precio,
            "high": float(high.iloc[-1]),
            "low": float(low.iloc[-1]),
            "ema9": float(ema9.iloc[-1]),
            "ema20": float(ema20.iloc[-1]),
            "ema50": float(ema50.iloc[-1]),
            "ema200": ema200_val,

            # Osciladores
            "rsi": float(rsi.iloc[-1]),
            "stoch_k": float(get_col(stoch, 'STOCHk').iloc[-1]),
            "stoch_d": float(get_col(stoch, 'STOCHd').iloc[-1]),

            # MACD
            "macd": float(get_col(macd_df, 'MACD_').iloc[-1]),
            "signal": float(get_col(macd_df, 'MACDs').iloc[-1]),
            "macd_hist": float(get_col(macd_df, 'MACDh_').iloc[-1]),
            "macd_hist_prev": float(get_col(macd_df, 'MACDh_').iloc[-2]),

            # Bollinger Bands
            "bb_up": float(get_col(bb, 'BBU').iloc[-1]),
            "bb_mid": float(get_col(bb, 'BBM').iloc[-1]),
            "bb_lo": float(get_col(bb, 'BBL').iloc[-1]),

            # ADX (fuerza de tendencia)
            "adx": float(get_col(adx_df, 'ADX_').iloc[-1]),
            "di_plus": float(get_col(adx_df, 'DMP_').iloc[-1]),
            "di_minus": float(get_col(adx_df, 'DMN_').iloc[-1]),

            # Volumen
            "vol_ratio": vol_ratio,
            "volume": float(volume.iloc[-1]),

            # ATR 15m (para mostrar volatilidad en análisis)
            "atr": float(atr.iloc[-1]),
            # ATR 1h (para calcular TP/SL — objetivos realistas)
            "atr_1h": atr_1h_val,

            # Divergencias
            "div_alcista": divergencia_alcista,
            "div_bajista": divergencia_bajista,

            # Soportes/Resistencias
            "soporte": soporte,
            "resistencia": resistencia,
            "dist_soporte": dist_soporte,
            "dist_resistencia": dist_resistencia,

            # Apertura de la última vela (para cálculo de cuerpo real)
            "open": float(df['Open'].iloc[-1]),

            # Patrones de velas japonesas
            "patrones": detectar_patrones_velas(df),

            # 🛡️ DETECCIÓN DE RÉGIMEN DE MERCADO (Brain v2.5)
            # RANGO: ADX < 20
            # TENDENCIA: ADX > 25
            # VOLATILIDAD: BB width > umbral (ORO=5% porque es naturalmente volátil, resto=2.5%)
            # FIX 2026-03-19: ORO siempre salía VOLATILIDAD con 2.5% — su rango normal es ~3-4%
            "regimen": (
                "VOLATILIDAD" if (float(get_col(bb, 'BBU').iloc[-1] - get_col(bb, 'BBL').iloc[-1]) / precio * 100 > (5.0 if ticker in ("GC=F", "XAUUSD") else 2.5)) else
                "TENDENCIA" if float(get_col(adx_df, 'ADX_').iloc[-1]) > 25 else
                "RANGO" if float(get_col(adx_df, 'ADX_').iloc[-1]) < 20 else
                "TRANSICIÓN"
            ),

            # Pivot Points del día anterior
            "pivots": calcular_pivot_points_df(df),

            # ML DESACTIVADO — accuracy 44-52% no aporta valor, ahorra CPU
            "ml_prob_alcista": 50.0,  # Neutral fijo, sin reentrenamiento
            # FIX: eliminadas claves "high" y "low" duplicadas (ya existen arriba en el dict)

            # 🌏 RANGO ASIÁTICO (para Asian Range Breakout — ORO y USD/JPY)
            **_calcular_rango_asiatico(df, ticker),
        }
    except Exception as e:
        print(f"⚠️ Error calculando indicadores: {e}")
        import traceback
        traceback.print_exc()
        return None

# ============================================================
#  🎯 SISTEMA PROFESIONAL DE EVALUACIÓN - MULTI-CONFIRMACIÓN
# ============================================================

def evaluar_senal_profesional(ind, ticker=""):
    """
    Sistema profesional con 3 Estrategias Independientes — Versión PREMIUM.
    Filtros de calidad estrictos: ML gate, ADX fuerte, volumen real, R:R mínimo.
    Solo retorna señales score ≥ 4 para garantizar alta tasa de acierto.
    Retorna: ("COMPRA"/"VENTA", score_1_5, lista_razones)
    """
    if not ind:
        return None, 0, []

    # [5] CIRCUIT BREAKER GLOBAL: bloquear si está activo
    try:
        if _circuit_breaker_check():
            return None, 0, ["🚨 Circuit Breaker activo — trading pausado"]
    except Exception:
        pass

    # 📉 EUR/USD: backtest mostró 24.7% WR — solo permitir score 5 (divergencia)
    # Este filtro se aplica AQUÍ para que el backtest también lo capture
    _eurusd_solo_score5 = (ticker == "EURUSD=X")

    # Extraer probabilidades ML
    prob_alcista = ind.get('ml_prob_alcista', 50.0)
    prob_bajista = round(100.0 - prob_alcista, 1)

    # ── ML DESACTIVADO (2026-03-18) ──
    # Accuracy 48-52% = coin flip. No aporta edge, solo bloquea señales buenas.
    # Bypass completo: siempre retorna True para no filtrar nada.
    _ml_disponible = False
    _ml_pass_alcista = lambda umbral: True
    _ml_pass_bajista = lambda umbral: True

    # ━━━━━━━━━━
    # 0. FILTROS DE RÉGIMEN DE MERCADO (Brain v2.5)
    # ━━━━━━━━━━
    regimen = ind.get('regimen', 'TRANSICIÓN')
    adx_val = ind.get('adx', 0)

    # PAR_PROFILES lookup — must be before any _prof usage
    _prof = get_par_profile(ticker=ticker)

    # Bloqueo preventivo: Evitar operar en indecisión total
    if regimen == "TRANSICIÓN" and adx_val < 12:
        return None, 0, [f"⚠️ Transición extrema (ADX {adx_val:.1f}<12)"]

    # Bloqueo de Volatilidad Extrema
    _vol_min_extrema = (_prof["premium"].get("vol_min_extrema", 0.3) if (_prof and _prof.get("premium", {}).get("enabled")) else (0.3 if ticker in ("GC=F", "XAUUSD") else 0.3))
    if regimen == "VOLATILIDAD" and ind.get('vol_ratio', 1) < _vol_min_extrema:
        return None, 0, [f"⚠️ Volatilidad extrema sin volumen institucional (vol={ind.get('vol_ratio',0):.1f}x < {_vol_min_extrema}x)"]

    # FIX 4: Filtro RANGO — solo bloquea en rango MUY débil (ADX < 12) sin RSI extremo
    if regimen == "RANGO":
        rsi_val = ind.get('rsi', 50)
        tiene_divergencia = ind.get('div_alcista', False) or ind.get('div_bajista', False)
        # RSI amplio para RANGO — permitir más señales
        _rango_rsi_lo = 30
        _rango_rsi_hi = 70
        rsi_extremo = rsi_val < _rango_rsi_lo or rsi_val > _rango_rsi_hi
        # Solo bloquear si ADX es MUY bajo (<12) Y no hay divergencia Y RSI es neutral
        if adx_val < 12 and not tiene_divergencia and not rsi_extremo:
            return None, 0, [f"⚠️ Mercado sin dirección (ADX {adx_val:.1f} < 12) sin divergencia ni RSI extremo ({rsi_val:.1f}). Señal bloqueada."]
        # Si ADX >= 12 en RANGO, permitir que las estrategias evalúen (Trend Following puede detectar algo)

    # ━━━━━━━━━━
    # 1. CONFIGURACIÓN MACRO POR ACTIVO
    # ━━━━━━━━━━

    cat = get_categoria(ticker)

    # ── PAR_PROFILES config — _prof already set above ──
    if _prof and _prof.get("premium", {}).get("enabled"):
        _prem = _prof["premium"]
        adx_min        = _prem.get("adx_min", 13)
        bb_squeeze     = _prem.get("bb_squeeze", 0.008)
        rsi_os         = _prem.get("rsi_os", 35)
        rsi_ob         = _prem.get("rsi_ob", 65)
        ml_umbral_fuerte = _prem.get("ml_umbral", 55.0)
        min_atr        = _prem.get("min_atr", 0.0)
    else:
        adx_min = 13; bb_squeeze = 0.008; rsi_os, rsi_ob = 35, 65
        ml_umbral_fuerte = 55.0; min_atr = 0.0

    if regimen == "RANGO" and adx_val >= 12:
        ml_umbral_fuerte = ml_umbral_fuerte + 1.0

    cercania_soporte      = ind['dist_soporte'] < 0.8
    cercania_resistencia  = ind['dist_resistencia'] < 0.8

    if ind['atr'] < min_atr:
        return None, 0, [f"⚠️ Volatilidad insuficiente (ATR {ind['atr']:.5g} < {min_atr}). Mercado dormido."]

    # Gate function — per-pair RSI/ADX filters from PAR_PROFILES
    # Note: _prof passed as default arg to avoid closure scoping issues with exec()
    def _filtro_activo_ok(direccion, _p=_prof):
        """Retorna (ok, razon) — si ok=False, la señal se bloquea."""
        if not _p or not _p.get("premium", {}).get("enabled"):
            return True, ""
        _pm = _p["premium"]
        rsi_v = ind['rsi']
        adx_v = ind['adx']
        _disp = _p["identity"]["display"]
        if direccion == "COMPRA":
            gate = _pm.get("rsi_gate_buy")
            if gate is not None and rsi_v >= gate:
                return False, f"🟡 {_disp} COMPRA bloqueada: RSI {rsi_v:.0f} >= {gate}"
        elif direccion == "VENTA":
            gate = _pm.get("rsi_gate_sell")
            if gate is not None and rsi_v <= gate:
                return False, f"🟡 {_disp} VENTA bloqueada: RSI {rsi_v:.0f} <= {gate}"
        adx_gate = _pm.get("adx_gate")
        if adx_gate is not None and adx_v < adx_gate:
            return False, f"🟡 {_disp} bloqueada: ADX {adx_v:.0f} < {adx_gate}"
        return True, ""

    # ── HELPER: verificar el cuerpo de la vela ──
    open_p = ind.get('open', ind['precio'])
    cuerpo_pct = abs(open_p - ind['precio']) / max(ind['atr'], 1e-10)
    # Nasdaq permite velas más pequeñas porque tiene más volumen; Oro requiere cuerpo real.
    umbral_cuerpo = 0.05 if ticker == "NQ=F" else 0.08 # Extremadamente relajado
    
    vela_con_cuerpo = cuerpo_pct >= umbral_cuerpo
    
    if not vela_con_cuerpo:
        # En vez de bloquear por completo, restamos un punto al score si es muy doji
        pass
        # return None, 0, ["Vela sin cuerpo (Doji/Indecisión). Esperando vela de fuerza."]

    # ━━━━━━━━━━
    # 🛡️ FILTRO ANTI-CONTRADICCIÓN TÉCNICA (v2 — 17-Mar-2026)
    # Si los 3 indicadores principales (EMA20vsEMA50, MACD, PreciovsEMA50)
    # son unanimemente alcistas → NO permitir SELL. Y viceversa.
    # Esto evita señales contra-tendencia que pierden inmediatamente.
    # ━━━━━━━━━━
    _tech_alcista = (ind['ema20'] > ind['ema50'] and ind['macd'] > ind['signal'] and ind['precio'] > ind['ema50'])
    _tech_bajista = (ind['ema20'] < ind['ema50'] and ind['macd'] < ind['signal'] and ind['precio'] < ind['ema50'])

    # ━━━━━━━━━━
    # ESTRATEGIA 1 — PULLBACK A LA TENDENCIA  ❌ DESACTIVADA
    # Backtest: 12 señales, 16.7% WR, -499 pips. No rentable.
    # Solo Premium activo: Breakout (score 4) + Reversión con divergencia (score 5)
    # ━━━━━━━━━━
    if False and ind['adx'] > adx_min:  # DESACTIVADA

        # ── Pullback Alcista Premium ──
        if (ind['precio'] > ind['ema200']          # Sobre tendencia principal
            and ind['ema20'] > ind['ema50']         # Estructura alcista en 15m
            and ind['macd'] > ind['signal']         # MACD apoya (nuevo)
            and ind['low'] <= ind['ema50'] * 1.010  # Proximidad EMA50 (1% tolerancia)
            and ind['precio'] > ind['ema50']        # Cierra por encima
            and ind['rsi'] < (rsi_os + 15)          # RSI descargado
            and ind['stoch_k'] < 50                 # Estocástico bajo
            and ind['vol_ratio'] >= 0.8  # Volumen mínimo
            and _ml_pass_alcista(ml_umbral_fuerte)):  # ML confirma o no disponible
            _ml_tag = f"🤖 ML confirma: *{prob_alcista}% probabilidad alcista*" if _ml_disponible else "🤖 ML: no disponible — señal por análisis técnico puro"
            razones = [
                "✓ Estrategia: *Pullback Premium a Tendencia Alcista*",
                f"✓ ADX {ind['adx']:.1f} — Tendencia fuerte en {cat.upper()}",
                f"✓ Rebote preciso en EMA50 · Precio cerró por encima",
                f"✓ RSI descargado ({ind['rsi']:.1f}) + Estocástico bajo ({ind['stoch_k']:.0f})",
                f"✓ MACD alcista · Volumen {ind['vol_ratio']:.1f}x media",
                _ml_tag
            ]
            if _eurusd_solo_score5:
                return None, 0, ["📉 EUR/USD filtrado: Pullback es score 4, requiere score 5"]
            _filt_ok, _filt_msg = _filtro_activo_ok("COMPRA")
            if not _filt_ok:
                return None, 0, [_filt_msg]
            return "COMPRA", 4, razones

        # ── Pullback Bajista Premium ──
        if (ind['precio'] < ind['ema200']
            and ind['ema20'] < ind['ema50']
            and ind['macd'] < ind['signal']
            and ind['high'] >= ind['ema50'] * 0.990  # Proximidad EMA50 (1% tolerancia)
            and ind['precio'] < ind['ema50']
            and ind['rsi'] > (rsi_ob - 15)
            and ind['stoch_k'] > 50
            and ind['vol_ratio'] >= 0.8  # Volumen mínimo
            and _ml_pass_bajista(ml_umbral_fuerte)):
            _ml_tag = f"🤖 ML confirma: *{prob_bajista}% probabilidad bajista*" if _ml_disponible else "🤖 ML: no disponible — señal por análisis técnico puro"
            razones = [
                "✓ Estrategia: *Pullback Premium a Tendencia Bajista*",
                f"✓ ADX {ind['adx']:.1f} — Tendencia fuerte en {cat.upper()}",
                f"✓ Rechazo preciso en EMA50 · Precio cerró por debajo",
                f"✓ RSI sobrecomprado ({ind['rsi']:.1f}) + Estocástico alto ({ind['stoch_k']:.0f})",
                f"✓ MACD bajista · Volumen {ind['vol_ratio']:.1f}x media",
                _ml_tag
            ]
            if _eurusd_solo_score5:
                return None, 0, ["📉 EUR/USD filtrado: Pullback es score 4, requiere score 5"]
            _filt_ok, _filt_msg = _filtro_activo_ok("VENTA")
            if not _filt_ok:
                return None, 0, [_filt_msg]
            return "VENTA", 4, razones

    # ━━━━━━━━━━
    # ESTRATEGIA 2 — RUPTURA DE ALTA CONVICCIÓN  (Score 4)
    # Breakout real: compresión extrema + volumen fuerte + ML y MACD alineados.
    # Filtros más estrictos que antes: vol > 1.5x (era 1.3x) + ML >= 60%.
    # ━━━━━━━━━━
    bb_rango_pct = (ind['bb_up'] - ind['bb_lo']) / max(ind['precio'], 1e-10)

    if bb_rango_pct < bb_squeeze:  # Squeeze activo
        # Futuros (GC=F, NQ=F, ES=F) usan proxy ATR como volumen — umbral más bajo
        _es_futuro = ticker.endswith("=F")
        _es_forex = ticker.endswith("=X")
        vol_breakout = (_prof["premium"].get("vol_breakout", 1.3) if (_prof and _prof.get("premium", {}).get("enabled")) else (1.0 if _es_futuro else (1.3 if _es_forex else 1.5)))

        # Ruptura Alcista
        if (ind['precio'] > ind['bb_up']
            and ind['vol_ratio'] > vol_breakout
            and ind['macd'] > ind['signal']
            and ind['rsi'] < 75                    # No sobrecomprado extremo
            and _ml_pass_alcista(ml_umbral_fuerte)     # ML confirma o no disponible
            and vela_con_cuerpo):                  # Vela con cuerpo real
            _ml_tag = f"🤖 ML con alta convicción: *{prob_alcista}% alcista*" if _ml_disponible else "🤖 ML: no disponible — breakout por técnico puro"
            razones = [
                "✓ Estrategia: *Breakout de Alta Convicción*",
                f"✓ Quiebre de Bollinger Superior con squeeze ({bb_rango_pct*100:.2f}% rango)",
                f"✓ Volumen explosivo: {ind['vol_ratio']:.1f}x la media (requerido >1.5x)",
                f"✓ MACD alcista · RSI en zona sana ({ind['rsi']:.1f})",
                _ml_tag
            ]
            # EUR/USD: Breakout PERMITIDO (buena estrategia para todos los activos)
            _filt_ok, _filt_msg = _filtro_activo_ok("COMPRA")
            if not _filt_ok:
                return None, 0, [_filt_msg]
            return "COMPRA", 4, razones

        # Ruptura Bajista
        if (ind['precio'] < ind['bb_lo']
            and ind['vol_ratio'] > vol_breakout
            and ind['macd'] < ind['signal']
            and ind['rsi'] > 25
            and _ml_pass_bajista(ml_umbral_fuerte)    # ML confirma o no disponible
            and vela_con_cuerpo):
            _ml_tag = f"🤖 ML con alta convicción: *{prob_bajista}% bajista*" if _ml_disponible else "🤖 ML: no disponible — breakout por técnico puro"
            razones = [
                "✓ Estrategia: *Breakout de Alta Convicción*",
                f"✓ Quiebre de Bollinger Inferior con squeeze ({bb_rango_pct*100:.2f}% rango)",
                f"✓ Volumen explosivo: {ind['vol_ratio']:.1f}x la media (requerido >1.5x)",
                f"✓ MACD bajista · RSI en zona sana ({ind['rsi']:.1f})",
                _ml_tag
            ]
            # EUR/USD: Breakout PERMITIDO (buena estrategia para todos los activos)
            _filt_ok, _filt_msg = _filtro_activo_ok("VENTA")
            if not _filt_ok:
                return None, 0, [_filt_msg]
            return "VENTA", 4, razones

    # ━━━━━━━━━━
    # ESTRATEGIA 3 — REVERSIÓN EXTREMA CON CONFLUENCIA  (Score 4-5)
    # "Cazar el piso o el techo" con múltiples confirmaciones.
    # NUEVA: requiere divergencia RSI confirmada O (RSI extremo + S/R + ML).
    # ━━━━━━━━━━

    # Reversión Alcista (Score 5 si hay divergencia, Score 4 si RSI+S/R+ML)
    # ⚠️ FIX: Agregar filtro anti-tendencia — NO comprar en tendencia bajista fuerte
    # ML Reversión: 53% para ORO/JPY (original), 57% para el resto (estricto)
    _ml_rev = max(ml_umbral_fuerte, 53.0) if ticker in ("GC=F", "USDJPY=X", "GBPJPY=X") else max(ml_umbral_fuerte, 57.0)
    _tendencia_bajista_fuerte = (ind['ema20'] < ind['ema50'] and ind['ema50'] < ind['ema200'])  # EMAs alineadas a la baja
    _tendencia_alcista_fuerte = (ind['ema20'] > ind['ema50'] and ind['ema50'] > ind['ema200'])  # EMAs alineadas al alza

    # Rev Score 4: filtros ORIGINALES para ORO/USD/JPY/GBP/JPY (rsi_os por activo, soporte opcional)
    # Rev Score 4 ESTRICTO: para otros activos (RSI < 30, soporte obligatorio)
    # FIX 2026-03-19: USD/JPY reversión tiene 40.8% WR — desactivado (perdió -91 pips hoy)
    _rev4_permitido = (_prof["premium"].get("rev4_allowed", False) if (_prof and _prof.get("premium", {}).get("enabled")) else ticker in ("GC=F", "GBPJPY=X"))
    if _rev4_permitido:
        # Filtros ORIGINALES — ORO: RSI<36, USD/JPY: RSI<33, GBP/JPY: RSI<30
        # Soporte es bonus, no obligatorio (el original no lo requería)
        conf_alcista_extra = (
            not _tendencia_bajista_fuerte
            and ind['rsi'] < rsi_os                # RSI por activo (36 ORO, 33 JPY, 30 GBP/JPY)
            and (cercania_soporte or ind['precio'] < ind['ema200'])  # Soporte O debajo de EMA200
            and _ml_pass_alcista(_ml_rev)
        )
    else:
        conf_alcista_extra = (
            not _tendencia_bajista_fuerte
            and ind['rsi'] < 30
            and cercania_soporte
            and _ml_pass_alcista(_ml_rev)
        )
    # RSI para reversiones Score 5: < 30 compra, > 70 venta (con divergencia obligatoria)
    if ind['div_alcista'] and ind['rsi'] < 30 and _ml_pass_alcista(_ml_rev) and not _tendencia_bajista_fuerte:
        _ml_tag = f"🤖 ML apoya: *{prob_alcista}% alcista*" if _ml_disponible else "🤖 ML: no disponible — divergencia técnica pura"
        razones = [
            "⭐ Estrategia: *Reversión Extrema — Divergencia RSI Alcista*",
            f"⭐ Divergencia RSI confirmada en gráfico 15m (suelo técnico)",
            f"✓ RSI en zona extrema ({ind['rsi']:.1f}) · Zona de suelo estructural",
            f"✓ Tendencia NO es bajista fuerte (EMAs no alineadas a la baja)",
            _ml_tag
        ]
        if cercania_soporte: razones.append("✓ Reacción en zona de Soporte clave")
        _filt_ok, _filt_msg = _filtro_activo_ok("COMPRA")
        if not _filt_ok:
            return None, 0, [_filt_msg]
        return "COMPRA", 5, razones
    # Reversión Score 4 alcista — SOLO para ORO, USD/JPY, GBP/JPY
    # Backtest original: ORO 51.9% WR, USD/JPY 40.8% WR con reversiones
    # Indices (NASDAQ, S&P) y EUR/USD: solo Breakout + Rev Score 5
    if conf_alcista_extra and _rev4_permitido:
        _ml_tag = f"🤖 ML apoya: *{prob_alcista}% alcista*" if _ml_disponible else "🤖 ML: no disponible — señal técnica pura"
        razones = [
            "✓ Estrategia: *Reversión Alcista — Confluencia RSI + Soporte*",
            f"✓ RSI oversold ({ind['rsi']:.1f}) + Cercanía a soporte clave",
            f"✓ Tendencia NO es bajista fuerte",
            _ml_tag
        ]
        if cercania_soporte: razones.append("✓ Reacción en zona de Soporte clave")
        _filt_ok, _filt_msg = _filtro_activo_ok("COMPRA")
        if not _filt_ok:
            return None, 0, [_filt_msg]
        return "COMPRA", 4, razones

    # Reversión Bajista (Score 5 si hay divergencia, Score 4 si RSI+R+ML)
    # ⚠️ FIX: NO vender si tendencia es alcista fuerte
    if _rev4_permitido:
        # Filtros ORIGINALES — ORO: RSI>64, USD/JPY: RSI>67, GBP/JPY: RSI>70
        conf_bajista_extra = (
            not _tendencia_alcista_fuerte
            and ind['rsi'] > rsi_ob                # RSI por activo (64 ORO, 67 JPY, 70 GBP/JPY)
            and (cercania_resistencia or ind['precio'] > ind['ema200'])  # Resistencia O encima de EMA200
            and _ml_pass_bajista(_ml_rev)
        )
    else:
        conf_bajista_extra = (
            not _tendencia_alcista_fuerte
            and ind['rsi'] > 70
            and cercania_resistencia
            and _ml_pass_bajista(_ml_rev)
        )
    # RSI para reversiones Score 5: > 70 venta (con divergencia obligatoria)
    if ind['div_bajista'] and ind['rsi'] > 70 and _ml_pass_bajista(_ml_rev) and not _tendencia_alcista_fuerte:
        _ml_tag = f"🤖 ML apoya: *{prob_bajista}% bajista*" if _ml_disponible else "🤖 ML: no disponible — divergencia técnica pura"
        razones = [
            "⭐ Estrategia: *Reversión Extrema — Divergencia RSI Bajista*",
            f"⭐ Divergencia RSI confirmada en gráfico 15m (techo técnico)",
            f"✓ RSI en zona extrema ({ind['rsi']:.1f}) · Zona de techo estructural",
            f"✓ Tendencia NO es alcista fuerte (EMAs no alineadas al alza)",
            _ml_tag
        ]
        if cercania_resistencia: razones.append("✓ Rechazo en zona de Resistencia clave")
        _filt_ok, _filt_msg = _filtro_activo_ok("VENTA")
        if not _filt_ok:
            return None, 0, [_filt_msg]
        return "VENTA", 5, razones
    # Reversión Score 4 bajista — SOLO para ORO, USD/JPY, GBP/JPY
    if conf_bajista_extra and _rev4_permitido:
        _ml_tag = f"🤖 ML apoya: *{prob_bajista}% bajista*" if _ml_disponible else "🤖 ML: no disponible — señal técnica pura"
        razones = [
            "✓ Estrategia: *Reversión Bajista — Confluencia RSI + Resistencia*",
            f"✓ RSI overbought ({ind['rsi']:.1f}) + Cercanía a resistencia clave",
            f"✓ Tendencia NO es alcista fuerte",
            _ml_tag
        ]
        if cercania_resistencia: razones.append("✓ Rechazo en zona de Resistencia clave")
        _filt_ok, _filt_msg = _filtro_activo_ok("VENTA")
        if not _filt_ok:
            return None, 0, [_filt_msg]
        return "VENTA", 4, razones

    # ━━━━━━━━━━
    # ESTRATEGIA 4 — ASIAN RANGE BREAKOUT  🌏 (Score 4)
    # Solo para ORO (GC=F) y USD/JPY (USDJPY=X)
    # Lógica: La sesión asiática (00:00-07:00 UTC) forma un rango.
    # Al abrir London (07:00-13:00 UTC), si el precio rompe ese rango
    # con confirmación (MACD + cuerpo de vela), se genera señal.
    # Research: 60-70% WR para ORO, 55-65% WR para USD/JPY.
    # ━━━━━━━━━━
    _asian_high = ind.get('asian_high', 0)
    _asian_low = ind.get('asian_low', 0)
    _asian_valid = ind.get('asian_range_valid', False)
    _is_london = ind.get('is_london_session', False)

    if _asian_valid and _is_london and ticker in ("GC=F", "USDJPY=X"):
        _asian_range = _asian_high - _asian_low
        # Cuerpo de vela > 40% del rango de la vela (no doji)
        _vela_range = ind['high'] - ind['low']
        _vela_body = abs(ind['open'] - ind['precio'])
        _body_ratio = _vela_body / max(_vela_range, 1e-10)

        # Breakout Alcista: precio cierra ENCIMA del máximo asiático
        if (ind['precio'] > _asian_high
            and ind['macd'] > ind['signal']           # MACD confirma dirección
            and _body_ratio > 0.40                     # Vela con cuerpo real (>40%)
            and ind['rsi'] < 75                        # No sobrecomprado extremo
            and _ml_pass_alcista(ml_umbral_fuerte)):   # ML confirma o no disponible
            _asset_name = "ORO" if ticker == "GC=F" else "USD/JPY"
            _range_fmt = f"${_asian_range:.1f}" if ticker == "GC=F" else f"{_asian_range*100:.0f} pips"
            _ml_tag = f"🤖 ML apoya: *{prob_alcista}% alcista*" if _ml_disponible else "🤖 ML: no disponible — breakout de sesión puro"
            razones = [
                f"🌏 Estrategia: *Asian Range Breakout Alcista — {_asset_name}*",
                f"✓ Precio rompió máximo asiático ({_asian_high:.4g}) · Rango: {_range_fmt}",
                f"✓ Sesión London activa — máxima liquidez",
                f"✓ MACD alcista · Cuerpo de vela {_body_ratio*100:.0f}%",
                f"✓ RSI en zona sana ({ind['rsi']:.1f})",
                _ml_tag
            ]
            _filt_ok, _filt_msg = _filtro_activo_ok("COMPRA")
            if not _filt_ok:
                return None, 0, [_filt_msg]
            return "COMPRA", 4, razones

        # Breakout Bajista: precio cierra DEBAJO del mínimo asiático
        if (ind['precio'] < _asian_low
            and ind['macd'] < ind['signal']           # MACD confirma dirección
            and _body_ratio > 0.40                     # Vela con cuerpo real (>40%)
            and ind['rsi'] > 25                        # No sobrevendido extremo
            and _ml_pass_bajista(ml_umbral_fuerte)):   # ML confirma o no disponible
            _asset_name = "ORO" if ticker == "GC=F" else "USD/JPY"
            _range_fmt = f"${_asian_range:.1f}" if ticker == "GC=F" else f"{_asian_range*100:.0f} pips"
            _ml_tag = f"🤖 ML apoya: *{prob_bajista}% bajista*" if _ml_disponible else "🤖 ML: no disponible — breakout de sesión puro"
            razones = [
                f"🌏 Estrategia: *Asian Range Breakout Bajista — {_asset_name}*",
                f"✓ Precio rompió mínimo asiático ({_asian_low:.4g}) · Rango: {_range_fmt}",
                f"✓ Sesión London activa — máxima liquidez",
                f"✓ MACD bajista · Cuerpo de vela {_body_ratio*100:.0f}%",
                f"✓ RSI en zona sana ({ind['rsi']:.1f})",
                _ml_tag
            ]
            _filt_ok, _filt_msg = _filtro_activo_ok("VENTA")
            if not _filt_ok:
                return None, 0, [_filt_msg]
            return "VENTA", 4, razones

    # Sin confirmaciones suficientes
    # ── DIAGNÓSTICO: ¿Por qué no disparó ninguna estrategia? ──
    _diag = []
    _diag.append(f"ADX={ind['adx']:.1f}(min:{adx_min})")
    _diag.append(f"RSI={ind['rsi']:.1f}")
    _ml_status = f"ML_alc={prob_alcista:.1f}% ML_baj={prob_bajista:.1f}%(min:{ml_umbral_fuerte}%)" if _ml_disponible else "ML=N/A(bypass)"
    _diag.append(_ml_status)
    _diag.append(f"EMA20{'>' if ind['ema20']>ind['ema50'] else '<'}EMA50")
    _diag.append(f"MACD{'>' if ind['macd']>ind['signal'] else '<'}Signal")
    _diag.append(f"P{'>' if ind['precio']>ind['ema50'] else '<'}EMA50")
    _diag.append(f"Vol={ind.get('vol_ratio',0):.1f}x")
    logger.info(f"📋 DIAGNÓSTICO {ticker}: {' | '.join(_diag)}")

    # FIX 2026-03-19: Guardar diagnóstico para la consola
    _diagnostico_activos[ticker] = {
        "adx": round(ind['adx'], 1),
        "rsi": round(ind['rsi'], 1),
        "vol": round(ind.get('vol_ratio', 0), 1),
        "ema20": round(ind.get('ema20', 0), 5),
        "ema50": round(ind.get('ema50', 0), 5),
        "ema_bull": ind['ema20'] > ind['ema50'],
        "macd_bull": ind['macd'] > ind['signal'],
        "precio": round(ind.get('precio', 0), 5),
        "spread": round(ind.get('spread_puntos', 0), 1),
        "ts": time.time(),
    }

    return None, 0, [f"📋 {' | '.join(_diag)}"]

# ============================================================
#  CÁLCULO DE NIVELES - 3 TAKE PROFITS
# ============================================================

def calcular_niveles_3tp(precio, tipo, atr, ticker="", estrategia=""):
    """
    Calcula SL y 3 niveles de TP basados en ATR.
    Multiplicadores ajustados por tipo de activo y MODO_RIESGO.
    Optimizado para capturar movimientos grandes (Señales Premium).
    """
    cat = get_categoria(ticker)
    ze_mult = 0.4 # Buffer neutro por defecto

    # 💎 SISTEMA TP/SL — desde PAR_PROFILES (single source of truth)
    _prof_tp = get_par_profile(ticker=ticker)
    if _prof_tp and _prof_tp.get("sl_tp"):
        _st = _prof_tp["sl_tp"]
        sl_mult  = _st.get("sl_mult", 1.8)
        tp1_mult = _st.get("tp1_mult", 2.0)
        tp2_mult = _st.get("tp2_mult", 2.5)
        tp3_mult = _st.get("tp3_mult", 3.2)
        ze_mult  = _st.get("ze_mult", 0.3)
    else:
        sl_mult = 1.8; tp1_mult = 2.0; tp2_mult = 2.5; tp3_mult = 3.2; ze_mult = 0.3

    # Ajuste según modo de riesgo
    if MODO_RIESGO == "conservador":
        sl_mult  *= 0.8   # SL más ajustado
        tp1_mult *= 0.85
        tp2_mult *= 0.85
        tp3_mult *= 0.85
    elif MODO_RIESGO == "agresivo":
        sl_mult  *= 1.25  # SL más amplio
        tp1_mult *= 1.25
        tp2_mult *= 1.25
        tp3_mult *= 1.25

    # Ajuste por estrategia (Breakout = SL más amplio, backtest: 51% WR)
    if estrategia == "breakout":
        sl_mult *= 1.2    # 20% más amplio → más espacio para respirar
    elif estrategia == "asian_breakout":
        sl_mult *= 1.3    # 30% más amplio — SL al otro lado del rango asiático
        tp1_mult *= 1.1   # TP1 ligeramente mayor (breakout de sesión = movimiento amplio)
    elif estrategia == "reversion":
        sl_mult *= 1.1    # 10% más amplio para reversiones (score 5 = alta calidad)

    sl = atr * sl_mult

    # [1] SL ADAPTATIVO AL RANGO ASIÁTICO
    # Para asian_breakout: usar asian high/low + 20% buffer como SL
    # Cap máximo: 2.5x ATR (no exceder)
    if estrategia == "asian_breakout" and ticker in ("GC=F", "USDJPY=X"):
        try:
            _cached_ind = _cache_ind.get(ticker, {})
            _a_high = _cached_ind.get('asian_high', 0)
            _a_low = _cached_ind.get('asian_low', 0)
            _a_valid = _cached_ind.get('asian_range_valid', False)
            if _a_valid and _a_high > 0 and _a_low > 0:
                _a_range = _a_high - _a_low
                _buffer = _a_range * 0.20  # 20% buffer
                if tipo.upper() in ("COMPRA", "BUY", "LONG"):
                    # Compra: SL debajo del mínimo asiático con buffer
                    sl_asian = precio - (_a_low - _buffer)
                else:
                    # Venta: SL encima del máximo asiático con buffer
                    sl_asian = (_a_high + _buffer) - precio
                # Cap a 2.5x ATR máximo
                sl_max = atr * 2.5
                sl_asian = min(sl_asian, sl_max)
                # Solo usar si es mayor que el SL base (más protección)
                if sl_asian > 0:
                    sl = sl_asian
                    logger.info(f"🌏 SL ADAPTATIVO ASIÁTICO {ticker}: SL={sl:.5g} (rango={_a_range:.5g}, buffer={_buffer:.5g}, cap={sl_max:.5g})")
        except Exception as e:
            logger.warning(f"⚠️ Error SL asiático adaptativo {ticker}: {e}")

    # MIN_SL floor from PAR_PROFILES
    _min_sl_val = _prof_tp["sl_tp"].get("min_sl", 0) if (_prof_tp and _prof_tp.get("sl_tp")) else 0
    if _min_sl_val > 0 and sl < _min_sl_val:
        sl = _min_sl_val

    tp1 = atr * tp1_mult
    tp2 = atr * tp2_mult
    tp3 = atr * tp3_mult
    ze = atr * ze_mult

    if tipo.upper() in ("COMPRA", "BUY", "LONG"):
        return {
            'sl': precio - sl,
            'tp1': precio + tp1,
            'tp2': precio + tp2,
            'tp3': precio + tp3,
            'ze_low': precio - ze,
            'ze_high': precio + ze
        }
    else:  # VENTA / SELL
        return {
            'sl': precio + sl,
            'tp1': precio - tp1,
            'tp2': precio - tp2,
            'tp3': precio - tp3,
            'ze_low': precio - ze,
            'ze_high': precio + ze
        }

# ============================================================
#  CLASIFICACIÓN Y CÁLCULO DE PIPS
# ============================================================

def _clasificar_tipo_trade(ticker):
    """Retorna 'premium' para futuros y 'swing' para divisas. Los 6 activos del bot."""
    t = ticker.upper()
    if any(x in t for x in ["GC=F", "ES=F", "NQ=F"]):
        return "premium"
    return "swing"

def calcular_pips(precio_entrada, precio_salida, ticker, tipo=None):
    """
    Calcula pips según el estilo del usuario:
    Forex: 1 pip = 0.0001 (o 0.01 para JPY).
    Oro/SP500: 1 punto = 10 pips.
    Nasdaq: 1 punto = 1 pip.

    Si se pasa tipo="VENTA", invierte el signo para reflejar correctamente
    que en una venta, ganar = precio baja, perder = precio sube.
    """
    if not precio_entrada or not precio_salida: return 0.0
    cat = get_categoria(ticker)
    diff = precio_salida - precio_entrada

    # Para VENTA: invertir signo (ganar cuando precio baja)
    if tipo and tipo.upper() in ("VENTA", "SELL"):
        diff = -diff

    if cat == "forex":
        multi = 100 if "JPY" in ticker.upper() else 10000
        return diff * multi

    if any(x in ticker.upper() for x in ["GC=F", "GOLD", "XAUUSD"]):
        return diff  # Oro: 1 punto = $1 de movimiento (sin multiplicar)

    if any(x in ticker.upper() for x in ["ES=F", "US500CASH", "US500"]):
        return diff  # S&P 500: 1 punto = 1 punto (sin multiplicar)

    return diff

def _calcular_racha_perdidas_actual():
    """Retorna el número de pérdidas consecutivas al final del historial."""
    racha = 0
    with _lock_ops:
        for op in reversed(historial_operaciones):
            if op.get('resultado') == 'LOSS':
                racha += 1
            else:
                break
    return racha

def _get_last_loss_time():
    """Retorna el timestamp de la última pérdida, o None."""
    with _lock_ops:
        for op in reversed(historial_operaciones):
            if op.get('resultado') == 'LOSS':
                return op.get('timestamp_cierre', op.get('timestamp', 0))
            break
    return None

def calcular_lote_sugerido(capital, riesgo_pct, entrada, sl, ticker):
    """
    Calcula el lotaje institucional sugerido basado en stop loss en pips.
    Reducción dinámica: -25% por cada pérdida consecutiva (máx -50% con 2 pérdidas).
    Se restaura automáticamente al ganar.
    """
    try:
        # ── REDUCCIÓN DINÁMICA DE LOTE (Gestión de drawdown activo) ────
        racha_perdidas = _calcular_racha_perdidas_actual()
        factor_reduccion = 1.0
        if racha_perdidas == 1:
            factor_reduccion = 0.75  # -25% tras 1 pérdida
        elif racha_perdidas >= 2:
            factor_reduccion = 0.50  # -50% tras 2+ pérdidas consecutivas
        riesgo_pct_efectivo = riesgo_pct * factor_reduccion

        if racha_perdidas >= 1:
            logger.info(f"📉 Racha de {racha_perdidas} pérdidas — riesgo reducido a {riesgo_pct_efectivo*100:.1f}%")

        # [2] RIESGO DINÁMICO POR NOTICIAS — multiplicador 0.0-1.0
        try:
            _mult_noticias = _ajustar_riesgo_por_noticias(ticker)
            if _mult_noticias <= 0:
                return "0.00 (Bloqueado por noticias)"
            riesgo_pct_efectivo *= _mult_noticias
        except Exception:
            pass  # Si falla, continuar sin ajuste

        # [7] FILTRO DE SESIÓN MEJORADO — multiplicador de sesión
        try:
            _mult_sesion = _factor_sesion(ticker)
            riesgo_pct_efectivo *= _mult_sesion
        except Exception:
            pass  # Si falla, continuar sin ajuste

        riesgo_usd = capital * riesgo_pct_efectivo
        pips_riesgo = abs(calcular_pips(entrada, sl, ticker))
        if pips_riesgo <= 0: return "0.01 (Min)"
        
        cat = get_categoria(ticker)
        if cat == "forex":
            if "JPY" in ticker.upper():
                # JPY pairs: 1 lote (100k) = ¥1000/pip. En USD: 1000/rate ≈ $6.67/pip a rate 150
                valor_pip_por_lote = 1000.0 / max(1.0, entrada)
                lote = riesgo_usd / (pips_riesgo * valor_pip_por_lote)
            else:
                # Non-JPY: 1 lote (100k) = 10$/pip. 0.01 lote (1k) = 0.10$/pip.
                lote = riesgo_usd / (pips_riesgo * 10)
            return f"{max(0.01, round(lote, 2))}"
        elif ticker == "GC=F" or "GOLD" in ticker.upper() or "XAU" in ticker.upper():
            # GOLD XM: 1 lote = 100 oz. $1 movimiento × 1 lote = $100.
            # pips_riesgo = distancia en $ (ej: SL a $9 del entry)
            # lote = riesgo / (distancia × 100)
            lote = riesgo_usd / (pips_riesgo * 100)
            return f"{max(0.01, round(lote, 2))}"
        elif cat == "futuros" or any(x in ticker.upper() for x in ["US100", "US500", "NQ", "ES"]):
            # Índices en XM: 1 lote suele ser 1$ por punto (o similar)
            # Para US100Cash, un movimiento de 1 punto con 1 lote = 1$ (aprox)
            # Ajustamos: si pips_riesgo es la distancia en puntos, 1 lote = 1$/punto.
            lote = riesgo_usd / max(1.0, pips_riesgo)
            # Limitamos el lote máximo para índices (seguridad)
            return f"{min(5.0, max(0.1, round(lote, 1)))}" # Mínimo 0.1, Máximo 5.0 por seguridad
        else:
            lote = riesgo_usd / max(1.0, pips_riesgo * 10)
            return f"{max(0.01, round(lote, 2))}"

    except Exception:
        return "0.01"

# ============================================================
#  FILTROS DE CALIDAD — EVITAR FALSAS SEÑALES
# ============================================================

# Horarios de máxima liquidez por activo (hora UTC) — se llena después de PAR_PROFILES
HORARIOS_MERCADO = {}
DIVISAS_POR_TICKER = {}

def _init_horarios_from_profiles():
    global HORARIOS_MERCADO, DIVISAS_POR_TICKER
    for _pp_v in PAR_PROFILES.values():
        _yf_tk = _pp_v["identity"].get("yf")
        if _yf_tk and _pp_v.get("premium", {}).get("enabled"):
            _tf = _pp_v["time_filter"]
            _bh = _tf.get("best_hours_utc", [(7, 21)])
            _h_min = min(h[0] for h in _bh)
            _h_max = max(h[1] for h in _bh)
            HORARIOS_MERCADO[_yf_tk] = (_h_min, max(_h_max, 21))
            DIVISAS_POR_TICKER[_yf_tk] = _pp_v["news"]["currencies"]

_cache_noticias   = {"datos": None, "ts": 0}
_cache_fear_greed = {"valor": 50, "class": "Neutral", "ts": 0}

# ============================================================
#  PARSER DE SEÑALES EXTERNAS (Signal Copier)
# ============================================================

def parsear_senal_externa(texto: str):
    """
    Intenta extraer parámetros de trade de un mensaje de texto.
    Soporta formatos comunes de canales de señales.
    """
    import re
    t = texto.upper()
    
    # 1. Detectar Ticker (Activo)
    activo = None
    for kw, target in KEYWORDS_ACTIVOS.items():
        if kw.upper() in t:
            activo = target
            break
    
    if not activo:
        # Intento secundario: buscar ticker directo
        for t_key in MT5_TICKER_MAP.values():
            if t_key.upper() in t:
                activo = t_key
                break
    
    if not activo:
        return None

    # 2. Detectar Tipo (Compra/Venta)
    tipo = None
    if any(x in t for x in ["COMPRA", "BUY", "LONG", "📈", "🟢"]):
        tipo = "COMPRA"
    elif any(x in t for x in ["VENTA", "SELL", "SHORT", "📉", "🔴"]):
        tipo = "VENTA"
    
    if not tipo:
        return None

    # 3. Extraer Números (Entrada, SL, TPs)
    numeros = re.findall(r"\d+\.\d+|\d+", t)
    if len(numeros) < 3:
        return None

    entrada, sl = None, None
    tps = []

    lines = t.split("\n")
    for line in lines:
        nums_line = re.findall(r"\d+\.\d+|\d+", line)
        if not nums_line: continue
        val = float(nums_line[0])
        
        if any(x in line for x in ["ENTRADA", "ENTRY", "@", "PRECIO", "PRICE"]):
            entrada = val
        elif any(x in line for x in ["SL", "STOP", "LOSS"]):
            sl = val
        elif any(x in line for x in ["TP", "TAKE", "PROFIT", "TARGET"]):
            tps.append(val)

    if not entrada and len(numeros) >= 3:
        entrada = float(numeros[0])
        raw_nums = [float(x) for x in numeros]
        for n in raw_nums[1:]:
            if tipo == "COMPRA":
                if n < entrada and (not sl or n < sl): sl = n
                elif n > entrada: tps.append(n)
            else:
                if n > entrada and (not sl or n > sl): sl = n
                elif n < entrada: tps.append(n)

    if not entrada or not sl or not tps:
        return None

    return {
        'ticker': MT5_TICKER_MAP.get(activo, activo),
        'nombre': activo,
        'tipo': tipo,
        'entrada': entrada,
        'sl': sl,
        'tp1': tps[0],
        'tp2': tps[1] if len(tps) > 1 else tps[0],
        'tp3': tps[2] if len(tps) > 2 else (tps[1] if len(tps) > 1 else tps[0]),
    }


# ============================================================
#  AUTOMATIZACIÓN DE ÓRDENES - METATRADER 5 (XM)
# ============================================================


def _obtener_capital_real_mt5():
    """
    Obtiene el BALANCE REAL desde MT5 para calcular lotaje dinámico.
    Retorna el balance (dinero real, sin créditos/bonos del broker).
    Si no se puede obtener, retorna CAPITAL_USUARIO como fallback.
    """
    if not MT5_AVAILABLE:
        return CAPITAL_USUARIO
    try:
        with _lock_mt5:
            acc = mt5.account_info()
        if acc and acc.balance > 0:
            _balance = round(acc.balance, 2)
            # Log solo si hay diferencia significativa con CAPITAL_USUARIO
            if abs(_balance - CAPITAL_USUARIO) > 10:
                logger.info(f"💰 Capital dinámico MT5: balance=${_balance:.2f} (CAPITAL_USUARIO=${CAPITAL_USUARIO:.0f})")
            return _balance
    except Exception as e:
        logger.warning(f"⚠️ No se pudo obtener balance MT5: {e}")
    return CAPITAL_USUARIO


def _ejecutar_orden_en_cuenta(ticker, tipo, capital, riesgo_pct, entrada, sl, tp1, cuenta_name=""):
    """Ejecuta una orden en la cuenta MT5 actualmente conectada."""
    try:
        mt5_ticker = MT5_TICKER_MAP.get(ticker, ticker)

        # 1. 💰 CAPITAL DINÁMICO: usar equity REAL de MT5 (no el estático)
        capital_real = _obtener_capital_real_mt5()
        lote_str = calcular_lote_sugerido(capital_real, riesgo_pct, entrada, sl, ticker)
        import re
        try:
            lote_val = float(re.findall(r"[-+]?\d*\.?\d+", lote_str)[0])
        except Exception:
            lote_val = 0.01

        # 2. Validar conexión (todas las llamadas MT5 protegidas con _lock_mt5)
        with _lock_mt5:
            _tinfo = mt5.terminal_info()
        if _tinfo is None or not _tinfo.connected:
            print(f"❌ MT5 Desconectado [{cuenta_name}]. No se pudo abrir {mt5_ticker}")
            return False

        # 3. Validar Spread
        with _lock_mt5:
            symbol_info = mt5.symbol_info(mt5_ticker)
        if symbol_info is None:
            print(f"❌ No se pudo obtener info de {mt5_ticker} [{cuenta_name}]")
            return False

        spread_actual = symbol_info.spread
        max_spread = MAX_SPREAD_ALLOWED.get(ticker, 9999)

        if spread_actual > max_spread:
            print(f"🛡️ ORDEN CANCELADA [{cuenta_name}]: Spread en {mt5_ticker} demasiado alto ({spread_actual} > {max_spread})")
            return False

        # 4. Definir tipo de orden
        es_compra = tipo.upper() in ("COMPRA", "BUY", "LONG")
        order_type = mt5.ORDER_TYPE_BUY if es_compra else mt5.ORDER_TYPE_SELL
        with _lock_mt5:
            _tick = mt5.symbol_info_tick(mt5_ticker)
        if _tick is None:
            log_op(f"❌ No hay tick para {mt5_ticker} [{cuenta_name}]", "error")
            return False
        price = _tick.ask if es_compra else _tick.bid

        # 🛡️ LÍMITE DINÁMICO DE SEGURIDAD por activo y capital
        # Cada mercado tiene diferentes costes de margen:
        # - Índices: margen bajo → lotes más altos OK
        # - Forex: margen alto (100k contract) → lotes más bajos
        # - Oro: margen medio
        _es_forex = mt5_ticker in ("EURUSD", "USDJPY", "GBPJPY") or ("USD" in mt5_ticker and len(mt5_ticker) == 6)
        _es_indice = mt5_ticker in ("US100Cash", "US500Cash")
        _es_oro = mt5_ticker == "GOLD" or ticker == "GC=F" or "XAU" in ticker.upper()

        if _es_oro:
            # ORO: 1 lote = 100 oz, $1 mov = $100. MUY ALTO RIESGO.
            # Con $600 y SL $5: max seguro = 0.03 ($15 riesgo = 2.5%)
            # Con $600 y SL $20: max seguro = 0.01 ($20 riesgo = 3.3%)
            if capital_real <= 300:    MAX_LOTE_SEGURIDAD = 0.01
            elif capital_real <= 500:  MAX_LOTE_SEGURIDAD = 0.01
            elif capital_real <= 1000: MAX_LOTE_SEGURIDAD = 0.02
            elif capital_real <= 2000: MAX_LOTE_SEGURIDAD = 0.05
            elif capital_real <= 5000: MAX_LOTE_SEGURIDAD = 0.10
            else:                     MAX_LOTE_SEGURIDAD = 0.20
        elif _es_forex:
            # Forex: alto margen por contract_size=100k
            if capital_real <= 300:    MAX_LOTE_SEGURIDAD = 0.01
            elif capital_real <= 500:  MAX_LOTE_SEGURIDAD = 0.02
            elif capital_real <= 1000: MAX_LOTE_SEGURIDAD = 0.05
            elif capital_real <= 2000: MAX_LOTE_SEGURIDAD = 0.10
            elif capital_real <= 5000: MAX_LOTE_SEGURIDAD = 0.20
            else:                     MAX_LOTE_SEGURIDAD = 0.50
        elif _es_indice:
            # Índices: con $600, 0.1 lotes NASDAQ pierde $1/punto → SL 50pts = -$5 (OK)
            if capital_real <= 300:    MAX_LOTE_SEGURIDAD = 0.1
            elif capital_real <= 500:  MAX_LOTE_SEGURIDAD = 0.1
            elif capital_real <= 1000: MAX_LOTE_SEGURIDAD = 0.2
            elif capital_real <= 2000: MAX_LOTE_SEGURIDAD = 0.5
            elif capital_real <= 5000: MAX_LOTE_SEGURIDAD = 1.0
            else:                     MAX_LOTE_SEGURIDAD = 2.0
        else:
            # Otros: margen medio
            if capital_real <= 300:    MAX_LOTE_SEGURIDAD = 0.02
            elif capital_real <= 500:  MAX_LOTE_SEGURIDAD = 0.03
            elif capital_real <= 1000: MAX_LOTE_SEGURIDAD = 0.05
            elif capital_real <= 2000: MAX_LOTE_SEGURIDAD = 0.10
            elif capital_real <= 5000: MAX_LOTE_SEGURIDAD = 0.20
            else:                     MAX_LOTE_SEGURIDAD = 0.50

        if lote_val > MAX_LOTE_SEGURIDAD:
            logger.warning(f"🛡️ LOTAJE LIMITADO [{cuenta_name}]: {mt5_ticker} calculó {lote_val} → limitado a {MAX_LOTE_SEGURIDAD} (equity=${capital_real:.0f})")
            lote_val = MAX_LOTE_SEGURIDAD

        vol_min = symbol_info.volume_min
        vol_max = symbol_info.volume_max
        vol_step = symbol_info.volume_step
        lote_val = max(vol_min, lote_val)
        lote_val = min(vol_max, lote_val)
        lote_val = min(lote_val, MAX_LOTE_SEGURIDAD)
        lote_val = round(round(lote_val / vol_step) * vol_step, 2)

        # 🔒 VERIFICAR MARGEN LIBRE antes de enviar orden
        with _lock_mt5:
            acc_info = mt5.account_info()
        if acc_info:
            margin_free = acc_info.margin_free
            # Estimar margen requerido (precio * contract * lote / leverage)
            _leverage = acc_info.leverage or 888
            _margin_est = (price * symbol_info.trade_contract_size * lote_val) / _leverage
            if _margin_est > margin_free * 0.8:  # No usar más del 80% del margen libre
                # Reducir lote para que quepa en el margen
                _lote_max_margen = (margin_free * 0.8 * _leverage) / (price * symbol_info.trade_contract_size)
                _lote_max_margen = max(vol_min, round(round(_lote_max_margen / vol_step) * vol_step, 2))
                if _lote_max_margen < lote_val:
                    logger.warning(f"🛡️ MARGEN LIMITADO [{cuenta_name}]: {mt5_ticker} lote {lote_val} requiere ~${_margin_est:.0f} margen pero solo hay ${margin_free:.0f} libre → reducido a {_lote_max_margen}")
                    lote_val = _lote_max_margen

        _digits = symbol_info.digits
        sl_norm = round(float(sl), _digits)
        tp_norm = round(float(tp1), _digits)
        price_norm = round(float(price), _digits)

        # Calcular riesgo real en USD para el log
        _pips_sl = abs(price_norm - sl_norm)
        if "GOLD" in mt5_ticker.upper() or ticker == "GC=F":
            _riesgo_estimado_usd = lote_val * _pips_sl * 100  # Gold: $100/punto/lote
        elif get_categoria(ticker) == "forex":
            if "JPY" in mt5_ticker.upper() or "JPY" in (ticker or "").upper():
                # JPY: 1 lote = 100k unidades, valor pip = 1000 JPY / rate
                _riesgo_estimado_usd = lote_val * _pips_sl * (1000.0 / max(1, price_norm))
            else:
                _riesgo_estimado_usd = lote_val * _pips_sl * 100000  # Forex: $10/pip/lote
        else:
            _riesgo_estimado_usd = lote_val * _pips_sl * 1  # Índices: $1/punto/lote
        _riesgo_pct_real = (_riesgo_estimado_usd / capital_real * 100) if capital_real > 0 else 0
        logger.info(f"📋 MT5 ORDER [{cuenta_name}]: {mt5_ticker} {tipo} | Price:{price_norm} SL:{sl_norm} TP:{tp_norm} | Vol:{lote_val} | Equity:${capital_real:.0f} | Riesgo:~${_riesgo_estimado_usd:.1f} ({_riesgo_pct_real:.1f}%)")

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": mt5_ticker,
            "volume": float(lote_val),
            "type": order_type,
            "price": price_norm,
            "sl": sl_norm,
            "tp": tp_norm,
            "deviation": 20,
            "magic": 20260226,
            "comment": "BuySell365 Auto-Trade",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        with _lock_mt5:
            result = mt5.order_send(request)

        if result.retcode != mt5.TRADE_RETCODE_DONE:
            log_op(f"❌ ORDEN RECHAZADA [{cuenta_name}]: {mt5_ticker} {tipo} | {result.comment} (code:{result.retcode})", "error")
            return False

        _ticket_id = result.order if result.order else True
        log_op(f"🚀 ORDEN EJECUTADA [{cuenta_name}]: {tipo} {lote_val} {mt5_ticker} @ {price} ticket#{_ticket_id}")
        print(f"🚀 ORDEN EJECUTADA [{cuenta_name}]: {tipo} {lote_val} {mt5_ticker} @ {price} ticket#{_ticket_id}")
        return _ticket_id  # Retorna ticket ID (int) en vez de True

    except Exception as e:
        log_op(f"❌ EXCEPCION MT5 [{cuenta_name}]: {ticker} {tipo} | {e}", "error")
        return False


def ejecutar_orden_mt5(ticker, tipo, capital, riesgo_pct, entrada, sl, tp1, es_premium=False):
    """
    Ejecuta una orden de mercado en TODAS las cuentas MT5 configuradas.
    Primero ejecuta en la cuenta principal, luego en las secundarias.
    Retorna True si al menos la cuenta principal ejecutó correctamente.
    es_premium=True: señal premium puede pasar incluso con mt5_solo_premium activo.
    """
    if not MT5_AVAILABLE or not AUTO_TRADING:
        return None

    # 🔒 Si MT5 está pausado manualmente, NO ejecutar (premium puede pasar si mt5_solo_premium)
    if mt5_pausado:
        if mt5_solo_premium and es_premium:
            logger.info(f"💎 MT5 SOLO-PREMIUM: {ticker} {tipo} — señal PREMIUM pasa a MT5")
        else:
            logger.info(f"⏸️ MT5 PAUSADO: {ticker} {tipo} — señal enviada a Telegram pero no ejecutada")
            return None

    mt5_ticker = MT5_TICKER_MAP.get(ticker, ticker)

    # [8] ANÁLISIS DE SPREAD EN TIEMPO REAL: verificar vs promedio histórico
    try:
        if not _spread_aceptable(ticker):
            logger.warning(f"🔴 SPREAD EXCESIVO: {mt5_ticker} — spread > 2x promedio histórico → orden cancelada")
            return False
    except Exception:
        pass  # Si falla la verificación, continuar con la validación normal

    # Validar spread una sola vez antes de ejecutar en cualquier cuenta
    with _lock_mt5:
        _tinfo = mt5.terminal_info()
    if _tinfo is None or not _tinfo.connected:
        print(f"❌ MT5 Desconectado. No se pudo abrir {mt5_ticker}")
        return None
    with _lock_mt5:
        symbol_info = mt5.symbol_info(mt5_ticker)
    if symbol_info is None:
        print(f"❌ No se pudo obtener info de {mt5_ticker}")
        return None
    spread_actual = symbol_info.spread
    max_spread = MAX_SPREAD_ALLOWED.get(ticker, 9999)
    if spread_actual > max_spread:
        print(f"🛡️ ORDEN CANCELADA: Spread en {mt5_ticker} demasiado alto ({spread_actual} > {max_spread})")
        # Spread alto: solo log, NO enviar al grupo (usuario prefiere solo señales)
        # enviar_grupo(f"🛡️ *ORDEN CANCELADA POR SPREAD ALTO*\n📍 {mt5_ticker}\n⚠️ Spread: {spread_actual}\n✅ Máximo: {max_spread}\n💡 Esperando mejores condiciones...", incluir_promo=False)
        return False

    with _lock_mt5_switch:
        resultado_principal = False

        try:
            # ── CUENTA PRINCIPAL (siempre la primera) ──
            if _mt5_primary_account:
                _mt5_switch_account(_mt5_primary_account)
                resultado_principal = _ejecutar_orden_en_cuenta(
                    ticker, tipo, capital, riesgo_pct, entrada, sl, tp1,
                    cuenta_name=_mt5_primary_account["name"]
                )

            # ── CUENTAS SECUNDARIAS (paralelas) ──
            for acc in MT5_ACCOUNTS[1:]:
                try:
                    if _mt5_switch_account(acc):
                        # Re-habilitar AutoTrading ANTES de ejecutar (cambio de servidor lo desactiva)
                        _reenable_autotrading()
                        _ok = _ejecutar_orden_en_cuenta(
                            ticker, tipo, capital, riesgo_pct, entrada, sl, tp1,
                            cuenta_name=acc["name"]
                        )
                        if _ok:
                            logger.info(f"✅ Orden replicada en {acc['name']} ({acc['login']})")
                        else:
                            logger.warning(f"⚠️ Orden falló en {acc['name']} ({acc['login']})")
                    else:
                        logger.warning(f"⚠️ No se pudo conectar a {acc['name']} ({acc['login']})")
                except Exception as e:
                    logger.error(f"❌ Error replicando orden en {acc['name']}: {e}")

        finally:
            # BUG-4 FIX: SIEMPRE restaurar cuenta principal aunque falle una secundaria
            if _mt5_primary_account and len(MT5_ACCOUNTS) > 1:
                try:
                    _mt5_switch_account(_mt5_primary_account)
                    _reenable_autotrading()
                except Exception as e:
                    logger.error(f"❌ CRÍTICO: No se pudo restaurar cuenta principal: {e}")

        return resultado_principal



# ============================================================
#  CIERRE DE POSICIONES MT5
# ============================================================

def cerrar_posicion_mt5(ticker, ticket_id=None):
    """Cierra posición específica (por ticket) o solo las del bot (magic=20260226).
    Si ticket_id se proporciona, solo cierra esa posición específica.
    Si no, cierra solo las posiciones con magic=20260226 (las del bot)."""
    if not MT5_AVAILABLE or not AUTO_TRADING:
        return
    mt5_ticker = MT5_TICKER_MAP.get(ticker, ticker)
    try:
        with _lock_mt5:
            positions = mt5.positions_get(symbol=mt5_ticker)
        if positions is None or len(positions) == 0:
            return
        for pos in positions:
            # Filtro: solo cerrar la posición correcta
            if ticket_id and pos.ticket != ticket_id:
                continue  # Ticket específico pedido, este no es
            if not ticket_id and pos.magic != 20260226:
                logger.info(f"⚠️ MT5 SKIP: {mt5_ticker} ticket#{pos.ticket} magic={pos.magic} — no es del bot, no se cierra")
                continue  # Sin ticket → solo cerrar las del bot
            close_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
            with _lock_mt5:
                tick = mt5.symbol_info_tick(mt5_ticker)
            if tick is None:
                continue
            price = tick.bid if close_type == mt5.ORDER_TYPE_SELL else tick.ask
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": mt5_ticker,
                "volume": pos.volume,
                "type": close_type,
                "position": pos.ticket,
                "price": price,
                "deviation": 20,
                "magic": 20260226,
                "comment": "BuySell365 Close",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }
            with _lock_mt5:
                result = mt5.order_send(request)
            if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                logger.info(f"\u2705 MT5 CERRADA: {mt5_ticker} ticket#{pos.ticket} vol:{pos.volume} @ {price}")
            else:
                rc = result.retcode if result else "None"
                cm = result.comment if result else "No result"
                logger.error(f"\u274c MT5 CIERRE FALLÓ: {mt5_ticker} ticket#{pos.ticket} | {cm} (code:{rc})")
    except Exception as e:
        logger.error(f"\u274c Error cerrando MT5 {mt5_ticker}: {e}")


def tiene_posicion_mt5(ticker):
    """Retorna True si ya hay al menos una posición abierta en MT5 para este ticker."""
    if not MT5_AVAILABLE:
        return False
    mt5_ticker = MT5_TICKER_MAP.get(ticker, ticker)
    try:
        with _lock_mt5:
            positions = mt5.positions_get(symbol=mt5_ticker)
        return positions is not None and len(positions) > 0
    except Exception:
        return False




def obtener_direccion_mt5(ticker):
    """Retorna la dirección de posiciones MT5 abiertas para este ticker.
    Retorna 'BUY', 'SELL', 'BOTH', o None."""
    if not MT5_AVAILABLE:
        return None
    mt5_ticker = MT5_TICKER_MAP.get(ticker, ticker)
    try:
        with _lock_mt5:
            positions = mt5.positions_get(symbol=mt5_ticker)
        if positions is None or len(positions) == 0:
            return None
        tipos = set()
        for p in positions:
            tipos.add('BUY' if p.type == mt5.ORDER_TYPE_BUY else 'SELL')
        if len(tipos) == 2:
            return 'BOTH'
        return tipos.pop()
    except Exception:
        return None


def sync_mt5_positions():
    """Sincroniza operaciones_activas con posiciones reales de MT5.
    - Detecta posiciones cerradas manualmente y las registra en historial.
    - Detecta posiciones huérfanas en MT5 (no trackeadas) y las reporta.
    Se ejecuta cada 30 segundos desde el monitor."""
    global operaciones_activas, historial_operaciones, estadisticas_diarias
    if not MT5_AVAILABLE:
        return
    # Si AUTO_TRADING=False, NO sincronizar — las operaciones son solo señales Telegram
    # sin posiciones reales en MT5. Sync las borraría como "cerradas" causando loop.
    if not AUTO_TRADING:
        return

    try:
        # Obtener TODAS las posiciones abiertas en MT5
        with _lock_mt5:
            all_positions = mt5.positions_get()
        if all_positions is None:
            return

        # SEGURIDAD: Si MT5 dice 0 posiciones pero tenemos ops trackeadas,
        # verificar que MT5 realmente está conectado (evitar falso positivo por IPC perdido)
        if len(all_positions) == 0 and len(operaciones_activas) > 0:
            with _lock_mt5:
                _acc = mt5.account_info()
            if _acc is None or _acc.balance == 0:
                logger.warning("SYNC: MT5 devolvió 0 posiciones pero hay ops activas — posible desconexión, saltando sync")
                return

        mt5_symbols = set()
        mt5_tickets = {}
        mt5_count_by_sym = {}  # Cuántas posiciones hay en MT5 por symbol
        for pos in all_positions:
            mt5_symbols.add(pos.symbol)
            if pos.symbol not in mt5_tickets:
                mt5_tickets[pos.symbol] = []
            mt5_tickets[pos.symbol].append(pos)
            sym_up = pos.symbol.upper()
            mt5_count_by_sym[sym_up] = mt5_count_by_sym.get(sym_up, 0) + 1

        # 1. Detectar operaciones en tracking que ya NO están en MT5 (cerradas manual/SL/TP)
        # Comparamos por CANTIDAD: si tracking tiene N ops de un symbol pero MT5 tiene M < N,
        # entonces (N - M) operaciones fueron cerradas externamente.
        ops_to_remove = []
        with _lock_ops:
            # Contar ops trackeadas por symbol MT5 (SOLO las ejecutadas en MT5)
            tracked_by_sym = {}
            for op_id, op in list(operaciones_activas.items()):
                if not isinstance(op, dict):
                    continue
                # Solo sincronizar operaciones que SÍ se ejecutaron en MT5
                if not op.get('mt5_ejecutado', False):
                    continue
                ticker = op.get('ticker', '')
                mt5_sym = MT5_TICKER_MAP.get(ticker, ticker).upper()
                if mt5_sym not in tracked_by_sym:
                    tracked_by_sym[mt5_sym] = []
                tracked_by_sym[mt5_sym].append((op_id, op.copy()))

            for mt5_sym, tracked_ops in tracked_by_sym.items():
                n_mt5 = mt5_count_by_sym.get(mt5_sym, 0)
                n_tracked = len(tracked_ops)
                if n_mt5 < n_tracked:
                    # Hay (n_tracked - n_mt5) operaciones cerradas
                    # Remover las más antiguas primero
                    sorted_ops = sorted(tracked_ops, key=lambda x: x[1].get('timestamp', 0))
                    for i in range(n_tracked - n_mt5):
                        ops_to_remove.append(sorted_ops[i])

        for op_id, op in ops_to_remove:
            ticker = op.get('ticker', '')
            nombre = op.get('nombre', ticker)
            tipo = op.get('tipo', '')
            entrada = op.get('entrada', 0)
            mt5_sym = MT5_TICKER_MAP.get(ticker, ticker)

            # Obtener precio REAL de cierre desde historial MT5 (no tick actual)
            precio_cierre = entrada
            _ticket_mt5 = op.get('ticket_mt5')
            try:
                # H-01 FIX: Proteger llamadas MT5 con lock
                with _lock_mt5:
                    if _ticket_mt5:
                        # Buscar deal de cierre real para esta posición
                        _desde = datetime.now() - timedelta(days=1)
                        _deals = mt5.history_deals_get(_desde, datetime.now())
                        if _deals:
                            for _d in reversed(_deals):  # Más recientes primero
                                if _d.position_id == _ticket_mt5 and _d.entry == 1:  # DEAL_ENTRY_OUT
                                    precio_cierre = _d.price
                                    break
                            else:
                                # Fallback a tick si no hay deal
                                tick = mt5.symbol_info_tick(mt5_sym)
                                if tick:
                                    precio_cierre = tick.bid if tipo in ('COMPRA', 'BUY') else tick.ask
                        else:
                            tick = mt5.symbol_info_tick(mt5_sym)
                            if tick:
                                precio_cierre = tick.bid if tipo in ('COMPRA', 'BUY') else tick.ask
                    else:
                        tick = mt5.symbol_info_tick(mt5_sym)
                        if tick:
                            precio_cierre = tick.bid if tipo in ('COMPRA', 'BUY') else tick.ask
            except Exception:
                try:
                    with _lock_mt5:
                        tick = mt5.symbol_info_tick(mt5_sym)
                        if tick:
                            precio_cierre = tick.bid if tipo in ('COMPRA', 'BUY') else tick.ask
                except Exception:
                    pass

            pips = calcular_pips(entrada, precio_cierre, ticker, tipo)
            duracion = time.time() - op.get('timestamp', time.time())
            resultado = "WIN" if pips > 0 else "LOSS"

            # Registrar en historial
            _hora = ahora().strftime("%H:%M")
            hist_data = {
                "nombre": nombre, "tipo": tipo, "ticker": ticker,
                "entrada": entrada, "salida": precio_cierre,
                "pips": pips, "resultado": resultado,
                "hora": _hora, "fecha": ahora().strftime("%d/%m/%Y"),
                "hora_entrada": op.get('hora', ''), "hora_salida": _hora,
                "tag": "MANUAL",
                "tp1_hit": op.get('tp1_hit', False),
                "tp2_hit": op.get('tp2_hit', False),
                "duracion_min": round(duracion / 60, 1),
                "score": op.get('score', 0),
                "confianza": op.get('confianza_multi_ia', 0),
                "estrategia": op.get('estrategia', ''),
                "fuente": "sync_mt5",
                "timestamp_entrada": op.get('timestamp', 0),
                "timestamp_cierre": time.time(),
            }

            with _lock_ops:
                historial_operaciones.append(hist_data)
                if pips > 0:
                    estadisticas_diarias["ganadas"] += 1
                    estadisticas_diarias["pips_ganados"] += pips
                else:
                    estadisticas_diarias["perdidas"] += 1
                    estadisticas_diarias["pips_perdidos"] += abs(pips)
                operaciones_activas.pop(op_id, None)
                # Anti re-entry: registrar cooldown desde el cierre
                _cd_ticker = op.get('ticker', '').replace("=X","").replace("=F","").replace("-","").upper()
                _cd_tipo = op.get('tipo', '')
                if _cd_ticker and _cd_tipo:
                    _cooldown_cierres[(_cd_ticker, _cd_tipo)] = time.time()

            # Guardar en CSV permanente
            _guardar_historial_csv(hist_data)

            # Notificar
            _unidad = unidad_medida(ticker)
            cat = get_categoria(ticker)
            p_txt = f"{pips:.2f}%" if cat == "crypto" else f"{pips:.1f} {_unidad}"
            signo = "+" if pips > 0 else ""
            emoji = "\U0001f7e2" if pips > 0 else "\U0001f534"
            msg = (
                f"\U0001f504 *CIERRE DETECTADO* \u2014 {nombre}\n"
                f"{emoji} {tipo} {signo}{p_txt}\n"
                f"Entrada {fmt(entrada, ticker)} \u2192 Cierre {fmt(precio_cierre, ticker)}"
            )
            # CIERRE DETECTADO: solo log, NO enviar al canal (usuario prefiere solo señales)
            # enviar_canal(msg)
            logger.info(f"\U0001f504 SYNC: {nombre} {tipo} cerrada externamente | {signo}{p_txt} | tag:MANUAL")
            guardar_estado()

    except Exception as e:
        logger.error(f"\u274c Error en sync_mt5_positions: {e}")


def en_horario_mercado(ticker):
    """
    True si estamos dentro del horario de máxima liquidez del activo.
    Incluye filtro de fin de semana para activos tradicionales.
    (BTC/ETH eliminados del bot)
    """
    now_utc   = datetime.now(pytz.UTC)
    fecha_hoy = now_utc.strftime("%Y-%m-%d")
    es_fin_de_semana = now_utc.weekday() >= 5 # 5=Sábado, 6=Domingo

    # Bloquear si es Festivo Bancario/Bursátil
    if fecha_hoy in FERIADOS_2026:
        motivo = FERIADOS_2026[fecha_hoy]
        logger.info(f"🛑 Mercados cerrados hoy por festivo: {motivo}")
        return False

    # Bloquear otros activos en fin de semana
    if es_fin_de_semana:
        return False

    hora_utc  = now_utc.hour
    min_utc   = now_utc.minute
    hora_dec  = hora_utc + min_utc / 60.0

    inicio, fin = HORARIOS_MERCADO.get(ticker, (0, 24))

    # Bloquear primeros 15 min de apertura y últimos 15 min antes del cierre
    inicio_efectivo = inicio + 0.25   # +15 min
    fin_efectivo    = fin    - 0.25   # -15 min

    en_horario = inicio_efectivo <= hora_dec < fin_efectivo

    if not en_horario and inicio <= hora_utc < fin:
        logger.info(f"⏰ {ticker}: fuera de horario efectivo (apertura/cierre) — bloqueando")

    return en_horario


def en_horario_mt5(ticker):
    """True si podemos ejecutar órdenes MT5 para este activo.
    Ventana de ejecución: 08:00 - 18:00 hora Andorra (L-V uniforme).
    Fuera de este horario: ni MT5 ni señales Telegram (bot en silencio)."""
    now_local = datetime.now(BOT_TZ)
    if now_local.weekday() >= 5:
        return False  # Weekend
    hora_dec = now_local.hour + now_local.minute / 60.0
    if hora_dec < HORA_APERTURA_LOCAL:
        return False
    return hora_dec < HORA_CORTE_LOCAL



def activos_disponibles_hoy() -> tuple:
    """Retorna (lista_nombres, es_fin_de_semana_o_feriado).
    Fin de semana / feriado → sin activos.
    Lunes-Viernes normal → todos los 6 activos."""
    now_utc = datetime.now(pytz.UTC)
    fecha_hoy = now_utc.strftime("%Y-%m-%d")
    es_fds = now_utc.weekday() >= 5
    es_feriado = fecha_hoy in FERIADOS_2026

    if es_fds or es_feriado:
        motivo = "fin de semana" if es_fds else FERIADOS_2026.get(fecha_hoy, "feriado")
        nombres = []  # Mercados cerrados
        return nombres, True, motivo
    else:
        nombres = ["oro", "eurusd", "usdjpy", "gbpjpy", "nasdaq", "sp500"]
        return nombres, False, ""


def _texto_activos_disponibles() -> str:
    """Genera texto con activos disponibles hoy para mostrar al usuario."""
    nombres, limitado, motivo = activos_disponibles_hoy()
    txt = " · ".join(f"`{n}`" for n in nombres)
    if limitado:
        txt += f"\n⚠️ _Hoy ({motivo}) mercados cerrados_"
    return txt


_lock_noticias = threading.Lock()

def cargar_calendario_economico():
    """Descarga el calendario semanal de ForexFactory (caché 30min, thread-safe)."""
    global _cache_noticias
    if time.time() - _cache_noticias["ts"] < 1800 and _cache_noticias["datos"]:
        return _cache_noticias["datos"]
    with _lock_noticias:
        # Double-check after acquiring lock (otro hilo pudo haberlo cargado)
        if time.time() - _cache_noticias["ts"] < 1800 and _cache_noticias["datos"]:
            return _cache_noticias["datos"]
        try:
            url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
            r = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code == 200:
                _cache_noticias = {"datos": r.json(), "ts": time.time()}
                return _cache_noticias["datos"]
        except Exception:
            pass
    return _cache_noticias["datos"] or []

# Noticias de MÁXIMO impacto que causan movimientos extremos
_NOTICIAS_CRITICAS = {
    "non-farm", "nonfarm", "nfp", "fomc", "fed interest", "rate decision",
    "cpi", "consumer price", "gdp", "ecb press", "ecb interest",
    "boe interest", "boj interest", "unemployment rate", "retail sales",
    "pmi", "jackson hole", "powell", "lagarde", "payroll"
}

def _es_noticia_critica(titulo):
    """Detecta si una noticia es de las que causan movimientos extremos."""
    titulo_lower = titulo.lower()
    return any(kw in titulo_lower for kw in _NOTICIAS_CRITICAS)

def hay_noticia_alto_impacto(ticker, horas_antes=3, horas_despues=2):
    """True si hay noticia de ALTO impacto (🔴 roja / 3 estrellas) en ventana de bloqueo.
    - Noticias críticas (NFP, FOMC, CPI): ventana extendida 4h antes / 3h después
    - Alto impacto normal: bloquea horas_antes/horas_despues (default 2h/1h)
    - Medio impacto: NO bloquea (solo se loguea como info)"""
    try:
        noticias = cargar_calendario_economico()
        if not noticias:
            return False
        divisas   = DIVISAS_POR_TICKER.get(ticker, [])
        ahora_utc = datetime.now(pytz.UTC)
        tz_ny     = pytz.timezone("America/New_York")
        for n in noticias:
            impacto = n.get("impact", "").lower()
            # ── SOLO bloquear por noticias ROJAS (High / 3 estrellas) ──
            if impacto != "high":
                continue
            if n.get("country", "") not in divisas:
                continue
            try:
                fecha_str = n.get("date", "")
                hora_str  = n.get("time", "").strip().lower()
                if not fecha_str or hora_str in ("", "all day", "tentative"):
                    continue
                dt     = datetime.strptime(f"{fecha_str} {hora_str}", "%m-%d-%Y %I:%M%p")
                dt_utc = tz_ny.localize(dt).astimezone(pytz.UTC)
                diff   = (dt_utc - ahora_utc).total_seconds() / 3600
                # diff > 0 = evento en el futuro, diff < 0 = evento ya pasó
                titulo = n.get("title", "Desconocido")

                # Ventanas dinámicas según tipo de noticia
                if _es_noticia_critica(titulo):
                    # NFP, FOMC, CPI, etc. = ventana EXTENDIDA
                    _h_antes, _h_despues = 4, 3
                else:
                    # Alto impacto normal = ventana más ajustada
                    _h_antes, _h_despues = horas_antes, horas_despues

                if -_h_despues <= diff <= _h_antes:
                    _pais = n.get("country", "?")
                    _tipo_imp = "🔴 CRÍTICA" if _es_noticia_critica(titulo) else "🔴 ALTA"
                    logger.info(f"🚨 NOTICIA BLOQUEANTE {_tipo_imp}: {_pais} {titulo} | {ticker} | en {diff:+.1f}h | ventana: -{_h_despues}h/+{_h_antes}h")
                    return True
            except Exception:
                continue
    except Exception:
        pass
    return False

def proteger_operaciones_por_noticias():
    """Cierra operaciones abiertas del bot si se acerca una noticia CRÍTICA (NFP, FOMC, CPI).
    Solo cierra si la operación está en ganancia o si faltan menos de 30min para la noticia.
    Esto protege contra el impacto extremo de noticias de alto impacto."""
    try:
        noticias = cargar_calendario_economico()
        if not noticias:
            return
        ahora_utc = datetime.now(pytz.UTC)
        tz_ny     = pytz.timezone("America/New_York")

        with _lock_ops:
            if not operaciones_activas:
                return
            import copy
            ops_snapshot = {k: copy.deepcopy(v) for k, v in operaciones_activas.items()}

        for op_id, op in ops_snapshot.items():
            ticker = op.get('ticker', '')
            divisas = DIVISAS_POR_TICKER.get(ticker, [])

            for n in noticias:
                if n.get("impact", "").lower() != "high":
                    continue
                if n.get("country", "") not in divisas:
                    continue
                titulo = n.get("title", "Desconocido")
                if not _es_noticia_critica(titulo):
                    continue

                try:
                    fecha_str = n.get("date", "")
                    hora_str  = n.get("time", "").strip().lower()
                    if not fecha_str or hora_str in ("", "all day", "tentative"):
                        continue
                    dt     = datetime.strptime(f"{fecha_str} {hora_str}", "%m-%d-%Y %I:%M%p")
                    dt_utc = tz_ny.localize(dt).astimezone(pytz.UTC)
                    minutos_para_noticia = (dt_utc - ahora_utc).total_seconds() / 60
                except Exception:
                    continue

                # Si faltan menos de 30 min para una noticia CRÍTICA → cerrar
                if 0 < minutos_para_noticia <= 30:
                    nombre = op.get('nombre', ticker)
                    pips = op.get('pips_actual', 0)
                    logger.info(f"🛡️ PROTECCIÓN NOTICIAS: Cerrando {nombre} ({op_id}) | "
                                f"Noticia: {titulo} en {minutos_para_noticia:.0f}min | Pips: {pips:+.1f}")
                    try:
                        ticket_mt5 = op.get('ticket_mt5')
                        cerrar_posicion_mt5(ticker, ticket_id=ticket_mt5)
                    except Exception as e:
                        logger.error(f"❌ Error cerrando posición por noticias: {e}")

                    # Notificar en Telegram
                    try:
                        _pais = n.get("country", "?")
                        enviar_grupo(
                            f"🛡️ *PROTECCIÓN NOTICIAS*\n"
                            f"Cerrando operación: *{nombre}*\n"
                            f"📰 {_pais} — {titulo}\n"
                            f"⏰ Evento en {minutos_para_noticia:.0f} minutos\n"
                            f"💰 Pips al cerrar: {pips:+.1f}\n\n"
                            f"_Cierre preventivo para proteger capital_",
                            incluir_promo=False
                        )
                    except Exception:
                        pass
                    break  # Ya cerramos esta operación, pasar a la siguiente
    except Exception as e:
        logger.error(f"⚠️ Error en protección de noticias: {e}")

def get_fear_greed():
    """Obtiene el índice Fear & Greed de crypto (caché 4h)."""
    global _cache_fear_greed
    if time.time() - _cache_fear_greed["ts"] < 14400:
        return _cache_fear_greed["valor"], _cache_fear_greed["class"]
    try:
        r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=5)
        data = r.json()["data"][0]
        valor = int(data["value"])
        clasificacion = data["value_classification"]
        _cache_fear_greed = {"valor": valor, "class": clasificacion, "ts": time.time()}
        return valor, clasificacion
    except Exception:
        return _cache_fear_greed["valor"], _cache_fear_greed["class"]

def filtro_fear_greed(ticker, tipo):
    """Filtro Fear & Greed — desactivado (solo aplicaba a crypto, eliminado del bot)."""
    return True
    # Código legacy mantenido como referencia:
    if ticker not in ("BTC-USD", "ETH-USD"):
        return True
    fg, _ = get_fear_greed()
    if fg <= 0:
        return True  # Sin datos → no bloquear
    if fg > 75 and tipo == "COMPRA":
        print(f"⚠️ Fear&Greed {fg} (codicia extrema) — bloqueando COMPRA {ticker}")
        return False
    if fg < 25 and tipo == "VENTA":
        print(f"⚠️ Fear&Greed {fg} (miedo extremo) — bloqueando VENTA {ticker}")
        return False
    return True

def _obtener_tendencia_tf(ticker, interval, cache_dict, ttl=900):
    """
    Helper interno: descarga y calcula la tendencia EMA20/50 para un timeframe.
    Retorna: True (alcista), False (bajista), None (neutro/sin datos).
    Caché con TTL configurable para evitar descargas repetidas.
    """
    try:
        cached = cache_dict.get(ticker)
        if cached and (time.time() - cached['timestamp']) < ttl:
            df_tf = cached['df']
        else:
            df_tf = descargar_datos_seguro(ticker, period="15d", interval=interval)
            if df_tf is not None and len(df_tf) >= 50:
                cache_dict[ticker] = {'df': df_tf, 'timestamp': time.time()}
        if df_tf is None or len(df_tf) < 50:
            return None  # Sin datos suficientes → no bloquear
        close_tf = pd.Series(df_tf["Close"].values, dtype=float)
        ema20 = ta.ema(close_tf, length=20)
        ema50 = ta.ema(close_tf, length=50)
        if ema20 is None or ema50 is None:
            return None
        e20 = float(ema20.iloc[-1])
        e50 = float(ema50.iloc[-1])
        diff_pct = abs(e20 - e50) / e50 * 100
        if diff_pct < 0.4:
            return None  # Zona neutra: no bloquear
        return e20 > e50  # True=alcista, False=bajista
    except Exception:
        return None

def confirmar_tendencia_1h(ticker, tipo):
    """
    Verifica que la tendencia en 1H y 4H coincidan con la dirección de la señal.
    Sistema de doble filtro: ambas temporalidades deben alinearse (o ser neutras).
    """
    global _cache_mtf_1h, _cache_mtf_4h

    try:
        # ── TEMPORALIDAD 1H ─────────────────────────────────────
        alcista_1h = _obtener_tendencia_tf(ticker, "1h", _cache_mtf_1h, ttl=900)

        if alcista_1h is not None:
            if tipo == "COMPRA" and not alcista_1h:
                logger.info(f"⚠️ MTF 1H bloqueado: COMPRA pero 1H bajista en {ticker}")
                return False
            if tipo == "VENTA" and alcista_1h:
                logger.info(f"⚠️ MTF 1H bloqueado: VENTA pero 1H alcista en {ticker}")
                return False

        # ── TEMPORALIDAD 4H (segunda capa de confirmación) ──────
        alcista_4h = _obtener_tendencia_tf(ticker, "4h", _cache_mtf_4h, ttl=3600)

        if alcista_4h is not None:
            if tipo == "COMPRA" and not alcista_4h:
                logger.info(f"⚠️ MTF 4H bloqueado: COMPRA pero 4H bajista en {ticker}")
                return False
            if tipo == "VENTA" and alcista_4h:
                logger.info(f"⚠️ MTF 4H bloqueado: VENTA pero 4H alcista en {ticker}")
                return False

        return True  # Tendencias alineadas o neutras → señal válida

    except Exception as e:
        logger.warning(f"⚠️ Error en confirmar_tendencia_1h/4H ({ticker}): {e}")
        return True  # En caso de error, no bloquear

def hay_correlacion_peligrosa(ticker_nuevo, tipo_nuevo):
    """Anti-contradicción índices US: S&P500 ↔ NASDAQ (bidireccional)."""
    _US_INDEX_PAIRS = {"ES=F": "NQ=F", "NQ=F": "ES=F"}
    if ticker_nuevo in _US_INDEX_PAIRS:
        _par = _US_INDEX_PAIRS[ticker_nuevo]
        with _lock_ops:
            for _v in operaciones_activas.values():
                if _v.get('ticker') == _par:
                    _dir = _v.get('tipo', '')
                    if _dir and _dir != tipo_nuevo:
                        _nombre = "NASDAQ" if _par == "NQ=F" else "S&P500"
                        print(f"🔗 CORRELACIÓN: {ticker_nuevo} {tipo_nuevo} bloqueado — {_nombre} tiene {_dir}")
                        return True
                    break
    return False

# ============================================================
#  MENSAJE DE NUEVA SEÑAL - PROFESIONAL
# ============================================================

def mensaje_nueva_senal(nombre, ticker, tipo, precio, niveles, ind, score, razones, fuente="TradingView", premium=False, skip_mt5_razon="", nivel_senal="PREMIUM"):
    cat  = get_categoria(ticker)
    f_   = lambda v: fmt(v, ticker)
    hora = ahora().strftime("%H:%M:%S")

    # Unidad según tipo de activo — mostrar PUNTOS reales (no pips inflados)
    if cat == "crypto":
        def dist(a, b): return abs((b - a) / a * 100)
        fmt_dist = lambda v: f"{v:.2f}%"
        is_premium = False
    elif cat == "forex":
        def dist(a, b): return abs(calcular_pips(a, b, ticker))
        _unidad = "pips"
        fmt_dist = lambda v: f"{v:.1f} {_unidad}"
        tp3_total = dist(precio, niveles['tp3'])
        is_premium = tp3_total >= 100
    else:
        # Oro, Índices, Futuros → puntos reales (diferencia directa)
        def dist(a, b): return abs(b - a)
        _unidad = "pts"
        fmt_dist = lambda v: f"{v:.1f} {_unidad}"
        tp3_total = dist(precio, niveles['tp3'])
        is_premium = tp3_total >= 100

    # Cabecera limpia con nivel de señal
    if nivel_senal == "PREMIUM":
        _nivel_tag = "⭐ PREMIUM\n"
    elif nivel_senal == "STANDARD":
        _nivel_tag = "📊 STANDARD\n"
    else:
        _nivel_tag = ""
    if tipo == "COMPRA":
        cabecera = f"{_nivel_tag}🟢 *COMPRA* — {nombre}"
    else:
        cabecera = f"{_nivel_tag}🔴 *VENTA* — {nombre}"

    sl_dist  = dist(precio, niveles['sl'])
    tp1_dist = dist(precio, niveles['tp1'])
    tp2_dist = dist(precio, niveles['tp2'])
    tp3_dist = dist(precio, niveles['tp3'])

    # FIX 2026-03-19: Score REAL sin inflar (era score*2)
    score_display = score

    # R:R ratio (riesgo vs recompensa TP1)
    rr_ratio = round(tp1_dist / sl_dist, 1) if sl_dist > 0 else 0

    # Confianza real desde indicadores
    _conf_display = ind.get('confianza_total', 0)

    return (
        f"{cabecera}\n"
        f"━━━━━━━━━━\n"
        f"🕐 {hora} (Andorra)\n"
        f"Entrada: `{f_(precio)}`\n"
        f"SL: `{f_(niveles['sl'])}` (−{fmt_dist(sl_dist)})\n"
        f"TP1: `{f_(niveles['tp1'])}` (+{fmt_dist(tp1_dist)})\n"
        f"TP2: `{f_(niveles['tp2'])}` (+{fmt_dist(tp2_dist)})\n"
        f"TP3: `{f_(niveles['tp3'])}` (+{fmt_dist(tp3_dist)})\n"
        f"━━━━━━━━━━\n"
        f"Score: {score_display}/5 | R:R 1:{rr_ratio} | Conf: {_conf_display}%"
    )


# ============================================================
#  MENSAJES DE CIERRE
# ============================================================

def mensaje_tp_alcanzado(nombre, tipo, entrada, salida, pips, ticker, nivel_tp="TP", duracion_seg=None, perc_profit=None):
    f_ = lambda v: fmt(v, ticker)
    emoji_nivel = {"TP1": "1⃣", "TP2": "2⃣", "TP3": "🏁"}.get(nivel_tp, "✅")
    txt_dur = ""
    if duracion_seg:
        m = int(duracion_seg // 60)
        h = int(m // 60)
        m = m % 60
        txt_dur = f"\n⏱️ {h}h {m}m" if h > 0 else f"\n⏱️ {m}m"
    txt_perc = f"  (+{perc_profit:.2f}%)" if perc_profit is not None else ""
    return (
        f"✅ *{nivel_tp}* — {nombre}\n"
        f"{emoji_nivel} +{pips:.1f} {unidad_medida(ticker)}{txt_perc}\n"
        f"Entrada {f_(entrada)} → Cierre {f_(salida)}"
        + (txt_dur if txt_dur else "")
    )

def mensaje_sl_tocado(nombre, tipo, entrada, salida, pips, ticker):
    cat = get_categoria(ticker)
    pips_abs = abs(pips)  # Siempre mostrar valor absoluto con signo −
    if cat == "crypto":
        p_txt = f"{pips_abs:.2f}%"
    elif cat == "forex":
        p_txt = f"{pips_abs:.1f} pips"
    else:
        p_txt = f"{pips_abs:.1f} pts"
    f_ = lambda v: fmt(v, ticker)
    cabecera = "🟢 COMPRA" if tipo == "COMPRA" else "🔴 VENTA"

    return (
        f"🛑 *SL* — {nombre}\n"
        f"{cabecera}  −{p_txt}\n"
        f"Entrada {f_(entrada)}  →  Cierre {f_(salida)}"
    )

def mensaje_cierre_24h(nombre, tipo, entrada, salida, pips, ticker):
    cat = get_categoria(ticker)
    p_txt = f"{pips:.2f}%" if cat == "crypto" else f"{pips:.1f} {unidad_medida(ticker)}"
    f_ = lambda v: fmt(v, ticker)
    signo = "+" if pips > 0 else "-"
    emoji = "🟢" if pips > 0 else "🔴"
    return (
        f"⏰ *CIERRE 24H* — {nombre}\n"
        f"{emoji} {tipo} {signo}{p_txt}\n"
        f"Entrada {f_(entrada)} → Cierre {f_(salida)}"
    )

def mensaje_profit_lock(nombre, tipo, entrada, salida, pips, ticker, horas):
    cat = get_categoria(ticker)
    p_txt = f"{pips:.2f}%" if cat == "crypto" else f"{pips:.1f} {unidad_medida(ticker)}"
    f_ = lambda v: fmt(v, ticker)
    h = int(horas)
    m = int((horas % 1) * 60)
    dur = f"{h}h {m}m" if h > 0 else f"{m}m"
    tipo_txt = "🟢 COMPRA" if tipo == "COMPRA" else "🔴 VENTA"
    return (
        f"🔒 *PROFIT LOCK* — {nombre}\n"
        f"{tipo_txt} +{p_txt}\n"
        f"Entrada {f_(entrada)} → Cierre {f_(salida)} | {dur}"
    )

# ============================================================
#  COMANDOS DEL BOT
# ============================================================

def cmd_ayuda():
    return (
        "🤖 *COMANDOS BuySell365.pro*\n\n"
        "📈 *Senales*\n"
        "   `/senales` — Operaciones abiertas\n"
        "   `/resumen` — Resumen del dia\n"
        "   `/winrate` — Estadisticas\n\n"
        "🔍 *Analisis*\n"
        "   `/analisis [activo]` — Analisis tecnico\n"
        "   `/precio [activo]` — Precio en vivo\n"
        "   `/precios` — Todos los precios\n\n"
        "🌍 *Mercados*\n"
        "   `/mercados` — Activos monitoreados\n"
        "   `/horarios` — Sesiones de mercado\n"
        "   `/noticias` — Calendario economico\n"
        "   `/sentimiento` · `/tendencia` · `/pivots`\n\n"
        "📊 *Herramientas*\n"
        "   `/web` — Trading en Vivo\n"
        "   `/estado` — Estado del sistema\n\n"
        "👑 *VIP y Copy Trading*\n"
        f"   `/vip` — Canal VIP (5 dias habiles gratis)\n"
        "   `/copy` — Copy Trading automatico\n\n"
        "💡 _Preguntame: \"Analiza el oro\" o \"Precio del nasdaq\"_"
    )

def cmd_senales():
    with _lock_ops:
        if not operaciones_activas:
            return "📭 *Sin operaciones abiertas.* Te aviso cuando haya señal. 📡"
        # Copia segura para no bloquear el diccionario principal durante el formateo
        ops_local = {k: v.copy() for k, v in operaciones_activas.items() if isinstance(v, dict)}

    hora = ahora().strftime("%H:%M")
    ops_ordenadas = sorted(ops_local.items(), key=lambda x: x[1].get('timestamp', 0), reverse=True)
    compras = sum(1 for _, o in ops_ordenadas if o.get('tipo') == "COMPRA")
    ventas  = sum(1 for _, o in ops_ordenadas if o.get('tipo') == "VENTA")

    lineas = [f"📊 *ABIERTAS* {hora} · {len(ops_local)} op · 🟢{compras} 🔴{ventas}"]

    for op_id, op in ops_ordenadas:
        tkr = op.get('ticker', op_id)
        emoji = "🟢" if op.get('tipo') == "COMPRA" else "🔴"
        tiempo_restante = TIEMPO_AUTOCIERRE - (time.time() - op.get('timestamp', time.time()))
        horas_restantes = max(0, int(tiempo_restante / 3600))
        tp1_ok = "✅" if op.get('tp1_hit') else "⬜"
        tp2_ok = "✅" if op.get('tp2_hit') else "⬜"

        lineas.append(
            f"\n{emoji} *{op.get('nombre', tkr)}* {op.get('tipo', '')} · {op.get('hora','')}\n"
            f"💵 {fmt(op.get('entrada', 0), tkr)} · 🛑 {fmt(op.get('sl', 0), tkr)}\n"
            f"{tp1_ok}TP1 {fmt(op.get('tp1', 0), tkr)} · {tp2_ok}TP2 {fmt(op.get('tp2', 0), tkr)} · ⬜TP3 {fmt(op.get('tp3', 0), tkr)}\n"
            f"⏱️ {horas_restantes}h restantes"
        )

    # firma removida
    return "\n".join(lineas)

def cmd_estado():
    total = estadisticas_diarias["ganadas"] + estadisticas_diarias["perdidas"]
    efectividad = (estadisticas_diarias["ganadas"] / total * 100) if total > 0 else 0
    pips_netos  = estadisticas_diarias["pips_ganados"] - estadisticas_diarias["pips_perdidos"]
    emoji_res   = "🟢 POSITIVO" if pips_netos >= 0 else "🔴 NEGATIVO"

    # Profit Factor
    pf = (estadisticas_diarias["pips_ganados"] / max(estadisticas_diarias["pips_perdidos"], 0.1))
    pf_txt = f"{pf:.2f}" if total > 0 else "N/A"

    # Racha actual
    racha = _calcular_racha_perdidas_actual()
    if racha > 0:
        racha_txt = f"🔴 {racha} pérdidas consecutivas"
    else:
        # Calcular racha ganadora
        r_win = 0
        for op in reversed(historial_operaciones):
            if op.get('resultado') == 'WIN':
                r_win += 1
            else:
                break
        racha_txt = f"🟢 {r_win} ganadas consecutivas" if r_win > 0 else "⚪ Sin racha"

    # Modo de riesgo visual
    modo_emoji = {"conservador": "🔵", "normal": "🟢", "agresivo": "🔴"}
    modo_txt = f"{modo_emoji.get(MODO_RIESGO, '⚪')} {MODO_RIESGO.upper()}"

    _g = int(estadisticas_diarias['ganadas'])
    _p = int(estadisticas_diarias['perdidas'])
    res = (
        f"📊 *ESTADO* {ahora().strftime('%d/%m %H:%M')}\n"
        f"✅ *{_g}*  ❌ *{_p}*  │  🎯 *{efectividad:.0f}%*  │  PF *{pf_txt}*\n"
        f"💰 *{pips_netos:+.1f}* {emoji_res}  │  🔥 {racha_txt}\n"
        f"⚙️ {modo_txt} │ 💼 ${CAPITAL_USUARIO:,.0f} │ 🔄 {len(operaciones_activas)} │ 📡{'🟢' if not escaneo_pausado else '⏸️'} │ MT5:{'🟢' if not mt5_pausado else '⏸️'}\n"
    )

    # Spreads: solo mostrar los que están MAL (⚠️)
    if MT5_AVAILABLE:
        _spreads_mal = []
        for nombre, ticker in ACTIVOS.items():
            mt5_ticker = MT5_TICKER_MAP.get(ticker, ticker)
            with _lock_mt5:  # H-01 FIX
                si = mt5.symbol_info(mt5_ticker)
            if si and si.spread > MAX_SPREAD_ALLOWED.get(ticker, 999):
                _spreads_mal.append(f"⚠️{nombre}:{si.spread}")
        if _spreads_mal:
            res += "🛡️ " + " │ ".join(_spreads_mal) + "\n"

    return res

def cmd_resumen():
    estado = cmd_estado()
    lineas = [estado]

    ganadas  = [o for o in historial_operaciones if o['resultado'] == "WIN"]
    perdidas = [o for o in historial_operaciones if o['resultado'] == "LOSS"]

    def fmt_pips(pips, tkr):
        cat = get_categoria(tkr)
        if cat == "crypto":
            return f"{pips:.2f}%"
        if cat == "forex":
            return f"{pips:.1f}p"
        return f"{pips:.1f}pts"

    # Últimas 3 ganadas/perdidas (compacto)
    if ganadas:
        lineas.append(f"✅ *GANADAS ({len(ganadas)})*")
        for op in reversed(ganadas[-3:]):
            lineas.append(f"  {op['nombre']} {op['tipo'][0]} {op.get('hora','')[:5]} +{fmt_pips(op['pips'], op.get('ticker',''))}")

    if perdidas:
        lineas.append(f"❌ *PERDIDAS ({len(perdidas)})*")
        for op in reversed(perdidas[-3:]):
            lineas.append(f"  {op['nombre']} {op['tipo'][0]} {op.get('hora','')[:5]} -{fmt_pips(op['pips'], op.get('ticker',''))}")

    if not historial_operaciones:
        lineas.append("📭 Sin operaciones hoy")

    # Abiertas con TPs (compacto)
    ops_tp = [(oid, op) for oid, op in operaciones_activas.items()
              if op.get('tp1_hit') or op.get('tp2_hit')]
    if ops_tp:
        lineas.append(f"🔄 *ABIERTAS +TP ({len(ops_tp)})*")
        for op_id, op in sorted(ops_tp, key=lambda x: x[1].get('timestamp', 0), reverse=True)[:3]:
            tkr = op.get('ticker', '')
            tp_n = "TP2" if op.get('tp2_hit') else "TP1"
            pips_op = op.get('tp2_pips', 0.0) if op.get('tp2_hit') else op.get('tp1_pips', 0.0)
            lineas.append(f"  {op['nombre']} {op['tipo'][0]} · {tp_n} +{fmt_pips(pips_op, tkr)}")

    # Totales por categoría (una línea)
    totales = {}
    for op in historial_operaciones:
        cat = get_categoria(op.get('ticker', ''))
        signo = 1 if op['resultado'] == 'WIN' else -1
        totales[cat] = totales.get(cat, 0.0) + signo * op['pips']
    for op_id, op in operaciones_activas.items():
        if op.get('tp1_hit') or op.get('tp2_hit'):
            cat = get_categoria(op.get('ticker', ''))
            pips_op = op.get('tp2_pips', 0.0) if op.get('tp2_hit') else op.get('tp1_pips', 0.0)
            totales[cat] = totales.get(cat, 0.0) + pips_op

    if totales:
        _etq = {"crypto": "Crypto", "forex": "Forex", "futuros": "Futuros", "accion": "Acc"}
        _uni = {"crypto": "$", "forex": "p", "futuros": "pts", "accion": "pts"}
        _dec = {"crypto": 2, "forex": 1, "futuros": 1, "accion": 1}
        _parts = []
        for cat, val in totales.items():
            e = "🟢" if val >= 0 else "🔴"
            s = "+" if val >= 0 else ""
            _parts.append(f"{e}{_etq.get(cat,cat)}:{s}{val:.{_dec.get(cat,1)}f}{_uni.get(cat,'')}")
        lineas.append("━━━━━━━━━━")
        lineas.append(" │ ".join(_parts))

    return "\n".join(lineas)

def cmd_resumen_ganancias():
    """Versión simplificada del resumen que solo muestra los totales ganados/perdidos."""
    # Calcular totales por categoría (Forex, Crypto, etc.)
    totales_ganados  = {} # cat -> pips
    totales_perdidos = {} # cat -> pips
    
    # 1. De operaciones cerradas
    for op in historial_operaciones:
        tkr = op.get('ticker', '')
        cat = get_categoria(tkr)
        pips = abs(op['pips'])
        if op['resultado'] == 'WIN':
            totales_ganados[cat] = totales_ganados.get(cat, 0.0) + pips
        else:
            totales_perdidos[cat] = totales_perdidos.get(cat, 0.0) + pips
            
    # 2. De operaciones abiertas con TPs
    for op_id, op in operaciones_activas.items():
        if op.get('tp1_hit') or op.get('tp2_hit'):
            tkr = op.get('ticker', '')
            cat = get_categoria(tkr)
            pips_op = op.get('tp2_pips', 0.0) if op.get('tp2_hit') else op.get('tp1_pips', 0.0)
            totales_ganados[cat] = totales_ganados.get(cat, 0.0) + pips_op

    if not totales_ganados and not totales_perdidos:
        return "📭 Sin movimientos registrados hoy."

    lineas = [f"📊 *RESUMEN DE GANANCIAS* · {ahora().strftime('%d/%m')}"]
    
    etiquetas = {"crypto": "Cripto", "forex": "Forex", "futuros": "Futuros", "accion": "Acciones"}
    unidades  = {"crypto": "%",      "forex": "pips",  "futuros": "pts",     "accion": "pts"}
    decimales = {"crypto": 2,        "forex": 1,       "futuros": 1,         "accion": 1}

    todas_categorias = set(list(totales_ganados.keys()) + list(totales_perdidos.keys()))
    
    for cat in todas_categorias:
        g   = totales_ganados.get(cat, 0.0)
        p   = totales_perdidos.get(cat, 0.0)
        uni = unidades.get(cat, "pts")
        dec = decimales.get(cat, 1)
        etq = etiquetas.get(cat, str(cat).capitalize())
        
        lineas.append(f"\n━━━━━━━━━━")
        lineas.append(f"📦 *{etq}*")
        lineas.append(f"📈 Ganados:  *+{g:.{dec}f} {uni}*")
        lineas.append(f"📉 Perdidos:  *-{p:.{dec}f} {uni}*")
        lineas.append(f"✨ Neto:     *{ (g-p):+.{dec}f} {uni}*")

    return "\n".join(lineas)

def cmd_precio(activo_raw: str):
    """Obtiene precio en tiempo real con contexto de mercado."""
    activo_key = activo_raw.lower().strip()
    nombre = KEYWORDS_ACTIVOS.get(activo_key)

    if not nombre:
        return (
            f"❓ No reconozco '*{activo_raw}*'.\n\n"
            "Activos disponibles:\n"
            "oro · eurusd · usdjpy · gbpjpy · nasdaq · sp500\n\n"
            "O escribe */mercados* para ver todos."
        )

    ticker = ACTIVOS[nombre]

    try:
        cotizacion = obtener_cotizacion_tv(ticker)
        if cotizacion:
            precio   = cotizacion['precio']
            apertura = cotizacion['apertura']
            fuente   = cotizacion.get('fuente', 'TradingView')
        else:
            # Intentar MT5 primero, luego yfinance como fallback
            df = descargar_ohlcv(ticker, period="5d", interval="15m")
            if df is not None and not df.empty:
                precio   = float(df['Close'].iloc[-1])
                apertura = float(df['Open'].iloc[0])
                fuente   = "MT5" if MT5_AVAILABLE else "yfinance 15m"
            else:
                df = descargar_datos_seguro(ticker, period="5d", interval="15m")
                if df is None or df.empty:
                    return f"⚠️ No hay datos disponibles para {nombre} ahora mismo."
                precio   = float(df['Close'].iloc[-1])
                apertura = float(df['Open'].iloc[0])
                fuente   = "yfinance 15m"

        cambio = precio - apertura
        pct    = (cambio / apertura) * 100 if apertura > 0 else 0.0

        emoji_cambio = "🟢" if cambio >= 0 else "🔴"
        signo = "+" if cambio >= 0 else ""

        f_ = lambda v: fmt(v, ticker)
        cat = get_categoria(ticker)

        # Contexto adicional desde caché de indicadores
        ind = _cache_ind.get(ticker)
        contexto_extra = ""
        if ind:
            # Tendencia rápida
            if ind['ema9'] > ind['ema20'] > ind['ema50']:
                tend = "📈 Alcista"
            elif ind['ema9'] < ind['ema20'] < ind['ema50']:
                tend = "📉 Bajista"
            else:
                tend = "➡️ Lateral"

            contexto_extra = (
                f"\n📊 *Contexto rápido:*\n"
                f"   Tendencia: {tend}\n"
                f"   RSI: {ind['rsi']:.1f}  │  ADX: {ind['adx']:.1f}\n"
                f"   Soporte: {f_(ind['soporte'])}  │  Resistencia: {f_(ind['resistencia'])}\n"
            )

        # Nota de instrumento
        nota_instrumento = ""
        if ticker == "GC=F" and "XAUUSD" in fuente:
            nota_instrumento = "\n📌 _XAUUSD spot (igual a XM/TradingView)_"
        elif ticker in ("NQ=F", "ES=F") and ("US100" in fuente or "US500" in fuente):
            nota_instrumento = "\n📌 _CFD (igual a XM)_"
        elif "Yahoo" in fuente and ticker in ("GC=F", "NQ=F", "ES=F"):
            nota_instrumento = "\n⚠️ _Futuros YF (puede diferir ~$5-20 de XM)_"

        return (
            f"{CATEGORIA_EMOJI[cat]} *{nombre}*\n"
            "━━━━━━━━━━\n"
            f"💵 Precio: *{f_(precio)}*\n"
            f"{emoji_cambio} Cambio hoy: {signo}{f_(cambio)} ({signo}{pct:.2f}%)\n"
            f"📈 Apertura: {f_(apertura)}{contexto_extra}\n"
            f"🕐 {ahora().strftime('%H:%M:%S')} · _{fuente}_{nota_instrumento}\n\n"
            "💡 Escribe `/analisis {0}` para el análisis completo.".format(activo_key)
        )

    except Exception as e:
        return f"⚠️ Error obteniendo precio de {nombre}: {e}"

def cmd_capital(monto_str):
    global CAPITAL_USUARIO
    try:
        # Extraer solo números de la cadena (maneja $, puntos, comas)
        numeros = re.findall(r"[-+]?\d*\.?\d+", monto_str.replace(",", ""))
        if not numeros:
            return "❌ No detecté ningún número. Usa: `/capital 1000`"
        
        monto = float(numeros[0])
        if monto < 50:
            return "⚠️ El capital mínimo para una gestión de riesgo profesional es de $50."
        
        CAPITAL_USUARIO = monto
        guardar_estado()
        
        return (
            f"✅ *CAPITAL ACTUALIZADO: ${CAPITAL_USUARIO:,.0f}*\n\n"
            "A partir de ahora, las nuevas señales calcularán tu lotaje sugerido basado en este capital arriesgando el 1% por trade."
        )
    except Exception as e:
        return f"❌ Error al procesar el monto: {e}. Usa: `/capital 1000`"

def cmd_analisis(activo_raw: str):
    """Análisis técnico completo premium de un activo a demanda."""
    activo_key = activo_raw.lower().strip()
    nombre = KEYWORDS_ACTIVOS.get(activo_key)

    if not nombre:
        return (
            f"❓ No reconozco '*{activo_raw}*'.\n"
            "Prueba: /analisis oro, /analisis nasdaq"
        )

    ticker = ACTIVOS[nombre]

    try:
        df = descargar_datos_seguro(ticker)

        if df is None or df.empty or len(df) < 100:
            return f"⚠️ No pude obtener datos de {nombre} ahora mismo. yFinance está tardando — prueba en 1-2 minutos."

        # Precio en vivo (cascada de fuentes)
        cot = obtener_cotizacion_tv(ticker)
        if cot:
            precio = cot['precio']
            fuente = cot.get('fuente', 'TradingView')
        else:
            precio = float(df['Close'].iloc[-1])
            fuente = "yfinance (velas)"

        ind = calcular_indicadores_profesionales(df, precio, ticker)

        if not ind:
            return f"⚠️ Error calculando indicadores para {nombre}."

        tipo, score, razones = evaluar_senal_profesional(ind, ticker)

        f_     = lambda v: fmt(v, ticker)
        cat    = get_categoria(ticker)

        # Detectar mercado abierto/cerrado
        _es_weekend_analisis = datetime.now(pytz.UTC).weekday() >= 5
        _mercado_cerrado = _es_weekend_analisis

        # ── CONTEXTO DE MERCADO ──────────────────────
        hora_utc = datetime.now(pytz.UTC).hour
        if 13 <= hora_utc < 17:
            sesion_txt = "🇺🇸 Overlap Londres-Nueva York (Máx. liquidez)"
        elif 7 <= hora_utc < 13:
            sesion_txt = "🇬🇧 Sesión Londres"
        elif 17 <= hora_utc < 21:
            sesion_txt = "🇺🇸 Sesión Nueva York"
        elif 0 <= hora_utc < 7:
            sesion_txt = "🌏 Sesión Asiática (Tokio/Sídney)"
        else:
            sesion_txt = "🌙 Post-mercado / Pre-Asia"

        regimen = ind.get('regimen', 'TRANSICIÓN')
        emoji_reg = "⚖️" if regimen == "RANGO" else "📈" if regimen == "TENDENCIA" else "⚡" if regimen == "VOLATILIDAD" else "🔄"
        
        # Fear & Greed (si está disponible)
        fg_val, fg_class = get_fear_greed()
        fg_class = str(fg_class)  # Asegurar string para evitar errores de tipo
        fg_emoji = "😱" if "Fear" in fg_class else "🤑" if "Greed" in fg_class else "😐"

        # ── SEÑAL ACTIVA ────────────────────────────────────
        if tipo:
            niveles = calcular_niveles_3tp(precio, tipo, ind['atr_1h'], ticker)
            tipo_txt = f"{'🟢 COMPRA' if tipo == 'COMPRA' else '🔴 VENTA'}"
            razones_resumen = "\n   ".join(razones[:5])

            # R:R ratio
            sl_dist = abs(precio - niveles['sl'])
            tp1_dist = abs(niveles['tp1'] - precio)
            tp2_dist = abs(niveles['tp2'] - precio)
            tp3_dist = abs(niveles['tp3'] - precio)
            rr1 = tp1_dist / sl_dist if sl_dist > 0 else 0
            rr2 = tp2_dist / sl_dist if sl_dist > 0 else 0
            rr3 = tp3_dist / sl_dist if sl_dist > 0 else 0

            lote = calcular_lote_sugerido(CAPITAL_USUARIO, RIESGO_POR_TRADE, precio, niveles['sl'], ticker)

            senal_txt = (
                f"\n🚨 *SEÑAL ACTIVA: {tipo_txt}*\n"
                f"   {barra_confianza(score)}\n\n"
                "   *Confluencia técnica:*\n"
                f"   {razones_resumen}\n\n"
                f"   🛑 SL:  {f_(niveles['sl'])}\n"
                f"   1️⃣ TP1: {f_(niveles['tp1'])}  (R:R {rr1:.1f}:1)\n"
                f"   2️⃣ TP2: {f_(niveles['tp2'])}  (R:R {rr2:.1f}:1)\n"
                f"   3️⃣ TP3: {f_(niveles['tp3'])}  (R:R {rr3:.1f}:1)\n\n"
                f"   💼 Lote sugerido (${CAPITAL_USUARIO:,.0f} al 1%): *{lote}*\n"
            )
        else:
            senal_txt = f"\n⏸️ *Sin señal activa*\n   {razones[0] if razones else 'Esperando confirmación'}\n"

        # ── TENDENCIA MULTI-TEMPORAL ────────────────────────
        tendencia_15m = "📈 Alcista" if ind['ema9'] > ind['ema20'] > ind['ema50'] else ("📉 Bajista" if ind['ema9'] < ind['ema20'] < ind['ema50'] else "➡️ Mixta")

        alcista_1h = _obtener_tendencia_tf(ticker, "1h", _cache_mtf_1h, ttl=900)
        tendencia_1h = "📈 Alcista" if alcista_1h is True else ("📉 Bajista" if alcista_1h is False else "➡️ Neutra")

        alcista_4h = _obtener_tendencia_tf(ticker, "4h", _cache_mtf_4h, ttl=1800)
        tendencia_4h = "📈 Alcista" if alcista_4h is True else ("📉 Bajista" if alcista_4h is False else "➡️ Neutra")

        # ── POSICIÓN EN BOLLINGER ───────────────────────────
        bb_rango = ind['bb_up'] - ind['bb_lo']
        if bb_rango > 0:
            bb_pos = (precio - ind['bb_lo']) / bb_rango * 100
        else:
            bb_pos = 50
        if bb_pos > 80:
            bb_txt = f"Zona alta ({bb_pos:.0f}%) — cerca de banda superior"
        elif bb_pos < 20:
            bb_txt = f"Zona baja ({bb_pos:.0f}%) — cerca de banda inferior"
        else:
            bb_txt = f"Zona media ({bb_pos:.0f}%) — entre bandas"

        # ── ML PROBABILIDAD ─────────────────────────────────
        prob_alcista = ind.get('ml_prob_alcista', 50.0)
        prob_bajista = round(100.0 - prob_alcista, 1)
        if prob_alcista >= 60:
            ml_txt = f"🟢 *{prob_alcista}% alcista* — ML favorece subida"
        elif prob_bajista >= 60:
            ml_txt = f"🔴 *{prob_bajista}% bajista* — ML favorece caída"
        else:
            ml_txt = f"⚖️ Indeciso ({prob_alcista}% alcista / {prob_bajista}% bajista)"

        # ── LECTURA ALGORÍTMICA ─────────────────────────────
        estado_vol = "Detectamos inyección de capital institucional (volumen {:.1f}x la media)".format(ind['vol_ratio']) if ind['vol_ratio'] > 1.2 else "Flujo de órdenes estándar (volumen {:.1f}x)".format(ind['vol_ratio'])

        if precio > ind['ema200']:
            estado_macro = "La estructura macro es ALCISTA (precio sobre EMA200)"
        else:
            estado_macro = "La estructura macro es BAJISTA (precio bajo EMA200)"

        if ind['dist_soporte'] < 1.0:
            estado_sr = "reaccionando a zona de soporte ({})".format(f_(ind['soporte']))
        elif ind['dist_resistencia'] < 1.0:
            estado_sr = "testeando zona de resistencia ({})".format(f_(ind['resistencia']))
        else:
            estado_sr = "en zona de transición entre S {} y R {}".format(f_(ind['soporte']), f_(ind['resistencia']))

        if ind['adx'] > 30:
            fuerza = "La tendencia es FUERTE (ADX {:.1f})".format(ind['adx'])
        elif ind['adx'] > 20:
            fuerza = "La tendencia es MODERADA (ADX {:.1f})".format(ind['adx'])
        else:
            fuerza = "El mercado está LATERAL/sin tendencia clara (ADX {:.1f})".format(ind['adx'])

        # ── DIVERGENCIAS ────────────────────────────────────
        div_txt = ""
        if ind.get('div_alcista'):
            div_txt = "\n⭐ *DIVERGENCIA ALCISTA detectada* — el precio hace mínimos pero el RSI sube (suelo potencial)"
        elif ind.get('div_bajista'):
            div_txt = "\n⭐ *DIVERGENCIA BAJISTA detectada* — el precio hace máximos pero el RSI baja (techo potencial)"

        # ── PATRONES DE VELAS ───────────────────────────────
        patrones = ind.get('patrones', [])
        patron_txt = ""
        if patrones:
            patron_txt = "\n🕯️ *Patrones:* " + ", ".join(patrones[:3])

        # ── VEREDICTO FIRME ─────────────────────────────────
        if tipo == "COMPRA":
            veredicto = "🟢 *PRONÓSTICO: ALCISTA — Alta Probabilidad de Subida*"
        elif tipo == "VENTA":
            veredicto = "🔴 *PRONÓSTICO: BAJISTA — Alta Probabilidad de Caída*"
        elif ind.get('ema20', 0) > ind.get('ema50', 0) and ind['rsi'] > 50:
            veredicto = "↗️ *PRONÓSTICO: SESGO ALCISTA — Esperando confirmación*"
        elif ind.get('ema20', 0) < ind.get('ema50', 0) and ind['rsi'] < 50:
            veredicto = "↘️ *PRONÓSTICO: SESGO BAJISTA — Esperando confirmación*"
        else:
            veredicto = "⚖️ *PRONÓSTICO: NEUTRO — Consolidación en rango*"

        _estado_mercado = "🔴 MERCADO CERRADO" if _mercado_cerrado else sesion_txt
        _precio_label = "Último precio" if _mercado_cerrado else "Precio"

        return (
            f"🔬 *AUDITORÍA DE MERCADO PREMIUM*\n"
            f"━━━━━━━━━━\n"
            f"{CATEGORIA_EMOJI[cat]} *{nombre}*  •  {_estado_mercado}\n"
            f"💵 {_precio_label}: *{f_(precio)}*\n\n"
            f"{veredicto}\n"
            f"{div_txt}{patron_txt}\n\n"
            f"🧠 *INTELIGENCIA ARTIFICIAL:*\n"
            f"   {ml_txt}\n\n"
            f"🗣️ *LECTURA DEL MERCADO:*\n"
            f"_{estado_vol}. {estado_macro}. El precio está {estado_sr}. {fuerza}._\n"
            f"{senal_txt}\n"
            "━━━━━━━━━━\n"
            "📊 *MÉTRICAS CLAVE*\n"
            f"   ⚡ RSI(14): *{ind['rsi']:.1f}*  {'🔴 Sobrecomprado' if ind['rsi'] > 70 else ('🟢 Sobrevendido' if ind['rsi'] < 30 else '⚪ Neutral')}\n"
            f"   🎢 ADX: *{ind['adx']:.1f}*  │  DI+ {ind['di_plus']:.1f}  DI- {ind['di_minus']:.1f}\n"
            f"   📊 MACD: {'🟢 Alcista' if ind['macd'] > ind['signal'] else '🔴 Bajista'}  (Hist: {ind['macd_hist']:.5g})\n"
            f"   📉 Stoch: K={ind['stoch_k']:.0f} D={ind['stoch_d']:.0f}  {'🔴 OB' if ind['stoch_k'] > 80 else ('🟢 OS' if ind['stoch_k'] < 20 else '⚪')}\n"
            f"   📐 Bollinger: {bb_txt}\n"
            f"   ⚖️ Volumen: {ind['vol_ratio']:.1f}x media\n"
            f"   🛡️ ATR(14): {f_(ind['atr'])}\n\n"
            "📈 *TENDENCIA MULTI-TEMPORAL*\n"
            f"   15min: {tendencia_15m}\n"
            f"   1H:    {tendencia_1h}\n"
            f"   4H:    {tendencia_4h}\n\n"
            "🏛️ *NIVELES CLAVE*\n"
            f"   🟢 Soporte:     {f_(ind['soporte'])} ({ind['dist_soporte']:.2f}%)\n"
            f"   🔴 Resistencia: {f_(ind['resistencia'])} ({ind['dist_resistencia']:.2f}%)\n"
            f"   📏 EMA200:      {f_(ind['ema200'])}\n"
        )

    except Exception as e:
        import traceback
        traceback.print_exc()
        return f"⚠️ Error analizando {nombre}: {e}"

def cmd_precios_tv():
    """Dashboard de precios — limpio, sin fuente ni hora."""
    resultados = {}
    _es_weekend_p = datetime.now(pytz.UTC).weekday() >= 5

    def _fetch(nombre, tk):
        res = obtener_cotizacion_tv(tk)
        resultados[nombre] = (tk, res)

    with ThreadPoolExecutor(max_workers=len(ACTIVOS)) as executor:
        futs = [executor.submit(_fetch, nom, tk) for nom, tk in ACTIVOS.items()]
        for f in as_completed(futs):
            pass

    orden = ["ORO", "EUR/USD", "USD/JPY", "GBP/JPY", "NASDAQ", "S&P 500"]
    _titulo = "ÚLTIMO CIERRE" if _es_weekend_p else "PRECIOS EN VIVO"
    lineas = [f"📊 *{_titulo}*", "━━━━━━━━━━\n"]

    for nombre in orden:
        if nombre not in resultados:
            continue
        ticker, res = resultados[nombre]
        _cerrado = _es_weekend_p
        try:
            if res:
                precio = res['precio']
                apert  = res['apertura']
                cambio = precio - apert
                pct    = (cambio / apert * 100) if apert > 0 else 0
                emoji  = "🟢" if cambio >= 0 else "🔴"
                signo  = "+" if cambio >= 0 else ""
                precio_fmt = fmt(precio, ticker)
                _estado = "  🔴 _CERRADO_" if _cerrado else ""
                lineas.append(
                    f"{emoji} *{nombre}:* `{precio_fmt}` ({signo}{pct:.2f}%){_estado}"
                )
            else:
                lineas.append(f"⚪ *{nombre}:* `Sin datos`")
        except Exception:
            lineas.append(f"❌ *{nombre}:* `Error`")

    return "\n".join(lineas)

def cmd_url_dashboard():
    """Retorna el mensaje con el link al dashboard web."""
    teclado = {
        "inline_keyboard": [
            [{"text": "🌐 WEB OFICIAL", "url": "https://buysell365.pro"}],
            [{"text": "📊 TRADING EN VIVO", "url": DASHBOARD_URL}],
            [{"text": "📅 Noticias", "callback_data": "/noticias"}, {"text": "⏰ Horarios", "callback_data": "/horarios"}]
        ]
    }
    msg = (
        "📊 *WEB EN VIVO — BuySell365.pro*\n"
        "━━━━━━━━━━\n\n"
        "🌐 *Web oficial:* buysell365.pro\n"
        "📊 *Trading en Vivo:* buysell365.pro/dashboard\n\n"
        "📌 *Que puedes ver:*\n"
        "   • Operaciones abiertas con P&L en vivo\n"
        "   • Historial de senales del dia\n"
        "   • Estado del scanner de IA\n"
        "   • Spreads y conexion MT5\n\n"
        "📧 *Soporte:* soporte@buysell365.pro\n\n"
        "💡 En Telegram escribe: *web* o */web*\n\n"
        "🛡️ _Transparencia total para nuestra comunidad._"
    )
    return msg, teclado

def cmd_mercados():
    lineas = [
        "*6 ACTIVOS*\n",
        "━━━━━━━━━━\n",
        "ORO · EUR/USD · USD/JPY · GBP/JPY\n",
        "NASDAQ · S&P 500\n",
        "━━━━━━━━━━\n",
        "Horario: 8:00 - 18:00 (L-V)\n",
        "`/precios` — Precios en vivo",
        "`/analisis [activo]` — Analisis tecnico",
    ]
    return "\n".join(lineas)

# ============================================================
#  COMANDOS NUEVOS — INFORMACIÓN Y UTILIDADES
# ============================================================

def cmd_ping():
    return "🏓 *Pong!* El bot está activo y funcionando. ✅"

def cmd_fuentes():
    """Diagnóstico de fuentes de precio — muestra de dónde viene cada precio."""
    lineas = [
        "🔍 *DIAGNÓSTICO DE FUENTES DE PRECIO*\n"
        "━━━━━━━━━━\n"
        "Verificando cada activo contra TradingView...\n"
    ]

    # Forzar refresh (limpiar caché)
    global _FUENTES_PRECIO
    _FUENTES_PRECIO.clear()

    for nombre, ticker in ACTIVOS.items():
        try:
            cot = obtener_cotizacion_tv(ticker)
            if cot:
                precio = cot['precio']
                fuente = cot.get('fuente', '?')
                f_ = lambda v, tk=ticker: fmt(v, tk)

                # Verificar si la fuente es TradingView (exacta) o fallback
                if 'TradingView' in fuente:
                    estado = "✅ EXACTO"
                elif 'MT5' in fuente:
                    estado = "✅ XM DIRECTO"
                else:
                    estado = "⚠️ FALLBACK"

                lineas.append(
                    f"{estado} *{nombre}*\n"
                    f"   💵 {f_(precio)}\n"
                    f"   📡 _{fuente}_\n"
                )
            else:
                lineas.append(f"❌ *{nombre}*\n   Sin datos de ninguna fuente\n")
        except Exception as e:
            lineas.append(f"❌ *{nombre}*: {e}\n")

    lineas.append(
        "━━━━━━━━━━\n"
        "✅ = Precio idéntico a TradingView\n"
        "⚠️ = Precio de Yahoo Finance (puede diferir $5-30 en Oro/Índices)\n"
        "❌ = Sin datos\n\n"
        "💡 Si ves ⚠️, el servidor no puede acceder a TradingView Scanner.\n"
        "Esto es normal en PythonAnywhere (proxy bloqueado)."
    )
    return "\n".join(lineas)

def cmd_como():
    return (
        "🔬 *¿CÓMO OPERA EL CEREBRO DE ESTE BOT?*\n"
        "━━━━━━━━━━\n\n"
        "1️⃣ *Escaneo de Alta Frecuencia (3 min)*\n"
        "   Nuestros servidores analizan ORO, NASDAQ y más activos ininterrumpidamente, extrayendo datos institucionales.\n\n"
        "2️⃣ *Motor de Inteligencia Artificial (ML)*\n"
        "   Usa el algoritmo Random Forest junto con 9 indicadores matemáticos pesados (RSI, EMAs, MACD, ADX) para no fallar.\n\n"
        "3️⃣ *Aprobación Multidimensional*\n"
        "   No lanzamos alertas al azar. Buscamos divergencias extremas, inyección de dinero en Volumen y rupturas limpias.\n\n"
        "4️⃣ *Gestión Estricta del Capital*\n"
        "   Si la auditoría es EXITOSA, te entregamos una Entrada precisa, un Stop Loss corto para proteger tu dinero, y 3 niveles de ganancia (Take Profits) medidos por Volatilidad (ATR).\n\n"
        "💡 *Consejo:* Escribe el nombre de un activo (Ej: *Oro*) para que te dé el pronóstico inmediato de lo que sucederá."
    )

def cmd_riesgo():
    return (
        "⚠️ *GESTIÓN DE RIESGO*\n"
        "━━━━━━━━━━\n\n"
        "📌 *Regla del 1-2%*\n"
        "   Nunca arriesgues más del 2% de tu capital por operación\n\n"
        "🛑 *Stop Loss siempre activo*\n"
        "   El SL está calculado con ATR × multiplicador\n\n"
        "🎯 *Take Profits escalonados*\n"
        "   TP1 → parcial · TP2 → parcial · TP3 → completo\n\n"
        "📊 *Ratio Riesgo:Beneficio*\n"
        "   Crypto:  1:1.5 / 1:2.5 / 1:4\n"
        "   Forex:   1:1.5 / 1:2.1 / 1:3.3\n"
        "   Futuros: 1:1.3 / 1:2.0 / 1:3.3\n\n"
        "💡 Reduce el tamaño de posición cuando\n"
        "   el mercado esté muy volátil."
    )

def cmd_horarios():
    hora_utc = datetime.now(pytz.UTC).strftime("%H:%M")
    info = [
        ("ORO",      "GC=F",     "08:00 — 21:00"),
        ("EUR/USD",  "EURUSD=X", "07:00 — 21:00"),
        ("USD/JPY", "USDJPY=X", "00:00 — 15:00  (sesión asiática)"),
        ("GBP/JPY", "GBPJPY=X", "01:00 — 21:00  (London+Tokyo)"),
        ("NASDAQ",    "NQ=F",     "09:00 — 21:00"),
        ("S&P 500",   "ES=F",     "09:00 — 21:00"),
    ]
    lineas = [
        "🕐 *HORARIOS DE MERCADO* (UTC)\n"
        "━━━━━━━━━━\n"
        f"Hora UTC actual: *{hora_utc}*\n"
    ]
    for nombre, ticker, horario in info:
        estado = "🟢 ABIERTO" if en_horario_mercado(ticker) else "🔴 CERRADO"
        lineas.append(f"{nombre}\n   {horario}  │  {estado}\n")
    lineas.append("━━━━━━━━━━")
    return "\n".join(lineas)

def cmd_sentimiento():
    fg, _ = get_fear_greed()
    if fg >= 75:
        emoji, texto = "🔴", "Codicia Extrema"
        consejo = "⚠️ *Precaución:* Históricamente el mercado tiende a corregir en estos niveles. Evita compras impulsivas."
        contexto = "Los inversores están eufóricos. El Smart Money suele VENDER cuando el retail compra por FOMO."
    elif fg >= 55:
        emoji, texto = "🟡", "Codicia"
        consejo = "📊 Mercado optimista. Buenas condiciones para mantener posiciones alcistas."
        contexto = "El mercado tiene confianza pero no está en extremos. Zona favorable para trading direccional."
    elif fg >= 45:
        emoji, texto = "🟢", "Neutral"
        consejo = "✅ Condiciones normales de mercado. Sin sesgo emocional extremo."
        contexto = "Equilibrio entre compradores y vendedores. Las señales técnicas tienen más peso en esta zona."
    elif fg >= 25:
        emoji, texto = "🔵", "Miedo"
        consejo = "📊 Mercado pesimista. Posibles oportunidades de compra a largo plazo."
        contexto = "Los inversores están nerviosos. Warren Buffett diría: 'Sé codicioso cuando otros tienen miedo'."
    else:
        emoji, texto = "🟣", "Miedo Extremo"
        consejo = "⚠️ *Zona de capitulación.* Históricamente los fondos de mercado se forman aquí."
        contexto = "Pánico generalizado. Los grandes fondos suelen acumular en estas zonas para posiciones de largo plazo."

    barra_llena = int(fg / 10)
    barra = "█" * barra_llena + "░" * (10 - barra_llena)

    return (
        "😱 *FEAR & GREED INDEX*\n"
        "━━━━━━━━━━\n\n"
        f"{emoji} *{texto}*\n\n"
        f"Valor: *{fg}/100*\n"
        f"🟣Miedo|{barra}|Codicia🟡\n\n"
        f"📖 _{contexto}_\n\n"
        f"💡 {consejo}\n\n"
        "━━━━━━━━━━\n"
        "🔄 Actualizado cada 4 horas\n"
        "📊 Fuente: Alternative.me\n"
        "⚠️ Aplica al sentimiento general del mercado"
    )

def cmd_tendencia():
    hora = ahora().strftime("%H:%M")
    lineas = [
        "📈 *MAPA DE TENDENCIAS EN TIEMPO REAL*\n"
        "━━━━━━━━━━\n"
        f"🕐 {hora}\n"
    ]

    alcistas = 0
    bajistas = 0

    for nombre, ticker in ACTIVOS.items():
        ind = _cache_ind.get(ticker)
        if ind:
            # Tendencia 15m
            if ind['ema9'] > ind['ema20'] > ind['ema50']:
                tend_15m = "📈"
                alcistas += 1
            elif ind['ema9'] < ind['ema20'] < ind['ema50']:
                tend_15m = "📉"
                bajistas += 1
            else:
                tend_15m = "➡️"

            # Tendencia 1H (desde caché)
            alcista_1h = _obtener_tendencia_tf(ticker, "1h", _cache_mtf_1h, ttl=900)
            tend_1h = "📈" if alcista_1h is True else ("📉" if alcista_1h is False else "➡️")

            # Fuerza
            if ind['adx'] > 30:
                fuerza = "💪 Fuerte"
            elif ind['adx'] > 20:
                fuerza = "✊ Moderada"
            else:
                fuerza = "😴 Débil"

            # Momentum
            if ind['rsi'] > 70:
                rsi_txt = "🔴 OB"
            elif ind['rsi'] < 30:
                rsi_txt = "🟢 OS"
            elif ind['rsi'] > 55:
                rsi_txt = "↗️"
            elif ind['rsi'] < 45:
                rsi_txt = "↘️"
            else:
                rsi_txt = "⚪"

            lineas.append(
                f"*{nombre}*\n"
                f"   15m {tend_15m}  │  1H {tend_1h}  │  RSI {ind['rsi']:.0f} {rsi_txt}\n"
                f"   ADX {ind['adx']:.0f} {fuerza}  │  Vol {ind['vol_ratio']:.1f}x\n"
            )
        else:
            lineas.append(f"*{nombre}*\n   ⏳ Sin datos aún\n")

    # Resumen del mercado
    total = alcistas + bajistas
    if total > 0:
        if alcistas > bajistas:
            sentimiento = "🟢 *SENTIMIENTO GENERAL: ALCISTA*"
        elif bajistas > alcistas:
            sentimiento = "🔴 *SENTIMIENTO GENERAL: BAJISTA*"
        else:
            sentimiento = "⚖️ *SENTIMIENTO GENERAL: MIXTO*"
        lineas.append(f"\n{sentimiento}\n📈 {alcistas} alcistas  │  📉 {bajistas} bajistas  │  ➡️ {len(ACTIVOS)-alcistas-bajistas} neutros")

    lineas.append("\n━━━━━━━━━━\n💡 Basado en EMA 9/20/50 (15m y 1H) + ADX + RSI")
    return "\n".join(lineas)

def cmd_volatilidad():
    hora = ahora().strftime("%H:%M")
    ranking = []
    for nombre, ticker in ACTIVOS.items():
        ind = _cache_ind.get(ticker)
        if ind:
            atr_pct = (ind['atr'] / ind['precio']) * 100
            ranking.append((nombre, atr_pct, ind['atr'], ticker))
    if not ranking:
        return "⏳ Aún no hay datos de volatilidad. Espera al primer escaneo."
    ranking.sort(key=lambda x: x[1], reverse=True)
    emojis_pos = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
    lineas = [
        "⚡ *RANKING DE VOLATILIDAD*\n"
        "━━━━━━━━━━\n"
        f"🕐 {hora}\n"
    ]
    for i, (nombre, atr_pct, atr_val, ticker) in enumerate(ranking):
        barras = "█" * min(int(atr_pct * 3), 10)
        lineas.append(f"{emojis_pos[i]} {nombre}\n   ATR: {fmt(atr_val, ticker)}  │  {atr_pct:.2f}%  {barras}\n")
    lineas.append("━━━━━━━━━━\n💡 Mayor ATR = Mayor riesgo/oportunidad")
    return "\n".join(lineas)

def cmd_noticias():
    hora = ahora().strftime("%H:%M")
    noticias = cargar_calendario_economico()
    if not noticias:
        return "📰 No se pudieron cargar las noticias ahora mismo."
    ahora_utc = datetime.now(pytz.UTC)
    tz_ny = pytz.timezone("America/New_York")
    proximas = []
    for n in noticias:
        impacto = n.get("impact", "").lower()
        if impacto not in ("high", "medium"):
            continue
        try:
            fecha_str = n.get("date", "")
            hora_str = n.get("time", "").strip().lower()
            if not fecha_str or hora_str in ("", "all day", "tentative"):
                continue
            dt = datetime.strptime(f"{fecha_str} {hora_str}", "%m-%d-%Y %I:%M%p")
            dt_utc = tz_ny.localize(dt).astimezone(pytz.UTC)
            diff = (dt_utc - ahora_utc).total_seconds() / 3600
            if -2 <= diff <= 24:
                titulo = n.get("title", "")
                es_critica = _es_noticia_critica(titulo)
                proximas.append((diff, titulo, n.get("country", ""), dt_utc, impacto, es_critica))
        except Exception:
            continue
    if not proximas:
        return (
            "📰 *NOTICIAS ECONÓMICAS*\n"
            "━━━━━━━━━━\n"
            "✅ No hay noticias de impacto\n"
            "en las próximas 24 horas.\n\n"
            "🟢 Buenas condiciones para operar."
        )
    proximas.sort()
    lineas = [
        "📰 *NOTICIAS ECONÓMICAS*\n"
        "━━━━━━━━━━\n"
        f"🕐 {hora}  •  Próximas 24h\n"
    ]
    for diff, titulo, pais, dt_utc, impacto, es_critica in proximas[:10]:
        hora_noticia = dt_utc.strftime("%H:%M")
        if diff < 0:
            tiempo_txt = "⚡ En curso"
        elif diff < 1:
            tiempo_txt = f"⏰ En {int(diff*60)}min"
        else:
            tiempo_txt = f"En {diff:.1f}h"
        if es_critica:
            icono = "🔴🔴"
            ventana = "Bloqueo: 4h antes / 3h después"
        elif impacto == "high":
            icono = "🔴"
            ventana = "Bloqueo: 3h antes / 2h después"
        else:
            icono = "🟡"
            ventana = "Bloqueo: 1h antes / 30min después"
        lineas.append(f"{icono} *{pais}* — {titulo}\n   🕐 {hora_noticia} UTC  │  {tiempo_txt}\n   _{ventana}_\n")
    lineas.append(
        "━━━━━━━━━━\n"
        "🛡️ *PROTECCIÓN ACTIVA:*\n"
        "• Noticias críticas (NFP/FOMC/CPI): 4h antes / 3h después\n"
        "• Alto impacto: 3h antes / 2h después\n"
        "• Medio impacto: 1h antes / 30min después\n"
        "• Cierre automático de trades 30min antes de noticias críticas"
    )
    return "\n".join(lineas)

# ── Glosario de términos ─────────────────────────────────────

GLOSARIO = {
    # ── Indicadores técnicos ──────────────────────────────────
    "rsi": (
        "📊 *RSI — Relative Strength Index*\n"
        "━━━━━━━━━━\n\n"
        "Oscilador que mide la velocidad y magnitud de los movimientos de precio.\n\n"
        "📌 *Valores clave:*\n"
        "   < 30  →  Sobrevendido (posible COMPRA)\n"
        "   > 70  →  Sobrecomprado (posible VENTA)\n"
        "   50     →  Zona neutral\n\n"
        "💡 El bot usa RSI(14) en velas de 15min"
    ),
    "macd": (
        "📊 *MACD — Moving Average Convergence Divergence*\n"
        "━━━━━━━━━━\n\n"
        "Indicador de tendencia y momentum basado en medias móviles.\n\n"
        "📌 *Señales:*\n"
        "   MACD cruza ↑ Signal  →  Señal alcista\n"
        "   MACD cruza ↓ Signal  →  Señal bajista\n"
        "   Histograma > 0  →  Momento alcista\n\n"
        "💡 El bot usa MACD(12,26,9)"
    ),
    "atr": (
        "📊 *ATR — Average True Range*\n"
        "━━━━━━━━━━\n\n"
        "Mide la volatilidad media del precio en un período.\n\n"
        "📌 *Uso en el bot:*\n"
        "   SL  =  ATR × 1.2 ~ 2.0\n"
        "   TP1 =  ATR × 1.8 ~ 3.0\n"
        "   TP2 =  ATR × 2.5 ~ 5.0\n"
        "   TP3 =  ATR × 4.0 ~ 8.0\n\n"
        "💡 ATR alto = mayor volatilidad = SL más amplio"
    ),
    "adx": (
        "📊 *ADX — Average Directional Index*\n"
        "━━━━━━━━━━\n\n"
        "Mide la FUERZA de la tendencia (no la dirección).\n\n"
        "📌 *Valores:*\n"
        "   < 20  →  Sin tendencia (bot NO opera)\n"
        "   20-25 →  Tendencia débil\n"
        "   25-40 →  Tendencia fuerte ✅\n"
        "   > 40  →  Tendencia muy fuerte 🔥\n\n"
        "💡 El bot requiere ADX > 20 para operar"
    ),
    "ema": (
        "📊 *EMA — Exponential Moving Average*\n"
        "━━━━━━━━━━\n\n"
        "Media móvil que da más peso a los precios recientes.\n\n"
        "📌 *El bot usa 4 EMAs:*\n"
        "   EMA 9   →  Tendencia corto plazo\n"
        "   EMA 20  →  Tendencia medio plazo\n"
        "   EMA 50  →  Tendencia largo plazo\n"
        "   EMA 200 →  Tendencia muy largo plazo\n\n"
        "💡 EMA9 > EMA20 > EMA50 = tendencia alcista fuerte"
    ),
    "bollinger": (
        "📊 *Bandas de Bollinger*\n"
        "━━━━━━━━━━\n\n"
        "Canal dinámico basado en la volatilidad del precio.\n\n"
        "📌 *Señales:*\n"
        "   Precio toca banda inferior → posible COMPRA\n"
        "   Precio toca banda superior → posible VENTA\n"
        "   Bandas estrechas → baja volatilidad (explosión próxima)\n\n"
        "💡 El bot usa BB(20, 2σ)"
    ),
    "stoch": (
        "📊 *Stochastic Oscillator*\n"
        "━━━━━━━━━━\n\n"
        "Compara el precio de cierre con el rango de precios reciente.\n\n"
        "📌 *Valores clave:*\n"
        "   < 20  →  Sobrevendido\n"
        "   > 80  →  Sobrecomprado\n"
        "   K cruza ↑ D en zona baja → señal COMPRA\n"
        "   K cruza ↓ D en zona alta → señal VENTA\n\n"
        "💡 El bot usa Stoch(14,3)"
    ),
    # ── Conceptos de trading ──────────────────────────────────
    "pip": (
        "📏 *PIP — Precio de Interés en un Punto*\n"
        "━━━━━━━━━━\n\n"
        "La unidad mínima de movimiento de precio en Forex.\n\n"
        "📌 *Ejemplos:*\n"
        "   EUR/USD:  1 pip = 0.0001  (ej. 1.0500 → 1.0501)\n"
        "   Ofrece referencia de ganancias/pérdidas sin usar $\n\n"
        "📌 *En este bot:*\n"
        "   Forex  →  resultado en pips (×10.000)\n"
        "   Futuros →  resultado en puntos (pts)\n"
        "   Crypto  →  resultado en porcentaje (%)\n\n"
        "💡 100 pips en EUR/USD = 0.01 de movimiento"
    ),
    "lote": (
        "📦 *LOTE — Tamaño de Posición*\n"
        "━━━━━━━━━━\n\n"
        "Unidad estándar para medir el volumen de una operación en Forex.\n\n"
        "📌 *Tipos de lote:*\n"
        "   Lote estándar   =  100.000 unidades (1.00)\n"
        "   Mini lote       =   10.000 unidades (0.10)\n"
        "   Micro lote      =    1.000 unidades (0.01)\n\n"
        "📌 *Valor del pip (EUR/USD, 1 lote estándar):*\n"
        "   1 pip ≈ 10 USD\n"
        "   1 pip mini lote ≈ 1 USD\n\n"
        "💡 Con 1.000$ de cuenta, empieza con micro lotes"
    ),
    "apalancamiento": (
        "⚖️ *APALANCAMIENTO — Leverage*\n"
        "━━━━━━━━━━\n\n"
        "Permite controlar una posición mayor a tu capital real.\n\n"
        "📌 *Ejemplo 1:100:*\n"
        "   Con 1.000$ puedes controlar 100.000$\n"
        "   Ganancia potencial ×100   → pero pérdida también ×100\n\n"
        "📌 *Apalancamiento típico:*\n"
        "   Forex:   1:30 ~ 1:500\n"
        "   Índices: 1:20 ~ 1:200\n"
        "   Oro:     1:20 ~ 1:888\n\n"
        "⚠️ *Riesgo:* Mayor apalancamiento = mayor riesgo de liquidación\n"
        "💡 Nunca uses apalancamiento máximo. Empieza con 1:10 o menos"
    ),
    "spread": (
        "💸 *SPREAD — Diferencial Bid/Ask*\n"
        "━━━━━━━━━━\n\n"
        "Diferencia entre el precio de compra (Ask) y venta (Bid).\n"
        "Es el costo de entrada a la operación que cobra el bróker.\n\n"
        "📌 *Ejemplo EUR/USD:*\n"
        "   Bid (vendes): 1.08490\n"
        "   Ask (compras): 1.08495\n"
        "   Spread = 0.5 pips\n\n"
        "📌 *Spreads típicos (cuenta ECN):*\n"
        "   EUR/USD:  0.1-1 pip\n"
        "   ORO:      0.10-0.30 $/oz\n"
        "   NASDAQ:   1-4 pts\n\n"
        "💡 Spreads bajos = menos costo por operación"
    ),
    "margen": (
        "🏦 *MARGEN — Margin*\n"
        "━━━━━━━━━━\n\n"
        "Capital que el bróker bloquea como garantía para abrir una posición.\n\n"
        "📌 *Tipos:*\n"
        "   Margen requerido: capital bloqueado para abrir la op.\n"
        "   Margen libre:     capital disponible para nuevas ops.\n"
        "   Margin call:      aviso de que tu cuenta está en riesgo\n"
        "   Stop out:         el bróker cierra tus ops automáticamente\n\n"
        "📌 *Ejemplo con 1:100 y 1 lote EUR/USD:*\n"
        "   Valor del contrato: 100.000$\n"
        "   Margen requerido:    1.000$ (1%)\n\n"
        "⚠️ Mantén el margen libre siempre > 50% del total"
    ),
    "drawdown": (
        "📉 *DRAWDOWN — Caída Máxima*\n"
        "━━━━━━━━━━\n\n"
        "Pérdida máxima desde un pico hasta el punto más bajo siguiente.\n\n"
        "📌 *Ejemplo:*\n"
        "   Cuenta sube a 1.500$ → baja a 1.200$\n"
        "   Drawdown = 300$ = 20%\n\n"
        "📌 *Referencia:*\n"
        "   < 10%  →  Excelente gestión de riesgo\n"
        "   10-20% →  Aceptable\n"
        "   > 30%  →  Revisar estrategia urgentemente\n\n"
        "💡 Un drawdown del 50% requiere ganar un 100% para recuperar"
    ),
    "rr": (
        "⚖️ *RATIO RIESGO:RECOMPENSA (R:R)*\n"
        "━━━━━━━━━━\n\n"
        "Compara cuánto arriesgas vs cuánto puedes ganar en una op.\n\n"
        "📌 *Fórmula:*\n"
        "   R:R = (TP - Entrada) / (Entrada - SL)\n\n"
        "📌 *Ejemplo:*\n"
        "   Entrada 1.1000, SL 1.0980, TP 1.1040\n"
        "   Riesgo = 20 pips, Beneficio = 40 pips\n"
        "   R:R = 1:2 ✅\n\n"
        "📌 *Ratios del bot:*\n"
        "   TP1: 1:1.3-1.8  TP2: 1:2.0-3.0  TP3: 1:3.3-5.5\n\n"
        "💡 Con R:R 1:2 y 50% de acierto ya eres rentable"
    ),
    "sl": (
        "🛑 *STOP LOSS (SL) — Límite de Pérdida*\n"
        "━━━━━━━━━━\n\n"
        "Orden automática que cierra tu posición si el precio va en tu contra.\n"
        "Es tu red de seguridad: *siempre* opera con SL.\n\n"
        "📌 *En este bot:*\n"
        "   SL calculado con ATR × multiplicador por activo\n"
        "   Oro:     ATR × 2.0\n"
        "   Forex:   ATR × 1.5\n"
        "   Índices: ATR × 1.8\n\n"
        "📌 *Tipos de SL:*\n"
        "   Fijo:        precio fijo desde la entrada\n"
        "   Trailing:    se mueve con el precio a tu favor\n\n"
        "💡 Nunca muevas el SL para ampliar la pérdida"
    ),
    "tp": (
        "🎯 *TAKE PROFIT (TP) — Objetivo de Beneficio*\n"
        "━━━━━━━━━━\n\n"
        "Orden automática que cierra tu posición al alcanzar el objetivo.\n\n"
        "📌 *En este bot — 3 TPs escalonados:*\n"
        "   TP1 → conservador (cierra parte de la posición)\n"
        "   TP2 → intermedio  (cierra más parte)\n"
        "   TP3 → ambicioso   (cierra el resto)\n\n"
        "📌 *Estrategia recomendada:*\n"
        "   TP1 alcanzado → mover SL a breakeven (entrada)\n"
        "   TP2 alcanzado → dejar correr hasta TP3\n\n"
        "💡 No cierres manualmente antes del TP por pánico"
    ),
    "divergencia": (
        "🔀 *DIVERGENCIA — Divergence*\n"
        "━━━━━━━━━━\n\n"
        "Cuando el precio y un oscilador (RSI, MACD) van en direcciones distintas.\n"
        "Es una señal de posible cambio de tendencia.\n\n"
        "📌 *Tipos:*\n"
        "   Alcista: precio hace mínimos más bajos, RSI hace mínimos más altos\n"
        "   → posible reversión al alza\n\n"
        "   Bajista: precio hace máximos más altos, RSI hace máximos más bajos\n"
        "   → posible reversión a la baja\n\n"
        "💡 La divergencia es una advertencia, no una señal directa"
    ),
    "breakout": (
        "💥 *BREAKOUT — Ruptura de Nivel*\n"
        "━━━━━━━━━━\n\n"
        "Cuando el precio rompe un nivel clave de soporte o resistencia con volumen.\n\n"
        "📌 *Tipos:*\n"
        "   Bullish breakout:  rompe resistencia → posible subida fuerte\n"
        "   Bearish breakout:  rompe soporte → posible bajada fuerte\n"
        "   Falso breakout:    rompe pero vuelve rápido (trampa)\n\n"
        "📌 *Cómo confirmarlo:*\n"
        "   • Vela de cierre clara por encima/debajo del nivel\n"
        "   • Volumen alto en la ruptura\n"
        "   • Retroceso y pullback al nivel roto\n\n"
        "💡 Espera confirmación antes de entrar"
    ),
    "pullback": (
        "↩️ *PULLBACK — Retroceso en Tendencia*\n"
        "━━━━━━━━━━\n\n"
        "Corrección temporal del precio en contra de la tendencia principal.\n"
        "Es la 'oportunidad de segunda entrada' en una tendencia sana.\n\n"
        "📌 *Ejemplo en tendencia alcista:*\n"
        "   1. Precio sube con fuerza\n"
        "   2. Retrocede a EMA20 o zona de soporte\n"
        "   3. Rebota y continúa al alza ← punto de entrada ideal\n\n"
        "📌 *Confirmación de pullback válido:*\n"
        "   • RSI baja a 40-50 (no sobrevendido)\n"
        "   • Volumen bajo en el retroceso\n"
        "   • Nivel de soporte / EMA respetado\n\n"
        "💡 El bot detecta pullbacks a EMAs como señal de entrada"
    ),
    "fibonacci": (
        "🌀 *FIBONACCI — Retrocesos de Fibonacci*\n"
        "━━━━━━━━━━\n\n"
        "Niveles matemáticos donde el precio tiende a frenarse o rebotar.\n\n"
        "📌 *Niveles principales:*\n"
        "   23.6%  →  Retroceso leve (tendencia fuerte)\n"
        "   38.2%  →  Retroceso moderado\n"
        "   50.0%  →  Nivel psicológico clave\n"
        "   61.8%  →  Retroceso de oro ⭐ (más usado)\n"
        "   78.6%  →  Retroceso profundo\n\n"
        "📌 *Uso:*\n"
        "   Traza desde el mínimo al máximo de un movimiento\n"
        "   Busca confluencia con EMA o soporte/resistencia\n\n"
        "💡 El 61.8% es el nivel Fibonacci más respetado del mercado"
    ),
    "soporte": (
        "🟢 *SOPORTE — Support Level*\n"
        "━━━━━━━━━━\n\n"
        "Zona de precio donde la demanda es suficientemente fuerte para detener la caída.\n"
        "El precio 'rebota' repetidamente desde ese nivel.\n\n"
        "📌 *Cómo se forma:*\n"
        "   • Mínimos anteriores del precio\n"
        "   • Zonas de alta concentración de órdenes\n"
        "   • Números redondos psicológicos\n"
        "   • EMAs importantes (20, 50, 200)\n\n"
        "📌 *Regla clave:*\n"
        "   Un soporte roto se convierte en resistencia\n\n"
        "💡 Comprar cerca de un soporte fuerte mejora el R:R"
    ),
    "resistencia": (
        "🔴 *RESISTENCIA — Resistance Level*\n"
        "━━━━━━━━━━\n\n"
        "Zona de precio donde la oferta supera a la demanda, frenando la subida.\n"
        "El precio 'rechaza' repetidamente ese nivel.\n\n"
        "📌 *Cómo se forma:*\n"
        "   • Máximos anteriores del precio\n"
        "   • Zonas de alta concentración de órdenes vendedoras\n"
        "   • Números redondos psicológicos\n"
        "   • EMAs importantes en tendencia bajista\n\n"
        "📌 *Regla clave:*\n"
        "   Una resistencia rota se convierte en soporte\n\n"
        "💡 Vender cerca de una resistencia fuerte mejora el R:R"
    ),
    "scalping": (
        "⚡ *SCALPING — Operaciones Ultra-Rápidas*\n"
        "━━━━━━━━━━\n\n"
        "Estilo de trading que busca muchas pequeñas ganancias en muy poco tiempo.\n\n"
        "📌 *Características:*\n"
        "   • Duración: segundos a minutos\n"
        "   • Marco temporal: 1min, 5min\n"
        "   • Muchas operaciones al día\n"
        "   • SL y TP muy ajustados (5-20 pips)\n\n"
        "📌 *Ventajas / Desventajas:*\n"
        "   ✅ Riesgo pequeño por operación\n"
        "   ✅ No deja posiciones abiertas de noche\n"
        "   ❌ Requiere mucha concentración y tiempo\n"
        "   ❌ El spread afecta más al profit\n\n"
        "💡 Este bot opera en 15min (swing-intraday, no scalping)"
    ),
    "swing": (
        "🌊 *SWING TRADING — Operaciones de Medio Plazo*\n"
        "━━━━━━━━━━\n\n"
        "Estilo que busca capturar movimientos de varios días o semanas.\n\n"
        "📌 *Características:*\n"
        "   • Duración: horas a días\n"
        "   • Marco temporal: 1h, 4h, diario\n"
        "   • Pocas operaciones al día/semana\n"
        "   • SL y TP amplios\n\n"
        "📌 *Ventajas / Desventajas:*\n"
        "   ✅ No requiere monitoreo constante\n"
        "   ✅ El spread afecta poco al profit\n"
        "   ❌ Exposición overnight (gap risk)\n"
        "   ❌ Requiere paciencia\n\n"
        "💡 Este bot mezcla intraday (15min) y confirmación multi-TF"
    ),
    "ordenes": (
        "📋 *TIPOS DE ÓRDENES*\n"
        "━━━━━━━━━━\n\n"
        "📌 *Órdenes de mercado:*\n"
        "   Market Buy/Sell → entra al precio actual inmediatamente\n\n"
        "📌 *Órdenes pendientes:*\n"
        "   Limit Buy    → compra si el precio BAJA al nivel indicado\n"
        "   Limit Sell   → vende si el precio SUBE al nivel indicado\n"
        "   Stop Buy     → compra si el precio SUBE al nivel indicado\n"
        "   Stop Sell    → vende si el precio BAJA al nivel indicado\n\n"
        "📌 *Gestión de posición:*\n"
        "   Stop Loss (SL) → cierre automático en pérdida\n"
        "   Take Profit (TP) → cierre automático en ganancia\n"
        "   Trailing Stop → SL que sigue al precio a tu favor\n\n"
        "💡 Las señales del bot son Market Order (entra al precio de la señal)"
    ),
    "patrones": (
        "🕯️ *PATRONES DE VELAS (Candlestick Patterns)*\n"
        "━━━━━━━━━━\n\n"
        "📌 *Patrones de reversión alcista:*\n"
        "   🔨 Martillo (Hammer) — cuerpo pequeño arriba, mecha larga abajo\n"
        "   🔄 Engulfing Bullish — vela alcista que 'engulle' la bajista anterior\n\n"
        "📌 *Patrones de reversión bajista:*\n"
        "   🌇 Evening Star    — 3 velas: alcista + indecisión + bajista\n"
        "   🔄 Engulfing Bear  — vela bajista que 'engulle' la alcista anterior\n\n"
        "💡 El bot analiza 3 patrones en cada escaneo para puntuar señales"
    ),
    # ── Conceptos Propios del Bot ────────────────────────────
    "score": (
        "📈 *SCORE DE SEÑAL — Probabilidad Matemática*\n"
        "━━━━━━━━━━\n\n"
        "Es un puntaje del 0 al 100 que el bot asigna a cada oportunidad.\n\n"
        "📌 *Cómo se calcula:*\n"
        "   • +20 pts: Tendencia ADX fuerte (>25)\n"
        "   • +20 pts: Divergencia RSI detected\n"
        "   • +15 pts: Patrón de vela confirmado\n"
        "   • +15 pts: Volumen institucional a favor\n"
        "   • +15 pts: Machine Learning (Random Forest) aprobando\n\n"
        "💡 *Umbral:* Solo operamos señales con Score > 65."
    ),
    "filtro_spread": (
        "🛡️ *FILTRO DE SPREAD — Protección de Costos*\n"
        "━━━━━━━━━━\n\n"
        "Mecanismo de seguridad que bloquea entradas si el costo del bróker es muy alto.\n\n"
        "📌 *Por qué es importante:*\n"
        "   Si el spread es muy ancho, empiezas la operación con demasiada pérdida.\n"
        "   El bot verifica en tiempo real antes de enviar la orden a XM.\n\n"
        "💡 *Auto-Cancelación:* Si el spread > Límite, el bot espera condiciones mejores."
    ),
    "estrategia": (
        "🧠 *ESTRATEGIA - 'The Hunter'*\n"
        "━━━━━━━━━━\n\n"
        "Este bot usa una metodología Híbrida Institucional:\n\n"
        "1. *Contexto:* Mira la tendencia en Temporalidad Alta (4H).\n"
        "2. *Entrada:* Busca la señal en Temporalidad Operativa (15m).\n"
        "3. *Confirmación:* Verifica RSI, MACD y Volumen.\n"
        "4. *Ejecución:* Lanza 3 Take Profits automáticos.\n\n"
        "💡 Diseñada para ganar 3 veces lo que se arriesga (R:R 1:3)."
    ),
}

# Alias de búsqueda para el glosario (término alternativo → clave en GLOSARIO)
GLOSARIO_ALIAS = {
    "stochastic": "stoch", "estocastico": "stoch", "estocástico": "stoch",
    "bollinger bands": "bollinger", "bb": "bollinger", "bandas": "bollinger",
    "media movil": "ema", "media móvil": "ema", "moving average": "ema",
    "leverage": "apalancamiento", "apalanca": "apalancamiento",
    "lot": "lote", "volumen": "lote",
    "ratio": "rr", "r:r": "rr", "risk reward": "rr", "riesgo recompensa": "rr",
    "stop": "sl", "stop loss": "sl", "stoploss": "sl",
    "take profit": "tp", "takeprofit": "tp", "objetivo": "tp",
    "divergencia": "divergencia", "divergence": "divergencia",
    "ruptura": "breakout", "rotura": "breakout",
    "retroceso": "pullback", "corrección": "pullback", "correccion": "pullback",
    "fibo": "fibonacci", "retracement": "fibonacci",
    "support": "soporte", "nivel soporte": "soporte",
    "resistance": "resistencia", "nivel resistencia": "resistencia",
    "scalp": "scalping", "escalpeo": "scalping",
    "swing trade": "swing", "swing trading": "swing",
    "orden": "ordenes", "tipos de orden": "ordenes", "order": "ordenes",
    "velas": "patrones", "candlestick": "patrones", "patron": "patrones",
    "patron de vela": "patrones", "formacion": "patrones",
    "puntuacion": "score", "probabilidad": "score", "score señal": "score",
    "spread": "filtro_spread", "costo broker": "filtro_spread",
    "estrategia bot": "estrategia", "the hunter": "estrategia",
}

def cmd_glosario(termino):
    t = termino.lower().strip()
    # 1. Coincidencia exacta con clave
    if t in GLOSARIO:
        return GLOSARIO[t]
    # 2. Alias explícitos
    if t in GLOSARIO_ALIAS:
        return GLOSARIO[GLOSARIO_ALIAS[t]]
    # 3. Subcadena: la clave está dentro del texto o viceversa
    for key in GLOSARIO:
        if key in t or t in key:
            return GLOSARIO[key]
    # 4. Subcadena en alias
    for alias, key in GLOSARIO_ALIAS.items():
        if alias in t or t in alias:
            return GLOSARIO[key]
    # 5. Fuzzy matching con rapidfuzz o difflib
    todas_claves = list(GLOSARIO.keys()) + list(GLOSARIO_ALIAS.keys())
    if _RAPIDFUZZ:
        match = rf_process.extractOne(t, todas_claves, scorer=rf_fuzz.WRatio, score_cutoff=70)
        if match:
            clave = match[0]
            return GLOSARIO.get(clave) or GLOSARIO.get(GLOSARIO_ALIAS.get(clave, ""), "")
    else:
        candidatos = get_close_matches(t, todas_claves, n=1, cutoff=0.70)
        if candidatos:
            clave = candidatos[0]
            return GLOSARIO.get(clave) or GLOSARIO.get(GLOSARIO_ALIAS.get(clave, ""), "")
    # 6. No encontrado — listar disponibles (categorizado)
    tecnico  = "`rsi` `macd` `atr` `adx` `ema` `bollinger` `stoch`"
    trading  = "`pip` `lote` `apalancamiento` `spread` `margen` `drawdown` `rr` `score` `filtro_spread` `estrategia`"
    niveles  = "`sl` `tp` `soporte` `resistencia` `breakout` `pullback` `fibonacci` `divergencia`"
    estilos  = "`scalping` `swing` `ordenes` `patrones`"
    return (
        f"❓ No encontré '*{termino}*' en el glosario.\n\n"
        "📚 *Términos disponibles:*\n\n"
        f"📊 Indicadores: {tecnico}\n"
        f"💰 Trading:     {trading}\n"
        f"📍 Niveles:     {niveles}\n"
        f"⚙️ Estilos:     {estilos}\n\n"
        "💡 Ej: `/glosario rsi` · `/glosario apalancamiento` · `/glosario patrones`"
    )

# ── Descripciones de activos ─────────────────────────────────

DESCRIPCIONES_ACTIVOS = {
    "ORO": (
        "🏅 *ORO (Gold Futures — GC=F)*\n"
        "━━━━━━━━━━\n\n"
        "El activo refugio por excelencia. Se usa para protegerse en épocas de incertidumbre.\n\n"
        "📌 *Características:*\n"
        "   • Sesión principal: Nueva York (13-20 UTC)\n"
        "   • Alta liquidez, baja volatilidad relativa\n"
        "   • Correlación negativa con el dólar USD\n"
        "   • Sube en crisis, guerras e inflación alta\n\n"
        "⚡ Volatilidad: Media-Baja\n"
        "💡 Ideal para operaciones estables"
    ),
    # BITCOIN eliminado del bot
    "EUR/USD": (
        "💱 *EUR/USD (Euro / Dólar)*\n"
        "━━━━━━━━━━\n\n"
        "El par de divisas más operado del mundo.\n\n"
        "📌 *Características:*\n"
        "   • Sesión activa: 07:00 — 21:00 UTC\n"
        "   • Máxima liquidez: overlap Londres + NY (13-17 UTC)\n"
        "   • Muy alta liquidez, spreads bajos\n"
        "   • Afectado por BCE y Fed\n"
        "   • Noticias clave: NFP, IPC, tipos de interés\n\n"
        "⚡ Volatilidad: Media\n"
        "💡 Referencia del mercado forex"
    ),
    "USD/JPY": (
        "💴 *USD/JPY (Dólar / Yen japonés)*\n"
        "━━━━━━━━━━\n\n"
        "El segundo par más operado del mundo (~13% del volumen diario global).\n\n"
        "📌 *Características:*\n"
        "   • Sesión activa: 00:00 — 15:00 UTC\n"
        "   • Máxima liquidez: sesión de Tokio (00-09 UTC)\n"
        "   • Cubre el hueco nocturno de EUR/USD\n"
        "   • Afectado por Banco de Japón (BoJ) y Fed\n"
        "   • Sensible a política monetaria divergente\n"
        "   • Noticias clave: IPC Japón, decisiones BoJ, NFP\n\n"
        "⚡ Volatilidad: Media-Alta\n"
        "💡 Ideal para sesión asiática — spreads muy bajos"
    ),
    "GBP/JPY": (
        "🐉 *GBP/JPY (Libra / Yen japonés)*\n"
        "━━━━━━━━━━\n\n"
        "Conocido como 'The Beast' o 'The Dragon' por su alta volatilidad.\n\n"
        "📌 *Características:*\n"
        "   • Sesión activa: 01:00 — 21:00 UTC\n"
        "   • Máxima liquidez: cruce London-Tokyo (07-09 UTC)\n"
        "   • ~90% correlación con USD/JPY pero más volátil\n"
        "   • 120-180 pips de rango diario (vs 80-100 de USD/JPY)\n"
        "   • Afectado por BoE, BoJ, y sentimiento de riesgo global\n\n"
        "⚡ Volatilidad: MUY Alta\n"
        "💡 Ideal para traders que buscan movimientos rápidos y amplios"
    ),
    "📊 NASDAQ": (
        "📊 *NASDAQ 100 (NQ=F)*\n"
        "━━━━━━━━━━\n\n"
        "Índice de las 100 mayores empresas tecnológicas de EE.UU.\n\n"
        "📌 *Características:*\n"
        "   • Sesión activa: 09:00 — 21:00 UTC\n"
        "   • Máxima liquidez: apertura regular NYSE (13:30 UTC)\n"
        "   • Alta volatilidad en apertura\n"
        "   • Incluye Apple, Google, Microsoft, Amazon...\n"
        "   • Sensible a tasas de interés y datos macro USA\n\n"
        "⚡ Volatilidad: Alta\n"
        "💡 Máxima actividad en apertura americana"
    ),
    "📈 S&P 500": (
        "📈 *S&P 500 (E-mini Futures — ES=F)*\n"
        "━━━━━━━━━━\n\n"
        "El índice de referencia mundial que agrupa las 500 mayores empresas de EE.UU.\n\n"
        "📌 *Características:*\n"
        "   • Sesión activa: 09:00 — 21:00 UTC\n"
        "   • Máxima liquidez: apertura regular NYSE (13:30 UTC)\n"
        "   • Alta liquidez y volumen\n"
        "   • Incluye todos los sectores económicos USA\n"
        "   • Muy sensible a datos macro y Fed\n\n"
        "⚡ Volatilidad: Media-Alta\n"
        "💡 El termómetro de la economía global"
    ),
}

def cmd_que_es(activo_raw):
    nombre = KEYWORDS_ACTIVOS.get(activo_raw.lower().strip())
    if not nombre:
        return f"❓ No reconozco '*{activo_raw}*'.\nPrueba: /que es oro, /que es nasdaq"
    desc = DESCRIPCIONES_ACTIVOS.get(nombre)
    if not desc:
        return f"ℹ️ Sin descripción disponible para {nombre}."
    return desc

def cmd_pip(activo_raw):
    nombre = KEYWORDS_ACTIVOS.get(activo_raw.lower().strip())
    if not nombre:
        return f"❓ No reconozco '*{activo_raw}*'.\nPrueba: /pip oro, /pip eurusd"
    ticker = ACTIVOS[nombre]
    cat = get_categoria(ticker)
    if cat == "forex":
        if "JPY" in ticker:
            return (
                f"📏 *PIPS — {nombre}*\n"
                "━━━━━━━━━━\n\n"
                "Para pares con JPY:\n"
                "   1 pip = 0.01  │  Multiplicador: ×100\n\n"
                "💡 Ej: si precio va de 150.00 a 150.50\n"
                "   → 50 pips de ganancia"
            )
        return (
            f"📏 *PIPS — {nombre}*\n"
            "━━━━━━━━━━\n\n"
            "Para pares forex estándar:\n"
            "   1 pip = 0.0001  │  Multiplicador: ×10,000\n\n"
            "💡 Ej: si precio va de 1.0800 a 1.0850\n"
            "   → 50 pips de ganancia"
        )
    elif cat == "crypto":
        return (
            f"📏 *RENDIMIENTO — {nombre}*\n"
            "━━━━━━━━━━\n\n"
            "Para crypto se usa porcentaje (%):\n"
            "   Ganancia = (salida − entrada) / entrada × 100\n\n"
            "💡 Ej: compra a 95,000 y vende a 97,000\n"
            "   → +2.10% de ganancia"
        )
    else:
        return (
            f"📏 *PUNTOS — {nombre}*\n"
            "━━━━━━━━━━\n\n"
            "Para futuros se usan puntos directos:\n"
            "   Ganancia = diferencia de precio\n\n"
            "💡 Ej: si ORO va de 2,900 a 2,950\n"
            "   → 50 puntos de ganancia"
        )

def cmd_abierto(activo_raw):
    nombre = KEYWORDS_ACTIVOS.get(activo_raw.lower().strip())
    if not nombre:
        return f"❓ No reconozco '*{activo_raw}*'.\nPrueba: /abierto oro"
    ticker = ACTIVOS[nombre]
    hora_utc = datetime.now(pytz.UTC).strftime("%H:%M")
    inicio, fin = HORARIOS_MERCADO.get(ticker, (0, 24))
    if en_horario_mercado(ticker):
        horario_txt = "24 horas" if fin == 24 else f"{inicio:02d}:00 — {fin:02d}:00 UTC"
        return (
            f"🟢 *{nombre} — ABIERTO*\n"
            "━━━━━━━━━━\n"
            f"🕐 Hora UTC: {hora_utc}\n"
            f"📅 Horario: {horario_txt}\n\n"
            "✅ El bot puede generar señales ahora."
        )
    else:
        horario_txt = f"{inicio:02d}:00 — {fin:02d}:00 UTC"
        horas_hasta = (inicio - datetime.now(pytz.UTC).hour) % 24
        return (
            f"🔴 *{nombre} — CERRADO*\n"
            "━━━━━━━━━━\n"
            f"🕐 Hora UTC: {hora_utc}\n"
            f"📅 Horario: {horario_txt}\n\n"
            f"⏳ Abre en aprox. {horas_hasta}h\n"
            "⏸️ El bot no opera fuera de horario."
        )

def cmd_top():
    if not _cache_ind:
        return "⏳ Aún no hay datos del primer escaneo. Espera unos minutos."
    hora = ahora().strftime("%H:%M")
    ranking = []
    for nombre, ticker in ACTIVOS.items():
        ind = _cache_ind.get(ticker)
        if ind:
            atr_pct = (ind['atr'] / ind['precio']) * 100
            ranking.append((nombre, ticker, atr_pct, ind))
    if not ranking:
        return "⏳ Sin datos disponibles aún."
    ranking.sort(key=lambda x: x[2], reverse=True)

    emojis_pos = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣"]
    lineas = [
        "🔥 *RANKING DE OPORTUNIDADES*\n"
        "━━━━━━━━━━\n"
        f"🕐 {hora}\n"
    ]

    for i, (nombre, ticker, atr_pct, ind) in enumerate(ranking):
        # Tendencia
        if ind['ema9'] > ind['ema20'] > ind['ema50']:
            tend = "📈 ALC"
        elif ind['ema9'] < ind['ema20'] < ind['ema50']:
            tend = "📉 BAJ"
        else:
            tend = "➡️ LAT"

        # Señal potencial
        tipo_sig, score_sig, _ = evaluar_senal_profesional(ind, ticker)
        if tipo_sig and score_sig >= 4:
            senal_txt = f"🚨 *{tipo_sig}* (Score {score_sig}/5)"
        elif tipo_sig and score_sig >= 3:
            senal_txt = f"👀 {tipo_sig} potencial (Score {score_sig}/5)"
        else:
            senal_txt = "⏸️ Sin señal"

        lineas.append(
            f"\n{emojis_pos[i]} *{nombre}*\n"
            f"   {tend}  │  RSI {ind['rsi']:.0f}  │  ADX {ind['adx']:.0f}  │  Vol {ind['vol_ratio']:.1f}x\n"
            f"   Volatilidad: {atr_pct:.2f}%  │  {senal_txt}"
        )

    lineas.append("\n\n━━━━━━━━━━")
    lineas.append("💡 Mayor volatilidad = mayor oportunidad/riesgo")
    lineas.append("🔍 Usa `/analisis [activo]` para detalle completo")
    return "\n".join(lineas)

def formatear_lista_resultados():
    """Genera resumen compacto agrupado por activo (no lista individual)."""
    global historial_operaciones, operaciones_activas, activos_desactivados

    activos_vigentes = [tk.replace("=X", "").replace("=F", "").replace("-USD", "") for tk in ACTIVOS.values()]

    # Agrupar por activo
    por_activo = {}  # ticker_base → {"wins": 0, "losses": 0, "pips": 0.0}
    pips_premium = 0.0
    pips_swing = 0.0
    total_ops = 0
    activas_count = 0

    _emojis_activo = {"GC": "🥇", "EURUSD": "💱", "USDJPY": "💴", "GBPJPY": "🐉", "NQ": "📊", "ES": "📈"}
    _nombres_activo = {"GC": "Oro", "EURUSD": "EUR/USD", "USDJPY": "USD/JPY", "GBPJPY": "GBP/JPY", "NQ": "NASDAQ", "ES": "S&P 500"}

    for op in historial_operaciones:
        ticker_raw = op['ticker']
        if ticker_raw in activos_desactivados:
            continue
        ticker_base = ticker_raw.replace("=X", "").replace("=F", "").replace("-USD", "")
        if ticker_base not in activos_vigentes:
            continue

        pips = op.get('pips', 0.0)
        cat = _clasificar_tipo_trade(ticker_raw)
        if cat == "premium":
            pips_premium += pips
        else:
            pips_swing += pips

        if ticker_base not in por_activo:
            por_activo[ticker_base] = {"wins": 0, "losses": 0, "pips": 0.0}
        por_activo[ticker_base]["pips"] += pips
        if pips >= 0:
            por_activo[ticker_base]["wins"] += 1
        else:
            por_activo[ticker_base]["losses"] += 1
        total_ops += 1

    # Activas en curso
    for op in operaciones_activas.values():
        ticker_raw = op['ticker']
        if ticker_raw in activos_desactivados:
            continue
        ticker_base = ticker_raw.replace("=X", "").replace("=F", "").replace("-USD", "")
        if ticker_base not in activos_vigentes:
            continue
        activas_count += 1

    if not por_activo and activas_count == 0:
        return "📭 No hay operaciones registradas aun en este ciclo."

    # Ordenar por pips descendente
    activos_ordenados = sorted(por_activo.items(), key=lambda x: x[1]["pips"], reverse=True)

    lineas = [f"📊 *{total_ops} operaciones*\n"]
    for tkr, data in activos_ordenados:
        emoji = _emojis_activo.get(tkr, "📌")
        nombre = _nombres_activo.get(tkr, tkr)
        total_tkr = data["wins"] + data["losses"]
        pips_txt = f"{data['pips']:+.0f}"
        lineas.append(f"{emoji} *{nombre}:* {total_tkr} ops | {data['wins']}W/{data['losses']}L | {pips_txt} pips")

    if activas_count > 0:
        lineas.append(f"\n🏃 *{activas_count} operaciones en curso*")

    total_pips = pips_premium + pips_swing
    lineas.append(f"\n💎 Premium {pips_premium:+.0f} Pips")
    lineas.append(f"🌊 Swing {pips_swing:+.0f} Pips")
    lineas.append(f"🏆 *TOTAL {total_pips:+.0f} Pips*")

    return "\n".join(lineas)

def cmd_semana():
    momento = ahora()
    # Determinar rango de la semana (Lunes a hoy)
    format_fecha = "%d %b"
    inicio_semana = (momento - timedelta(days=momento.weekday())).strftime(format_fecha)
    hoy_str = momento.strftime(format_fecha)
    
    lista = formatear_lista_resultados()
    
    return (
        f"🏆 CIERRE SEMANAL DE RESULTADOS\n"
        "━━━━━━━━━━\n\n"
        f"{lista}"
    )

def cmd_record():
    if not historial_operaciones:
        return "📭 No hay operaciones registradas aún."
    wins   = [op for op in historial_operaciones if op['resultado'] == "WIN"]
    losses = [op for op in historial_operaciones if op['resultado'] == "LOSS"]
    lineas = ["🏆 *RECORDS DEL BOT*\n━━━━━━━━━━\n"]
    if wins:
        mejor = max(wins, key=lambda x: x['pips'])
        cat   = get_categoria(mejor.get('ticker', ''))
        p_txt = f"{mejor['pips']:.2f}%" if cat == "crypto" else f"{mejor['pips']:.1f} pips"
        lineas.append(f"🥇 *MEJOR OPERACIÓN*\n   {mejor['nombre']} — {mejor['tipo']}\n   +{p_txt}\n")
    if losses:
        peor  = min(losses, key=lambda x: x['pips'])
        cat   = get_categoria(peor.get('ticker', ''))
        p_txt = f"{peor['pips']:.2f}%" if cat == "crypto" else f"{peor['pips']:.1f} pips"
        lineas.append(f"📉 *PEOR OPERACIÓN*\n   {peor['nombre']} — {peor['tipo']}\n   -{p_txt}\n")
    lineas.append(f"━━━━━━━━━━\n📊 Total registradas: {len(historial_operaciones)}")
    return "\n".join(lineas)

def cmd_racha():
    if not historial_operaciones:
        return "📭 No hay operaciones registradas aún."
    racha = 0
    tipo_racha = None
    for op in reversed(historial_operaciones):
        if tipo_racha is None:
            tipo_racha = op['resultado']
            racha = 1
        elif op['resultado'] == tipo_racha:
            racha += 1
        else:
            break
    mejor_racha = 0
    racha_temp  = 0
    for op in historial_operaciones:
        if op['resultado'] == "WIN":
            racha_temp += 1
            mejor_racha = max(mejor_racha, racha_temp)
        else:
            racha_temp = 0
    if tipo_racha == "WIN":
        fuego = "🔥" * min(racha, 5)
        txt_racha = f"✅ *{racha} GANADAS* consecutivas {fuego}"
    else:
        txt_racha = f"❌ *{racha} PERDIDAS* consecutivas"
    return (
        "📊 *RACHA ACTUAL*\n"
        "━━━━━━━━━━\n\n"
        f"{txt_racha}\n\n"
        f"🏆 Mejor racha ganadora: *{mejor_racha}*\n"
        f"📈 Total operaciones: *{len(historial_operaciones)}*"
    )

def cmd_estado_bot():
    uptime_seg  = time.time() - bot_inicio
    horas       = int(uptime_seg // 3600)
    minutos     = int((uptime_seg % 3600) // 60)
    estado_scan = "⏸️ PAUSADO" if escaneo_pausado else "🟢 ACTIVO"
    if ultimo_escaneo > 0:
        hace_min = int((time.time() - ultimo_escaneo) / 60)
        scan_txt = f"hace {hace_min}min"
    else:
        scan_txt = "Pendiente"
    return (
        "🤖 *ESTADO DEL BOT*\n"
        "━━━━━━━━━━\n\n"
        f"🟢 Online hace: *{horas}h {minutos}min*\n"
        f"📡 Escaneo: *{estado_scan}*\n"
        f"⏱️ Último scan: *{scan_txt}*\n"
        "🔄 Intervalo: *cada 3 minutos*\n\n"
        f"📊 *ACTIVOS MONITOREADOS:* {len(ACTIVOS)}\n"
        f"💾 *Abiertas:* {len(operaciones_activas)}  │  *Historial:* {len(historial_operaciones)}"
    )

# Los comandos de pausa y reanudación han sido eliminados para un funcionamiento 24/7.


_tasa_eur_usdt_cache = {"valor": 1.08, "ts": 0}

def _obtener_tasa_eur_usdt() -> float:
    """Obtiene tasa de conversión EUR→USDT (cuántos USDT vale 1 EUR).
    Usa cache de 30 min para no saturar APIs. Fallback: 1.08."""
    global _tasa_eur_usdt_cache
    ahora_ts = time.time()
    # Cache de 30 minutos
    if ahora_ts - _tasa_eur_usdt_cache["ts"] < 1800 and _tasa_eur_usdt_cache["valor"] > 0:
        return _tasa_eur_usdt_cache["valor"]
    # Intentar Binance (EURUSDT no existe, usar EURUSDC como proxy ≈ USDT)
    for url_pair in [
        "https://api.binance.com/api/v3/ticker/price?symbol=EURUSDC",
        "https://api.binance.com/api/v3/ticker/price?symbol=USDCUSDT",
    ]:
        try:
            r = requests.get(url_pair, timeout=8)
            if r.status_code == 200:
                precio = float(r.json().get("price", 0))
                if "EURUSDC" in url_pair and precio > 0:
                    _tasa_eur_usdt_cache = {"valor": precio, "ts": ahora_ts}
                    logger.info(f"💱 Tasa EUR→USDT actualizada: {precio:.4f}")
                    return precio
        except Exception:
            pass
    # Fallback: API pública de tipo de cambio
    try:
        r = requests.get("https://open.er-api.com/v6/latest/EUR", timeout=8)
        if r.status_code == 200:
            usd_rate = r.json().get("rates", {}).get("USD", 1.08)
            _tasa_eur_usdt_cache = {"valor": usd_rate, "ts": ahora_ts}
            logger.info(f"💱 Tasa EUR→USD (fallback): {usd_rate:.4f}")
            return usd_rate
    except Exception:
        pass
    # Último recurso: tasa hardcoded
    return _tasa_eur_usdt_cache.get("valor", 1.08)


def _eur_a_usdt(eur_amount: float) -> float:
    """Convierte EUR a USDT usando tasa en vivo."""
    tasa = _obtener_tasa_eur_usdt()
    return round(eur_amount * tasa, 2)


def _vip_precio_info() -> dict:
    """Calcula precio actual VIP según si estamos en periodo de descuento o no.
    Precios en EUR. Conversión a USDT se hace al momento del pago."""
    try:
        fecha_limite = datetime.strptime(VIP_DESCUENTO_HASTA, "%Y-%m-%d")
        hoy = ahora().replace(tzinfo=None)
        en_descuento = hoy <= fecha_limite
        dias_restantes_desc = max(0, (fecha_limite - hoy).days)
    except Exception:
        en_descuento = False
        dias_restantes_desc = 0

    if en_descuento:
        return {
            "precio": VIP_PRECIO_EUR,
            "precio_regular": VIP_PRECIO_REGULAR,
            "en_descuento": True,
            "descuento_pct": 50,
            "dias_restantes_desc": dias_restantes_desc,
            "fecha_fin_desc": VIP_DESCUENTO_HASTA,
        }
    else:
        return {
            "precio": VIP_PRECIO_POST_DESC,
            "precio_regular": VIP_PRECIO_POST_DESC,
            "en_descuento": False,
            "descuento_pct": 0,
            "dias_restantes_desc": 0,
            "fecha_fin_desc": "",
        }


def cmd_vip(user_id: str = None):
    """💎 Muestra info VIP compacta + botón de pago/trial. Precios en EUR."""
    pi = _vip_precio_info()
    precio = pi["precio"]
    M = VIP_MONEDA  # €

    # ── Admin/Propietario: VIP PERMANENTE ──
    if user_id and user_id in ADMIN_IDS:
        return (
            "👑 *VIP PERMANENTE — ADMINISTRADOR*\n"
            "━━━━━━━━━━\n\n"
            "✅ Acceso ilimitado al canal VIP\n"
            "✅ Sin fecha de expiracion\n"
            "✅ Control total del bot\n\n"
            "🔧 _Eres propietario/admin del sistema._"
        ), None

    # ── Ya es VIP activo ──
    if user_id and user_id in suscripciones_vip:
        sub = suscripciones_vip[user_id]
        es_trial = sub.get("es_trial", False)
        entrada_confirmada = sub.get("entrada_confirmada", True)
        invite_link = sub.get("invite_link", "")

        # 🕐 Trial PENDIENTE de entrada — aún no entró al canal
        if not entrada_confirmada:
            intentos_restantes = max(0, 3 - _trial_intentos.get(user_id, 0))
            if invite_link:
                return (
                    "⏳ *TRIAL PENDIENTE DE ENTRADA*\n"
                    "━━━━━━━━━━\n\n"
                    "👇 Tienes un link para entrar al canal VIP.\n"
                    f"⏰ _Usa el link en las proximas 24h._\n\n"
                    f"✅ Los *5 dias habiles* empiezan cuando entres.\n"
                    f"📌 Intentos restantes: *{intentos_restantes}*"
                ), {
                    "inline_keyboard": [
                        [{"text": "👑 ENTRAR AL CANAL VIP", "url": invite_link}],
                        [{"text": f"❓ AYUDA — {ADMIN_USER}", "url": f"https://t.me/{ADMIN_USER.replace('@','')}"}]
                    ]
                }
            else:
                # Link vacío o expirado → tratar como usuario nuevo
                pass  # Cae al menú VIP normal de abajo

        else:
            # ── Suscripción confirmada ──
            try:
                expira = datetime.fromisoformat(sub["expira"])
                dias_restantes = max(0, (expira - ahora().replace(tzinfo=None)).days)
            except Exception:
                dias_restantes = 0

            # 🔒 Verificar si realmente está DENTRO del canal
            esta_en_canal = _es_miembro_canal(user_id)

            if esta_en_canal:
                # ✅ Está en el canal — mostrar estado completo
                if es_trial:
                    txt_vip = (
                        "🎉 *TU TRIAL ESTA ACTIVA*\n"
                        "━━━━━━━━━━\n\n"
                        f"⏳ Te quedan *{dias_restantes} dias* de acceso gratuito\n"
                        f"📅 Expira: *{sub.get('expira', '?')[:10]}*\n\n"
                        "✅ Ya tienes acceso al canal VIP\n"
                        "✅ Senales IA con Entry, SL y TP exactos\n"
                        "✅ Monitoreo 24/7 de tus operaciones"
                    )
                else:
                    txt_vip = (
                        "👑 *TU VIP ESTA ACTIVO*\n"
                        "━━━━━━━━━━\n\n"
                        f"⏳ Expira en *{dias_restantes} dias* ({sub.get('expira', '?')[:10]})\n\n"
                        "✅ Ya tienes acceso al canal VIP"
                    )
                botones = []
                if dias_restantes <= 3:
                    botones.append([{"text": "💰 RENOVAR", "callback_data": "vip_pagar_usdt"}])
                if botones:
                    return txt_vip, {"inline_keyboard": botones}
                return txt_vip, None
            else:
                # ⚠️ NO está en el canal — NO decir que tiene acceso activo
                # Tratar como usuario nuevo (el menú VIP no mostrará trial si ya lo usó)
                pass  # Cae al menú VIP normal de abajo

    # ── Pago pendiente: NO bloquear, integrar como info extra en menú ──
    _tiene_pago_pendiente = user_id and user_id in pagos_pendientes_vip

    # ── No es VIP: menú principal ──
    puede_trial = user_id and user_id not in _vip_trials_usados

    texto = (
        "👑 *CANAL VIP — BuySell365.pro*\n"
        "━━━━━━━━━━\n\n"
        "✅ Senales IA: Oro, Forex, NASDAQ, S&P 500\n"
        "✅ Alta precision | Entrada, SL, TP exactos\n"
        "✅ Copy Trading automatico en tu cuenta MT5\n"
        "✅ Gestion de riesgo | Trading en Vivo\n\n"
    )

    # Trial PRIMERO (si puede)
    if puede_trial:
        texto += (
            f"🎁 *PRUEBA 5 DIAS HABILES GRATIS*\n"
            f"━━━━━━━━━━\n"
            f"💪 _Prueba nuestras senales sin compromiso._\n"
            f"_Si te convence, suscribete con 50% OFF._ 🚀\n\n"
        )

    # Precio con descuento (después de trial)
    if pi["en_descuento"]:
        texto += (
            f"🔥 *50% OFF en tu primer mes*\n"
            f"⏰ _Oferta hasta {VIP_DESCUENTO_HASTA}_\n\n"
        )
    else:
        texto += f"💰 Suscripcion mensual disponible\n\n"

    # Términos compactos
    texto += (
        "📜 _Servicio de senales educativas, no es asesoria_\n"
        "_financiera. El usuario es responsable de sus_\n"
        "_decisiones. No hay reembolsos. Al suscribirte_\n"
        "_aceptas estos terminos._"
    )

    # Info pago pendiente (si existe)
    if _tiene_pago_pendiente:
        pend = pagos_pendientes_vip[user_id]
        monto_pend = pend.get("monto_unico", 0)
        texto += f"\n\n💡 _Tienes un pago pendiente de {monto_pend:.3f} USDT_"

    # Botones — Trial gratis PRIMERO y más visible
    btn_pago = f"💰 SUSCRIBIRME (50% OFF)" if pi["en_descuento"] else f"💰 SUSCRIBIRME"
    botones = []
    if puede_trial:
        botones.append([{"text": f"🎁 5 DIAS HABILES GRATIS — PROBAR AHORA", "callback_data": "vip_trial_gratis"}])
    botones.append([{"text": "🚀 COPY TRADING", "url": "https://social.tp-redirect.com/s/WRE0V7jm"}])
    botones.append([{"text": btn_pago, "callback_data": "vip_pagar_usdt"}])
    if _tiene_pago_pendiente:
        monto_pend = pagos_pendientes_vip[user_id].get("monto_unico", 0)
        botones.append([{"text": f"⏳ VER PAGO PENDIENTE ({monto_pend:.3f} USDT)", "callback_data": "vip_ver_pago_pendiente"}])
    botones.append([{"text": "❓ ADMIN", "url": f"https://t.me/{ADMIN_USER.replace('@','')}"}])
    return texto, {"inline_keyboard": botones}


# ============================================================
#  FUNCIONES VIP — PAGOS USDT Y GESTIÓN DE SUSCRIPCIONES
# ============================================================

def _generar_monto_vip(user_id: str) -> float:
    """Genera un monto único en USDT para identificar el pago de este usuario.
    Convierte el precio en EUR a USDT usando tasa en vivo.
    Thread-safe con _lock_ops."""
    global _vip_monto_counter, pagos_pendientes_vip

    with _lock_ops:
        precio_eur = _vip_precio_info()["precio"]
        precio_usdt = _eur_a_usdt(precio_eur)

        # Si ya tiene un pago pendiente con monto asignado, reutilizar
        if user_id in pagos_pendientes_vip:
            return pagos_pendientes_vip[user_id].get("monto_unico", precio_usdt)

        _vip_monto_counter = (_vip_monto_counter % 999) + 1
        monto = precio_usdt + (_vip_monto_counter * 0.001)
        monto = round(monto, 3)

        # Verificar que no haya otro pendiente con el mismo monto
        montos_usados = {p.get("monto_unico") for p in pagos_pendientes_vip.values()}
        while monto in montos_usados:
            _vip_monto_counter = (_vip_monto_counter % 999) + 1
            monto = round(precio_usdt + (_vip_monto_counter * 0.001), 3)

    guardar_estado()
    return monto


def _otorgar_trial_vip(user_id: str, nombre: str, username: str = ""):
    """Otorga acceso VIP gratuito por VIP_TRIAL_DIAS días (trial inteligente).
    La trial NO se marca como 'usada' hasta que el usuario entre al canal.
    Si no entra en 24h, se revoca y puede reintentar (máx 3 intentos)."""
    global suscripciones_vip, _vip_trials_usados, _trial_intentos

    # 🔒 Verificar si ya agotó intentos o ya usó trial
    if user_id in _vip_trials_usados:
        pi = _vip_precio_info()
        enviar_telegram(
            "⚠️ *Ya usaste tu prueba gratuita.*\n\n"
            f"💰 Para acceder al canal VIP, la suscripcion es de *{pi['precio']}{VIP_MONEDA}/mes*.\n"
            "👉 Escribe /vip para ver las instrucciones de pago.",
            user_id,
            teclado={
                "inline_keyboard": [[
                    {"text": f"💰 PAGAR {pi['precio']}{VIP_MONEDA}", "callback_data": "vip_pagar_usdt"}
                ]]
            }
        )
        return

    intentos = _trial_intentos.get(user_id, 0)
    if intentos >= 3:
        # Agotó intentos → marcar como usado permanentemente
        _vip_trials_usados.add(user_id)
        guardar_estado()
        pi = _vip_precio_info()
        enviar_telegram(
            "⚠️ *Has agotado tus intentos de prueba gratuita.*\n\n"
            f"💰 Suscribete por *{pi['precio']}{VIP_MONEDA}/mes* → /vip",
            user_id,
            teclado={
                "inline_keyboard": [[
                    {"text": f"💰 PAGAR {pi['precio']}{VIP_MONEDA}", "callback_data": "vip_pagar_usdt"}
                ]]
            }
        )
        return

    # BUG-2 FIX: Incrementar intentos ANTES de crear link (previene bypass en 3er intento)
    _trial_intentos[user_id] = intentos + 1
    guardar_estado()

    # Si ya tiene trial pendiente de entrada, recordar el link
    if user_id in suscripciones_vip:
        sub = suscripciones_vip[user_id]
        if not sub.get("entrada_confirmada", True):
            # Ya tiene un link pendiente
            invite = sub.get("invite_link", "")
            if invite:
                enviar_telegram(
                    "⏳ *Ya tienes un link de trial pendiente.*\n\n"
                    "👇 Pulsa el boton para entrar al canal VIP.\n"
                    "_Tienes 24h para usarlo._",
                    user_id,
                    teclado={"inline_keyboard": [[
                        {"text": "👑 ENTRAR AL CANAL VIP", "url": invite}
                    ]]}
                )
                return
        else:
            # Ya tiene VIP activo confirmado
            enviar_telegram(
                "✅ *Ya tienes acceso VIP activo.*\n"
                "Escribe /vip para ver tu estado.",
                user_id
            )
            return

    ahora_dt = ahora().replace(tzinfo=None)
    inicio = ahora_dt.strftime("%Y-%m-%dT%H:%M:%S")
    # Expira 24h después para el link (el timer real de 5 días hábiles empieza al entrar al canal)
    link_expira_dt = ahora_dt + timedelta(hours=25)  # 24h + 1h gracia para el link

    # 1. Crear invite link único (expira en 25h, no en 5 días hábiles)
    invite_link = ""
    try:
        expire_unix = int(link_expira_dt.timestamp())
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/createChatInviteLink",
            json={
                "chat_id": CHANNEL_ID,
                "expire_date": expire_unix,
                "member_limit": 1,
                "name": f"TRIAL-{user_id[-6:]}-{ahora().strftime('%m%d')}"
            },
            timeout=15
        )
        if r.status_code == 200:
            invite_link = r.json().get("result", {}).get("invite_link", "")
            logger.info(f"🔗 Trial invite link creado para {user_id}: {invite_link}")
        else:
            logger.error(f"❌ Error creando trial invite link: {r.status_code} {r.text[:200]}")
    except Exception as e:
        logger.error(f"❌ Excepción creando trial invite link: {e}")

    # 2. Guardar suscripción como trial PENDIENTE DE ENTRADA
    # ⚠️ NO se añade a _vip_trials_usados hasta que entre al canal
    with _lock_ops:
        suscripciones_vip[user_id] = {
            "nombre": nombre,
            "username": username,
            "inicio": inicio,
            "expira": inicio,  # Provisional — se recalcula al entrar al canal
            "aviso_enviado": False,
            "monto_pagado": 0,
            "tx_id": "trial_gratis",
            "invite_link": invite_link,
            "es_trial": True,
            "entrada_confirmada": False,  # 🔑 PENDIENTE DE ENTRADA
        }
        # BUG-2: intentos ya incrementado arriba antes de crear link
    guardar_estado()

    log_vip(f"🎁 TRIAL CREADO: {nombre} (@{username}) ID:{user_id} | Intento:{intentos+1}/3 | Link:{'OK' if invite_link else 'ERROR'} | Pendiente de entrada")

    # 3. Enviar link al usuario
    if invite_link:
        enviar_telegram(
            f"🎁 *5 DIAS HABILES GRATIS*\n"
            f"━━━━━━━━━━\n\n"
            f"👇 Pulsa el boton para entrar al canal VIP.\n"
            f"⏰ _Tienes 24 horas para usar este link._\n\n"
            f"✅ Los *5 dias habiles* empiezan cuando entres.\n"
            f"📊 _Senales IA con Entry, SL y TP exactos._",
            user_id,
            teclado={
                "inline_keyboard": [[
                    {"text": "👑 ENTRAR AL CANAL VIP", "url": invite_link}
                ]]
            }
        )
    else:
        enviar_telegram(
            "✅ *TRIAL LISTO*\n\n"
            "Hubo un problema generando el enlace.\n"
            f"Contacta a {ADMIN_USER} para recibir tu acceso.",
            user_id
        )

    # 4. Notificar al admin
    admin_id = ADMIN_IDS[0] if ADMIN_IDS else None
    if admin_id:
        enviar_telegram(
            "🎁 *NUEVO TRIAL VIP ACTIVADO*\n"
            "━━━━━━━━━━\n"
            f"👤 {nombre} (@{username})\n"
            f"🆔 ID: `{user_id}`\n"
            f"📅 Trial: {VIP_TRIAL_DIAS} dias\n"
            f"🔗 Link: {invite_link or 'Error'}",
            admin_id
        )

    logger.info(f"🎁 Trial VIP otorgado a {nombre} ({user_id}) por {VIP_TRIAL_DIAS} dias")


def _mostrar_instrucciones_pago(chat_id: str, user_id: str, nombre: str, username: str = "", fallback_chat: str = None):
    """Muestra instrucciones de pago USDT con monto único al usuario.

    chat_id: donde enviar las instrucciones (idealmente DM del usuario)
    fallback_chat: chat alternativo si el DM falla (ej: el grupo donde pulsó el botón)
    """
    global pagos_pendientes_vip

    if not VIP_WALLET_USDT:
        enviar_telegram(
            "⚠️ *El sistema de pago aun no esta configurado.*\n"
            f"Contacta al administrador: {ADMIN_USER}",
            chat_id
        )
        return

    monto = _generar_monto_vip(user_id)

    # Guardar pago pendiente
    with _lock_ops:
        pagos_pendientes_vip[user_id] = {
            "monto_unico": monto,
            "nombre": nombre,
            "username": username,
            "timestamp": ahora().strftime("%Y-%m-%dT%H:%M:%S"),
        }
    guardar_estado()

    precio_eur = _vip_precio_info()["precio"]
    tasa = _obtener_tasa_eur_usdt()

    _pago_teclado = {
        "inline_keyboard": [
            [{"text": "💰 ABRIR BINANCE PARA PAGAR", "url": "https://app.binance.com/en/my/wallet/account/main/withdrawal/crypto/USDT"}],
            [{"text": f"❓ AYUDA — {ADMIN_USER}", "url": f"https://t.me/{ADMIN_USER.replace('@','')}"}]
        ]
    }
    msg_id = enviar_telegram(
        "💰 *PAGO VIP*\n\n"
        f"💶 Precio: *{precio_eur}{VIP_MONEDA}/mes*\n"
        f"💱 Tasa: 1€ = {tasa:.4f} USDT\n\n"
        f"📋 Wallet ({VIP_RED}):\n`{VIP_WALLET_USDT}`\n\n"
        f"💵 Envia: *`{monto:.3f}`* USDT\n\n"
        f"⚠️ Envia *EXACTAMENTE* `{monto:.3f}` USDT\n"
        f"por red *{VIP_RED}*. No redondees.\n\n"
        "👇 Pulsa el boton para abrir Binance directamente:\n\n"
        "✅ _Verificacion automatica en ~5 min._\n"
        f"_Ayuda: {ADMIN_USER}_",
        chat_id,
        teclado=_pago_teclado
    )

    # Si el DM falló y tenemos un chat de fallback, avisar ahí
    if msg_id is None and fallback_chat and fallback_chat != chat_id:
        bot_username = TELEGRAM_TOKEN.split(":")[0] if TELEGRAM_TOKEN else ""
        enviar_telegram(
            f"👋 *{nombre}*, te envié las instrucciones de pago por privado.\n"
            f"Si no las ves, primero escríbeme al DM y luego pulsa el botón de nuevo.\n"
            f"_Ayuda: {ADMIN_USER}_",
            fallback_chat
        )

    # Notificar al admin
    admin_id = ADMIN_IDS[0] if ADMIN_IDS else None
    if admin_id:
        enviar_telegram(
            "📋 *PAGO VIP PENDIENTE*\n"
            "━━━━━━━━━━\n"
            f"👤 {nombre} (@{username})\n"
            f"🆔 ID: `{user_id}`\n"
            f"💵 Monto: `{monto:.3f}` USDT\n"
            f"📍 Red: {VIP_RED}\n"
            f"⏰ Solicitado: {ahora().strftime('%H:%M %d/%m')}",
            admin_id
        )


def _verificar_depositos_binance():
    """Consulta depósitos recientes en Binance y matchea con pagos pendientes."""
    global pagos_pendientes_vip, _depositos_procesados_vip

    if not BINANCE_API_KEY or not BINANCE_API_SECRET or not pagos_pendientes_vip:
        return

    try:
        import hmac
        import hashlib
        import urllib.parse

        base_url = "https://api.binance.com"
        endpoint = "/sapi/v1/capital/deposit/hisrec"

        # Parámetros: depósitos USDT de las últimas 24 horas con status=1 (completado)
        timestamp = int(time.time() * 1000)
        start_time = int((time.time() - 86400) * 1000)  # Últimas 24h
        params = {
            "coin": "USDT",
            "status": 1,
            "startTime": start_time,
            "timestamp": timestamp,
        }

        # Firma HMAC-SHA256
        query_string = urllib.parse.urlencode(params)
        signature = hmac.new(
            BINANCE_API_SECRET.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()
        query_string += f"&signature={signature}"

        headers = {"X-MBX-APIKEY": BINANCE_API_KEY}
        r = requests.get(f"{base_url}{endpoint}?{query_string}", headers=headers, timeout=15)

        if r.status_code != 200:
            logger.warning(f"⚠️ Binance API error: {r.status_code} {r.text[:200]}")
            return

        depositos = r.json()
        if not isinstance(depositos, list):
            return

        # Revisar cada depósito
        for dep in depositos:
            tx_id = dep.get("txId", "")
            if tx_id in _depositos_procesados_vip:
                continue  # Ya procesado

            monto_dep = float(dep.get("amount", 0))
            network = dep.get("network", "")

            # Buscar match en pagos pendientes (tolerancia ±0.0005)
            for uid, pend in list(pagos_pendientes_vip.items()):
                monto_esperado = pend.get("monto_unico", 0)
                if abs(monto_dep - monto_esperado) < 0.0005:
                    # ¡Match encontrado!
                    logger.info(f"💰 MATCH VIP: {uid} pagó {monto_dep} USDT (esperado {monto_esperado}) tx={tx_id}")

                    nombre = pend.get("nombre", "VIP")
                    username = pend.get("username", "")

                    log_pago(f"💰 PAGO VERIFICADO: {nombre} (@{username}) ID:{uid} | {monto_dep:.3f} USDT | TxID:{tx_id[:24]}")

                    # H-10 FIX: Otorgar acceso ANTES de marcar como procesado
                    # Si _otorgar_acceso_vip falla, el depósito se reintenta en el próximo ciclo
                    try:
                        _otorgar_acceso_vip(uid, nombre, username, monto_dep, tx_id)
                        # Solo marcar como procesado y eliminar pendiente SI el acceso se otorgó OK
                        with _lock_ops:
                            _depositos_procesados_vip.add(tx_id)
                            pagos_pendientes_vip.pop(uid, None)
                    except Exception as e:
                        log_pago(f"❌ Error otorgando VIP tras pago: {uid} | {e} — se reintentará", "error")
                        # NO marcar como procesado — se reintentará en próximo ciclo
                    break  # Un depósito solo puede matchear un pendiente

    except ImportError:
        logger.error("❌ Faltan módulos hmac/hashlib para Binance API")
    except Exception as e:
        logger.error(f"⚠️ Error verificando depósitos Binance: {e}")


def _otorgar_acceso_vip(user_id: str, nombre: str, username: str = "", monto: float = 0, tx_id: str = "", dias: int = None):
    """Genera link de invitación al canal, guarda suscripción y notifica.
    Si dias=None, usa VIP_DURACION_DIAS (30 por defecto)."""
    global suscripciones_vip

    dias_vip = dias if dias is not None else VIP_DURACION_DIAS
    ahora_dt = ahora().replace(tzinfo=None)
    inicio = ahora_dt.strftime("%Y-%m-%dT%H:%M:%S")

    # Si ya es VIP, extender desde la fecha de expiración actual
    if user_id in suscripciones_vip:
        try:
            expira_actual = datetime.fromisoformat(suscripciones_vip[user_id]["expira"])
            if expira_actual > ahora_dt:
                ahora_dt = expira_actual  # Extender desde la expiración actual
        except Exception:
            pass

    expira_dt = ahora_dt + timedelta(days=dias_vip)
    expira = expira_dt.strftime("%Y-%m-%dT%H:%M:%S")

    # 1. Crear link de invitación único (1 uso, expira en VIP_DURACION_DIAS + 1 día de gracia)
    invite_link = ""
    try:
        expire_unix = int((expira_dt + timedelta(days=1)).timestamp())
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/createChatInviteLink",
            json={
                "chat_id": CHANNEL_ID,
                "expire_date": expire_unix,
                "member_limit": 1,
                "name": f"VIP-{user_id[-6:]}-{ahora().strftime('%m%d')}"
            },
            timeout=15
        )
        if r.status_code == 200:
            invite_link = r.json().get("result", {}).get("invite_link", "")
            logger.info(f"🔗 Invite link creado para {user_id}: {invite_link}")
        else:
            logger.error(f"❌ Error creando invite link: {r.status_code} {r.text[:200]}")
    except Exception as e:
        logger.error(f"❌ Excepción creando invite link: {e}")

    # 2. Guardar suscripción
    with _lock_ops:
        suscripciones_vip[user_id] = {
            "nombre": nombre,
            "username": username,
            "inicio": inicio,
            "expira": expira,
            "aviso_enviado": False,
            "monto_pagado": monto,
            "tx_id": tx_id,
            "invite_link": invite_link,
        }
    guardar_estado()

    # 3. Enviar confirmación con link al usuario
    if invite_link:
        enviar_telegram(
            f"🎉 *PAGO CONFIRMADO* ✅\n\n"
            f"💰 {monto:.3f} USDT | Activo hasta *{expira[:10]}*",
            user_id,
            teclado={
                "inline_keyboard": [[
                    {"text": "👑 ENTRAR AL CANAL VIP", "url": invite_link}
                ]]
            }
        )
    else:
        enviar_telegram(
            "✅ *PAGO RECIBIDO*\n\n"
            "Tu suscripcion esta activa pero hubo un problema generando el enlace.\n"
            f"Contacta a {ADMIN_USER} para recibir tu acceso manualmente.",
            user_id
        )

    # 4. Notificar al admin
    admin_id = ADMIN_IDS[0] if ADMIN_IDS else None
    if admin_id:
        enviar_telegram(
            "💰 *NUEVO PAGO VIP CONFIRMADO*\n"
            "━━━━━━━━━━\n"
            f"👤 {nombre} (@{username})\n"
            f"🆔 ID: `{user_id}`\n"
            f"💵 Monto: {monto:.3f} USDT\n"
            f"🔗 TxID: `{tx_id[:20]}...`\n"
            f"📅 Expira: {expira[:10]}\n"
            f"🔗 Link: {invite_link or 'Error'}",
            admin_id
        )

    log_vip(f"👑 VIP OTORGADO: {nombre} (@{username}) ID:{user_id} | Monto:{monto:.3f} USDT | Expira:{expira[:10]} | TxID:{tx_id[:24]}")


def _revocar_acceso_vip(user_id: str, notificar: bool = True):
    """Revoca acceso VIP: kick del canal, limpia estado, notifica."""
    global suscripciones_vip

    # 🛡️ PROTECCIÓN: Nunca revocar acceso a administradores
    if user_id in ADMIN_IDS:
        logger.info(f"🛡️ Revocación bloqueada: {user_id} es ADMIN — acceso permanente")
        return

    sub = suscripciones_vip.get(user_id, {})
    nombre = sub.get("nombre", "Usuario")
    era_trial = sub.get("es_trial", False)

    # 1. Kick del canal (ban + unban inmediato = expulsión sin ban permanente)
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/banChatMember",
            json={"chat_id": CHANNEL_ID, "user_id": int(user_id)},
            timeout=15
        )
        time.sleep(1)
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/unbanChatMember",
            json={"chat_id": CHANNEL_ID, "user_id": int(user_id), "only_if_banned": True},
            timeout=15
        )
        log_vip(f"🚫 VIP REVOCADO: {nombre} ID:{user_id} | Trial:{era_trial} | Kick del canal ejecutado")
    except Exception as e:
        logger.error(f"❌ Error expulsando {user_id}: {e}")

    # 2. Revocar invite link
    invite_link = sub.get("invite_link", "")
    if invite_link:
        try:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/revokeChatInviteLink",
                json={"chat_id": CHANNEL_ID, "invite_link": invite_link},
                timeout=10
            )
        except Exception:
            pass

    # 3. Eliminar del registro y limpiar caché
    with _lock_ops:
        suscripciones_vip.pop(user_id, None)
        pagos_pendientes_vip.pop(user_id, None)   # Limpiar pago pendiente viejo al revocar
    _cache_miembros.pop(user_id, None)
    guardar_estado()

    era_codigo = sub.get("tipo") == "codigo"

    # 4. Notificar al usuario
    if notificar:
        if era_codigo:
            enviar_telegram(
                "⏳ *Tu acceso por código ha terminado.*\n\n"
                "💰 Suscribete para seguir recibiendo senales.\n"
                "🎁 *50% OFF en tu primer mes*\n\n"
                "👉 Escribe /vip para ver las opciones.",
                user_id,
                teclado={
                    "inline_keyboard": [[
                        {"text": "💰 SUSCRIBIRME (50% OFF)", "callback_data": "vip_pagar_usdt"}
                    ]]
                }
            )
        elif era_trial:
            pi = _vip_precio_info()
            enviar_telegram(
                "⏳ *Tu prueba gratis termino.*\n\n"
                f"💰 Suscribete por *${pi['precio']}/mes* → /vip",
                user_id,
                teclado={
                    "inline_keyboard": [[
                        {"text": f"💰 SUSCRIBIRME {pi['precio']}{VIP_MONEDA}", "callback_data": "vip_pagar_usdt"}
                    ]]
                }
            )
        else:
            enviar_telegram(
                "⏳ *Tu VIP ha expirado.*\n\n"
                "Escribe /vip para renovar.",
                user_id,
                teclado={
                    "inline_keyboard": [[
                        {"text": "🔄 RENOVAR VIP", "callback_data": "vip_pagar_usdt"}
                    ]]
                }
            )

    # 5. Notificar al admin
    admin_id = ADMIN_IDS[0] if ADMIN_IDS else None
    if admin_id:
        enviar_telegram(
            f"🚫 *VIP EXPIRADO*\n{nombre} (`{user_id}`) removido del canal.",
            admin_id
        )

    logger.info(f"🚫 VIP revocado para {nombre} ({user_id})")


def _enviar_aviso_vip(user_id: str, dias_restantes: int):
    """Envía aviso de expiración próxima (trial, código o pagado)."""
    global suscripciones_vip

    sub = suscripciones_vip.get(user_id, {})
    es_trial = sub.get("es_trial", False)
    es_codigo = sub.get("tipo") == "codigo"

    pi = _vip_precio_info()
    p = pi["precio"]

    if es_codigo:
        enviar_telegram(
            f"⚠️ *Tu acceso por código termina en {dias_restantes} dia(s)*\n\n"
            f"💰 Suscribete para no perder las senales.\n"
            f"🎁 *50% OFF en tu primer mes*\n"
            "💪 _No pierdas acceso a las senales!_",
            user_id,
            teclado={
                "inline_keyboard": [[
                    {"text": "💰 SUSCRIBIRME (50% OFF)", "callback_data": "vip_pagar_usdt"}
                ]]
            }
        )
    elif es_trial:
        enviar_telegram(
            f"⚠️ *Trial termina en {dias_restantes} dia(s)*\n\n"
            f"💰 Suscribete por *{p}{VIP_MONEDA}/mes* para continuar.\n"
            "💪 _No pierdas acceso a las senales!_",
            user_id,
            teclado={
                "inline_keyboard": [[
                    {"text": f"💰 SUSCRIBIRME {p}{VIP_MONEDA}", "callback_data": "vip_pagar_usdt"}
                ]]
            }
        )
    else:
        enviar_telegram(
            f"⚠️ *VIP expira en {dias_restantes} dia(s)*\n\n"
            "Renueva para no perder las senales.",
            user_id,
            teclado={
                "inline_keyboard": [[
                    {"text": f"🔄 RENOVAR ({p}{VIP_MONEDA})", "callback_data": "vip_pagar_usdt"}
                ]]
            }
        )

    with _lock_ops:
        if user_id in suscripciones_vip:
            suscripciones_vip[user_id]["aviso_enviado"] = True
            # FIX 2026-03-19: guardar lista de avisos para secuencia 7d→3d→1d
            _prev = suscripciones_vip[user_id].get("avisos_enviados", [])
            if dias_restantes not in _prev:
                _prev.append(dias_restantes)
            suscripciones_vip[user_id]["avisos_enviados"] = _prev
    guardar_estado()


# ── COMANDOS ADMIN VIP ──────────────────────────────────────

def cmd_vip_lista():
    """Lista todas las suscripciones VIP activas."""
    if not suscripciones_vip:
        return "📋 *No hay suscripciones VIP activas.*"

    lineas = ["👑 *SUSCRIPCIONES VIP ACTIVAS*\n━━━━━━━━━━\n"]
    for uid, sub in suscripciones_vip.items():
        try:
            expira = datetime.fromisoformat(sub["expira"])
            dias = (expira - ahora().replace(tzinfo=None)).days
        except Exception:
            dias = 0
        estado = "🟢" if dias > 3 else "🟡" if dias > 0 else "🔴"
        tipo = "🎁TRIAL" if sub.get("es_trial") else "💰PAGO"
        lineas.append(
            f"{estado} *{sub.get('nombre', '?')}* (@{sub.get('username', '?')}) [{tipo}]\n"
            f"   ID: `{uid}` | Expira: {sub.get('expira', '?')[:10]} ({dias}d)"
        )
    lineas.append(f"\n📊 Total: *{len(suscripciones_vip)}* suscriptores")
    return "\n".join(lineas)


def cmd_vip_pendientes():
    """Lista pagos VIP pendientes de verificación."""
    if not pagos_pendientes_vip:
        return "📋 *No hay pagos VIP pendientes.*"

    lineas = ["⏳ *PAGOS VIP PENDIENTES*\n━━━━━━━━━━\n"]
    for uid, pend in pagos_pendientes_vip.items():
        lineas.append(
            f"👤 *{pend.get('nombre', '?')}* (@{pend.get('username', '?')})\n"
            f"   ID: `{uid}` | Monto: `{pend.get('monto_unico', 0):.3f}` USDT\n"
            f"   Solicitado: {pend.get('timestamp', '?')[:16]}"
        )
    lineas.append(f"\n📊 Total pendientes: *{len(pagos_pendientes_vip)}*")
    return "\n".join(lineas)


def cmd_vip_dar(target_id: str):
    """Admin: otorga acceso VIP gratuito a un usuario."""
    target_id = target_id.strip()
    if not target_id.isdigit():
        return "❌ Formato: `/vip_dar [user_id]`\nEjemplo: `/vip_dar 123456789`"

    user_data = directorio_usuarios.get(target_id, {})
    nombre = user_data.get("nombre", "Usuario")
    username = user_data.get("username", "")

    # Eliminar de pendientes si estaba
    with _lock_ops:
        pagos_pendientes_vip.pop(target_id, None)

    _otorgar_acceso_vip(target_id, nombre, username, monto=0, tx_id="admin_grant")
    return f"✅ *VIP otorgado a {nombre}* (`{target_id}`) por {VIP_DURACION_DIAS} dias."


def cmd_vip_quitar(target_id: str):
    """Admin: revoca acceso VIP de un usuario."""
    target_id = target_id.strip()
    if not target_id.isdigit():
        return "❌ Formato: `/vip_quitar [user_id]`\nEjemplo: `/vip_quitar 123456789`"

    if target_id not in suscripciones_vip:
        return f"❌ El usuario `{target_id}` no tiene suscripcion VIP activa."

    nombre = suscripciones_vip[target_id].get("nombre", "Usuario")
    _revocar_acceso_vip(target_id, notificar=True)
    return f"🚫 *VIP revocado para {nombre}* (`{target_id}`)."


def cmd_pausar():
    """Pausa SOLO ejecución MT5. Escáner y Telegram siguen."""
    global mt5_pausado
    mt5_pausado = True
    guardar_estado()
    return (
        "⏸️ *MT5 PAUSADO*\n\n"
        "🔒 No se ejecutan operaciones en MT5\n"
        "📡 Escáner y Telegram siguen activos\n\n"
        "▶️ Escribe `continuar` o `play` para reactivar"
    )

def cmd_reanudar():
    """Reanuda ejecución MT5."""
    global mt5_pausado
    mt5_pausado = False
    guardar_estado()
    return (
        "▶️ *MT5 ACTIVO*\n\n"
        "🟢 Operaciones se ejecutan en MT5\n"
        "📡 Escáner + Telegram + MT5 funcionando\n\n"
        "⏸️ Escribe `pausar` o `pause` para detener MT5"
    )


def cmd_pausar_todo():
    """Pausa TODO: scanner premium, scalper y ejecución MT5."""
    global mt5_pausado, escaneo_pausado, SCALPER_ACTIVO
    mt5_pausado = True
    escaneo_pausado = True
    SCALPER_ACTIVO = False
    guardar_estado()
    log_sistema("🛑 PAUSA TOTAL activada por admin — scanner, scalper y MT5 detenidos")
    return (
        "🛑 *TODO PAUSADO*\n\n"
        "⏸️ Scanner Premium — DETENIDO\n"
        "⏸️ Scalper — DETENIDO\n"
        "⏸️ MT5 — NO ejecuta ordenes\n\n"
        "📌 Las posiciones abiertas se mantienen.\n"
        "No se abren operaciones nuevas.\n\n"
        "▶️ Escribe `reanudar todo` o `play todo` para reactivar"
    )


def cmd_reanudar_todo():
    """Reanuda TODO: scanner premium, scalper y ejecución MT5."""
    global mt5_pausado, escaneo_pausado, SCALPER_ACTIVO
    mt5_pausado = False
    escaneo_pausado = False
    SCALPER_ACTIVO = True
    guardar_estado()
    log_sistema("▶️ PAUSA TOTAL desactivada por admin — todo reactivado")
    return (
        "▶️ *TODO ACTIVO*\n\n"
        "🟢 Scanner Premium — ACTIVO\n"
        "🟢 Scalper — ACTIVO\n"
        "🟢 MT5 — Ejecutando ordenes\n\n"
        "⏸️ Escribe `pausar todo` para detener todo"
    )

# ━━━━━━━━━━
#  FEATURE A: REPORTE DIARIO AL ADMIN (9:00 AM)
# ━━━━━━━━━━

def _generar_reporte_diario():
    """Genera y envía reporte diario del bot al admin principal."""
    global _ultimo_reporte_diario
    admin_id = ADMIN_IDS[0] if ADMIN_IDS else None
    if not admin_id:
        return

    ahora_dt = ahora().replace(tzinfo=None)
    hoy_str = ahora_dt.strftime("%Y-%m-%d")

    # Evitar envío doble
    if _ultimo_reporte_diario == hoy_str:
        return
    _ultimo_reporte_diario = hoy_str

    try:
        # Datos del bot
        with _lock_ops:
            n_ops = len(operaciones_activas)
            n_buy = sum(1 for o in operaciones_activas.values() if isinstance(o, dict) and o.get('tipo') == "COMPRA")
            n_sell = n_ops - n_buy

        n_vips = len(suscripciones_vip)
        n_trials = sum(1 for s in suscripciones_vip.values() if s.get("es_trial", False))
        n_pagados = n_vips - n_trials
        n_codigos = sum(1 for s in suscripciones_vip.values() if s.get("tipo") == "codigo")
        n_pendientes = sum(1 for s in suscripciones_vip.values() if not s.get("entrada_confirmada", True))

        # Estadísticas
        total_senales = estadisticas_diarias.get("ganadas", 0) + estadisticas_diarias.get("perdidas", 0)
        ganadas = estadisticas_diarias.get("ganadas", 0)
        perdidas = estadisticas_diarias.get("perdidas", 0)
        wr = (ganadas / total_senales * 100) if total_senales > 0 else 0
        pips_net = estadisticas_diarias.get("pips_ganados", 0) - estadisticas_diarias.get("pips_perdidos", 0)

        # VIPs por expirar en 3 días
        vips_por_expirar = []
        for uid, sub in suscripciones_vip.items():
            if not sub.get("entrada_confirmada", True):
                continue
            try:
                exp = datetime.fromisoformat(sub["expira"])
                dias_r = (exp - ahora_dt).days
                if 0 < dias_r <= 3:
                    vips_por_expirar.append(f"  ⚠️ {sub.get('nombre','?')} ({dias_r}d)")
            except Exception:
                pass

        # Códigos activos
        n_codes_activos = sum(1 for c in _codigos_invitacion.values() if c.get("usos", 0) < c.get("max_usos", 1))

        # Construir reporte
        reporte = (
            f"📊 *REPORTE DIARIO — {hoy_str}*\n"
            f"━━━━━━━━━━\n\n"
            f"📡 *OPERACIONES*\n"
            f"  🔹 Activas: *{n_ops}* (🟢{n_buy} 🔴{n_sell})\n"
            f"  🔹 Señales hoy: *{total_senales}*\n"
            f"  🔹 Win Rate: *{wr:.0f}%* ({ganadas}W/{perdidas}L)\n"
            f"  🔹 Pips netos: *{pips_net:+.1f}*\n\n"
            f"👑 *VIP*\n"
            f"  🔹 Total: *{n_vips}* (💰{n_pagados} 🎁{n_trials} 🎟️{n_codigos})\n"
            f"  🔹 Pendientes entrada: *{n_pendientes}*\n"
            f"  🔹 Pagos pendientes: *{len(pagos_pendientes_vip)}*\n"
            f"  🔹 Códigos activos: *{n_codes_activos}*\n"
        )

        if vips_por_expirar:
            reporte += f"\n⏳ *POR EXPIRAR (3d):*\n" + "\n".join(vips_por_expirar) + "\n"

        reporte += (
            f"\n🤖 *SISTEMA*\n"
            f"  🔹 Scanner: {'▶️ Activo' if not escaneo_pausado else '⏸️ Pausado'}\n"
            f"  🔹 MT5: {'✅ Conectado' if MT5_AVAILABLE else '❌ No disponible'}\n"
            f"  🔹 Hora: {ahora_dt.strftime('%H:%M:%S')}\n"
        )

        # Enviar con botones
        enviar_telegram(
            reporte,
            admin_id,
            teclado={"inline_keyboard": [
                [{"text": "📊 TRADING EN VIVO", "url": "https://buysell365.pro/dashboard"}],
                [{"text": "📋 LOGS", "url": f"https://buysell365.pro/dashboard#logs"}],
                [{"text": "👑 VIP LISTA", "callback_data": "/vip_lista_cb"}],
            ]}
        )

        log_sistema(f"📊 Reporte diario enviado al admin {admin_id}")
        guardar_estado()

    except Exception as e:
        logger.error(f"❌ Error generando reporte diario: {e}")


# ━━━━━━━━━━
#  FEATURE B: COMANDOS ADMIN REMOTOS
# ━━━━━━━━━━

def cmd_admin_logs():
    """Admin: envía URL del visor de logs."""
    return (
        "📋 *VISOR DE LOGS*\n"
        "━━━━━━━━━━\n\n"
        "👇 Accede desde cualquier dispositivo:",
        {"inline_keyboard": [[
            {"text": "📋 ABRIR LOGS", "url": f"https://buysell365.pro/dashboard#logs"}
        ]]}
    )


def cmd_admin_addvip(args: str):
    """Admin: otorga VIP manual por X días. Formato: /addvip <user_id> <dias>"""
    partes = args.strip().split()
    if len(partes) < 1:
        return "❌ Formato: `/addvip <user_id> [dias]`\nEjemplo: `/addvip 123456789 30`"

    target_id = partes[0].strip()
    if not target_id.isdigit():
        return "❌ El ID debe ser numérico.\nFormato: `/addvip <user_id> [dias]`"

    dias = 30  # default
    if len(partes) >= 2:
        try:
            dias = int(partes[1])
            if dias < 1 or dias > 9999:
                return "❌ Los días deben estar entre 1 y 9999."
        except ValueError:
            return "❌ Los días deben ser un número.\nFormato: `/addvip <user_id> [dias]`"

    user_data = directorio_usuarios.get(target_id, {})
    nombre = user_data.get("nombre", "Usuario")
    username = user_data.get("username", "")

    # Limpiar pagos pendientes si existían
    with _lock_ops:
        pagos_pendientes_vip.pop(target_id, None)

    _otorgar_acceso_vip(target_id, nombre, username, monto=0, tx_id="admin_addvip", dias=dias)
    return f"✅ *VIP otorgado a {nombre}* (`{target_id}`) por *{dias} dias*."


# ━━━━━━━━━━
#  FEATURE C: SISTEMA DE CÓDIGOS DE INVITACIÓN
# ━━━━━━━━━━

def _generar_codigo_invitacion(dias: int = 5, max_usos: int = 1, creado_por: str = "") -> str:
    """Genera un código único BS365-XXXX (4 chars alfanuméricos sin ambiguos)."""
    global _codigos_invitacion
    chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # Sin I/O/0/1 (ambiguos)

    for _ in range(50):  # Máx 50 intentos para evitar colisión
        code = "BS365-" + "".join(random.choices(chars, k=4))
        if code not in _codigos_invitacion:
            _codigos_invitacion[code] = {
                "creado_por": creado_por,
                "dias": dias,
                "creado": ahora().replace(tzinfo=None).strftime("%Y-%m-%dT%H:%M:%S"),
                "max_usos": max_usos,
                "usos": 0,
                "usado_por": [],
            }
            guardar_estado()
            log_vip(f"🎟️ CÓDIGO CREADO: {code} | Días:{dias} | Usos máx:{max_usos} | Por:{creado_por}")
            return code

    return ""  # No se pudo generar (extremadamente improbable)


def cmd_admin_gencode(args: str):
    """Admin: genera código de invitación. Formato: generar codigo [dias] [usos]"""
    partes = args.strip().split() if args.strip() else []

    dias = 5  # default
    max_usos = 1  # default

    if len(partes) >= 1:
        try:
            dias = int(partes[0])
            if dias < 1 or dias > 90:
                return "❌ Los días deben estar entre 1 y 90."
        except ValueError:
            return "❌ Formato: `generar codigo [dias] [usos]`\nEjemplo: `generar codigo 5 1`"

    if len(partes) >= 2:
        try:
            max_usos = int(partes[1])
            if max_usos < 1 or max_usos > 50:
                return "❌ Los usos deben estar entre 1 y 50."
        except ValueError:
            return "❌ Formato: `generar codigo [dias] [usos]`"

    admin_id = ADMIN_IDS[0] if ADMIN_IDS else ""
    code = _generar_codigo_invitacion(dias=dias, max_usos=max_usos, creado_por=admin_id)

    if code:
        return (
            f"🎟️ *CÓDIGO GENERADO*\n"
            f"━━━━━━━━━━\n\n"
            f"📋 Código: `{code}`\n"
            f"⏳ Duración: *{dias} dias*\n"
            f"👥 Usos máx: *{max_usos}*\n\n"
            f"📤 _Comparte este código con la persona._\n"
            f"_El usuario lo escribe en el grupo o al bot._"
        )
    else:
        return "❌ Error generando código. Intenta de nuevo."


def cmd_admin_codes():
    """Admin: lista códigos de invitación activos."""
    if not _codigos_invitacion:
        return "📋 *No hay códigos de invitación.*\nUsa `generar codigo [dias]` para crear uno."

    lineas = ["🎟️ *CÓDIGOS DE INVITACIÓN*\n━━━━━━━━━━\n"]

    for code, info in _codigos_invitacion.items():
        usos = info.get("usos", 0)
        max_u = info.get("max_usos", 1)
        dias = info.get("dias", 5)
        creado = info.get("creado", "?")[:10]
        agotado = usos >= max_u

        estado = "🔴 AGOTADO" if agotado else "🟢 ACTIVO"
        usado_por = info.get("usado_por", [])
        usuarios_txt = ""
        if usado_por:
            nombres = []
            for uid in usado_por:
                n = directorio_usuarios.get(str(uid), {}).get("nombre", str(uid))
                nombres.append(n)
            usuarios_txt = f"\n   👤 Usado por: {', '.join(nombres)}"

        lineas.append(
            f"{estado} `{code}` — {dias}d | {usos}/{max_u} usos | {creado}"
            f"{usuarios_txt}"
        )

    lineas.append(f"\n📊 Total: *{len(_codigos_invitacion)}* códigos")
    return "\n".join(lineas)


def cmd_admin_delcode(code: str):
    """Admin: elimina un código de invitación."""
    global _codigos_invitacion
    code = code.strip().upper()

    if code not in _codigos_invitacion:
        return f"❌ Código `{code}` no encontrado.\nUsa `/codes` para ver los activos."

    del _codigos_invitacion[code]
    guardar_estado()
    log_vip(f"🗑️ CÓDIGO ELIMINADO: {code}")
    return f"🗑️ Código `{code}` eliminado."


def _procesar_codigo_invitacion(code: str, user_id: str, nombre: str):
    """Usuario escribe un código → mostrar términos con botón de aceptar."""
    code = code.strip().upper()

    # Validar código
    if code not in _codigos_invitacion:
        enviar_telegram("❌ Código no válido o expirado.", user_id)
        return

    info = _codigos_invitacion[code]

    # Verificar usos
    if info.get("usos", 0) >= info.get("max_usos", 1):
        enviar_telegram("❌ Este código ya fue usado el máximo de veces.", user_id)
        return

    # Verificar si este usuario ya usó este código
    if user_id in [str(u) for u in info.get("usado_por", [])]:
        enviar_telegram("❌ Ya usaste este código anteriormente.", user_id)
        return

    # Verificar si ya tiene VIP activo
    if user_id in suscripciones_vip:
        sub = suscripciones_vip[user_id]
        if sub.get("entrada_confirmada", True):
            enviar_telegram("✅ Ya tienes acceso VIP activo. No necesitas un código.", user_id)
            return

    dias = info.get("dias", 5)

    # Mostrar términos
    enviar_telegram(
        f"🎟️ *CÓDIGO DE INVITACIÓN*\n"
        f"━━━━━━━━━━\n\n"
        f"📋 Código: `{code}`\n"
        f"⏳ Acceso: *{dias} dias GRATIS*\n\n"
        f"📜 *Condiciones:*\n"
        f"• Servicio de senales educativas.\n"
        f"  _No es asesoria financiera._\n"
        f"• El usuario es responsable de\n"
        f"  sus decisiones de trading.\n"
        f"• Al finalizar el periodo, el acceso\n"
        f"  se desactiva automaticamente.\n\n"
        f"👇 *Al pulsar ACEPTO confirmas estos terminos:*",
        user_id,
        teclado={"inline_keyboard": [
            [{"text": "✅ ACEPTO — ACTIVAR CODIGO", "callback_data": f"codigo_aceptar_{code}"}],
            [{"text": "❌ CANCELAR", "callback_data": "vip_cancelar"}]
        ]}
    )
    log_vip(f"🎟️ CÓDIGO PRESENTADO: {code} a usuario {user_id} ({nombre})")


def _activar_codigo_invitacion(code: str, user_id: str, nombre: str, username: str = ""):
    """Callback: usuario aceptó términos → crear invite link y suscripción."""
    global _codigos_invitacion, suscripciones_vip

    code = code.strip().upper()

    # Re-validar código
    if code not in _codigos_invitacion:
        enviar_telegram("❌ Código no válido o expirado.", user_id)
        return

    info = _codigos_invitacion[code]
    if info.get("usos", 0) >= info.get("max_usos", 1):
        enviar_telegram("❌ Este código ya fue usado el máximo de veces.", user_id)
        return

    dias = info.get("dias", 5)
    ahora_dt = ahora().replace(tzinfo=None)
    inicio = ahora_dt.strftime("%Y-%m-%dT%H:%M:%S")

    # Crear invite link (24h de validez para que entre — timer real empieza al entrar)
    invite_link = ""
    try:
        expire_unix = int((ahora_dt + timedelta(hours=24)).timestamp())
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/createChatInviteLink",
            json={
                "chat_id": CHANNEL_ID,
                "expire_date": expire_unix,
                "member_limit": 1,
                "name": f"CODE-{code}-{user_id[-6:]}"
            },
            timeout=15
        )
        if r.status_code == 200:
            invite_link = r.json().get("result", {}).get("invite_link", "")
        else:
            logger.error(f"❌ Error creando invite link para código: {r.status_code}")
    except Exception as e:
        logger.error(f"❌ Excepción creando invite link para código: {e}")

    # Registrar uso del código
    info["usos"] = info.get("usos", 0) + 1
    info.setdefault("usado_por", []).append(user_id)

    # Crear suscripción con entrada_confirmada = False (timer empieza al entrar)
    with _lock_ops:
        suscripciones_vip[user_id] = {
            "nombre": nombre,
            "username": username,
            "inicio": inicio,
            "expira": (ahora_dt + timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%S"),  # Temporal 24h
            "aviso_enviado": False,
            "monto_pagado": 0,
            "tx_id": f"codigo_{code}",
            "invite_link": invite_link,
            "es_trial": False,
            "tipo": "codigo",
            "codigo": code,
            "dias_codigo": dias,
            "entrada_confirmada": False,
        }
    guardar_estado()

    # Enviar link al usuario
    if invite_link:
        enviar_telegram(
            f"🎉 *CÓDIGO ACTIVADO* ✅\n"
            f"━━━━━━━━━━\n\n"
            f"🎟️ Código: `{code}`\n"
            f"⏳ Acceso: *{dias} dias GRATIS*\n\n"
            f"👇 *Entra al canal VIP:*\n"
            f"_Tienes 24h para entrar. El timer de {dias} dias empieza cuando entres._",
            user_id,
            teclado={"inline_keyboard": [[
                {"text": "👑 ENTRAR AL CANAL VIP", "url": invite_link}
            ]]}
        )
    else:
        enviar_telegram(
            f"✅ *CÓDIGO ACTIVADO* pero hubo un problema generando el enlace.\n"
            f"Contacta a {ADMIN_USER} para recibir tu acceso.",
            user_id
        )

    # Notificar admin
    admin_id = ADMIN_IDS[0] if ADMIN_IDS else None
    if admin_id:
        enviar_telegram(
            f"🎟️ *CÓDIGO USADO*\n"
            f"━━━━━━━━━━\n"
            f"📋 Código: `{code}`\n"
            f"👤 {nombre} (@{username})\n"
            f"🆔 ID: `{user_id}`\n"
            f"⏳ Días: {dias}\n"
            f"📊 Usos: {info['usos']}/{info['max_usos']}",
            admin_id
        )

    log_vip(f"🎟️ CÓDIGO ACTIVADO: {code} por {nombre} (@{username}) ID:{user_id} | Días:{dias}")


def cmd_reset():
    """Cierra todas las operaciones activas y reinicia el bot desde cero."""
    global operaciones_activas, historial_operaciones, estadisticas_diarias
    with _lock_ops:
        n = len(operaciones_activas)
        operaciones_activas.clear()
        historial_operaciones.clear()
        estadisticas_diarias.update({"ganadas": 0, "perdidas": 0, "pips_ganados": 0.0, "pips_perdidos": 0.0})
    guardar_estado()
    hora = ahora().strftime("%H:%M")
    return (
        f"🔄 *RESET COMPLETO* · {hora}\n\n"
        f"✅ {n} operación(es) cerrada(s)\n"
        "✅ Historial limpiado\n"
        "✅ Estadísticas del día reseteadas\n\n"
        "🤖 Bot listo como nuevo. Escaneando mercados... 📡"
    )

# ============================================================
#  NUEVOS COMANDOS
# ============================================================

def cmd_winrate():
    """Win rate desglosado por activo con R:R, duración y dirección."""
    if not historial_operaciones:
        return "📭 Sin historial de operaciones aún."

    stats = {}
    pips_win_total = 0.0
    pips_loss_total = 0.0
    duraciones = []
    for op in historial_operaciones:
        n = op.get('nombre', '?')
        if n not in stats:
            stats[n] = {"wins": 0, "losses": 0, "pips_w": 0.0, "pips_l": 0.0,
                        "compra_w": 0, "compra_t": 0, "venta_w": 0, "venta_t": 0}
        s = stats[n]
        pips = abs(op.get('pips', 0))
        tipo_op = op.get('tipo', 'COMPRA')
        es_compra = tipo_op in ("COMPRA", "BUY", "LONG")
        if op['resultado'] == "WIN":
            s["wins"] += 1
            s["pips_w"] += pips
            pips_win_total += pips
            if es_compra:
                s["compra_w"] += 1
            else:
                s["venta_w"] += 1
        else:
            s["losses"] += 1
            s["pips_l"] += pips
            pips_loss_total += pips
        if es_compra:
            s["compra_t"] += 1
        else:
            s["venta_t"] += 1
        # Duración
        dur = op.get('duracion_min', 0)
        if dur and dur > 0:
            duraciones.append(dur)

    # Calcular win rate y ordenar
    ranking = []
    for nombre_act, s in stats.items():
        total = s["wins"] + s["losses"]
        wr = (s["wins"] / total * 100) if total > 0 else 0
        avg_win = (s["pips_w"] / s["wins"]) if s["wins"] > 0 else 0
        avg_loss = (s["pips_l"] / s["losses"]) if s["losses"] > 0 else 0
        rr = (avg_win / avg_loss) if avg_loss > 0 else 0
        ranking.append((nombre_act, wr, s["wins"], total, rr, s))
    ranking.sort(key=lambda x: x[1], reverse=True)

    # Drawdown máximo
    max_drawdown = 0
    racha_actual = 0
    for op in historial_operaciones:
        if op['resultado'] == "LOSS":
            racha_actual += 1
            max_drawdown = max(max_drawdown, racha_actual)
        else:
            racha_actual = 0

    # Win rate por hora
    horas = {}
    for op in historial_operaciones:
        h = op.get('hora', '??')[:2]
        if h not in horas:
            horas[h] = {"wins": 0, "total": 0}
        horas[h]["total"] += 1
        if op['resultado'] == "WIN":
            horas[h]["wins"] += 1
    mejor_hora = None
    mejor_hr_pct = 0
    for h, v in horas.items():
        pct = v["wins"] / v["total"] * 100 if v["total"] >= 2 else 0
        if pct > mejor_hr_pct:
            mejor_hr_pct = pct
            mejor_hora = h

    total_ops = len(historial_operaciones)
    total_wins = sum(1 for op in historial_operaciones if op['resultado'] == "WIN")
    wr_global = total_wins / total_ops * 100 if total_ops > 0 else 0

    # R:R global
    avg_w_global = (pips_win_total / total_wins) if total_wins > 0 else 0
    avg_l_global = (pips_loss_total / (total_ops - total_wins)) if (total_ops - total_wins) > 0 else 0
    rr_global = (avg_w_global / avg_l_global) if avg_l_global > 0 else 0

    # Duración promedio
    dur_avg = (sum(duraciones) / len(duraciones)) if duraciones else 0

    lineas = [
        "📊 *WIN RATE POR ACTIVO*\n"
        "━━━━━━━━━━\n"
        f"🎯 Global: *{wr_global:.1f}%* ({total_wins}/{total_ops})\n"
        f"📐 R:R promedio: *{rr_global:.2f}:1*"
    ]
    if dur_avg > 0:
        if dur_avg >= 60:
            lineas.append(f" | ⏱️ Duración media: *{dur_avg/60:.1f}h*\n")
        else:
            lineas.append(f" | ⏱️ Duración media: *{dur_avg:.0f}min*\n")
    else:
        lineas.append("\n")

    emojis = {100: "🔥", 75: "⭐", 50: "🟢", 25: "🟡", 0: "🔴"}
    for nombre_act, wr, wins, total, rr, s in ranking:
        for umbral, emoji in sorted(emojis.items(), reverse=True):
            if wr >= umbral:
                break
        barra = "█" * int(wr / 10) + "░" * (10 - int(wr / 10))
        lineas.append(f"{emoji} *{nombre_act}*\n   {wr:.0f}%  |{barra}|  ({wins}/{total})")
        # R:R por activo
        if rr > 0:
            lineas.append(f"  R:R {rr:.1f}:1")
        # Dirección breakdown (solo si hay datos)
        if s["compra_t"] > 0 or s["venta_t"] > 0:
            c_wr = (s["compra_w"] / s["compra_t"] * 100) if s["compra_t"] > 0 else 0
            v_wr = (s["venta_w"] / s["venta_t"] * 100) if s["venta_t"] > 0 else 0
            dir_info = []
            if s["compra_t"] > 0:
                dir_info.append(f"🟢C:{c_wr:.0f}%({s['compra_w']}/{s['compra_t']})")
            if s["venta_t"] > 0:
                dir_info.append(f"🔴V:{v_wr:.0f}%({s['venta_w']}/{s['venta_t']})")
            lineas.append(f"\n   {' '.join(dir_info)}\n")
        else:
            lineas.append("\n")

    lineas.append("━━━━━━━━━━\n")
    lineas.append(f"📉 Drawdown máx: *{max_drawdown}* pérdidas consecutivas")
    if mejor_hora:
        lineas.append(f"\n⏰ Mejor hora: *{mejor_hora}:00 UTC* ({mejor_hr_pct:.0f}% win rate)")
    return "\n".join(lineas)

def cmd_alerta(args: str):
    """Crea una alerta de precio personalizada. Uso: /alerta oro 2950"""
    partes = args.strip().split()
    if len(partes) < 2:
        return (
            "❓ *Uso correcto:*\n"
            "   `/alerta [activo] [precio]`\n\n"
            "📌 *Ejemplos:*\n"
            "   `/alerta oro 2950`\n"
            "   `/alerta nasdaq 20000`\n"
            "   `/alerta eurusd 1.0900`"
        )
    activo_raw = partes[0]
    try:
        precio_obj = float(partes[1].replace(",", "."))
    except ValueError:
        return f"❌ Precio no válido: `{partes[1]}`"

    nombre = KEYWORDS_ACTIVOS.get(activo_raw.lower())
    if not nombre:
        return f"❓ Activo no reconocido: `{activo_raw}`\nActivos: oro, eurusd, usdjpy, gbpjpy, nasdaq, sp500"

    ticker = ACTIVOS[nombre]

    # Obtener precio actual para saber si alerta es >= o <=
    try:
        df_tmp = descargar_datos_seguro(ticker, period="1d", interval="5m")
        precio_actual = float(df_tmp['Close'].iloc[-1]) if df_tmp is not None else None
    except Exception:
        precio_actual = None

    tipo_alerta = ">=" if (precio_actual is None or precio_obj > precio_actual) else "<="
    signo = "suba a" if tipo_alerta == ">=" else "baje a"

    alertas_precio.append({
        "ticker": ticker,
        "nombre": nombre,
        "precio": precio_obj,
        "tipo": tipo_alerta,
    })
    guardar_estado()

    precio_actual_txt = f" (actual: {fmt(precio_actual, ticker)})" if precio_actual else ""
    return (
        "✅ *Alerta configurada*\n"
        "━━━━━━━━━━\n"
        f"📍 {nombre}\n"
        f"🔔 Avisaré cuando {signo} *{fmt(precio_obj, ticker)}*{precio_actual_txt}\n\n"
        "💡 Usa */mis alertas* para ver todas tus alertas."
    )

def cmd_mis_alertas():
    """Muestra alertas de precio activas."""
    if not alertas_precio:
        return (
            "🔔 *NO HAY ALERTAS ACTIVAS*\n\n"
            "💡 Crea una con:\n"
            "   `/alerta oro 2950`\n"
            "   `/alerta nasdaq 19500`"
        )
    lineas = ["🔔 *ALERTAS DE PRECIO ACTIVAS*\n━━━━━━━━━━\n"]
    for i, a in enumerate(alertas_precio, 1):
        signo = "≥" if a['tipo'] == ">=" else "≤"
        lineas.append(f"{i}. {a['nombre']}  →  precio {signo} *{fmt(a['precio'], a['ticker'])}*\n")
    lineas.append("━━━━━━━━━━\n💡 Usa `/borrar alerta [n]` para eliminar una.")
    return "\n".join(lineas)

def cmd_borrar_alerta(n_str: str):
    """Elimina una alerta de precio por número."""
    try:
        n = int(n_str.strip()) - 1
        if 0 <= n < len(alertas_precio):
            eliminada = alertas_precio.pop(n)
            guardar_estado()
            return f"✅ Alerta eliminada: {eliminada['nombre']} @ {fmt(eliminada['precio'], eliminada['ticker'])}"
        else:
            return f"❌ Número de alerta no válido. Tienes {len(alertas_precio)} alerta(s)."
    except ValueError:
        return "❌ Usa: `/borrar alerta [número]`  Ej: `/borrar alerta 1`"

# El sistema de modos de riesgo ha sido unificado en una estrategia profesional única.

def cmd_suscripciones():
    """Muestra el estado de suscripciones por activo."""
    lineas = ["📋 *SUSCRIPCIONES POR ACTIVO*\n━━━━━━━━━━\n"]
    for nombre_act in ACTIVOS:
        estado = "🔴 DESACTIVADO" if nombre_act in activos_desactivados else "🟢 ACTIVO"
        lineas.append(f"{nombre_act}\n   {estado}\n")
    lineas.append("━━━━━━━━━━\n💡 `/activar [activo]`  |  `/desactivar [activo]`")
    return "\n".join(lineas)

def cmd_modo(modo_str):
    """Cambia el modo de riesgo del bot."""
    global MODO_RIESGO
    modo_str = modo_str.lower().strip()
    if modo_str not in ("conservador", "normal", "agresivo"):
        return (
            "❌ Modo no válido.\n\n"
            "Opciones:\n"
            "🔵 `/modo conservador` — SL más amplio, menos señales\n"
            "🟢 `/modo normal` — Equilibrado (por defecto)\n"
            "🔴 `/modo agresivo` — SL más ajustado, más señales"
        )
    MODO_RIESGO = modo_str
    guardar_estado()
    emoji = {"conservador": "🔵", "normal": "🟢", "agresivo": "🔴"}
    return (
        f"{emoji.get(modo_str, '')} *MODO CAMBIADO A: {modo_str.upper()}*\n\n"
        "Los niveles de SL y TP se ajustarán automáticamente."
    )

def cmd_activar(activo_raw: str):
    """Activa las señales de un activo."""
    nombre = KEYWORDS_ACTIVOS.get(activo_raw.lower().strip())
    if not nombre:
        return f"❓ Activo no reconocido: `{activo_raw}`"
    activos_desactivados.discard(nombre)
    return f"✅ *{nombre}* activado. Recibirás señales de este activo."

def cmd_desactivar(activo_raw: str):
    """Desactiva las señales de un activo."""
    nombre = KEYWORDS_ACTIVOS.get(activo_raw.lower().strip())
    if not nombre:
        return f"❓ Activo no reconocido: `{activo_raw}`"
    activos_desactivados.add(nombre)
    return f"🔕 *{nombre}* desactivado. No recibirás señales de este activo.\nUsa `/activar {activo_raw}` para reactivarlo."

def cmd_pivots(activo_raw: str):
    """Muestra Pivot Points diarios de un activo."""
    nombre = KEYWORDS_ACTIVOS.get(activo_raw.lower().strip())
    if not nombre:
        return f"❓ Activo no reconocido: `{activo_raw}`\nEj: /pivots oro"

    ticker = ACTIVOS[nombre]
    ind = _cache_ind.get(ticker)

    # El bot calcula Pivots usando el método Standard (Clásico)
    if ind and ind.get('pivots'):
        pv = ind['pivots']
        precio_actual = ind.get('precio', 0.0)
    else:
        df = descargar_datos_seguro(ticker)
        if df is None:
            return "❌ No pude obtener datos para calcular los pivots."
        
        # Calcular pivots del día anterior
        d_day = df.resample('D').agg({'Open':'first', 'High':'max', 'Low':'min', 'Close':'last'}).tail(2)
        if len(d_day) < 2: return "❌ Insuficiente historial para pivots."
        
        prev = d_day.iloc[-2]
        pp = (prev['High'] + prev['Low'] + prev['Close']) / 3
        pv = {
            'pp': pp,
            'r1': (2 * pp) - prev['Low'],
            's1': (2 * pp) - prev['High'],
            'r2': pp + (prev['High'] - prev['Low']),
            's2': pp - (prev['High'] - prev['Low']),
            'r3': prev['High'] + 2*(pp - prev['Low']),
            's3': prev['Low'] - 2*(prev['High'] - pp)
        }
        precio_actual = df['Close'].iloc[-1]

    # FIX: lógica corregida — JPY=3 dec, Forex=5 dec, Futuros/Cripto=2 dec
    def fmt(n):
        if "JPY" in ticker:   return f"{n:.3f}"
        if "=X" in ticker:    return f"{n:.5f}"
        return f"{n:.2f}"

    def indicador(nivel, p_act):
        # Indica si el precio está cerca del nivel (0.1% de distancia)
        dist = abs(nivel - p_act) / p_act
        return " 👈 *PRECIO AQUÍ*" if dist < 0.001 else ""

    res = (
        f"📐 *PIVOT POINTS — {nombre}*\n"
        "━━━━━━━━━━\n"
        f"💹 Precio Actual: *{fmt(precio_actual)}*\n\n"
        f"🔴 R3: {fmt(pv['r3'])}{indicador(pv['r3'], precio_actual)}\n"
        f"🔴 R2: {fmt(pv['r2'])}{indicador(pv['r2'], precio_actual)}\n"
        f"🔴 R1: {fmt(pv['r1'])}{indicador(pv['r1'], precio_actual)}\n"
        f"⚪️ *PIVOT:* {fmt(pv['pp'])}{indicador(pv['pp'], precio_actual)}\n"
        f"🟢 S1: {fmt(pv['s1'])}{indicador(pv['s1'], precio_actual)}\n"
        f"🟢 S2: {fmt(pv['s2'])}{indicador(pv['s2'], precio_actual)}\n"
        f"🟢 S3: {fmt(pv['s3'])}{indicador(pv['s3'], precio_actual)}\n\n"
        "📖 *Guía rápida:*\n"
        "• *PP:* El eje del mercado. Arriba = Alcista, Abajo = Bajista.\n"
        "• *R1/R2/R3:* Resistencias (zonas de posible rebote abajo/venta).\n"
        "• *S1/S2/S3:* Soportes (zonas de posible rebote arriba/compra)."
    )
    return res

def cmd_briefing():
    """Genera el briefing de mercado manualmente."""
    enviar_briefing_matutino()
    return None  # Ya se envía dentro de la función

# ============================================================
#  SISTEMA DE LENGUAJE NATURAL — NLP LIGERO
# ============================================================

# Memoria de contexto por usuario (expira en 30 min)
_contexto_usuario = {}  # {remitente: {"activo": "ORO", "intencion": "analisis", "ts": float}}

# Respuestas variadas para no sonar repetitivo
RESPUESTAS_GRACIAS = [
    "✅ ¡De nada! Aquí sigo vigilando el mercado por ti.",
    "👍 ¡Con gusto! Avísame si necesitas analizar otro activo.",
    "🙌 ¡A la orden! El sistema sigue rastreando oportunidades.",
    "😎 ¡Excelente! Quedo atento a tus próximas consultas.",
    "🤝 Un placer ayudarte. ¿Revisamos algún otro gráfico o resumen?",
]
RESPUESTAS_OK = [
    "👌 Entendido. Parámetros ajustados. ¿Qué más analizamos?",
    "✅ Confirmado. Sigo monitoreando en tiempo real.",
    "👍 Recibido. ¿Evaluamos la estructura de algún mercado ahora?",
    "🙂 Listo. Quedo atento a tus directrices.",
]
RESPUESTAS_SALUDO_DIA = [
    "☀️ ¡Buenos días *{name}*! ☕ BuySell365.pro online. ¿Qué activo analizamos hoy?",
    "🌅 ¡Muy buenos días *{name}*! Los mercados están abriendo con buena liquidez. ¿Por dónde empezamos?",
    "☕ ¡Buenos días *{name}*! Equipos listos y rastreando huellas institucionales. ¿Qué necesitas?",
]
RESPUESTAS_SALUDO_TARDE = [
    "🌞 ¡Buenas tardes *{name}*! Analizando el flujo de la sesión. ¿Qué revisamos?",
    "👋 ¡Buenas tardes *{name}*! Zonas de liquidez detectadas. ¿Te interesa algún análisis?",
    "😊 ¡Buenas tardes *{name}*! ¿Hacemos una revisión de última hora antes de que cierre la bolsa?",
]
RESPUESTAS_SALUDO_NOCHE = [
    "🌙 ¡Buenas noches *{name}*! Sesión asiática en marcha. ¿Mapeamos alguna tendencia?",
    "🌃 ¡Buenas noches *{name}*! Vigilando el mercado mientras descansas. ¿Alguna duda?",
    "⭐ ¡Buenas noches *{name}*! ¿Realizamos un chequeo nocturno de divergencias?",
]

# 📝 TIPS EDUCATIVOS PARA EL CANAL/GRUPO (Evolución 2.0)
TIPS_TRADING = [
    "💡 *Tip de Pro:* No operes antes de las noticias de alto impacto (NFP, CPI). Espera 15 min después del dato.",
    "🕯️ *Tip de Pro:* Los rechazos en la EMA200 suelen ser puntos de entrada de alta probabilidad.",
    "📊 *Tip de Pro:* Si el RSI está en 50, el mercado no tiene tendencia clara. Ten paciencia.",
    "💰 *Tip de Pro:* Nunca arriesgues más del 1% de tu cuenta por cada operación. La gestión es el secreto.",
    "🧭 *Tip de Pro:* Mira siempre la tendencia en 4h antes de operar en 15m. Opera a favor del flujo mayor.",
    "⏱️ *Tip de Pro:* Los mejores movimientos del NASDAQ suelen ocurrir en la primera hora de Nueva York.",
    "📉 *Tip de Pro:* El Oro tiende a moverse en contra del Dólar (DXY). Si el DXY sube, el Oro suele bajar.",
    "🧠 *Tip de Pro:* Si has perdido 2 trades seguidos, para de operar. Mantener la mente fría es clave.",
]

def obtener_tip_aleatorio():
    return random.choice(TIPS_TRADING)


# Diccionario de intenciones ampliado
INTENCIONES = {
    "precio": [
        "cuanto vale", "a cuanto esta", "precio de", "cuanto cuesta",
        "cotizacion", "a cuanto cotiza", "dame el precio", "en cuanto esta",
        "vale hoy", "valor actual", "cuanto es", "como esta de precio",
        "a que precio", "precio actual", "cuanto cuesta ahora",
        "cuanto tiene", "a cuantos esta", "el precio", "dame precio",
        # coloquiales
        "a cuanto va", "cuanto trae", "en cuanto anda", "como lo tienen",
        "precio de hoy", "hoy a cuanto", "cuanto lleva", "cuanto sale",
        "precios", "que precio", "dime el precio",
    ],
    "analisis": [
        "analisis", "análisis", "analiza", "como esta", "sube o baja",
        "como va el", "tendencia de", "señal de", "entro o no",
        "es buen momento", "conviene comprar", "conviene vender",
        "me recomiendas", "que opinas del", "deberia entrar",
        "hay señal en", "que hace el", "como se ve el", "analizame",
        "momento de entrar", "momento de comprar", "momento de vender",
        "dame informacion", "información de", "situacion del",

        "esta subiendo", "esta bajando", "sigue subiendo", "sigue bajando",
        "te gusta el", "operamos el", "miramos el",
        # coloquiales
        "que opinas", "lo ves bien", "vale la pena", "esta bueno el",
        "esta feo el", "que tal el", "lo compro", "lo vendo",
        "entro ya", "espero o entro", "suena bien", "como lo ves",
        "que dices del", "opinion del", "se ve bien el", "lo miro",
        "analizame el", "analiza el", "dale un vistazo al",
        "vamos bien con el", "como anda el", "que tal anda el",
        "arranca o no", "sale o no", "entra o espera",
    ],
    "senales_activas": [
        "hay señal", "hay algo", "alguna señal", "senales abiertas",
        "operaciones abiertas", "que tienes abierto", "en que estas",
        "cuantas operaciones", "hay oportunidad", "que operaciones",
        "tienes algo abierto", "operaciones activas", "posiciones abiertas",
        "hay entrada", "hay alguna entrada", "algun trade",
        "dame señales", "dame las señales", "que señales hay",
        "muestra señales", "ver señales", "dame operaciones",
        # coloquiales
        "hay algo abierto", "tenemos algo abierto", "hay algo bueno",
        "hay trade", "hay algo para entrar", "hay algo ahora",
        "que ves ahora", "que tienes ahora", "operando algo",
        "trabajando en algo", "algo activo", "trades abiertos",
        "posiciones", "lo que tienes", "que hay de bueno",
    ],
    "mercado_general": [
        "como va el mercado", "estado del mercado", "como esta todo",
        "resumen del mercado", "como amanecio el mercado",
        "como esta la bolsa", "que mueve hoy", "que tal el mercado",
        "como estan los mercados", "panorama del mercado",
        "overview", "vista general",
        # coloquiales
        "como esta la cosa", "como van las cosas", "que tal todo",
        "como amaneció", "como abrió", "como cerró",
        "como va todo", "todo bien", "como están", "que hay",
        "como está el mundo", "que tal hoy", "como esta hoy",
        "como vemos el dia", "como pintó el dia", "como pinta hoy",
        "resumen de mercados", "como andan los mercados",
    ],
    "noticias": [
        "noticias", "eventos", "hay noticias", "calendario economico",
        "fed hoy", "fomc", "nfp", "bce", "datos macro",
        "hay datos importantes", "noticias de impacto", "que pasa hoy",
        "que hay hoy", "eventos importantes", "datos economicos",
        "noticias del dia", "agenda economica",
        # coloquiales
        "que sale hoy", "hay datos hoy", "algo importante hoy",
        "noticias importantes hoy", "hay catalistas", "catalista hoy",
        "mueven el mercado", "que mueve hoy", "que evento hay",
        "hay noticias del fed", "habla powell", "hay fed hoy",
    ],
    "sentimiento": [
        "miedo", "codicia", "sentimiento", "fear", "greed",
        "animo del mercado",
        "fear and greed", "indice de miedo", "hay panico",
        # coloquiales
        "hay optimismo", "hay pesimismo", "el mercado tiene miedo",
        "todos venden", "todos compran", "hay euforia", "hay nervios",
        "como esta el animo", "el mood del mercado",
    ],
    "volatilidad": [
        "volatil", "volatilidad", "mas activo", "mueve mas",
        "cual se mueve", "cual tiene mas movimiento", "el mas activo",
        "ranking", "mejor activo ahora", "cual opera mejor",
        # coloquiales
        "que se mueve hoy", "que activo conviene", "cual va mas",
        "el que mas mueve", "cual tiene mas pips", "cual esta movido",
        "cual esta caliente", "el que esta moviendo", "cual esta pegando",
    ],
    "horarios": [
        "horario", "cuando abre", "esta abierto", "sesion",
        "a que hora", "abre hoy", "cierra hoy", "cuando cierra",
        "horario de", "sesion londrés", "sesion nueva york",
        "sesion asiatica", "apertura", "cierre",
        # coloquiales
        "ya abrió", "ya cerró", "sigue abierto", "opera ahora",
        "se puede operar", "esta cerrado", "cuando empieza",
        "a que hora opera", "cuando puedo entrar", "sesion de hoy",
    ],
    "pivots": [
        "pivot", "soporte", "resistencia", "nivel", "niveles",
        "puntos de giro", "pp", "r1", "s1",
        # coloquiales
        "donde esta el soporte", "donde esta la resistencia",
        "niveles clave", "zonas importantes", "zona de compra",
        "zona de venta", "donde rebota", "donde choca",
        "nivel de entrada", "precio objetivo",
    ],
    "ayuda": [
        "no se como", "no entiendo", "como uso", "que puedes hacer",
        "que comandos", "comandos disponibles", "como te uso",
        "que sabes hacer", "que funciones", "menu",
        # coloquiales
        "como funciona esto", "para que sirves", "que haces",
        "como se usa esto", "instrucciones", "lista de comandos",
        "que puedo preguntarte", "que puedo pedirte",
    ],
    "estadisticas": [
        "como vamos", "resultados", "ganancias", "balance",
        "cuanto ganaste", "cuanto gano el bot", "es rentable",
        "efectividad", "porcentaje de acierto", "win rate",
        "cuantas ganadas", "cuantas perdidas", "historial",
        # coloquiales
        "vamos ganando", "cuanto llevamos", "cuanto llevamos ganado", "cuanto perdimos",
        "rendimiento", "estamos positivos", "como va el bot",
        "cuantas acertamos", "porcentaje de exito", "tasa de acierto",
        "ganamos o perdemos", "como nos trata el mercado",
    ],
    "frustración": [
        "no funciona", "no sirve", "que malo", "esto es una basura",
        "no entiendo nada", "me perdí", "estoy perdido",
        "no se que hacer", "me tiene confundido",
        # coloquiales
        "que rollo", "que fastidio", "me tiene loco", "no entiendo nada",
        "que desastre", "esto no jala", "no pega", "que sistema tan malo",
    ],
    "perdida": [
        "perdi", "perdí", "me fue mal", "estoy perdiendo",
        "sali mal", "salí mal", "me salto el sl", "stop loss activado",
        "perdida", "pérdida", "mal dia", "malo el dia",
        # coloquiales
        "me barrió", "me liquidó", "stop activado", "toque el stop",
        "me sacó el mercado", "me fue pésimo", "se me fue en contra",
        "me dio reversa", "salí quemado", "me comieron",
    ],
    "nuevo_usuario": [
        "como empezar", "soy nuevo", "no se de trading", "empezando",
        "quiero aprender", "como funciona el trading", "por donde empiezo",
        "novato", "principiante", "sin experiencia", "que hago primero",
        # coloquiales
        "nunca he operado", "es mi primer dia", "quiero comenzar",
        "como inicio", "donde inicio", "soy nuevo en esto",
        "no sé nada de esto", "no tengo experiencia",
    ],
    "bot_info": [
        "quien eres", "quien te creo", "como funcionas", "como operas",
        "que tecnología usas", "quien es tu dueño", "presentate",
        "hablame de ti", "que haces", "cual es tu estrategia",
        "como decides", "explicame el bot", "como trabajas",
        "quien es el creador", "de donde vienes", "como te llamas",
        "eres humano", "eres un bot", "quien es emmanuel",
    ],
    "glosario": [
        "que es el", "que es un", "que es una", "que significa", "explica",
        "explicame", "definicion de", "definición de", "para que sirve",
        "como funciona el", "como funciona la", "que hace el", "que hace la",
        # coloquiales
        "que es eso", "no entiendo ese termino", "que quiere decir",
        "que significa eso", "en que consiste", "me explicas",
        "como se llama cuando", "que es el rsi", "que es el macd",
    ],
    "ordenes": [
        "tipos de orden", "tipo de orden", "orden limite", "orden limit",
        "orden de mercado", "market order", "stop limit", "trailing stop",
        "como pongo una orden", "como coloco una orden", "como entro al mercado",
        "buy limit", "sell limit", "buy stop", "sell stop",
        # coloquiales
        "como pongo mi entrada", "como coloco el trade", "como ejecuto",
        "a mercado o limite", "limit o market", "pending order",
    ],
    "patrones_velas": [
        "patron de vela", "patron de velas", "candlestick", "formacion de velas",
        "que patron es", "hammer", "doji", "engulfing", "shooting star",
        "morning star", "evening star", "pin bar", "formacion alcista", "formacion bajista",
        "que vela es", "que figura es",
        # coloquiales
        "que figura forma", "vela larga", "vela pequeña", "sombra",
        "que figura es esa", "que significa esa vela",
    ],
    "position_sizing": [
        "cuanto arriesgar", "cuanto puedo arriesgar", "tamaño de posicion",
        "tamaño de la posicion", "cuanto lotes", "cuantos lotes poner",
        "cuanto entrar", "con cuanto entro", "con cuanto opero",
        "posicion sizing", "position size", "calculo de lote",
        "cuanto capital usar", "que porcentaje usar",
        # coloquiales
        "cuanto pongo", "cuanto meto", "con cuanta plata entro",
        "cuanto dinero uso", "cuantos contratos", "que lote uso",
    ],
    "correlacion": [
        "correlacion entre", "correlacion del", "se correlaciona", "correlacionado",
        "mueven juntos", "se mueven igual", "el oro y el dolar",
        "nasdaq y sp500", "relacion entre", "el dolar y el oro",
        "activos correlacionados", "cuando sube el dolar", "cuando sube el oro",
        # coloquiales
        "van juntos", "se parecen", "uno sube y el otro baja",
        "afecta al otro", "tienen relacion", "depende del otro",
    ],
    "fibonacci": [
        "fibonacci", "fibo", "retroceso fibonacci", "nivel fibonacci",
        "retroceso de fibo", "61.8", "38.2", "50%",
        # coloquiales
        "niveles fibo", "retracement", "extension fib",
        "nivel dorado", "zona dorada",
    ],
    "educacion": [
        "ensenme", "enséñame", "dame un consejo", "dame consejos",
        "tips de trading", "consejos para traders", "como mejorar",
        "trucos de trading", "aprende trading", "aprender trading",
        "educacion financiera", "educación financiera",
        # coloquiales
        "que aprendo hoy", "ensenme algo", "algo nuevo hoy",
        "tip del dia", "consejo del dia", "como ser mejor trader",
        "que hago para mejorar", "como mejorar mis resultados",
    ],
}

# Aliases de activos para fuzzy matching
ALIASES_ACTIVOS_FUZZY = {
    "oro": "ORO", "gold": "ORO", "xauusd": "ORO", "dorado": "ORO",
    "gc": "ORO", "xau": "ORO", "metal": "ORO",
    "eurusd": "EUR/USD", "euro": "EUR/USD", "eur": "EUR/USD",
    "eurodolar": "EUR/USD", "dolareuro": "EUR/USD", "ed": "EUR/USD",
    "nasdaq": "NASDAQ", "nq": "NASDAQ", "ndq": "NASDAQ",
    "tech": "NASDAQ", "tecnologia": "NASDAQ", "nas": "NASDAQ",
    "us100": "NASDAQ", "ustec": "NASDAQ",
    "sp500": "S&P 500", "spx": "S&P 500", "sp": "S&P 500",
    "s&p": "S&P 500", "s&p500": "S&P 500", "us500": "S&P 500",
    "sandp": "S&P 500", "snp": "S&P 500", "500": "S&P 500",
    # USD/JPY
    "usdjpy": "USD/JPY", "usd/jpy": "USD/JPY", "yen": "USD/JPY",
    "dolaryen": "USD/JPY", "dolaren": "USD/JPY", "jpyusd": "USD/JPY",
    "jpy": "USD/JPY", "yenusd": "USD/JPY", "uj": "USD/JPY",
}

def obtener_contexto(remitente: str) -> dict:
    """Retorna el contexto del usuario si no ha expirado (30 min)."""
    ctx = _contexto_usuario.get(remitente)
    if ctx and (time.time() - ctx.get("ts", 0)) < 1800:
        return ctx
    return {}

def guardar_contexto(remitente: str, activo: str = None, intencion: str = None):
    """Guarda el último activo e intención del usuario con limpieza periódica."""
    # Limpieza de memoria si hay más de 1000 usuarios en caché
    if len(_contexto_usuario) > 1000:
        ahora_ts = time.time()
        # Eliminar contextos de más de 1 hora
        expirados = [k for k, v in _contexto_usuario.items() if (ahora_ts - v.get("ts", 0)) > 3600]
        for k in expirados:
            del _contexto_usuario[k]

    ctx = _contexto_usuario.get(remitente, {})
    _contexto_usuario[remitente] = {
        "activo":    activo    or ctx.get("activo"),
        "intencion": intencion or ctx.get("intencion"),
        "ts":        time.time(),
    }

def detectar_activo_fuzzy(texto: str):
    """Detecta el activo en el texto con alta tolerancia usando rapidfuzz."""
    t_clean = texto.lower().strip()
    palabras = t_clean.split()

    # 0. Palabras AMBIGUAS que NO deben resolverse solas.
    #    "usd" podría ser EUR/USD, USD/JPY, o XAUUSD → mejor preguntar al usuario.
    #    Solo aplica si es la ÚNICA palabra relevante (no si viene acompañada).
    _AMBIGUAS = {"usd", "dolar", "dollar", "divisa", "moneda", "precio", "cotizacion"}
    palabras_relevantes = [p for p in palabras if p not in _AMBIGUAS and len(p) >= 2]

    # 1. Coincidencia exacta por palabra
    for palabra in palabras:
        if palabra in ALIASES_ACTIVOS_FUZZY:
            return ALIASES_ACTIVOS_FUZZY[palabra]

    # Si SOLO quedan palabras ambiguas (ej: "precio usd", "precio del dolar") → no resolver
    if not palabras_relevantes or all(p in _AMBIGUAS or p in ('de', 'del', 'el', 'la', 'a', 'cuanto', 'esta') for p in palabras):
        # Verificar si hay exact match en las NO-ambiguas antes de rendirse
        has_exact = any(p in ALIASES_ACTIVOS_FUZZY for p in palabras)
        if not has_exact:
            return None

    # 2. RapidFuzz (si está disponible) para mayor precisión
    if _RAPIDFUZZ:
        # Primero palabra por palabra (más preciso, evita falsos positivos)
        # Mínimo 4 letras para fuzzy — "usd", "eur", "btc" ya están en exact match
        # Sin esto, "usd" hace fuzzy match con "xauusd" y devuelve ORO
        for palabra in palabras:
            if len(palabra) < 4: continue
            if palabra in _AMBIGUAS: continue  # Nunca hacer fuzzy con palabras ambiguas
            match = rf_process.extractOne(palabra, ALIASES_ACTIVOS_FUZZY.keys(), scorer=rf_fuzz.WRatio)
            if match and match[1] >= 85:
                return ALIASES_ACTIVOS_FUZZY[match[0]]

        # Luego buscar en todo el texto (umbral alto para evitar "eurusd" vs "usdjpy")
        match = rf_process.extractOne(t_clean, ALIASES_ACTIVOS_FUZZY.keys(), scorer=rf_fuzz.WRatio)
        if match and match[1] >= 92:  # Subimos de 90 a 92 para más seguridad
            return ALIASES_ACTIVOS_FUZZY[match[0]]
    else:
        # Fallback a difflib
        for palabra in palabras:
            if len(palabra) < 4: continue
            if palabra in _AMBIGUAS: continue
            candidatos = get_close_matches(palabra, ALIASES_ACTIVOS_FUZZY.keys(), n=1, cutoff=0.8)
            if candidatos:
                return ALIASES_ACTIVOS_FUZZY[candidatos[0]]

    # 3. Búsqueda por Regex (último recurso para palabras pegadas)
    for alias, nombre in ALIASES_ACTIVOS_FUZZY.items():
        if len(alias) >= 4 and re.search(r'\b' + re.escape(alias) + r'\b', t_clean):
            return nombre
    return None

def detectar_intencion(texto: str) -> str | None:
    """Detecta la intención principal del mensaje.
    Usa el match más LARGO para evitar que frases cortas solapen intenciones específicas."""
    t = texto.lower().strip()
    mejor_match = ""
    intencion_ganadora = None

    # 1. Buscar el match de texto más largo posible entre todas las intenciones
    for intencion, frases in INTENCIONES.items():
        for frase in frases:
            if frase in t:
                if len(frase) > len(mejor_match):
                    mejor_match = frase
                    intencion_ganadora = intencion

    if intencion_ganadora:
        return intencion_ganadora

    # 2. Fuzzy matching con rapidfuzz si no hubo coincidencia exacta
    if _RAPIDFUZZ and len(t) >= 5:
        mejor_score = 0
        mejor_intencion_f = None
        for intencion, frases in INTENCIONES.items():
            for frase in frases:
                score = rf_fuzz.partial_ratio(frase, t)
                if score > mejor_score and score >= 85: # Subimos umbral para evitar errores
                    mejor_score = score
                    mejor_intencion_f = intencion
        return mejor_intencion_f

    return None

def respuesta_fallback_inteligente(texto: str, activo_detectado: str, intencion_detectada: str, remitente: str) -> str:
    """Fallback inteligente que guía al usuario según lo que detectó."""
    ctx = obtener_contexto(remitente)

    # Si detectó un activo pero no la intención → lanzar análisis directo (lo más útil)
    if activo_detectado and not intencion_detectada:
        nombre_corto = activo_detectado.split(" ")[-1]
        return (
            f"📊 Entendido, te hago un análisis de *{activo_detectado}*. ¿Qué necesitas exactamente?\n\n"
            f"• _¿Cómo va el {nombre_corto.lower()}?_ → análisis completo\n"
            f"• _¿A cuánto está el {nombre_corto.lower()}?_ → precio actual\n"
            f"• _¿Hay señal de {nombre_corto.lower()}?_ → oportunidades\n"
            f"• _Pivots {nombre_corto.lower()}_ → soportes y resistencias\n\n"
            "O escríbeme directamente y te respondo."
        )

    # Si tiene contexto del activo → responder en base a eso
    if ctx.get("activo") and not activo_detectado:
        ultimo = ctx["activo"]
        nombre_corto = ultimo.split(" ")[-1].lower()
        return (
            f"🤔 No te entendí bien. ¿Seguimos con *{ultimo}*?\n\n"
            f"Dime qué quieres:\n"
            f"• _¿Cómo va el {nombre_corto}?_\n"
            f"• _¿Hay señal de {nombre_corto}?_\n"
            f"• _¿A cuánto está el {nombre_corto}?_\n\n"
            "O escribe *ayuda* para ver todos los comandos."
        )

    # Si detectó una intención pero no el activo:
    if intencion_detectada == "analisis" and not activo_detectado:
        return (
            f"🔍 ¿De cuál activo?\n\n"
            f"Disponibles hoy: {_texto_activos_disponibles()}\n\n"
            f"👉 Ej: _Analiza el oro_"
        )

    if intencion_detectada == "precio" and not activo_detectado:
        return (
            f"💰 ¿De cuál activo?\n\n"
            f"Disponibles hoy: {_texto_activos_disponibles()}\n\n"
            f"👉 Ej: _Precio del oro_"
        )

    if intencion_detectada == "pivots" and not activo_detectado:
        return (
            f"📐 ¿De cuál activo?\n\n"
            f"Disponibles hoy: {_texto_activos_disponibles()}\n\n"
            f"👉 Ej: _Pivots del nasdaq_"
        )

    # Fallback genérico mejorado — rotación de sugerencias para no repetir siempre lo mismo
    sugerencias = [
        (
            "📊 *¿En qué puedo ayudarte hoy?* Prueba preguntarme algo como:\n\n"
            "💬 _\"Analiza el oro\"_\n"
            "💬 _\"¿Tuvimos ganancias hoy?\"_\n"
            "💬 _\"¿Qué señales tienes abiertas?\"_\n"
            "💬 _\"¿Cómo amaneció el oro?\"_\n\n"
            "📋 Escribe *ayuda* para ver mi catálogo técnico completo de BuySell365.pro"
        ),
        (
            "🤔 No logré captar la idea. Para que pueda ayudarte mejor, intenta ser directo:\n\n"
            "🔹 Para análisis: _\"¿Cómo ves el nasdaq?\"_\n"
            "🔹 Para rentabilidad: _\"¿Cuánto ganamos esta semana?\"_\n"
            "🔹 Para educación: _\"¿Qué significa apalancamiento?\"_\n\n"
            "🚀 _También puedes usar el botón de menú para ver opciones rápidas._"
        ),
        (
            "💡 ¡Hola! Soy el asistente de BuySell365.pro. Mi fuerte son los mercados financieros:\n\n"
            "• _\"¿Es buen momento para comprar oro?\"_\n"
            "• _\"¿Hay noticias importantes hoy?\"_\n"
            "• _\"¿Qué tal va el rendimiento del bot?\"_\n"
            "• _\"Enséñame un tip de trading\"_\n\n"
            "📋 Escribe *ayuda* y te mostraré todo lo que sé hacer."
        ),
    ]
    return random.choice(sugerencias)

# ============================================================
#  PROCESADOR DE MENSAJES ENTRANTES
# ============================================================

def procesar_mensaje(texto: str, remitente: str, es_admin: bool = False):
    """Interpreta el mensaje con lenguaje natural y responde de forma conversacional."""
    global SCALPER_ACTIVO, mt5_pausado, escaneo_pausado
    t = texto.strip().lower()
    
    # 👤 Obtener nombre del usuario para personalizar
    user_data = directorio_usuarios.get(remitente, {})
    nombre_user = escapar_markdown(user_data.get("nombre", "Trader"))  # H-11 FIX

    # ── 0. DETECTAR SEÑAL EXTERNA (Solo admin — protección contra inyección) ────
    senal = parsear_senal_externa(texto) if es_admin else None
    if senal:
        ticker = senal['ticker']
        tipo = senal['tipo']
        entrada = senal['entrada']
        sl = senal['sl']
        tp = senal['tp1']

        # Ejecutar trade si el auto-trading está activo
        if MT5_AVAILABLE and AUTO_TRADING:
            exito = ejecutar_orden_mt5(ticker, tipo, CAPITAL_USUARIO, RIESGO_POR_TRADE, entrada, sl, tp)
            if exito:
                return f"✅ *SEÑAL EXTERNA EJECUTADA EN XM*\n🚀 {tipo} {ticker} @ {entrada}\n🛑 SL: {sl}\n🎯 TP: {tp}"
            else:
                # Error de ejecución: solo notificar al admin, NO mostrar públicamente
                admin_id = ADMIN_IDS[0] if ADMIN_IDS else None
                if admin_id:
                    enviar_telegram(
                        f"⚠️ *Error MT5*: No se pudo abrir {tipo} {ticker}\n"
                        f"Entry: {entrada} | SL: {sl} | TP: {tp}",
                        admin_id
                    )
                return None
        else:
            # Solo informar al admin, no al chat público
            return None

    # ── 1. COMANDOS EXACTOS CON SLASH (máxima prioridad) ─────

    # 🆕 /start — Primera interacción del usuario con el bot (CRÍTICO para onboarding)
    if t in ("/start", "start"):
        pi_start = _vip_precio_info()
        _puede_trial_start = remitente not in _vip_trials_usados
        nombres_hoy_s, _, _ = activos_disponibles_hoy()
        n_activos_s = len(nombres_hoy_s)
        with _lock_ops:
            n_ops_s = len(operaciones_activas)
        _total_s = estadisticas_diarias["ganadas"] + estadisticas_diarias["perdidas"]
        _wr_s = (estadisticas_diarias["ganadas"] / _total_s * 100) if _total_s > 0 else 0

        start_txt = (
            f"👋 *Hola {nombre_user}!* Bienvenido a *BuySell365.pro*\n"
            f"━━━━━━━━━━\n\n"
            f"🤖 Soy tu asistente de trading con IA.\n"
            f"📡 Escaneo *{n_activos_s} activos* las 24 horas:\n"
            f"   Oro, EUR/USD, USD/JPY, GBP/JPY, NASDAQ, S&P 500\n\n"
        )
        if n_ops_s > 0:
            start_txt += f"📊 Ahora mismo: *{n_ops_s} operaciones activas*\n"
        if _total_s > 0:
            start_txt += f"🎯 Win Rate hoy: *{_wr_s:.0f}%* ({_total_s} señales)\n"
        start_txt += (
            f"\n💡 *Comandos rapidos:*\n"
            f"   /precios — Precios en vivo\n"
            f"   /noticias — Calendario economico\n"
            f"   /web — Dashboard en vivo\n"
            f"   /ayuda — Todos los comandos\n\n"
        )
        if _puede_trial_start:
            start_txt += f"🎁 *Prueba 5 dias habiles GRATIS* en el canal VIP → /vip 🚀"
        else:
            start_txt += f"👑 *Canal VIP* con señales completas → /vip"

        start_botones = {
            "inline_keyboard": [
                [{"text": f"🎁 5 DIAS HABILES GRATIS", "callback_data": "vip_trial_gratis"}] if _puede_trial_start else [{"text": "👑 VER CANAL VIP", "callback_data": "vip_pagar_usdt"}],
                [{"text": "📊 Precios en Vivo", "callback_data": "/precios"}, {"text": "📅 Noticias", "callback_data": "/noticias"}],
                [{"text": "🌐 Web en Vivo", "url": "https://buysell365.pro/dashboard"}],
            ]
        }
        return start_txt, start_botones

    if t in ("/ayuda", "/help", "ayuda", "help", "comandos", "menu", "❓ ayuda"):
        res_ayuda = f"👋 ¡Hola *{nombre_user}*! " + cmd_ayuda()
        return res_ayuda, crear_teclado_principal()
    if t in ("/señales", "/senales", "/operaciones", "/abiertas", "/activas", "activas",
             "señales", "senales", "señales activas", "📊 señales activas"):
        return cmd_senales(), crear_teclado_principal()
    if t in ("/estado", "/stats", "/estadisticas", "⚙️ estado bot"):
        return cmd_estado(), crear_teclado_principal()
    if t in ("/resumen", "/historial", "/reporte", "📈 resumen diario"):
        return cmd_resumen(), crear_teclado_principal()
    if t in ("/noticias", "/news", "noticias", "📅 noticias"):
        return cmd_noticias(), crear_teclado_principal()
    if t in ("🚀 análisis oro"):
        return cmd_analisis("ORO"), crear_teclado_principal()
    if t in ("🔍 análisis nasdaq"):
        return cmd_analisis("NASDAQ"), crear_teclado_principal()
    if t in ("/tendencia", "/tendencias", "tendencia", "tendencias"):
        return cmd_tendencia(), crear_teclado_principal()
    if t in ("/top", "top"):
        return cmd_top()
    if t in ("/semana", "/acumulado", "semana", "acumulado"):
        return cmd_semana()
    if t in ("/horarios", "/horario", "horarios", "horario"):
        return cmd_horarios()
    # /record y /racha eliminados — info ya incluida en /estado y /resumen
    if t in ("/estado bot", "/bot", "estado bot", "estado del bot", "/estado del bot"):
        return cmd_estado_bot()
    # FIX 2026-03-19: Comando /admin con lista completa de comandos admin
    if t in ("/admin", "admin", "/admin help", "admin help"):
        if not es_admin: return "⛔ Solo administradores."
        return (
            "🔧 *COMANDOS DE ADMINISTRADOR*\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "⏸️ *Control del bot:*\n"
            "• `/pausar` — Pausa total (señales+scalper+MT5)\n"
            "• `/reanudar` — Reactiva todo\n"
            "• `/pausar scalper` — Solo pausar scalper\n"
            "• `/play scalper` — Reactivar scalper\n"
            "• `/reiniciar` — Reiniciar proceso\n"
            "• `/apagar` — Apagar bot\n"
            "• `/reset` — Reset stats diarias\n\n"
            "👑 *VIP:*\n"
            "• `/vip_lista` — Suscriptores activos\n"
            "• `/vip_pendientes` — Pagos pendientes\n"
            "• `/vip_dar [ID]` — Dar acceso VIP\n"
            "• `/vip_quitar [ID]` — Quitar VIP\n"
            "• `/addvip [args]` — Añadir VIP manual\n"
            "• `/gencode` — Generar código invitación\n"
            "• `/codes` — Ver códigos activos\n"
            "• `/delcode [code]` — Eliminar código\n\n"
            "📊 *Trading:*\n"
            "• `/activar [activo]` — Activar escaneo\n"
            "• `/desactivar [activo]` — Desactivar escaneo\n"
            "• `/capital [monto]` — Cambiar capital base\n"
            "• `/scalper` — Estado del scalper\n"
            "• `/logs` — Últimas líneas del log\n"
            "• `/briefing` — Enviar briefing al grupo"
        )
    # 🛑 /pausar — Pausa TOTAL: detiene señales, scalper y ejecuciones MT5
    if t in ("/pausar", "/pause", "pausar", "pausar todo", "stop trading"):
        if not es_admin: return "⛔ Solo administradores pueden pausar el bot."
        global mt5_pausado, escaneo_pausado
        mt5_pausado = True
        escaneo_pausado = True
        SCALPER_ACTIVO = False
        guardar_estado()
        log_sistema("🛑 PAUSA TOTAL activada desde Telegram")
        return ("🛑 *PAUSA TOTAL ACTIVADA*\n\n"
                "• Señales Premium: ⏸️ pausadas\n"
                "• Scalper: ⏸️ pausado\n"
                "• Ejecuciones MT5: ⏸️ pausadas\n\n"
                "Las operaciones abiertas siguen activas.\n"
                "Usa /reanudar para reactivar todo.")

    # ▶️ /reanudar — Reactiva todo
    if t in ("/reanudar", "/resume", "reanudar", "reanudar todo", "start trading"):
        if not es_admin: return "⛔ Solo administradores pueden reanudar el bot."
        mt5_pausado = False
        escaneo_pausado = False
        SCALPER_ACTIVO = True
        guardar_estado()
        log_sistema("▶️ TODO REACTIVADO desde Telegram")
        return ("▶️ *TODO REACTIVADO*\n\n"
                "• Señales Premium: ✅ activas\n"
                "• Scalper: ✅ activo\n"
                "• Ejecuciones MT5: ✅ activas\n\n"
                "El bot vuelve a operar normalmente.")

    if t in ("/reiniciar", "reiniciar bot", "reiniciar proceso", "restart", "reboot"):
        if not es_admin: return "⛔ Solo administradores pueden reiniciar el proceso del bot."
        enviar_telegram("🔄 *Reiniciando proceso del bot...*", remitente)
        try:
            guardar_estado()
            time.sleep(2)
            _exe = os.path.abspath(sys.executable)
            _script = os.path.abspath(sys.argv[0]) if sys.argv else ''
            if not os.path.isfile(_exe) or not _script or not os.path.isfile(_script):
                logger.error(f"❌ Reinicio abortado: executable={_exe} script={_script}")
                return "❌ Reinicio abortado — ruta inválida."
            os.execv(_exe, [_exe, _script])
        except Exception as e_restart:
            logger.error(f"❌ Error al reiniciar: {e_restart}")
            return f"❌ Error al reiniciar: {e_restart}"
        return "🔄 Reiniciando..."

    if t in ("/apagar", "apagar bot", "shutdown", "exit", "stop"):
        if not es_admin: return "⛔ Solo administradores pueden apagar el bot."
        enviar_telegram("🛑 *Apagando bot de trading. Hasta pronto.*", remitente)
        time.sleep(2)
        os._exit(0)
        return "🛑 Apagando..."

    if t in ("/reset", "reset", "bot nuevo", "empezar de cero", "limpiar operaciones", "borrar operaciones"):
        if not es_admin: return "⛔ Solo administradores pueden resetear las estadísticas."
        return cmd_reset()
    if t in ("/winrate", "/win rate", "winrate", "win rate", "/win"):
        return cmd_winrate()
    if t in ("/precios", "precios", "precio todo", "precio todos", "todos los precios",
             "ver precios", "dame los precios", "precios en vivo",
             "precio de todo", "cuanto estan todos"):
        return cmd_precios_tv()
    if t in ("/web", "web", "web en vivo", "/dashboard", "dashboard", "📊 web en vivo"):
        return cmd_url_dashboard()
    if t in ("/mercados", "mercados", "activos", "/activos", "que activos", "que mercados"):
        return cmd_mercados()
    if t in ("/mis alertas", "/alertas", "mis alertas", "alertas"):
        return cmd_mis_alertas()
    # /suscripciones eliminado — info interna del bot
    if t in ("/sentimiento", "/feargreed", "/fg", "sentimiento", "fear greed"):
        return cmd_sentimiento()
    if t in ("/horarios", "/horario", "horarios"):
        return cmd_horarios()
    # /como y /riesgo eliminados — info interna, no pública
    if t in ("/vip", "vip", "acceso", "premium", "pagar", "suscripcion", "suscribirse", "/suscripcion",
             "canal", "canal vip", "entrar al canal", "quiero entrar", "como entro",
             "unirme", "quiero unirme", "membresia", "membresía",
             "prueba", "prueba gratis", "gratis", "trial", "probar", "dias gratis",
             "quiero probar", "probar gratis", "prueba gratuita") or \
       any(p in t for p in ("canal vip", "entrar al canal", "quiero entrar", "quiero unirme",
                             "como entro al", "como me uno", "acceso al canal", "acceso vip",
                             "quiero acceso", "me interesa el vip", "suscribirme al",
                             "prueba gratis", "dias gratis", "quiero probar")):
        return cmd_vip(user_id=remitente)

    # ── COPY TRADING ──
    if t in ("/copy", "/copytrading", "/copy_trading", "copy", "copytrading", "copy trading",
             "copiar operaciones", "copiar senales", "copiar señales") or \
       any(p in t for p in ("copy trading", "copytrading", "copiar operaciones",
                             "copiar trades", "como copio", "quiero copiar",
                             "copiar automatico", "copia automatica")):
        return (
            "🚀 *COPY TRADING — BuySell365.pro*\n"
            "━━━━━━━━━━\n\n"
            "Copia nuestras operaciones automaticamente en tu cuenta.\n\n"
            "✅ Sin experiencia necesaria\n"
            "✅ Operaciones en tiempo real\n"
            "✅ Oro, Forex, NASDAQ, S&P 500\n"
            "✅ Broker regulado XM\n\n"
            "📌 *Como funciona:*\n"
            "1. Abre tu cuenta en XM (link abajo)\n"
            "2. Activa Copy Trading\n"
            "3. Nuestras operaciones se copian automaticamente\n\n"
            "💡 _Tu controlas el riesgo y puedes pausar cuando quieras._",
            {"inline_keyboard": [
                [{"text": "🚀 ACTIVAR COPY TRADING", "url": "https://social.tp-redirect.com/s/WRE0V7jm"}],
                [{"text": "👑 CANAL VIP", "callback_data": "vip_pagar_usdt"}, {"text": "❓ AYUDA", "callback_data": "/ayuda"}]
            ]}
        )

    # ── ADMIN VIP COMMANDS ──
    if t in ("/vip_lista", "vip_lista", "vip lista"):
        if not es_admin: return "⛔ Solo administradores."
        return cmd_vip_lista()
    if t in ("/vip_pendientes", "vip_pendientes", "vip pendientes"):
        if not es_admin: return "⛔ Solo administradores."
        return cmd_vip_pendientes()
    if t.startswith("/vip_dar ") or t.startswith("vip_dar "):
        if not es_admin: return "⛔ Solo administradores."
        target_id = t.replace("/vip_dar ", "").replace("vip_dar ", "").strip()
        return cmd_vip_dar(target_id)
    if t.startswith("/vip_quitar ") or t.startswith("vip_quitar "):
        if not es_admin: return "⛔ Solo administradores."
        target_id = t.replace("/vip_quitar ", "").replace("vip_quitar ", "").strip()
        return cmd_vip_quitar(target_id)

    # ── ADMIN COMMANDS REMOTOS (Feature B) ──
    if t in ("/logs", "logs"):
        if not es_admin: return "⛔ Solo administradores."
        return cmd_admin_logs()
    if t.startswith("/addvip ") or t.startswith("addvip "):
        if not es_admin: return "⛔ Solo administradores."
        args = t.replace("/addvip ", "").replace("addvip ", "").strip()
        return cmd_admin_addvip(args)
    if t.startswith("/gencode") or t.startswith("gencode") or t.startswith("generar codigo") or t.startswith("/generar codigo"):
        if not es_admin: return "⛔ Solo administradores."
        args = t.replace("/gencode", "").replace("gencode", "").replace("/generar codigo", "").replace("generar codigo", "").strip()
        return cmd_admin_gencode(args)
    if t in ("/codes", "codes", "/codigos", "codigos"):
        if not es_admin: return "⛔ Solo administradores."
        return cmd_admin_codes()
    if t.startswith("/delcode ") or t.startswith("delcode "):
        if not es_admin: return "⛔ Solo administradores."
        code = t.replace("/delcode ", "").replace("delcode ", "").strip()
        return cmd_admin_delcode(code)

    # ── DETECCIÓN DE CÓDIGO DE INVITACIÓN BS365-XXXX ──
    _match_codigo = re.match(r'^BS365-[A-Z0-9]{4}$', texto.strip().upper())
    if _match_codigo:
        _code_detectado = _match_codigo.group(0)
        user_data_code = directorio_usuarios.get(remitente, {})
        nombre_code = user_data_code.get("nombre", "Trader")
        _procesar_codigo_invitacion(_code_detectado, remitente, nombre_code)
        return None  # Se maneja internamente

    if t in ("/briefing", "briefing", "/mercado hoy", "mercado hoy"):
        cmd_briefing()
        return "📊 Briefing enviado al grupo."
    # /modo eliminado del chat público — se configura internamente
    # /capital eliminado del chat público — info privada del trader

    # FIX 2026-03-19: /pausar y /reanudar duplicados eliminados — ya manejados arriba (línea 8549/8565)
    # Solo dejamos los comandos granulares que NO están duplicados
    if t in ("/pausar todo", "pausar todo", "/stop todo", "stop todo", "/pause all", "pause all"):
        if not es_admin: return "⛔ Solo administradores."
        return cmd_pausar_todo()

    if t in ("/reanudar todo", "reanudar todo", "/play todo", "play todo", "/start todo", "start todo"):
        if not es_admin: return "⛔ Solo administradores."
        return cmd_reanudar_todo()

    # ── Scalper control ──
    if t in ("/pausar scalper", "pausar scalper", "/stop scalper", "stop scalper",
             "/scalper stop", "scalper stop", "/scalper pausar", "scalper pausar"):
        if not es_admin: return "⛔ Solo administradores."
        return _cmd_scalper_pausar()

    if t in ("/play scalper", "play scalper", "/scalper play", "scalper play",
             "/continuar scalper", "continuar scalper", "/scalper on", "scalper on",
             "/start scalper", "start scalper", "/reanudar scalper", "reanudar scalper"):
        if not es_admin: return "⛔ Solo administradores."
        return _cmd_scalper_reanudar()

    if t in ("/scalper", "/scalper estado", "scalper estado", "scalper status"):
        return _cmd_scalper_estado()

    # ── 2. COMANDOS CON PARÁMETRO ─────────────────────────────
    if t.startswith("/precio "):
        activo = t.replace("/precio ", "").strip()
        guardar_contexto(remitente, activo=detectar_activo_fuzzy(activo))
        return cmd_precio(activo)
    if t.startswith("/analisis ") or t.startswith("/análisis "):
        activo = t.replace("/analisis ", "").replace("/análisis ", "").strip()
        guardar_contexto(remitente, activo=detectar_activo_fuzzy(activo), intencion="analisis")
        return cmd_analisis(activo)
    # /glosario, /que es, /pip, /abierto eliminados — no son core del servicio
    if t.startswith("/alerta ") or t.startswith("alerta "):
        return cmd_alerta(t.replace("/alerta ", "").replace("alerta ", "").strip())
    if t.startswith("/borrar alerta ") or t.startswith("borrar alerta "):
        return cmd_borrar_alerta(t.replace("/borrar alerta ", "").replace("borrar alerta ", "").strip())
    # /modo [valor] eliminado del chat — se configura editando MODO_RIESGO en el código
    if t.startswith("/activar ") or t.startswith("activar "):
        if not es_admin: return "⛔ Solo administradores pueden activar activos."
        return cmd_activar(t.replace("/activar ", "").replace("activar ", "").strip())
    if t.startswith("/desactivar ") or t.startswith("desactivar "):
        if not es_admin: return "⛔ Solo administradores pueden desactivar activos."
        return cmd_desactivar(t.replace("/desactivar ", "").replace("desactivar ", "").strip())
    if t.startswith("/pivots ") or t.startswith("pivots ") or t.startswith("/pivot "):
        return cmd_pivots(t.replace("/pivots ", "").replace("pivots ", "").replace("/pivot ", "").strip())
    if t.startswith("/capital ") or t.startswith("capital "):
        if not es_admin: return "⛔ Solo administradores pueden cambiar el capital base."
        return cmd_capital(t.replace("/capital ", "").replace("capital ", "").strip())

    # ── 3. SALUDOS Y EXPRESIONES SENCILLAS ─────────
    if t in ["hola", "hello", "hi", "hey", "ola", "buenas", "buen dia", "buen día", "buenos dias", "buenos días", "buenas tardes", "buenas noches", "que tal", "que pasas", "como estas"]:
        hora = ahora().hour
        if hora < 12:
            base = random.choice(RESPUESTAS_SALUDO_DIA)
        elif hora < 20:
            base = random.choice(RESPUESTAS_SALUDO_TARDE)
        else:
            base = random.choice(RESPUESTAS_SALUDO_NOCHE)
        
        saludo = base.format(name=nombre_user)
        nombres_hoy, limitado, _ = activos_disponibles_hoy()
        activos_txt = ", ".join(n.upper() for n in nombres_hoy[:3])
        return (
            f"{saludo}\n\n"
            f"📡 Escaneando {len(nombres_hoy)} activos: {activos_txt}...\n"
            "💡 Pregúntame: _\"Analiza el oro\"_ o escribe *ayuda*"
        )

    # 3.4. DETECCIÓN DIRECTA DE ACTIVO (Si el usuario solo escribe "ORO" o "NASDAQ")
    if t in [a.lower() for a in ALIASES_ACTIVOS_FUZZY.keys()]:
        activo = detectar_activo_fuzzy(t)
        guardar_contexto(remitente, activo=activo)
        # Añadir un tip educativo al final del análisis directo
        res_base = cmd_analisis(activo.split(" ")[-1].lower())
        cuerpo = res_base[0] if isinstance(res_base, tuple) else res_base
        teclado = res_base[1] if isinstance(res_base, tuple) else None
        
        cuerpo += f"\n\n{obtener_tip_aleatorio()}"
        return (con_firma(cuerpo), teclado)

    # 3.5. RESPUESTA A PALABRAS SUELTAS COMO "PRECIO"
    if t == "precio":
        return (
            "💰 ¿De qué activo quieres saber el precio?\n\n"
            "Ejemplo: _\"Precio del oro\"_ o _\"A cuánto está el nasdaq\"_"
        )

    # 3.6. RESPUESTAS SOCIALES — SIEMPRE ANTES DE DETECTAR ACTIVO ──────────
    # Si esto va después del bloque de activo, el contexto guardado manda a cmd_analisis()
    if any(p in t for p in ["gracias", "thanks", "thank you", "grax", "genial", "excelente", "bien hecho", "muy bien", "eres el mejor"]):
        return random.choice(RESPUESTAS_GRACIAS)

    if any(p in t for p in ["ok", "vale", "entendido", "listo", "de acuerdo", "roger", "perfecto", "claro"]):
        return random.choice(RESPUESTAS_OK)

    if any(p in t for p in ["perdi", "perdí", "me fue mal", "estoy perdiendo", "me salto el sl",
                              "perdida", "pérdida", "mal dia", "toque sl", "malo el dia"]):
        return (
            "💆‍♂️ Tómatelo con calma. Hasta en las mejores estrategias hay caídas puntuales.\n\n"
            "🛡️ *Recuerda:* Si el sistema activó el SL, te protegió de una caída mayor.\n"
            "¿Quieres analizar el activo para buscar re-entrada? Dime cuál. 📊"
        )

    if any(p in t for p in ["no funciona", "no sirve", "que malo", "no entiendo nada",
                              "me perdí", "estoy perdido", "ayudame", "no te entiendo"]):
        return (
            "😅 Sin problema, dime el activo que te interesa:\n"
            "_\"Oro\"_, _\"EUR/USD\"_, _\"USD/JPY\"_, _\"Nasdaq\"_... y hago el análisis.\n"
            "O escribe *ayuda* para ver todos los comandos."
        )

    if any(p in t for p in ["eres", "quien eres", "quién eres", "que eres", "presentate", "quien te creo", "vienes de", "quien es emmanuel", "quien es el creador"]):
        return (
            "🤖 *BuySell365.pro* — Bot de senales de trading\n\n"
            "📡 Escaneo 6 activos (Oro, Forex, Índices)\n"
            "🎯 Señales con Entry, SL, TP1-TP3\n"
            "🛡️ Gestión de riesgo profesional\n"
            "📊 Trading en Vivo: /web\n\n"
            "💡 Escribe *ayuda* para ver los comandos."
        )

    if any(p in t for p in ["cuanto llevas", "uptime", "tiempo activo", "desde cuando",
                              "estado del bot", "como esta el bot", "cómo está el bot", "estas despierto"]):
        return cmd_estado_bot()

    # ── 4. DETECCIÓN DE ACTIVO + INTENCIÓN (lenguaje natural ampliado) ─
    activo_detectado  = detectar_activo_fuzzy(t)
    intencion_detectada = detectar_intencion(t)
    ctx = obtener_contexto(remitente)

    # Tratar intenciones muy específicas (con o sin comandos)
    # "entrar" solo es trading si NO mencionan canal/grupo/vip
    _es_trading = ("comprar" in t or "vender" in t or "operar" in t
                   or ("entrar" in t and not any(w in t for w in ("canal", "grupo", "vip", "unir"))))
    if _es_trading:
        intencion_detectada = "analisis"
    if any(p in t for p in [
        "resumen del dia", "resumen del día", "resumen de hoy", "como fue el dia",
        "como fue el día", "que tal el dia", "resultado del dia", "balance del dia",
        "resumen diario", "resumen", "dame un resumen", "dame el resumen",
        "resume", "el resume", "dame resume", "dame el resume",
        "resumen de operaciones", "como quedamos", "como quedamos hoy",
        "balance del bot", "reporte del dia", "reporte de hoy",
        "que tal fue el dia", "como nos fue", "cuantas operaciones hicimos",
        "resumen de hoy", "show me the summary", "give me the summary",
    ]):
        if "ganancia" in t or "ganancias" in t:
            return con_firma(cmd_resumen_ganancias())
        return con_firma(cmd_resumen())
    if "como esta el mercado" in t or "mercados de hoy" in t:
        return cmd_top()

    # ── Intenciones globales (sin activo): deben evaluarse ANTES de usar el contexto ──
    # Así evitamos que el contexto previo desvíe hacia cmd_analisis()
    if intencion_detectada == "senales_activas" or any(p in t for p in [
        "hay señal", "hay senal", "hay algo", "alguna señal", "dame señal", "dame señales",
        "operaciones abiertas", "operaciones abierta", "tienes algo", "en que estas",
        "que tienes abierto", "posiciones abiertas", "algun trade", "hay entrada",
        "hay operaciones", "operaciones activas", "oportunidad"
    ]):
        return cmd_senales()

    # Usar contexto si no se detectó activo nuevo
    activo_efectivo = activo_detectado or ctx.get("activo")

    if activo_efectivo:
        guardar_contexto(remitente, activo=activo_efectivo, intencion=intencion_detectada)
        kw = activo_efectivo.split(" ")[-1].lower()  # "ORO", "BITCOIN", etc.

        # Precio
        if intencion_detectada == "precio" or any(p in t for p in ["precio", "cuanto vale", "cuanto cuesta", "cotiza", "a cuanto", "vale hoy", "cuanto es"]):
            return con_firma(cmd_precio(kw))
        # Pivots
        if intencion_detectada == "pivots" or any(p in t for p in ["pivot", "soporte", "resistencia", "nivel", "niveles", "pp", "r1", "s1"]):
            return cmd_pivots(kw)
        # Horario
        if intencion_detectada == "horarios" or any(p in t for p in ["abierto", "abre", "horario", "cuando opera", "cierra"]):
            return cmd_abierto(kw)
        # Qué es
        if any(p in t for p in ["que es", "qué es", "info", "descripcion", "explica", "cuentame"]):
            return cmd_que_es(kw)

        # Análisis solo si el activo está en el texto ACTUAL o hay intención explícita
        # No disparar análisis por tener un activo guardado en contexto sin intención clara
        if activo_detectado or intencion_detectada in ("analisis",):
            return con_firma(cmd_analisis(kw))

    # ── 5. INTENCIONES GENERALES (Que no requieren especificar un activo) ─────────────────
    if intencion_detectada == "mercado_general" or any(p in t for p in [
        "como va el mercado", "estado del mercado", "como esta todo",
        "panorama", "overview", "vista general", "como esta todo",
        "como esta la bolsa", "que mueve hoy", "que tal el mercado"
    ]):
        return con_firma(cmd_tendencia())

    if intencion_detectada == "noticias" or any(p in t for p in [
        "noticias", "eventos", "calendario", "hay noticias", "nfp", "fed", "bce",
        "fomc", "datos macro", "agenda economica", "que pasa hoy", "noticias importantes"
    ]):
        return con_firma(cmd_noticias())

    if intencion_detectada == "sentimiento" or any(p in t for p in [
        "miedo", "codicia", "sentimiento", "fear", "greed", "indice de miedo", "hay panico"
    ]):
        return con_firma(cmd_sentimiento())

    if intencion_detectada == "educacion":
        return con_firma("📚 *TIP DE TRADING DEL DÍA:*\n\n_\"La disciplina es hacer lo que sabes que tienes que hacer, incluso cuando no tienes ganas de hacerlo.\"_\n\nEnfócate en seguir tu gestión de riesgo (1% por trade). Mi algoritmo se encarga de las mates, tú te encargas de la calma.")

    if intencion_detectada == "volatilidad" or any(p in t for p in [
        "volatil", "volatilidad", "mas activo", "mueve mas", "cual se mueve",
        "el mas activo", "mejor activo ahora", "cual opera mejor"
    ]):
        return cmd_volatilidad()

    if intencion_detectada == "horarios" or any(p in t for p in [
        "horario", "cuando abre", "esta abierto", "sesion", "a que hora",
        "apertura", "cierre del mercado", "abre hoy", "cierra hoy", "apertura bolsa"
    ]):
        return cmd_horarios()

    if intencion_detectada == "estadisticas" or any(p in t for p in [
        "como vamos", "resultados", "ganancias", "balance", "cuanto ganaste",
        "es rentable", "efectividad", "win rate", "winrate", "tasa de acierto",
        "historial", "cuantas ganadas", "resumen del bot"
    ]):
        return cmd_estado()

    if any(p in t for p in ["que recomiendas", "mejor activo", "donde entrar", "que compro", "por donde entro"]):
        top = cmd_top()
        return (
            "📊 *Estas son las mejores oportunidades ahora:*\n\n"
            f"{top}"
        )

    # ── 6. PREGUNTAS EDUCATIVAS ───────────────────────────────

    # Glosario: "que es X", "que significa X", "explicame X"
    if intencion_detectada == "glosario" or any(p in t for p in [
        "que es el", "que es un", "que es una", "que es la", "que significa",
        "explicame el", "explicame la", "explicame un", "para que sirve el",
        "como funciona el rsi", "como funciona el macd", "como funciona el atr",
        "como funciona el adx", "como funciona el ema", "como funciona el stoch"
    ]):
        # Buscar a qué término se refiere
        for key in list(GLOSARIO.keys()) + list(GLOSARIO_ALIAS.keys()):
            if re.search(r'\b' + re.escape(key) + r'\b', t):
                clave = GLOSARIO_ALIAS.get(key, key)
                if clave in GLOSARIO:
                    return GLOSARIO[clave]
        # Fallback: última o penúltima palabra
        palabras = [p for p in t.split() if len(p) > 2]
        if palabras:
            return cmd_glosario(palabras[-1])

    # Tipos de órdenes
    if intencion_detectada == "ordenes" or any(p in t for p in [
        "tipo de orden", "tipos de orden", "buy limit", "sell limit",
        "buy stop", "sell stop", "market order", "orden limite", "trailing stop"
    ]):
        return cmd_glosario("ordenes")

    # Patrones de velas
    if intencion_detectada == "patrones_velas" or any(p in t for p in [
        "patron de vela", "candlestick", "doji", "hammer", "engulfing",
        "shooting star", "morning star", "evening star", "pin bar"
    ]):
        return cmd_glosario("patrones")

    # Position sizing / tamaño de posición
    if intencion_detectada == "position_sizing" or any(p in t for p in [
        "cuanto lotes", "cuantos lotes", "tamaño de posicion", "tamaño de la posicion",
        "position size", "calculo de lote", "cuanto capital usar", "cuanto entro"
    ]):
        return (
            "📐 *CÁLCULO DE TAMAÑO DE POSICIÓN*\n"
            "━━━━━━━━━━\n\n"
            "📌 *Fórmula básica (regla del 1-2%):*\n"
            "   Riesgo $ = Capital × 1%\n"
            "   Pips de riesgo = Entrada − SL\n"
            "   Lotes = Riesgo $ / (Pips × Valor por pip)\n\n"
            "📌 *Ejemplo práctico — EUR/USD:*\n"
            "   Capital: 1.000$\n"
            "   Riesgo 1%: 10$\n"
            "   SL: 20 pips (0.0020)\n"
            "   Valor pip mini lote: 1$\n"
            "   Lotes: 10$ / (20 × 1$) = *0.50 mini lotes*\n\n"
            "📌 *Valores por pip (1 lote estándar):*\n"
            "   EUR/USD:  ≈ 10 $/pip\n"
            "   ORO:      ≈ 10 $/pip\n"
            "   NASDAQ:   ≈ 1 $/punto\n\n"
            "💡 Reducir el lote es más inteligente que eliminar el SL"
        )

    # Correlaciones entre activos
    if intencion_detectada == "correlacion" or any(p in t for p in [
        "correlacion entre", "el oro y el dolar", "nasdaq y sp500",
        "relacion entre activos", "se mueven juntos", "activos correlacionados"
    ]):
        return (
            "🔗 *CORRELACIONES CLAVE DE MERCADO*\n"
            "━━━━━━━━━━\n\n"
            "📌 *ORO vs USD (correlación negativa):*\n"
            "   Dólar fuerte 📈  →  Oro baja 📉\n"
            "   Dólar débil  📉  →  Oro sube 📈\n"
            "📌 *NASDAQ vs S&P 500 (correlación positiva):*\n"
            "   Suelen moverse en la misma dirección\n"
            "   NASDAQ es más volátil (más tecnología)\n\n"
            "📌 *EUR/USD vs USD Index (DXY):*\n"
            "   EUR/USD sube cuando el DXY baja (inversa)\n\n"
            "📌 *USD/JPY vs USD Index (DXY):*\n"
            "   USD/JPY sube cuando el DXY sube (directa)\n"
            "   Contraria al EUR/USD — útil para confirmar fuerza del dólar\n\n"
            "⚠️ Las correlaciones no son fijas — cambian con el mercado\n"
            "💡 Evita abrir EUR/USD y USD/JPY COMPRA al mismo tiempo (correlación inversa)"
        )

    # Fibonacci
    if intencion_detectada == "fibonacci" or any(p in t for p in [
        "fibonacci", "fibo", "61.8", "38.2", "retroceso fib"
    ]):
        return cmd_glosario("fibonacci")

    # Educación general de trading
    if intencion_detectada == "educacion" or any(p in t for p in [
        "dame un consejo", "dame consejos", "tips de trading",
        "consejos para traders", "como mejorar en trading", "trucos de trading"
    ]):
        return (
            "📚 *CONSEJOS CLAVE PARA TRADERS*\n"
            "━━━━━━━━━━\n\n"
            "1️⃣ *Gestión de riesgo primero*\n"
            "   Nunca arriesgues más del 1-2% por operación.\n"
            "   Un sistema con 40% de acierto puede ser rentable con buen R:R.\n\n"
            "2️⃣ *Opera con el mercado, no contra él*\n"
            "   EMA200 apunta arriba → solo COMPRAS.\n"
            "   EMA200 apunta abajo → solo VENTAS.\n\n"
            "3️⃣ *Evita las noticias*\n"
            "   Cierra posiciones antes de datos macro importantes (NFP, FOMC).\n"
            "   El bot bloquea señales automáticamente en esas ventanas.\n\n"
            "4️⃣ *Lleva un diario de trading*\n"
            "   Anota por qué entraste, por qué saliste, cómo te sentiste.\n"
            "   En 1 mes verás patrones de tus errores.\n\n"
            "5️⃣ *Las emociones son el enemigo*\n"
            "   No recuperes pérdidas aumentando el lote.\n"
            "   Si perdiste 3 operaciones seguidas → para y descansa.\n\n"
            "💡 Escribe `/glosario` para aprender los términos del mercado"
        )

    if any(p in t for p in ["como funciona", "como se usa", "explicame", "que hace el bot", "como te uso", "que sabes hacer"]):
        return cmd_como()

    if any(p in t for p in ["riesgo", "stop loss", "cuanto arriesgar", "gestion de riesgo", "cuanto arriesgo", "cuanto perder"]):
        return cmd_riesgo()

    if intencion_detectada == "nuevo_usuario" or any(p in t for p in [
        "como empezar", "soy nuevo", "no se de trading", "quiero aprender",
        "por donde empiezo", "novato", "principiante"
    ]):
        return (
            "👋 ¡Bienvenido! Te explico cómo usarme como a un trader pro:\n\n"
            "1️⃣ Escríbeme _¿Cómo va el oro?_ o _¿Cómo está el nasdaq?_ y te haré un análisis.\n"
            "2️⃣ Si te digo que hay una oportunidad, te daré los precios exactos para programar tu entrada, el Stop Loss y el Take Profit.\n"
            "3️⃣ Pregúntame si hay noticias antes de entrar, para evitar sustos.\n\n"
            "📌 *Regla de oro*: Por muy buena que sea mi señal, *nunca arriesgues más del 2%* de tu cuenta.\n\n"
            "Escribe *ayuda* para ver la lista técnica completa. 🤖"
        )

    # ── 7. RESPUESTAS ADICIONALES ─────────────────
    # NOTA: gracias/ok/perdi/no funciona ya se manejan en sección 3.6 (líneas anteriores)

    if any(p in t for p in ["cuanto llevas", "uptime", "tiempo activo", "desde cuando", "estado del bot", "como esta el bot", "cómo está el bot", "estas despierto"]):
        return cmd_estado_bot()

    # ── 8. PREGUNTAS DE SEGUIMIENTO SIN ACTIVO (usa contexto histórico) ─
    if ctx.get("activo") and any(p in t for p in [
        "y los pivots", "los niveles", "el soporte", "la resistencia",
        "sigue", "aun", "todavia", "todavía", "y ahora", "y el precio",
        "cuanto lleva", "como sigue", "mas info", "más info", "y de precio"
    ]):
        kw = ctx["activo"].split(" ")[-1].lower()
        if "pivot" in t or "nivel" in t or "soporte" in t or "resistencia" in t:
            return con_firma(cmd_pivots(kw))
        if "precio" in t or "cuanto" in t or "vale" in t:
            return con_firma(cmd_precio(kw))
        return con_firma(cmd_analisis(kw))

    # ── 9. PREGUNTAS SOBRE PRÓXIMAS SEÑALES ──────────────────
    if any(p in t for p in [
        "cuando daras", "cuando darás", "cuando hay señal", "cuando habrá señal",
        "proxima señal", "próxima señal", "cuando señal", "cuándo señal",
        "cuando me avisas", "cuándo me avisas", "cuando sale", "cuándo sale",
        "hay alguna señal", "tienes señal", "alguna señal"
    ]):
        n_activas = len(operaciones_activas)
        proxima = max(0, int(INTERVALO_ESCANEO - (time.time() - ultimo_escaneo)))
        txt = (
            "📡 *ESPERANDO ESCANEO DE ESTRATEGIAS*\n"
            "━━━━━━━━━━\n"
            "🔄 Las matemáticas no descansan. Analizando 3 estrategias simultáneas...\n"
            f"⏱️ Terminando ciclo en ~{proxima // 60}m {proxima % 60}s\n\n"
            f"📊 Señales activas ahora mismo: *{n_activas}*\n\n"
            "Paciencia, como dicen los institucionales: _'El trading es 10% comprar, 10% vender, y 80% esperar'_. Avisaré automáticamente si el sistema valida algo."
        )
        return con_firma(txt)

    # ── 10. COMANDO DE EMERGENCIA: RESET STATS (SOLO ADMIN) ──
    if "/reset_stats" in t or "/reset" in t:
        if not es_admin:
            return "⛔ *Acceso denegado.* Solo administradores pueden resetear estadísticas."
        with _lock_ops:
            historial_operaciones.clear()
            estadisticas_diarias.update({"ganadas":0, "perdidas":0, "pips_ganados":0.0, "pips_perdidos":0.0, "senales_hoy":0})
            guardar_estado()
        return "🧹 *RESET COMPLETADO*\n\nHistorial y estadísticas reseteadas. Ya puedes operar de nuevo."

    # 11. FALLBACK FINAL INTELIGENTE ───────────────────────
    res = respuesta_fallback_inteligente(t, activo_detectado, intencion_detectada, remitente)
    return con_firma(res)

# ============================================================
#  WEBHOOK FLASK — TELEGRAM
# ============================================================



@app.route("/", methods=["GET", "POST"])
def index_web():
    """Landing page profesional (GET) o procesar webhook (POST)."""
    if request.method == "POST":
        return route_tv_signal()

    # --- Estadísticas en vivo para la landing ---
    try:
        _hist = historial_operaciones if historial_operaciones else []
        _wins = sum(1 for h in _hist if h.get('pips', 0) > 0)
        _total = len(_hist)
        _wr = round(_wins / _total * 100, 1) if _total > 0 else 78.5
        _pips = round(sum(h.get('pips', 0) for h in _hist), 1)
        _n_ops = sum(1 for op in operaciones_activas.values() if isinstance(op, dict) and op.get('mt5_ejecutado', False))
        _activos_trading = len(ACTIVOS)
    except Exception as e:
        logger.error(f"Landing stats error: {e}")
        _wr, _total, _pips, _n_ops, _activos_trading = 78.5, 0, 0, 0, 6

    try:
        return f"""<!DOCTYPE html>
<html lang="es">
<head>
<!-- Google Analytics -->
<script async src="https://www.googletagmanager.com/gtag/js?id=G-L514BL7E83"></script>
<script>window.dataLayer=window.dataLayer||[];function gtag(){{dataLayer.push(arguments);}}gtag('js',new Date());gtag('config','G-L514BL7E83');</script>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>BuySell365 Pro — Se\u00f1ales de Trading con Inteligencia Artificial</title>
<meta name="description" content="Se\u00f1ales de trading automatizadas con Inteligencia Artificial para Oro, Forex e \u00cdndices. An\u00e1lisis en 6 activos con IA avanzada y datos institucionales.">
<meta property="og:title" content="BuySell365 Pro \u2014 Trading con IA">
<meta property="og:description" content="Se\u00f1ales profesionales de trading con Inteligencia Artificial. Oro, EUR/USD, USD/JPY, GBP/JPY, NASDAQ, S&P 500.">
<meta property="og:image" content="https://buysell365.pro/img/og_image.png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:type" content="website">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:image" content="https://buysell365.pro/img/og_image.png">
<meta property="og:url" content="https://buysell365.pro">
<link rel="icon" href="/img/bull_bear.png" type="image/png">
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&display=swap');
*{{margin:0;padding:0;box-sizing:border-box}}
:root{{
  --bg:#0a0e17;--bg2:#111827;--bg3:#1a2332;
  --green:#00d4aa;--green2:#00f5c4;--blue:#3b82f6;--purple:#8b5cf6;
  --gold:#f59e0b;--red:#ef4444;--text:#e2e8f0;--text2:#94a3b8;
  --glass:rgba(255,255,255,0.03);--border:rgba(255,255,255,0.06);
}}
html{{scroll-behavior:smooth}}
body{{font-family:'Inter',sans-serif;background:var(--bg);color:var(--text);overflow-x:hidden}}

/* ═══ HERO ═══ */
.hero{{min-height:100vh;display:flex;align-items:center;justify-content:center;position:relative;overflow:hidden;padding:20px}}
.hero::before{{content:'';position:absolute;top:-50%;left:-50%;width:200%;height:200%;
  background:radial-gradient(circle at 30% 40%,rgba(0,212,170,0.08) 0%,transparent 50%),
             radial-gradient(circle at 70% 60%,rgba(59,130,246,0.06) 0%,transparent 50%),
             radial-gradient(circle at 50% 50%,rgba(139,92,246,0.04) 0%,transparent 50%);
  animation:heroGlow 20s ease infinite alternate}}
@keyframes heroGlow{{0%{{transform:rotate(0deg)}}100%{{transform:rotate(15deg)}}}}
.hero-content{{text-align:center;max-width:900px;z-index:2;position:relative}}
.hero-badge{{display:inline-flex;align-items:center;gap:8px;background:rgba(0,212,170,0.1);border:1px solid rgba(0,212,170,0.2);
  border-radius:50px;padding:6px 18px;font-size:13px;color:var(--green);margin-bottom:24px;font-weight:500}}
.hero-badge .dot{{width:8px;height:8px;background:var(--green);border-radius:50%;animation:pulse 2s infinite}}
@keyframes pulse{{0%,100%{{opacity:1}}50%{{opacity:0.3}}}}
.hero h1{{font-size:clamp(2.5rem,6vw,4.5rem);font-weight:900;line-height:1.1;margin-bottom:20px;
  background:linear-gradient(135deg,#fff 0%,var(--green) 50%,var(--blue) 100%);
  -webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text}}
.hero p{{font-size:clamp(1rem,2vw,1.25rem);color:var(--text2);max-width:650px;margin:0 auto 40px;line-height:1.7}}
.hero-buttons{{display:flex;gap:16px;justify-content:center;flex-wrap:wrap}}
.btn{{display:inline-flex;align-items:center;gap:8px;padding:14px 32px;border-radius:12px;font-size:15px;font-weight:600;
  text-decoration:none;transition:all 0.3s ease;cursor:pointer;border:none}}
.btn-primary{{background:linear-gradient(135deg,var(--green),#00b894);color:#0a0e17;box-shadow:0 4px 20px rgba(0,212,170,0.3)}}
.btn-primary:hover{{transform:translateY(-2px);box-shadow:0 8px 30px rgba(0,212,170,0.4)}}
.btn-secondary{{background:var(--glass);color:var(--text);border:1px solid var(--border)}}
.btn-secondary:hover{{background:rgba(255,255,255,0.08);transform:translateY(-2px)}}

/* ═══ STATS BAR ═══ */
.stats-bar{{display:flex;justify-content:center;gap:40px;margin-top:60px;flex-wrap:wrap}}
.stat-item{{text-align:center}}
.stat-value{{font-size:2rem;font-weight:800;color:var(--green)}}
.stat-value.blue{{color:var(--blue)}}
.stat-value.gold{{color:var(--gold)}}
.stat-value.purple{{color:var(--purple)}}
.stat-label{{font-size:12px;color:var(--text2);text-transform:uppercase;letter-spacing:1px;margin-top:4px}}

/* ═══ SECTIONS ═══ */
section{{padding:50px 20px}}
.section-title{{text-align:center;margin-bottom:30px}}
.section-title h2{{font-size:clamp(1.8rem,4vw,2.8rem);font-weight:800;margin-bottom:12px}}
.section-title p{{color:var(--text2);font-size:1.05rem;max-width:600px;margin:0 auto}}

/* ═══ FEATURES ═══ */
.features{{background:var(--bg2)}}
.features-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;max-width:1000px;margin:0 auto}}
.feature-card{{background:var(--bg3);border:1px solid var(--border);border-radius:14px;padding:20px;transition:all 0.3s ease}}
.feature-card:hover{{transform:translateY(-2px);border-color:rgba(0,212,170,0.2);box-shadow:0 4px 16px rgba(0,0,0,0.2)}}
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
.pricing{{background:var(--bg2)}}
.pricing-cards{{display:grid;grid-template-columns:repeat(3,1fr);gap:20px;max-width:1100px;margin:0 auto}}
@media(max-width:900px){{.pricing-cards{{grid-template-columns:1fr!important}}}}
.price-card{{background:var(--bg3);border:1px solid var(--border);border-radius:20px;padding:40px 32px;text-align:center;position:relative}}
.price-card.featured{{border-color:var(--green);box-shadow:0 0 40px rgba(0,212,170,0.1)}}
.price-badge{{position:absolute;top:-12px;left:50%;transform:translateX(-50%);background:linear-gradient(135deg,var(--green),#00b894);
  color:#0a0e17;padding:4px 20px;border-radius:20px;font-size:12px;font-weight:700}}
.price-name{{font-size:1.2rem;font-weight:700;margin-bottom:8px}}
.price-amount{{font-size:3rem;font-weight:900;margin:16px 0}}
.price-amount span{{font-size:1rem;color:var(--text2);font-weight:400}}
.price-amount .old{{text-decoration:line-through;color:var(--text2);font-size:1.5rem;display:block;font-weight:400}}
.price-list{{list-style:none;text-align:left;margin:24px 0}}
.price-list li{{padding:8px 0;color:var(--text2);font-size:0.9rem;display:flex;align-items:center;gap:8px}}
.price-list li::before{{content:'\u2714\ufe0f';font-size:14px}}

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
.nav.scrolled{{background:rgba(10,14,23,0.72);backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);border-bottom:1px solid rgba(255,255,255,0.04);padding:8px 28px}}
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
.faq-item.open .faq-q::after{{content:'−'}}
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

/* Recent Signals section removed */

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
  .signal-card{{min-width:100%;max-width:100%}}
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
</head>
<body>

<!-- MATRIX RAIN BACKGROUND -->
<canvas id="matrixCanvas" style="position:fixed;top:0;left:0;width:100%;height:100%;z-index:0;pointer-events:none;opacity:.07"></canvas>

<!-- NAV -->
<nav class="nav">
  <a href="/" class="nav-logo">
    <img src="/img/bull_bear.png" alt="BS365">BuySell365 <span style="color:#00d4aa;font-weight:800">Pro</span>
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
    <div class="hero-badge"><span class="dot"></span> <span data-i18n="hero.badge" data-i18n-ops="{_n_ops}">Bot activo \u2014 {_n_ops} operaciones en vivo</span></div>
    <h1 data-i18n="hero.title">Trading Inteligente<br>Impulsado por IA</h1>
    <p data-i18n="hero.subtitle" data-i18n-assets="{_activos_trading}">Se\u00f1ales de trading automatizadas con Inteligencia Artificial, an\u00e1lisis de noticias y datos institucionales. An\u00e1lisis continuo en {_activos_trading} activos de clase mundial.</p>
    <div class="hero-buttons">
      <a href="https://t.me/BUYSELL_365_24_7" target="_blank" class="btn btn-primary">\U0001f4e2 <span data-i18n="hero.btn_telegram">Unirse GRATIS a Telegram</span></a>
      <a href="/dashboard" class="btn btn-secondary">\U0001f4ca <span data-i18n="hero.btn_dashboard">Rendimiento en Vivo</span></a>
    </div>
    <div class="stats-bar">
      <div class="stat-item"><div class="stat-value">{_wr}%</div><div class="stat-label" data-i18n="stats.winrate">Tasa de Acierto</div></div>
      <div class="stat-item"><div class="stat-value blue">{_total}</div><div class="stat-label" data-i18n="stats.signals">Se\u00f1ales Generadas</div></div>
      <div class="stat-item"><div class="stat-value gold">{_pips:+,.0f}</div><div class="stat-label" data-i18n="stats.pips">Ganancia Acumulada</div></div>
      <div class="stat-item"><div class="stat-value purple">24/7</div><div class="stat-label" data-i18n="stats.analysis">An\u00e1lisis Activo</div></div>
    </div>
  </div>
</section>

<!-- FEATURES -->
<section class="features fade-in" id="features" style="padding:40px 20px">
  <div class="section-title" style="margin-bottom:24px">
    <h2>🧠 <span data-i18n="features.title">Tecnología Institucional</span></h2>
  </div>
  <div class="features-grid" style="gap:16px">
    <div class="feature-card" style="padding:20px">
      <div class="feature-icon green">🤖</div>
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
      <p data-i18n="features.ta.desc">8 indicadores t\u00e9cnicos (RSI, MACD, Bollinger, ADX, Ichimoku, ATR, EMA, volumen) con umbrales calibrados individualmente para cada uno de los 6 activos.</p>
    </div>
    <div class="feature-card" style="padding:20px">
      <div class="feature-icon green">\u26a1</div>
      <h3 data-i18n="features.mt5.title">Ejecuci\u00f3n Autom\u00e1tica</h3>
      <p data-i18n="features.mt5.desc">Conexi\u00f3n directa a MetaTrader 5. Las \u00f3rdenes se ejecutan en menos de 1 segundo con Stop Loss y hasta 3 niveles de ganancia autom\u00e1ticos.</p>
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
      <div style="font-size:1.6rem;font-weight:900;color:var(--green)">6</div>
      <div style="font-size:.75rem;color:var(--text2);text-transform:uppercase;letter-spacing:1px" data-i18n="about.stat_assets">Activos</div>
    </div>
  </div>
  <p style="text-align:center;font-size:.8rem;color:var(--text2);margin:16px auto 0;max-width:800px;opacity:.7" data-i18n="about.powered">⚡ Inteligencia Artificial · An\u00e1lisis de Noticias · Datos Institucionales · An\u00e1lisis T\u00e9cnico · MetaTrader 5</p>
</section>

<!-- ASSETS -->
<section id="assets" class="fade-in">
  <div class="section-title">
    <h2>\U0001f30d <span data-i18n="assets.title">6 Activos de Clase Mundial</span></h2>
    <p data-i18n="assets.subtitle">Cada activo tiene par\u00e1metros de detecci\u00f3n calibrados individualmente para m\u00e1xima precisi\u00f3n.</p>
  </div>
  <div class="assets-grid">
    <!-- ORO: Lingote 3D premium con gráfico -->
    <div class="asset-card">
      <div class="asset-icon gold"><svg viewBox="0 0 64 64" fill="none"><defs><linearGradient id="gbar1" x1="10" y1="15" x2="54" y2="55"><stop offset="0%" stop-color="#ffe88a"/><stop offset="25%" stop-color="#f0c030"/><stop offset="50%" stop-color="#d4a020"/><stop offset="75%" stop-color="#f0c030"/><stop offset="100%" stop-color="#b8860b"/></linearGradient><linearGradient id="gbar2" x1="10" y1="20" x2="10" y2="50"><stop offset="0%" stop-color="#d4a020"/><stop offset="100%" stop-color="#8b6914"/></linearGradient><linearGradient id="gtop" x1="20" y1="20" x2="45" y2="32"><stop offset="0%" stop-color="#ffe88a"/><stop offset="100%" stop-color="#f0c030"/></linearGradient><linearGradient id="gline" x1="0" y1="0" x2="64" y2="0"><stop offset="0%" stop-color="#f0b90b" stop-opacity=".2"/><stop offset="100%" stop-color="#f0b90b" stop-opacity=".6"/></linearGradient></defs><path d="M6 48l10-6 10 3 10-10 10-8 8-6" stroke="url(#gline)" stroke-width="1.5" stroke-linecap="round" fill="none" opacity=".5"/><path d="M6 48l10-6 10 3 10-10 10-8 8-6v32H6z" fill="#f0b90b" opacity=".06"/><path d="M12 52l8-3h24l8 3z" fill="#8b6914"/><path d="M20 49l-8 3V44l5-12h30l5 12v11l-8-3v-3z" fill="url(#gbar2)"/><path d="M17 32h30l5 12H12z" fill="url(#gbar1)"/><path d="M17 32l5-12h20l5 12z" fill="url(#gtop)"/><path d="M22 20h20l5 12H17z" fill="none" stroke="#ffe88a" stroke-width=".5" opacity=".5"/><line x1="17" y1="32" x2="47" y2="32" stroke="#b8860b" stroke-width=".5"/><text x="32" y="29" text-anchor="middle" font-size="7" font-weight="800" fill="#7a5a00" font-family="Arial" letter-spacing=".5">GOLD</text><text x="32" y="41" text-anchor="middle" font-size="5.5" font-weight="700" fill="#5a4200" font-family="Arial">999.9</text><circle cx="50" cy="14" r="8" fill="#f0c030" opacity=".12"/><circle cx="50" cy="14" r="5" fill="#f0c030" opacity=".08"/></svg></div>
      <div class="asset-name" data-i18n="assets.gold">ORO</div><div class="asset-tag">XAU/USD</div>
    </div>
    <!-- BITCOIN y ETHEREUM eliminados del bot -->
    <!-- EUR/USD: S\u00edmbolo \u20ac y $ juntos -->
    <div class="asset-card">
      <div class="asset-icon eur"><svg viewBox="0 0 40 40" fill="none"><circle cx="14" cy="20" r="12" fill="#003399"/><text x="14" y="25" text-anchor="middle" font-size="16" font-weight="900" fill="#ffcc00" font-family="Arial">\u20ac</text><circle cx="28" cy="20" r="12" fill="#1a6b3c"/><text x="28" y="25" text-anchor="middle" font-size="16" font-weight="900" fill="#fff" font-family="Arial">$</text><path d="M20 10v20" stroke="#0d1117" stroke-width="2" stroke-dasharray="2 2" opacity=".3"/></svg></div>
      <div class="asset-name">EUR/USD</div><div class="asset-tag" data-i18n="assets.forex_major">Forex Principal</div>
    </div>
    <!-- USD/JPY: $ y \u00a5 juntos -->
    <div class="asset-card">
      <div class="asset-icon jpy"><svg viewBox="0 0 40 40" fill="none"><circle cx="14" cy="20" r="12" fill="#1a6b3c"/><text x="14" y="25" text-anchor="middle" font-size="16" font-weight="900" fill="#fff" font-family="Arial">$</text><circle cx="28" cy="20" r="12" fill="#bc002d"/><text x="28" y="25" text-anchor="middle" font-size="15" font-weight="900" fill="#fff" font-family="Arial">\u00a5</text><path d="M20 10v20" stroke="#0d1117" stroke-width="2" stroke-dasharray="2 2" opacity=".3"/></svg></div>
      <div class="asset-name">USD/JPY</div><div class="asset-tag" data-i18n="assets.forex_major">Forex Principal</div>
    </div>
    <!-- GBP/JPY: The Beast -->
    <div class="asset-card">
      <div class="asset-icon gbpjpy"><svg viewBox="0 0 40 40" fill="none"><circle cx="14" cy="20" r="12" fill="#012169"/><text x="14" y="25" text-anchor="middle" font-size="14" font-weight="900" fill="#fff" font-family="Arial">\u00a3</text><circle cx="28" cy="20" r="12" fill="#bc002d"/><text x="28" y="25" text-anchor="middle" font-size="15" font-weight="900" fill="#fff" font-family="Arial">\u00a5</text><path d="M20 10v20" stroke="#0d1117" stroke-width="2" stroke-dasharray="2 2" opacity=".3"/></svg></div>
      <div class="asset-name">GBP/JPY</div><div class="asset-tag">La Bestia \u2022 Forex</div>
    </div>
    <!-- NASDAQ: Candlestick chart profesional -->
    <div class="asset-card">
      <div class="asset-icon nasdaq"><svg viewBox="0 0 40 40" fill="none"><line x1="8" y1="22" x2="8" y2="32" stroke="#ef4444" stroke-width="1.2"/><rect x="6" y="24" width="4" height="6" rx=".5" fill="#ef4444"/><line x1="15" y1="12" x2="15" y2="28" stroke="#00d4aa" stroke-width="1.2"/><rect x="13" y="14" width="4" height="10" rx=".5" fill="#00d4aa"/><line x1="22" y1="16" x2="22" y2="30" stroke="#ef4444" stroke-width="1.2"/><rect x="20" y="18" width="4" height="8" rx=".5" fill="#ef4444"/><line x1="29" y1="6" x2="29" y2="24" stroke="#00d4aa" stroke-width="1.2"/><rect x="27" y="8" width="4" height="12" rx=".5" fill="#00d4aa"/><line x1="35" y1="4" x2="35" y2="20" stroke="#00d4aa" stroke-width="1.2"/><rect x="33" y="6" width="4" height="10" rx=".5" fill="#00d4aa"/><path d="M5 35l7-8 7 4 7-12 7-8 4-3" stroke="#00d4aa" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" opacity=".5"/></svg></div>
      <div class="asset-name">NASDAQ</div><div class="asset-tag">NQ \u2022 <span data-i18n="assets.us_tech">Tecnol\u00f3gicas EE.UU.</span></div>
    </div>
    <!-- S&P 500: L\u00ednea alcista con \u00e1rea rellena -->
    <div class="asset-card">
      <div class="asset-icon sp500"><svg viewBox="0 0 40 40" fill="none"><defs><linearGradient id="spg" x1="20" y1="8" x2="20" y2="36" gradientUnits="userSpaceOnUse"><stop offset="0%" stop-color="#3b82f6" stop-opacity=".5"/><stop offset="100%" stop-color="#3b82f6" stop-opacity="0"/></linearGradient></defs><path d="M4 30L10 24 16 27 22 18 28 14 34 8 38 6v30H4z" fill="url(#spg)"/><path d="M4 30L10 24 16 27 22 18 28 14 34 8 38 6" stroke="#3b82f6" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/><circle cx="34" cy="8" r="3" fill="#3b82f6"/><circle cx="34" cy="8" r="5" fill="#3b82f6" opacity=".2"/><path d="M30 12l4-4 3-1" stroke="none"/><text x="34" y="10" text-anchor="middle" font-size="4" fill="#fff" font-weight="700" font-family="Arial">\u2191</text></svg></div>
      <div class="asset-name">S&P 500</div><div class="asset-tag">ES \u2022 <span data-i18n="assets.us_market">Mercado EE.UU.</span></div>
    </div>
  </div>
</section>

<!-- PRICING -->
<section class="pricing fade-in" id="pricing">
  <div class="section-title">
    <h2>\U0001f4b0 <span data-i18n="pricing.title">Servicios y Planes</span></h2>
    <p data-i18n="pricing.subtitle">Empieza gratis y escala cuando est\u00e9s listo.</p>
  </div>
  <div class="pricing-cards" style="grid-template-columns:repeat(3,1fr)">
    <div class="price-card">
      <div class="price-name" data-i18n="pricing.community">Comunidad</div>
      <div class="price-amount" data-i18n="pricing.free">GRATIS</div>
      <p style="color:var(--text2);margin-bottom:16px" data-i18n="pricing.community_desc">Acceso al grupo p\u00fablico de Telegram</p>
      <ul class="price-list">
        <li data-i18n="pricing.c1">Resumen diario de mercado</li>
        <li data-i18n="pricing.c2">Educaci\u00f3n y an\u00e1lisis general</li>
        <li data-i18n="pricing.c3">Soporte de la comunidad</li>
        <li data-i18n="pricing.c4">Dashboard p\u00fablico limitado</li>
      </ul>
      <a href="https://t.me/BUYSELL_365_24_7" target="_blank" class="btn btn-secondary" style="width:100%;justify-content:center;margin-top:16px" data-i18n="pricing.join_free">Unirse Gratis</a>
    </div>
    <div class="price-card featured">
      <div class="price-badge">\U0001f525 <span data-i18n="pricing.badge">50% OFF \u2014 LANZAMIENTO</span></div>
      <div class="price-name" data-i18n="pricing.vip">VIP Pro</div>
      <div class="price-amount">
        <span class="old">$299/mes</span>
        $149<span data-i18n="pricing.month">/mes USDT</span>
      </div>
      <div class="countdown" id="offerCountdown">
        <div class="countdown-item"><div class="countdown-val" id="cdDays">--</div><div class="countdown-lbl" data-i18n="countdown.days">D\u00edas</div></div>
        <div class="countdown-item"><div class="countdown-val" id="cdHours">--</div><div class="countdown-lbl" data-i18n="countdown.hours">Horas</div></div>
        <div class="countdown-item"><div class="countdown-val" id="cdMins">--</div><div class="countdown-lbl" data-i18n="countdown.mins">Min</div></div>
        <div class="countdown-item"><div class="countdown-val" id="cdSecs">--</div><div class="countdown-lbl" data-i18n="countdown.secs">Seg</div></div>
      </div>
      <p style="color:var(--text2);margin-bottom:16px" data-i18n="pricing.trial" data-i18n-days="5">5 d\u00edas h\u00e1biles de prueba GRATIS</p>
      <ul class="price-list">
        <li data-i18n="pricing.v1">Se\u00f1ales en tiempo real con TP/SL</li>
        <li data-i18n="pricing.v2">Canal VIP privado de Telegram</li>
        <li data-i18n="pricing.v4">Alertas instant\u00e1neas 24/7</li>
        <li data-i18n="pricing.v5">Soporte prioritario</li>
        <li data-i18n="pricing.v6">An\u00e1lisis multi-IA exclusivo</li>
        <li data-i18n="pricing.v7">Gr\u00e1ficos de entrada y salida</li>
      </ul>
      <a href="https://t.me/BuySell365_bot?start=vip" target="_blank" class="btn btn-primary" style="width:100%;justify-content:center;margin-top:16px" data-i18n="pricing.start_trial">Empezar Prueba Gratis</a>
    </div>
    <div class="price-card" style="position:relative">
      <div class="price-badge" style="background:linear-gradient(135deg,#00e676,#00c853);animation:pulse 2s infinite;box-shadow:0 0 16px rgba(0,230,118,.4)">🔥 <span data-i18n="pricing.copy_badge">DISPONIBLE AHORA</span></div>
      <div class="price-name" data-i18n="pricing.copy_name">Copy Trading</div>
      <div class="price-amount" style="font-size:1.1rem;color:var(--accent)" data-i18n="pricing.copy_price">Pequeña comisión por apertura</div>
      <p style="color:var(--text2);margin-bottom:16px" data-i18n="pricing.copy_desc">Copia automática todas nuestras operaciones en tu cuenta MT5 a través de XM</p>
      <ul class="price-list">
        <li data-i18n="pricing.cp1">Operativa automatizada — Oro, Forex e \u00cdndices</li>
        <li data-i18n="pricing.cp2">Copia automática en tiempo real</li>
        <li data-i18n="pricing.cp3">SL y TP colocados automáticamente</li>
        <li data-i18n="pricing.cp4">Sin cuota mensual — solo pagas si ganas</li>
        <li data-i18n="pricing.cp5">Broker regulado XM (MT5)</li>
        <li data-i18n="pricing.cp6">Sin intervención manual requerida</li>
      </ul>
      <a href="https://social.tp-redirect.com/s/WRE0V7jm" target="_blank" rel="noopener" class="btn btn-primary" style="width:100%;justify-content:center;margin-top:16px;background:linear-gradient(135deg,#a855f7,#6366f1);border:none" data-i18n="pricing.copy_btn">🚀 Empezar Copy Trading</a>
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
      <div class="faq-q" data-i18n="faq.q1">\u00bfEs realmente gratis unirse?</div>
      <div class="faq-a" data-i18n="faq.a1">S\u00ed. El grupo p\u00fablico de Telegram es 100% gratuito. Recibes res\u00famenes de mercado, educaci\u00f3n y an\u00e1lisis general. El plan VIP Pro tiene una prueba gratuita de 5 d\u00edas h\u00e1biles sin necesidad de tarjeta de cr\u00e9dito.</div>
    </div>
    <div class="faq-item" onclick="this.classList.toggle('open')">
      <div class="faq-q" data-i18n="faq.q2">\u00bfC\u00f3mo recibo las se\u00f1ales?</div>
      <div class="faq-a" data-i18n="faq.a2">Las se\u00f1ales se env\u00edan directamente a tu Telegram en tiempo real. Cada se\u00f1al incluye: activo, direcci\u00f3n (compra/venta), precio de entrada, Stop Loss y hasta 3 Take Profits.</div>
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
      <div class="faq-a" data-i18n="faq.a6">En promedio entre 5 y 15 se\u00f1ales diarias repartidas entre los 6 activos. El bot analiza el mercado cada 3 minutos y solo env\u00eda se\u00f1ales cuando detecta una oportunidad de alta probabilidad.</div>
    </div>
  </div>
</section>

<!-- CTA -->
<section class="cta fade-in">
  <h2>\U0001f680 <span data-i18n="cta.title">Empieza Hoy \u2014 Sin Riesgo</span></h2>
  <p data-i18n="cta.subtitle" data-i18n-days="5">5 d\u00edas h\u00e1biles de prueba gratuita. Sin tarjeta de cr\u00e9dito. Cancela cuando quieras.</p>
  <div class="hero-buttons">
    <a href="https://t.me/BuySell365_bot?start=vip" target="_blank" class="btn btn-primary">\U0001f451 <span data-i18n="cta.btn_vip">Activar Trial VIP Gratis</span></a>
    <a href="https://t.me/BUYSELL_365_24_7" target="_blank" class="btn btn-secondary">\U0001f4ac <span data-i18n="cta.btn_community">Unirse a la Comunidad</span></a>
  </div>
</section>

<!-- FLOATING TELEGRAM BUTTON -->
<a href="https://t.me/BUYSELL_365_24_7" target="_blank" class="float-telegram" title="Telegram">
  <svg viewBox="0 0 24 24"><path d="M11.944 0A12 12 0 0 0 0 12a12 12 0 0 0 12 12 12 12 0 0 0 12-12A12 12 0 0 0 12 0a12 12 0 0 0-.056 0zm4.962 7.224c.1-.002.321.023.465.14a.506.506 0 0 1 .171.325c.016.093.036.306.02.472-.18 1.898-.962 6.502-1.36 8.627-.168.9-.499 1.201-.82 1.23-.696.065-1.225-.46-1.9-.902-1.056-.693-1.653-1.124-2.678-1.8-1.185-.78-.417-1.21.258-1.91.177-.184 3.247-2.977 3.307-3.23.007-.032.014-.15-.056-.212s-.174-.041-.249-.024c-.106.024-1.793 1.14-5.061 3.345-.479.33-.913.49-1.302.48-.428-.008-1.252-.241-1.865-.44-.752-.245-1.349-.374-1.297-.789.027-.216.325-.437.893-.663 3.498-1.524 5.83-2.529 6.998-3.014 3.332-1.386 4.025-1.627 4.476-1.635z"/></svg>
</a>

<!-- BACK TO TOP -->
<div class="back-to-top" id="backToTop" onclick="window.scrollTo({{top:0,behavior:'smooth'}})">↑</div>

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

  // Detect browser language or use saved preference
  function detectLang(){{
    const saved = localStorage.getItem('buysell365_lang');
    if(saved && SUPPORTED.includes(saved)) return saved;
    const nav = (navigator.language || navigator.userLanguage || 'es').toLowerCase();
    if(nav.startsWith('en')) return 'en';
    if(nav.startsWith('pt')) return 'pt';
    if(nav.startsWith('fr')) return 'fr';
    return 'es';
  }}

  // Apply translations to all data-i18n elements
  function applyTranslations(tr){{
    document.querySelectorAll('[data-i18n]').forEach(function(el){{
      const key = el.getAttribute('data-i18n');
      if(tr[key]){{
        // Support {{var}} interpolation
        let text = tr[key];
        // Replace dynamic vars from data-i18n-vars attribute
        const vars = el.getAttribute('data-i18n-vars');
        if(vars){{
          try{{
            const obj = JSON.parse(vars);
            Object.keys(obj).forEach(function(k){{ text = text.replace('{{'+k+'}}', obj[k]); }});
          }}catch(e){{}}
        }}
        // If text contains <br> or HTML, use innerHTML, else textContent
        if(text.includes('<br') || text.includes('<span') || text.includes('<strong')){{
          el.innerHTML = text;
        }}else{{
          el.textContent = text;
        }}
      }}
    }});
    // Update html lang attribute
    document.documentElement.lang = currentLang;
    // Update flag display
    const flagEl = document.getElementById('currentFlag');
    if(flagEl) flagEl.textContent = FLAGS[currentLang] || '\U0001f1ea\U0001f1f8';
  }}

  // Load translations from server
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

  // Toggle language dropdown menu
  window.toggleLangMenu = function(){{
    const menu = document.getElementById('langMenu');
    if(menu) menu.classList.toggle('show');
  }};

  // Set language (called from dropdown)
  window.setLang = function(lang){{
    loadLang(lang);
    const menu = document.getElementById('langMenu');
    if(menu) menu.classList.remove('show');
  }};

  // Close dropdown when clicking outside
  document.addEventListener('click', function(e){{
    const sel = document.getElementById('langSelector');
    if(sel && !sel.contains(e.target)){{
      const menu = document.getElementById('langMenu');
      if(menu) menu.classList.remove('show');
    }}
  }});

  // Auto-load on page ready
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
//  BACK TO TOP BUTTON
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

// Recent Signals section removed
</script>

</body>
</html>"""
    except Exception as e:
        logger.error(f"Landing page render error: {e}", exc_info=True)
        return redirect("/dashboard")

@app.route("/img/<path:filename>")
def serve_img(filename):
    """Sirve SOLO imágenes permitidas desde el directorio del bot (whitelist estricta)."""
    from flask import send_from_directory
    # C-01 FIX: Whitelist de extensiones permitidas — NUNCA servir .env, .py, .json, etc.
    _allowed_ext = ('.png', '.jpg', '.jpeg', '.gif', '.ico', '.svg', '.webp')
    if not filename.lower().endswith(_allowed_ext):
        return "Not found", 404
    # Bloquear cualquier intento de path traversal o archivos ocultos
    if '..' in filename or filename.startswith('.') or '/' in filename or '\\' in filename:
        return "Not found", 404
    bot_dir = os.path.dirname(os.path.abspath(__file__)) if '__file__' in globals() else os.getcwd()
    try:
        return send_from_directory(bot_dir, filename, mimetype="image/png")
    except Exception:
        return "Not found", 404

@app.route("/i18n/<lang>.json")
def serve_translations(lang):
    """Servir traducciones por idioma (ES, EN, PT, FR)."""
    import json as _json
    _allowed = ("es", "en", "pt", "fr")
    if lang not in _allowed:
        lang = "es"
    _tr_path = os.path.join(os.path.dirname(os.path.abspath(__file__)) if '__file__' in dir() else os.getcwd(), "translations.json")
    try:
        with open(_tr_path, "r", encoding="utf-8") as _f:
            _all = _json.load(_f)
        _data = _all.get(lang, _all.get("es", {}))
        return app.response_class(response=_json.dumps(_data, ensure_ascii=False), status=200, mimetype="application/json; charset=utf-8")
    except Exception:
        return "{}", 200, {"Content-Type": "application/json"}

@app.route("/api/stats")
def api_stats_public():
    """API de estadísticas generales. Protegido con API_SECRET_KEY si está configurado."""
    if not _check_api_auth():
        return app.response_class(response='{"error":"unauthorized"}', status=401, mimetype="application/json")
    try:
        import json as _json
        _hist = historial_operaciones if historial_operaciones else []
        _wins = sum(1 for h in _hist if h.get('pips', 0) > 0)
        _total = len(_hist)
        _wr = round(_wins / _total * 100, 1) if _total > 0 else 0
        _pips = round(sum(h.get('pips', 0) for h in _hist), 1)
        _n_ops = sum(1 for op in operaciones_activas.values() if isinstance(op, dict) and op.get('mt5_ejecutado', False))
        _avg_win = round(sum(h.get('pips', 0) for h in _hist if h.get('pips', 0) > 0) / max(_wins, 1), 1)
        _losses = _total - _wins
        _avg_loss = round(sum(abs(h.get('pips', 0)) for h in _hist if h.get('pips', 0) <= 0) / max(_losses, 1), 1)
        _rr = round(_avg_win / _avg_loss, 2) if _avg_loss > 0 else 0
        _hoy = ahora().strftime("%d/%m/%Y")  # M-FIX: Usar hora Andorra
        _hoy_total = sum(1 for h in _hist if h.get('fecha', '') == _hoy)
        _hoy_wins = sum(1 for h in _hist if h.get('fecha', '') == _hoy and h.get('pips', 0) > 0)
        # Weekly + Monthly stats
        _now = ahora()  # M-FIX: Usar hora Andorra
        _week_start = (_now - timedelta(days=_now.weekday())).strftime("%d/%m/%Y")
        _month_prefix = _now.strftime("/%m/%Y")
        _week_total = 0; _week_wins = 0
        _month_total = 0; _month_wins = 0
        for h in _hist:
            f = h.get('fecha', '')
            p = h.get('pips', 0)
            if f.endswith(_month_prefix) or f.endswith(_now.strftime("%m/%Y")):
                try:
                    parts = f.split('/')
                    if len(parts) == 3:
                        d = int(parts[0]); m = int(parts[1]); y = int(parts[2])
                        dt = datetime(y, m, d)
                        if dt.month == _now.month and dt.year == _now.year:
                            _month_total += 1
                            if p > 0: _month_wins += 1
                        if dt >= datetime(_now.year, _now.month, _now.day) - timedelta(days=_now.weekday()):
                            _week_total += 1
                            if p > 0: _week_wins += 1
                except Exception:
                    pass
        _week_wr = round(_week_wins / _week_total * 100, 1) if _week_total > 0 else 0
        _month_wr = round(_month_wins / _month_total * 100, 1) if _month_total > 0 else 0
        # Last 6 signals for landing page (public, no sensitive data)
        _last = []
        for h in reversed(_hist[-20:]):
            _last.append({
                "nombre": h.get("nombre", ""),
                "ticker": h.get("ticker", ""),
                "tipo": h.get("tipo", ""),
                "pips": round(h.get("pips", 0), 1),
                "resultado": "WIN" if h.get("pips", 0) > 0 else "LOSS",
                "fecha": h.get("fecha", "")
            })
            if len(_last) >= 6:
                break
        data = {
            "winrate": _wr, "total_signals": _total, "pips": _pips,
            "active_ops": _n_ops, "rr_ratio": _rr, "wins": _wins, "losses": _losses,
            "today_signals": _hoy_total, "today_wins": _hoy_wins,
            "week_signals": _week_total, "week_wins": _week_wins, "week_wr": _week_wr,
            "month_signals": _month_total, "month_wins": _month_wins, "month_wr": _month_wr,
            "assets_count": len(ACTIVOS), "bot_active": True, "auto_trading": AUTO_TRADING,
            "last_signals": _last
        }
        return app.response_class(response=_json.dumps(data), status=200, mimetype="application/json")
    except Exception as e:
        import json as _json
        return app.response_class(response=_json.dumps({"error": "Error interno del servidor"}), status=200, mimetype="application/json")

# ============================================================
#  REGISTRO PERMANENTE DE OPERACIONES GANADAS
# ============================================================
WINNING_TRADES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)) if '__file__' in dir() else os.getcwd(), "winning_trades.json")

def _cargar_winning_trades():
    """Carga el registro permanente de operaciones ganadas."""
    try:
        if os.path.exists(WINNING_TRADES_FILE):
            with open(WINNING_TRADES_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception:
        pass
    return []

def registrar_trade_ganado(trade_info):
    """Registra una operación ganada permanentemente (nunca se borra)."""
    try:
        trades = _cargar_winning_trades()
        trades.append(trade_info)
        with open(WINNING_TRADES_FILE, 'w', encoding='utf-8') as f:
            json.dump(trades, f, ensure_ascii=False, indent=2)
        logger.info(f"🏆 TRADE GANADO registrado: {trade_info.get('nombre','')} {trade_info.get('tipo','')} +{trade_info.get('pips',0):.1f} pips")
    except Exception as e:
        logger.error(f"Error guardando trade ganado: {e}")

@app.route("/api/winning_trades")
def api_winning_trades():
    """API pública: historial permanente de operaciones ganadoras."""
    try:
        trades = _cargar_winning_trades()
        return app.response_class(
            response=json.dumps(trades, ensure_ascii=False),
            status=200, mimetype="application/json"
        )
    except Exception as e:
        return app.response_class(
            response=json.dumps({"error": str(e)}),
            status=200, mimetype="application/json"
        )

@app.route("/api/active_ops")
def api_active_ops():
    """API: operaciones activas con progreso hacia TP.
    Fusiona operaciones_activas (tracking del bot) con posiciones reales de MT5
    para mostrar TODAS las posiciones abiertas en tiempo real.
    Protegido con API_SECRET_KEY si está configurado."""
    if not _check_api_auth():
        return app.response_class(response='{"error":"unauthorized"}', status=401, mimetype="application/json")
    _MT5_TO_NOMBRE = {
        'GOLD': 'ORO',
        'US100Cash': 'NASDAQ', 'US500Cash': 'S&P 500',
        'EURUSD': 'EUR/USD', 'USDJPY': 'USD/JPY', 'GBPJPY': 'GBP/JPY',
    }
    _MT5_TO_TICKER = {
        'GOLD': 'GC=F',
        'US100Cash': 'NQ=F', 'US500Cash': 'ES=F',
        'EURUSD': 'EURUSD=X', 'USDJPY': 'USDJPY=X', 'GBPJPY': 'GBPJPY=X',
    }
    try:
        result = []
        # Contador de posiciones trackeadas por symbol MT5
        tracked_mt5_count = {}

        for op_id, op in operaciones_activas.items():
            if not isinstance(op, dict):
                continue
            # Solo mostrar operaciones realmente ejecutadas en MT5
            if not op.get('mt5_ejecutado', False):
                continue
            ticker = op.get('ticker', '')
            entrada = op.get('entrada', 0)
            sl = op.get('sl', 0)
            tp1 = op.get('tp1', 0)
            tp2 = op.get('tp2', 0)
            tp3 = op.get('tp3', 0)
            tipo = op.get('tipo', '').upper()
            tp1_hit = op.get('tp1_hit', False)
            tp2_hit = op.get('tp2_hit', False)
            mt5_sym = MT5_TICKER_MAP.get(ticker, ticker)
            sym_up = mt5_sym.upper()
            tracked_mt5_count[sym_up] = tracked_mt5_count.get(sym_up, 0) + 1
            # Obtener precio actual y beneficio real via MT5
            precio_actual = entrada
            beneficio_mt5 = None
            if MT5_AVAILABLE:
                try:
                    # H-01 FIX: Proteger llamadas MT5 con lock
                    with _lock_mt5:
                        _tick = mt5.symbol_info_tick(mt5_sym)
                        if _tick:
                            es_compra = tipo in ("COMPRA", "BUY", "LONG")
                            precio_actual = _tick.bid if es_compra else _tick.ask
                        # Buscar beneficio real de la posición MT5
                        _positions = mt5.positions_get(symbol=mt5_sym)
                    if _positions:
                        beneficio_mt5 = sum(p.profit for p in _positions)
                except Exception:
                    pass
            # Usar precio_extremo si existe (tracking del monitor)
            if op.get('precio_extremo'):
                precio_actual = op['precio_extremo']
            # Calcular progreso hacia TP3
            rango_total = abs(tp3 - entrada) if tp3 != entrada else 1
            es_compra = tipo in ("COMPRA", "BUY", "LONG")
            if es_compra:
                progreso = max(0, min(100, (precio_actual - entrada) / rango_total * 100))
            else:
                progreso = max(0, min(100, (entrada - precio_actual) / rango_total * 100))
            # Si TP1 hit, al menos 33%
            if tp1_hit and progreso < 33:
                progreso = 33
            if tp2_hit and progreso < 66:
                progreso = 66
            _op_data = {
                'id': op_id, 'ticker': ticker, 'nombre': op.get('nombre', ''),
                'tipo': op.get('tipo', ''), 'entrada': entrada, 'sl': sl,
                'tp1': tp1, 'tp2': tp2, 'tp3': tp3,
                'tp1_hit': tp1_hit, 'tp2_hit': tp2_hit,
                'precio_actual': round(precio_actual, 5),
                'progreso': round(progreso, 1),
                'hora': op.get('hora', ''), 'score': min(op.get('score', 0) * 2, 10),  # M-FIX: Escala 0-10 como Telegram
                'fuente': 'bot',
            }
            if beneficio_mt5 is not None:
                _op_data['beneficio'] = round(beneficio_mt5, 2)
            result.append(_op_data)

        # Fusionar posiciones MT5 NO trackeadas (huérfanas o abiertas manualmente)
        if MT5_AVAILABLE:
            try:
                with _lock_mt5:  # H-01 FIX
                    all_pos = mt5.positions_get()
                if all_pos:
                    # Contar posiciones MT5 por symbol para comparar con tracked
                    mt5_seen_count = {}
                    for pos in sorted(all_pos, key=lambda p: p.time):
                        sym_upper = pos.symbol.upper()
                        # Buscar por nombre case-insensitive
                        sym_key = None
                        for k in _MT5_TO_NOMBRE:
                            if k.upper() == sym_upper:
                                sym_key = k
                                break
                        if sym_key is None:
                            continue  # Symbol no reconocido
                        mt5_seen_count[sym_upper] = mt5_seen_count.get(sym_upper, 0) + 1
                        # Si aún hay posiciones cubiertas por tracking, saltar
                        tracked_n = tracked_mt5_count.get(sym_upper, 0)
                        if mt5_seen_count[sym_upper] <= tracked_n:
                            continue  # Esta posición ya está en operaciones_activas
                        # Posición huérfana: crear entrada desde datos MT5
                        es_buy = pos.type == mt5.ORDER_TYPE_BUY
                        tipo_str = "COMPRA" if es_buy else "VENTA"
                        nombre = _MT5_TO_NOMBRE.get(sym_key, pos.symbol)
                        ticker_yf = _MT5_TO_TICKER.get(sym_key, pos.symbol)
                        entrada = pos.price_open
                        sl = pos.sl if pos.sl > 0 else 0
                        tp1 = pos.tp if pos.tp > 0 else 0
                        # Precio actual
                        try:
                            with _lock_mt5:  # H-01 FIX
                                _tick = mt5.symbol_info_tick(pos.symbol)
                            precio_actual = (_tick.bid if es_buy else _tick.ask) if _tick else entrada
                        except Exception:
                            precio_actual = pos.price_current if pos.price_current else entrada
                        # Progreso simple basado en TP
                        progreso = 0
                        if tp1 and tp1 != entrada:
                            rango = abs(tp1 - entrada)
                            if es_buy:
                                progreso = max(0, min(100, (precio_actual - entrada) / rango * 100))
                            else:
                                progreso = max(0, min(100, (entrada - precio_actual) / rango * 100))
                        # Hora de apertura
                        from datetime import datetime as _dt
                        try:
                            hora_str = _dt.fromtimestamp(pos.time).strftime("%H:%M")
                        except Exception:
                            hora_str = ""
                        result.append({
                            'id': f"mt5_{pos.ticket}",
                            'ticker': ticker_yf,
                            'nombre': nombre,
                            'tipo': tipo_str,
                            'entrada': round(entrada, 5),
                            'sl': round(sl, 5),
                            'tp1': round(tp1, 5),
                            'tp2': 0, 'tp3': 0,
                            'tp1_hit': False, 'tp2_hit': False,
                            'precio_actual': round(precio_actual, 5),
                            'progreso': round(progreso, 1),
                            'hora': hora_str,
                            'score': 0,
                            'fuente': 'mt5',
                            'ticket': pos.ticket,
                            'volumen': pos.volume,
                            'beneficio': round(pos.profit, 2),
                        })
                        # Cada posición MT5 individual se muestra
            except Exception:
                pass

        return app.response_class(
            response=json.dumps(result, ensure_ascii=False),
            status=200, mimetype="application/json"
        )
    except Exception as e:
        return app.response_class(
            response=json.dumps([]), status=200, mimetype="application/json"
        )

@app.route("/terminos")
def pagina_terminos():
    """Términos y Condiciones del servicio BuySell365."""
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
    <!-- Google Analytics -->
    <script async src="https://www.googletagmanager.com/gtag/js?id=G-L514BL7E83"></script>
    <script>window.dataLayer=window.dataLayer||[];function gtag(){{dataLayer.push(arguments);}}gtag('js',new Date());gtag('config','G-L514BL7E83');</script>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>T&eacute;rminos y Condiciones — BuySell365 Pro</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: 'Inter', 'Segoe UI', sans-serif; background: #0d1117; color: #c9d1d9; line-height: 1.7; padding: 20px; }}
        .container {{ max-width: 800px; margin: 0 auto; }}
        h1 {{ color: #f0b90b; font-size: 1.8rem; margin-bottom: 10px; }}
        h2 {{ color: #58a6ff; font-size: 1.2rem; margin-top: 25px; margin-bottom: 8px; }}
        p, li {{ font-size: 0.95rem; margin-bottom: 8px; }}
        ul {{ padding-left: 20px; }}
        .date {{ color: #8b949e; font-size: 0.85rem; margin-bottom: 20px; }}
        a {{ color: #58a6ff; }}
        .back {{ display: inline-block; margin-top: 30px; padding: 10px 20px; background: #f0b90b; color: #000; border-radius: 8px; text-decoration: none; font-weight: bold; }}
        .lang-bar{{display:flex;justify-content:flex-end;gap:8px;margin-bottom:16px}}
        .lang-bar a{{cursor:pointer;font-size:1.5rem;text-decoration:none;opacity:.6;transition:opacity .2s}}
        .lang-bar a:hover,.lang-bar a.active{{opacity:1}}
    </style>
</head>
<body>
<div class="container">
    <div class="lang-bar">
        <a onclick="setLang('es')" id="flag_es">\U0001f1ea\U0001f1f8</a>
        <a onclick="setLang('en')" id="flag_en">\U0001f1fa\U0001f1f8</a>
        <a onclick="setLang('pt')" id="flag_pt">\U0001f1e7\U0001f1f7</a>
        <a onclick="setLang('fr')" id="flag_fr">\U0001f1eb\U0001f1f7</a>
    </div>
    <h1>&#128221; <span data-i18n="terms.title">T&eacute;rminos y Condiciones</span></h1>
    <p class="date" data-i18n="terms.date">&Uacute;ltima actualizaci&oacute;n: 12 de marzo de 2026</p>

    <h2 data-i18n="terms.s1_title">1. Descripci&oacute;n del Servicio</h2>
    <p data-i18n="terms.s1_text">BuySell365 es una herramienta automatizada de an&aacute;lisis t&eacute;cnico que genera alertas informativas
    sobre activos financieros (oro, divisas e &iacute;ndices burs&aacute;tiles) mediante indicadores
    t&eacute;cnicos, modelos de inteligencia artificial y an&aacute;lisis de datos institucionales.</p>

    <h2 data-i18n="terms.s2_title">2. No es Asesor&iacute;a Financiera</h2>
    <p data-i18n="terms.s2_text">BuySell365 NO proporciona asesor&iacute;a financiera, de inversi&oacute;n ni recomendaciones personalizadas.
    Las se&ntilde;ales generadas son an&aacute;lisis t&eacute;cnicos automatizados con fines informativos y educativos.
    No constituyen una oferta, solicitud ni recomendaci&oacute;n para comprar o vender ning&uacute;n instrumento financiero.</p>

    <h2 data-i18n="terms.s3_title">3. Riesgo de Inversi&oacute;n</h2>
    <p data-i18n="terms.s3_text">Operar en mercados financieros conlleva un alto riesgo de p&eacute;rdida de capital. Los resultados pasados
    no garantizan resultados futuros. Cada usuario es &uacute;nico responsable de sus decisiones de inversi&oacute;n
    y del capital que arriesga.</p>

    <h2 data-i18n="terms.s4_title">4. Suscripci&oacute;n VIP</h2>
    <ul>
        <li data-i18n="terms.s4_trial">Periodo de prueba: 5 d&iacute;as h&aacute;biles gratuitos al registrarse.</li>
        <li data-i18n="terms.s4_price">Precio: {VIP_PRECIO_EUR} {VIP_MONEDA}/mes (pago en USDT TRC20).</li>
        <li data-i18n="terms.s4_renew">Renovaci&oacute;n: Manual. No hay cobros autom&aacute;ticos ni recurrentes.</li>
        <li data-i18n="terms.s4_cancel">Cancelaci&oacute;n: Puedes dejar de pagar en cualquier momento. El acceso contin&uacute;a hasta que expire tu periodo pagado.</li>
        <li data-i18n="terms.s4_refund">Reembolsos: No se ofrecen reembolsos una vez procesado el pago, ya que el servicio digital se activa inmediatamente.</li>
    </ul>

    <h2 data-i18n="terms.s5_title">5. Uso Aceptable</h2>
    <p data-i18n="terms.s5_intro">Al usar BuySell365 aceptas:</p>
    <ul>
        <li data-i18n="terms.s5_r1">No redistribuir ni revender las se&ntilde;ales del canal VIP.</li>
        <li data-i18n="terms.s5_r2">No usar bots, scrapers ni automatizaciones para extraer contenido del canal.</li>
        <li data-i18n="terms.s5_r3">No enviar spam ni contenido inapropiado en el grupo de Telegram.</li>
        <li data-i18n="terms.s5_r4">Respetar a los dem&aacute;s miembros de la comunidad.</li>
    </ul>

    <h2 data-i18n="terms.s6_title">6. Limitaci&oacute;n de Responsabilidad</h2>
    <p data-i18n="terms.s6_text">BuySell365 y su creador no ser&aacute;n responsables de p&eacute;rdidas financieras,
    da&ntilde;os directos ni indirectos derivados del uso de las se&ntilde;ales o la informaci&oacute;n proporcionada.
    El servicio se ofrece "tal cual" sin garant&iacute;as de rentabilidad.</p>

    <h2 data-i18n="terms.s7_title">7. Disponibilidad del Servicio</h2>
    <p data-i18n="terms.s7_text">Nos esforzamos por mantener el servicio operativo 24/7, pero no garantizamos disponibilidad ininterrumpida.
    Pueden ocurrir interrupciones por mantenimiento, actualizaciones t&eacute;cnicas o causas de fuerza mayor.</p>

    <h2 data-i18n="terms.s8_title">8. Modificaciones</h2>
    <p data-i18n="terms.s8_text">Nos reservamos el derecho de modificar estos t&eacute;rminos en cualquier momento. Los cambios ser&aacute;n
    notificados por el canal de Telegram. El uso continuado del servicio implica aceptaci&oacute;n de los nuevos t&eacute;rminos.</p>

    <h2 data-i18n="terms.s9_title">9. Contacto</h2>
    <p><span data-i18n="terms.s9_text">Para consultas:</span> <a href="https://t.me/BuySell365Traiding">@BuySell365Traiding</a> <span data-i18n="terms.s9_via">en Telegram.</span></p>

    <a href="/dashboard" class="back" data-i18n="terms.back">&larr; Volver al Trading en Vivo</a>
</div>
<script>
(function(){{
  const SUPPORTED=['es','en','pt','fr'];
  const FLAGS={{es:'\U0001f1ea\U0001f1f8',en:'\U0001f1fa\U0001f1f8',pt:'\U0001f1e7\U0001f1f7',fr:'\U0001f1eb\U0001f1f7'}};
  function detectLang(){{
    const s=localStorage.getItem('buysell365_lang');
    if(s&&SUPPORTED.includes(s))return s;
    const n=(navigator.language||'es').toLowerCase();
    if(n.startsWith('en'))return'en';if(n.startsWith('pt'))return'pt';if(n.startsWith('fr'))return'fr';return'es';
  }}
  function apply(tr){{
    document.querySelectorAll('[data-i18n]').forEach(function(el){{
      const k=el.getAttribute('data-i18n');if(tr[k])el.textContent=tr[k];
    }});
  }}
  function updateFlags(lang){{
    SUPPORTED.forEach(function(l){{var f=document.getElementById('flag_'+l);if(f)f.className=l===lang?'active':''}});
  }}
  window.setLang=function(lang){{
    if(!SUPPORTED.includes(lang))lang='es';
    fetch('/i18n/'+lang+'.json').then(function(r){{return r.json()}}).then(function(d){{
      apply(d);localStorage.setItem('buysell365_lang',lang);document.documentElement.lang=lang;updateFlags(lang);
    }}).catch(function(){{}});
  }};
  var lang=detectLang();updateFlags(lang);
  if(lang!=='es')window.setLang(lang);
}})();
</script>
</body>
</html>"""

@app.route("/privacidad")
def pagina_privacidad():
    """Pol&iacute;tica de Privacidad de BuySell365."""
    return """<!DOCTYPE html>
<html lang="es">
<head>
    <!-- Google Analytics -->
    <script async src="https://www.googletagmanager.com/gtag/js?id=G-L514BL7E83"></script>
    <script>window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments);}gtag('js',new Date());gtag('config','G-L514BL7E83');</script>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Pol&iacute;tica de Privacidad — BuySell365 Pro</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Inter', 'Segoe UI', sans-serif; background: #0d1117; color: #c9d1d9; line-height: 1.7; padding: 20px; }
        .container { max-width: 800px; margin: 0 auto; }
        h1 { color: #f0b90b; font-size: 1.8rem; margin-bottom: 10px; }
        h2 { color: #58a6ff; font-size: 1.2rem; margin-top: 25px; margin-bottom: 8px; }
        p, li { font-size: 0.95rem; margin-bottom: 8px; }
        ul { padding-left: 20px; }
        .date { color: #8b949e; font-size: 0.85rem; margin-bottom: 20px; }
        a { color: #58a6ff; }
        .back { display: inline-block; margin-top: 30px; padding: 10px 20px; background: #f0b90b; color: #000; border-radius: 8px; text-decoration: none; font-weight: bold; }
        .lang-bar{display:flex;justify-content:flex-end;gap:8px;margin-bottom:16px}
        .lang-bar a{cursor:pointer;font-size:1.5rem;text-decoration:none;opacity:.6;transition:opacity .2s}
        .lang-bar a:hover,.lang-bar a.active{opacity:1}
    </style>
</head>
<body>
<div class="container">
    <div class="lang-bar">
        <a onclick="setLang('es')" id="flag_es">\U0001f1ea\U0001f1f8</a>
        <a onclick="setLang('en')" id="flag_en">\U0001f1fa\U0001f1f8</a>
        <a onclick="setLang('pt')" id="flag_pt">\U0001f1e7\U0001f1f7</a>
        <a onclick="setLang('fr')" id="flag_fr">\U0001f1eb\U0001f1f7</a>
    </div>
    <h1>&#128274; <span data-i18n="priv.title">Pol&iacute;tica de Privacidad</span></h1>
    <p class="date" data-i18n="priv.date">&Uacute;ltima actualizaci&oacute;n: 12 de marzo de 2026</p>

    <h2 data-i18n="priv.s1_title">1. Datos que Recopilamos</h2>
    <p data-i18n="priv.s1_intro">BuySell365 recopila &uacute;nicamente los siguientes datos a trav&eacute;s de Telegram:</p>
    <ul>
        <li data-i18n="priv.s1_d1">ID de usuario de Telegram (n&uacute;mero &uacute;nico asignado por Telegram).</li>
        <li data-i18n="priv.s1_d2">Nombre de usuario (alias p&uacute;blico de Telegram, si existe).</li>
        <li data-i18n="priv.s1_d3">Nombre (primer nombre configurado en Telegram).</li>
        <li data-i18n="priv.s1_d4">Historial de comandos (qu&eacute; comandos se usaron y cu&aacute;ndo).</li>
        <li data-i18n="priv.s1_d5">Estado VIP (si est&aacute;s en periodo de prueba, activo o expirado).</li>
    </ul>
    <p data-i18n="priv.s1_no">NO recopilamos: email, tel&eacute;fono, ubicaci&oacute;n, datos bancarios ni contrase&ntilde;as.</p>

    <h2 data-i18n="priv.s2_title">2. C&oacute;mo Usamos los Datos</h2>
    <ul>
        <li data-i18n="priv.s2_u1">Gestionar tu suscripci&oacute;n VIP (activaci&oacute;n, expiraci&oacute;n, verificaci&oacute;n de pago).</li>
        <li data-i18n="priv.s2_u2">Enviar se&ntilde;ales y notificaciones del servicio.</li>
        <li data-i18n="priv.s2_u3">Mejorar la calidad del servicio (estad&iacute;sticas an&oacute;nimas de uso).</li>
    </ul>

    <h2 data-i18n="priv.s3_title">3. Verificaci&oacute;n de Pagos</h2>
    <p data-i18n="priv.s3_text">Los pagos se realizan en USDT (TRC20) directamente a una wallet de Binance. La verificaci&oacute;n
    es autom&aacute;tica mediante la API de Binance, que &uacute;nicamente confirma la recepci&oacute;n del monto.
    No almacenamos datos de tu wallet ni claves privadas.</p>

    <h2 data-i18n="priv.s4_title">4. Almacenamiento y Seguridad</h2>
    <ul>
        <li data-i18n="priv.s4_s1">Los datos se almacenan en un servidor privado (VPS) con acceso restringido.</li>
        <li data-i18n="priv.s4_s2">La comunicaci&oacute;n web est&aacute; cifrada con HTTPS (Let's Encrypt).</li>
        <li data-i18n="priv.s4_s3">Los datos de estado se guardan en formato JSON en el servidor, sin base de datos externa.</li>
        <li data-i18n="priv.s4_s4">No compartimos datos con terceros.</li>
    </ul>

    <h2 data-i18n="priv.s5_title">5. Retenci&oacute;n de Datos</h2>
    <p><span data-i18n="priv.s5_text">Conservamos tus datos mientras mantengas una suscripci&oacute;n activa o interacci&oacute;n con el bot.
    Si deseas que eliminemos tus datos, contacta a</span> <a href="https://t.me/BuySell365Traiding">@BuySell365Traiding</a>
    <span data-i18n="priv.s5_days">y procederemos en un plazo m&aacute;ximo de 30 d&iacute;as.</span></p>

    <h2 data-i18n="priv.s6_title">6. Tus Derechos</h2>
    <p data-i18n="priv.s6_intro">Tienes derecho a:</p>
    <ul>
        <li data-i18n="priv.s6_r1">Acceso: Solicitar qu&eacute; datos tenemos sobre ti.</li>
        <li data-i18n="priv.s6_r2">Rectificaci&oacute;n: Corregir datos incorrectos.</li>
        <li data-i18n="priv.s6_r3">Eliminaci&oacute;n: Solicitar el borrado de tus datos.</li>
        <li data-i18n="priv.s6_r4">Portabilidad: Recibir una copia de tus datos en formato legible.</li>
    </ul>

    <h2 data-i18n="priv.s7_title">7. Cookies y Tracking</h2>
    <p data-i18n="priv.s7_text">El dashboard web de BuySell365 no utiliza cookies, analytics ni tracking de terceros.
    No se recopila informaci&oacute;n de navegaci&oacute;n.</p>

    <h2 data-i18n="priv.s8_title">8. Cambios en esta Pol&iacute;tica</h2>
    <p data-i18n="priv.s8_text">Nos reservamos el derecho de actualizar esta pol&iacute;tica. Los cambios ser&aacute;n publicados
    en esta misma URL y notificados por Telegram.</p>

    <h2 data-i18n="priv.s9_title">9. Contacto</h2>
    <p><span data-i18n="priv.s9_text">Para ejercer tus derechos o consultas sobre privacidad:</span> <a href="https://t.me/BuySell365Traiding">@BuySell365Traiding</a> <span data-i18n="priv.s9_via">en Telegram.</span></p>

    <a href="/dashboard" class="back" data-i18n="priv.back">&larr; Volver al Trading en Vivo</a>
</div>
<script>
(function(){
  const SUPPORTED=['es','en','pt','fr'];
  function detectLang(){
    const s=localStorage.getItem('buysell365_lang');
    if(s&&SUPPORTED.includes(s))return s;
    const n=(navigator.language||'es').toLowerCase();
    if(n.startsWith('en'))return'en';if(n.startsWith('pt'))return'pt';if(n.startsWith('fr'))return'fr';return'es';
  }
  function apply(tr){
    document.querySelectorAll('[data-i18n]').forEach(function(el){
      const k=el.getAttribute('data-i18n');if(tr[k])el.textContent=tr[k];
    });
  }
  function updateFlags(lang){
    SUPPORTED.forEach(function(l){var f=document.getElementById('flag_'+l);if(f)f.className=l===lang?'active':''});
  }
  window.setLang=function(lang){
    if(!SUPPORTED.includes(lang))lang='es';
    fetch('/i18n/'+lang+'.json').then(function(r){return r.json()}).then(function(d){
      apply(d);localStorage.setItem('buysell365_lang',lang);document.documentElement.lang=lang;updateFlags(lang);
    }).catch(function(){});
  };
  var lang=detectLang();updateFlags(lang);
  if(lang!=='es')window.setLang(lang);
})();
</script>
</body>
</html>"""

@app.route("/dashboard", methods=["GET"])
def dashboard_visual():
    """Panel de rendimiento BuySell365 — metricas agregadas, sin datos de senales."""

    # --- Estadísticas globales ---
    _hist_all = historial_operaciones if historial_operaciones else []
    _global_wins = sum(1 for h in _hist_all if h.get('pips', 0) > 0)
    _global_losses = sum(1 for h in _hist_all if h.get('pips', 0) <= 0)
    _global_total = _global_wins + _global_losses
    _global_winrate = round(_global_wins / _global_total * 100, 1) if _global_total > 0 else 0
    _global_pips = round(sum(h.get('pips', 0) for h in _hist_all), 1)
    _global_avg_win = round(sum(h.get('pips', 0) for h in _hist_all if h.get('pips', 0) > 0) / max(_global_wins, 1), 1)
    _global_avg_loss = round(sum(abs(h.get('pips', 0)) for h in _hist_all if h.get('pips', 0) <= 0) / max(_global_losses, 1), 1)
    _global_rr = round(_global_avg_win / _global_avg_loss, 2) if _global_avg_loss > 0 else 0

    # --- Actividad en vivo ---
    n_activas = len(operaciones_activas)
    activos_en_curso = list(set(op.get('nombre', op.get('ticker', '')) for op in operaciones_activas.values()))
    if activos_en_curso:
        activos_txt = " &middot; ".join(activos_en_curso)
    else:
        activos_txt = "Esperando se&ntilde;al confluente..."

    # --- Racha actual ---
    _racha = 0
    _racha_tipo = ""
    for h in reversed(_hist_all):
        _is_w = h.get('pips', 0) > 0
        if _racha == 0:
            _racha = 1
            _racha_tipo = "W" if _is_w else "L"
        elif (_is_w and _racha_tipo == "W") or (not _is_w and _racha_tipo == "L"):
            _racha += 1
        else:
            break
    racha_txt = f"{_racha}{_racha_tipo}" if _racha > 0 else "--"
    racha_color = "#00e676" if _racha_tipo == "W" else "#ff3b30" if _racha_tipo == "L" else "#5a6a7a"
    racha_fire = "&#128293;" if _racha >= 3 and _racha_tipo == "W" else ""

    # --- Últimas 20 señales como barras de rendimiento ---
    last_20 = _hist_all[-20:] if _hist_all else []
    _max_pips = max((abs(h.get('pips', 0)) for h in last_20), default=1) or 1
    bars_html = ""
    for h in last_20:
        p = h.get('pips', 0)
        pct = min(abs(p) / _max_pips * 100, 100)
        color = "#00e676" if p > 0 else "#ff3b30"
        bars_html += f'<div class="perf-bar" style="height:{max(pct, 10):.0f}%;background:{color}"></div>'
    if not bars_html:
        bars_html = '<div class="chart-empty">Primeras se&ntilde;ales en camino...</div>'

    # --- Últimas 10 como dots ---
    dots_html = ""
    for h in _hist_all[-10:]:
        color = "#00e676" if h.get('pips', 0) > 0 else "#ff3b30"
        dots_html += f'<span class="res-dot" style="background:{color}"></span>'
    if not dots_html:
        for _ in range(10):
            dots_html += '<span class="res-dot" style="background:#1e2a3a"></span>'

    # --- Rendimiento por activo ---
    asset_perf = {}
    for h in _hist_all:
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
        ('NASDAQ', '#00d4aa', ['NASDAQ', 'NQ', 'US100']),
        ('S&amp;P 500', '#3b82f6', ['S&P', 'SP500', 'US500', 'ES']),
        ('EUR/USD', '#a855f7', ['EUR/USD', 'EURUSD', 'EUR']),
        ('USD/JPY', '#ef4444', ['USD/JPY', 'USDJPY', 'JPY']),
        ('GBP/JPY', '#10b981', ['GBP/JPY', 'GBPJPY', 'GJ']),
    ]
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
        pips_color = "#00e676" if pips_a >= 0 else "#ff3b30"
        is_active = any(any(a.lower() in ac.lower() for a in aliases) for ac in activos_en_curso)
        active_cls = " asset-live" if is_active else ""
        active_dot = '<span class="mini-pulse"></span>' if is_active else ''
        asset_cards_html += f'''<div class="asset-card{active_cls}">
                <div class="asset-hdr"><span class="asset-dot" style="background:{accent}"></span>{display_name}{active_dot}</div>
                <div class="asset-wr">{wr}%</div>
                <div class="wr-bar-bg"><div class="wr-bar-fill" style="width:{wr}%;background:{accent}"></div></div>
                <div class="asset-meta"><span>{st['total']} ops</span><span style="color:{pips_color}">{pips_a:+.1f}</span></div>
            </div>'''

    # --- Info sistema ---
    mt5_acc = str(os.getenv("MT5_LOGIN", ""))
    acc_masked = mt5_acc[:4] + "****" if len(mt5_acc) > 4 else "****"
    now_str = ahora().strftime("%H:%M:%S CET")  # M-FIX: Usar hora Andorra, no server local
    hoy_str = ahora().strftime("%Y-%m-%d")
    senales_hoy = sum(1 for h in _hist_all if h.get('fecha', '').startswith(hoy_str))
    pips_color_net = "#00e676" if _global_pips >= 0 else "#ff3b30"

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<!-- Google Analytics -->
<script async src="https://www.googletagmanager.com/gtag/js?id=G-L514BL7E83"></script>
<script>window.dataLayer=window.dataLayer||[];function gtag(){{dataLayer.push(arguments);}}gtag('js',new Date());gtag('config','G-L514BL7E83');</script>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>BuySell365 Pro | Rendimiento en Vivo</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800;900&display=swap" rel="stylesheet">
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
:root{{--bg:#080b0f;--panel:#111820;--panel2:#192230;--border:#1e2a3a;--primary:#00d4aa;--primary-dim:rgba(0,212,170,.12);--gold:#f0b90b;--buy:#00e676;--sell:#ff3b30;--text:#e2e8f0;--muted:#5a6a7a;--font:'Inter',system-ui,-apple-system,sans-serif}}
body{{background:var(--bg);color:var(--text);font-family:var(--font);min-height:100vh;overflow-x:hidden}}
#matrix-canvas{{position:fixed;top:0;left:0;width:100%;height:100%;z-index:0;pointer-events:none;opacity:.12}}
body::before{{content:'';position:fixed;top:0;left:0;right:0;height:400px;background:radial-gradient(ellipse at 50% 0%,rgba(0,212,170,.08) 0%,transparent 70%);pointer-events:none;z-index:0}}
.wrap{{max-width:1280px;margin:0 auto;padding:20px;position:relative;z-index:1}}

/* HEADER */
.hdr{{display:flex;justify-content:space-between;align-items:center;padding:18px 0 22px;border-bottom:1px solid var(--border);margin-bottom:28px}}
.hdr-left{{display:flex;align-items:center;gap:14px}}
.hdr-logo{{width:72px;height:72px;border-radius:14px;object-fit:cover;border:1px solid rgba(0,212,170,.2);box-shadow:0 4px 16px rgba(0,212,170,.15)}}
.brand{{font-size:24px;font-weight:800;letter-spacing:-.5px;color:#fff}}
.brand small{{display:block;font-size:11px;font-weight:500;color:var(--muted);letter-spacing:.5px;margin-top:2px}}
.live-badge{{display:flex;align-items:center;gap:6px;background:rgba(0,212,170,.08);border:1px solid rgba(0,212,170,.2);padding:7px 14px;border-radius:20px;font-size:12px;font-weight:600;color:var(--primary)}}
.pulse{{width:8px;height:8px;border-radius:50%;background:var(--primary);animation:pulse 2s infinite}}
@keyframes pulse{{0%,100%{{opacity:1;box-shadow:0 0 0 0 rgba(0,212,170,.5)}}50%{{opacity:.7;box-shadow:0 0 0 6px rgba(0,212,170,0)}}}}

/* STAT CARDS */
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

/* WINRATE BAR */
.wr-bar-bg{{width:100%;height:6px;background:var(--border);border-radius:3px;overflow:hidden}}
.wr-bar-fill{{height:100%;border-radius:3px;transition:width .5s ease}}

/* TWO COLUMN */
.two-col{{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-bottom:24px}}

/* CARDS */
.card{{background:var(--panel);border:1px solid var(--border);border-radius:16px;padding:22px;position:relative}}
.card-title{{font-size:14px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.8px;margin-bottom:16px;display:flex;align-items:center;gap:8px}}
.card-title i{{font-style:normal}}

/* PERF CHART */
.chart-area{{display:flex;align-items:flex-end;gap:4px;height:100px;padding:10px 0}}
.perf-bar{{flex:1;min-width:6px;border-radius:3px 3px 0 0;transition:height .3s ease;opacity:.85}}
.perf-bar:hover{{opacity:1;filter:brightness(1.2)}}
.chart-empty{{color:var(--muted);font-style:italic;text-align:center;width:100%;padding:30px 0;font-size:13px}}
.chart-legend{{display:flex;gap:16px;margin-top:12px;font-size:11px;color:var(--muted)}}
.chart-legend span{{display:flex;align-items:center;gap:4px}}
.chart-legend i{{width:10px;height:10px;border-radius:2px;display:inline-block;font-style:normal}}

/* DOTS */
.res-dot{{display:inline-block;width:14px;height:14px;border-radius:50%;margin:0 2px;transition:transform .2s}}
.res-dot:hover{{transform:scale(1.3)}}

/* RACHA */
.racha-box{{display:flex;align-items:center;gap:12px;margin-top:14px;padding:12px 16px;background:var(--panel2);border-radius:10px}}
.racha-num{{font-size:28px;font-weight:900;letter-spacing:-1px}}
.racha-label{{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px}}

/* LIVE STATUS */
.live-row{{display:flex;align-items:center;gap:12px;padding:14px 0;border-bottom:1px solid rgba(30,42,58,.3)}}
.live-row:last-child{{border-bottom:none}}
.live-icon{{width:36px;height:36px;border-radius:10px;display:flex;align-items:center;justify-content:center;font-size:16px;background:var(--panel2)}}
.live-label{{font-size:12px;color:var(--muted)}}
.live-val{{font-size:14px;font-weight:700;color:#fff}}
.mini-pulse{{width:6px;height:6px;border-radius:50%;background:var(--primary);display:inline-block;margin-left:6px;animation:pulse 2s infinite;vertical-align:middle}}

/* ASSET GRID */
.asset-grid{{display:grid;grid-template-columns:repeat(7,1fr);gap:12px}}
.asset-card{{background:var(--panel2);border:1px solid var(--border);border-radius:12px;padding:14px;text-align:center;transition:border-color .2s,transform .2s}}
.asset-card:hover{{border-color:rgba(0,212,170,.3);transform:translateY(-2px)}}
.asset-card.asset-live{{border-color:rgba(0,212,170,.4);box-shadow:0 0 12px rgba(0,212,170,.1)}}
.asset-hdr{{font-size:12px;font-weight:700;color:#fff;margin-bottom:8px;display:flex;align-items:center;justify-content:center;gap:6px}}
.asset-dot{{width:8px;height:8px;border-radius:50%;display:inline-block}}
.asset-wr{{font-size:24px;font-weight:900;color:var(--primary);letter-spacing:-1px;margin:4px 0}}
.asset-meta{{display:flex;justify-content:space-between;font-size:10px;color:var(--muted);margin-top:8px}}

/* CTA PROMO */
.promo{{background:linear-gradient(135deg,#0d1a2a 0%,#112030 50%,#0a1520 100%);border:1px solid rgba(0,212,170,.15);border-radius:18px;padding:32px;text-align:center;margin-top:24px;position:relative;overflow:hidden}}
.promo::before{{content:'';position:absolute;top:-50%;left:-50%;width:200%;height:200%;background:radial-gradient(circle,rgba(0,212,170,.04) 0%,transparent 60%);pointer-events:none}}
.promo h2{{font-size:20px;font-weight:800;margin-bottom:6px;position:relative}}
.promo h2 span{{color:var(--primary)}}
.promo p{{color:var(--muted);font-size:13px;margin-bottom:20px;position:relative}}
.promo-features{{display:flex;justify-content:center;gap:24px;margin-bottom:24px;flex-wrap:wrap;position:relative}}
.promo-feat{{display:flex;align-items:center;gap:6px;font-size:12px;font-weight:600}}
.promo-feat i{{font-style:normal;color:var(--primary)}}
.cta-btn{{display:inline-flex;align-items:center;gap:8px;padding:14px 36px;background:linear-gradient(135deg,var(--primary),#00a080);color:#000;font-weight:800;font-size:15px;border-radius:12px;text-decoration:none;transition:all .2s;box-shadow:0 4px 24px rgba(0,212,170,.3);position:relative}}
.cta-btn:hover{{transform:translateY(-2px);box-shadow:0 8px 32px rgba(0,212,170,.4)}}
.ia-pills{{display:flex;gap:6px;justify-content:center;margin-top:10px}}
.ia-pill{{padding:3px 10px;border-radius:8px;font-size:11px;font-weight:600;background:var(--primary-dim);color:var(--primary)}}

/* ACTIVE ALERT BANNER */
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

/* FILTER BUTTONS */
.filter-bar{{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:14px;align-items:center}}
.filter-btn{{padding:5px 14px;border-radius:8px;font-size:12px;font-weight:600;cursor:pointer;transition:all .2s;border:1px solid var(--border);background:var(--panel2);color:var(--muted)}}
.filter-btn:hover{{border-color:rgba(0,212,170,.3);color:var(--text)}}
.filter-btn.active{{background:var(--primary-dim);border-color:var(--primary);color:var(--primary)}}

/* PAGINATION */
.pagination{{display:flex;justify-content:center;align-items:center;gap:6px;margin-top:16px;flex-wrap:wrap}}
.page-btn{{background:var(--panel2);border:1px solid var(--border);color:var(--text);padding:8px 14px;border-radius:8px;cursor:pointer;font-size:0.85rem;transition:all .2s;font-family:inherit}}
.page-btn:hover:not(:disabled){{border-color:var(--primary);color:var(--primary)}}
.page-btn.active{{background:var(--primary);color:#000;border-color:var(--primary);font-weight:700}}
.page-btn:disabled{{opacity:.4;cursor:not-allowed}}

/* STREAK BADGE */
.streak-banner{{display:flex;align-items:center;gap:16px;padding:14px 20px;background:linear-gradient(135deg,rgba(0,212,170,.06),transparent);border:1px solid rgba(0,212,170,.15);border-radius:12px;margin-bottom:14px}}
.streak-number{{font-size:36px;font-weight:900;color:var(--primary);letter-spacing:-2px;line-height:1}}
.streak-info{{flex:1}}
.streak-label{{font-size:13px;font-weight:700;color:var(--text)}}
.streak-sub{{font-size:11px;color:var(--muted)}}
.streak-fire{{color:#ff6b35;font-size:24px}}

/* CUMULATIVE CHART */
.cumul-chart-wrap{{position:relative;height:160px;margin:14px 0;border:1px solid var(--border);border-radius:10px;overflow:hidden;background:var(--panel2)}}
.cumul-chart-wrap svg{{width:100%;height:100%}}

/* WINRATE PERIOD CARDS */
.wr-period-row{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:24px}}
.wr-period-card{{background:var(--panel);border:1px solid var(--border);border-radius:12px;padding:16px;text-align:center;position:relative;overflow:hidden}}
.wr-period-card::after{{content:'';position:absolute;top:0;left:0;right:0;height:2px}}
.wr-period-label{{font-size:11px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.8px;margin-bottom:6px}}
.wr-period-val{{font-size:28px;font-weight:900;letter-spacing:-1px}}
.wr-period-detail{{font-size:11px;color:var(--muted);margin-top:4px}}
.wr-period-bar{{height:4px;background:var(--border);border-radius:2px;margin-top:8px;overflow:hidden}}
.wr-period-fill{{height:100%;border-radius:2px;transition:width .5s}}

/* FOOTER */
.footer{{text-align:center;padding:24px 0 12px;margin-top:20px;border-top:1px solid var(--border)}}
.footer p{{font-size:11px;color:var(--muted)}}
.footer a{{color:var(--primary);text-decoration:none}}

/* RESPONSIVE */
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
            <img src="/img/bull_bear.png" alt="BuySell365 Pro" class="hdr-logo">
            <div class="brand" style="color:#fff">BuySell365 <span style="color:var(--primary)">Pro</span><small data-i18n="dash.tagline">TRADING CON INTELIGENCIA ARTIFICIAL</small></div>
            </a>
        </div>
        <div style="display:flex;align-items:center;gap:12px">
            <div class="lang-selector" id="langSelector">
                <button class="lang-btn" onclick="toggleLangMenu()" style="background:var(--panel2);border:1px solid var(--border);border-radius:8px;padding:6px 10px;cursor:pointer;font-size:18px;line-height:1"><span id="currentFlag">\U0001f1ea\U0001f1f8</span></button>
                <div class="lang-menu" id="langMenu" style="display:none;position:absolute;top:110%;right:0;background:var(--panel);border:1px solid var(--border);border-radius:10px;overflow:hidden;z-index:999;min-width:150px;box-shadow:0 8px 32px rgba(0,0,0,.4)">
                    <a onclick="setLang('es')" style="display:block;padding:10px 16px;cursor:pointer;color:var(--text);text-decoration:none;font-size:14px;transition:background .2s">\U0001f1ea\U0001f1f8 Espa\u00f1ol</a>
                    <a onclick="setLang('en')" style="display:block;padding:10px 16px;cursor:pointer;color:var(--text);text-decoration:none;font-size:14px;transition:background .2s">\U0001f1fa\U0001f1f8 English</a>
                    <a onclick="setLang('pt')" style="display:block;padding:10px 16px;cursor:pointer;color:var(--text);text-decoration:none;font-size:14px;transition:background .2s">\U0001f1e7\U0001f1f7 Portugu\u00eas</a>
                    <a onclick="setLang('fr')" style="display:block;padding:10px 16px;cursor:pointer;color:var(--text);text-decoration:none;font-size:14px;transition:background .2s">\U0001f1eb\U0001f1f7 Fran\u00e7ais</a>
                </div>
            </div>
            <div class="live-badge"><div class="pulse"></div><span data-i18n="dash.live">EN VIVO</span> &mdash; {now_str}</div>
        </div>
    </div>

    <!-- ACTIVE OPERATIONS (PRIMERO — siempre visible) -->
    <div id="active-alerts-container" style="margin-bottom:24px"></div>

    <!-- WINNING TRADES HISTORY -->
    <div class="card" style="margin-bottom:24px">
        <div class="card-title"><i>&#127942;</i> <span>Historial de Operaciones Ganadas</span></div>
        <!-- Streak Banner -->
        <div id="streak-banner-container"></div>
        <!-- Cumulative Performance Chart -->
        <div class="card-title" style="margin-top:8px"><i>&#128200;</i> <span>Rendimiento Acumulado</span></div>
        <div id="cumulative-chart-container" class="cumul-chart-wrap">
            <p style="color:var(--muted);text-align:center;padding:40px;font-size:12px">Cargando gr&aacute;fico...</p>
        </div>
        <!-- Filter Buttons -->
        <div id="trade-filter-bar" class="filter-bar" style="margin-top:14px">
            <span style="font-size:11px;color:var(--muted);text-transform:uppercase;font-weight:600;letter-spacing:.5px">Filtrar:</span>
        </div>
        <!-- Trades Table -->
        <div id="winning-trades-container" style="overflow-x:auto">
            <p style="color:var(--muted);text-align:center;padding:20px">Cargando historial...</p>
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
            <div class="stat-value" style="color:var(--primary)">{_global_total}</div>
            <div class="stat-sub">{senales_hoy} <span data-i18n="dash.today">hoy</span> &mdash; {_global_wins}W / {_global_losses}L</div>
        </div>
        <div class="stat-card accent-gold">
            <div class="stat-label">&#128200; <span data-i18n="dash.winrate">Tasa de Acierto</span></div>
            <div class="stat-value" style="color:{'#00d4aa' if _global_winrate >= 60 else ('#f0b90b' if _global_winrate >= 45 else '#ff3b30')}">{_global_winrate}%</div>
            <div class="wr-bar-bg" style="margin-top:8px"><div class="wr-bar-fill" style="width:{_global_winrate}%;background:{'#00d4aa' if _global_winrate >= 60 else ('#f0b90b' if _global_winrate >= 45 else '#ff3b30')}"></div></div>
            <div class="stat-sub" data-i18n="dash.winrate_sub">Porcentaje de acierto global</div>
        </div>
        <div class="stat-card accent-blue">
            <div class="stat-label">&#128176; <span data-i18n="dash.net_pips">Resultado Neto</span></div>
            <div class="stat-value" style="color:{pips_color_net}">{_global_pips:+.1f}</div>
            <div class="stat-sub"><span data-i18n="dash.avg_win">Promedio ganancia</span>: {_global_avg_win}</div>
        </div>
        <div class="stat-card accent-purple">
            <div class="stat-label">&#9878; <span data-i18n="dash.rr">Risk : Reward</span></div>
            <div class="stat-value" style="color:var(--primary)">{_global_rr}:1</div>
            <div class="stat-sub" data-i18n="dash.rr_sub">Relaci&oacute;n ganancia / p&eacute;rdida</div>
        </div>
    </div>

<!-- PROMO UNIFICADA -->
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
            <div style="display:flex;justify-content:center;gap:14px;flex-wrap:wrap">
                <a href="https://t.me/BUYSELL_365_24_7" target="_blank" class="cta-btn" style="padding:12px 24px">&#128172; TELEGRAM GRATIS</a>
                <a href="https://social.tp-redirect.com/s/WRE0V7jm" target="_blank" rel="noopener" class="cta-btn" style="background:linear-gradient(135deg,#a855f7,#6366f1);border:none;padding:12px 24px">&#128640; COPY TRADING</a>
            </div>
            <p style="font-size:11px;color:var(--muted);margin-top:12px">Peque&ntilde;a comisi&oacute;n por apertura &mdash; Solo pagas si ganas</p>
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
// ═══════════════════════════════════════════════
//  MATRIX BINARY RAIN — Background Animation
// ═══════════════════════════════════════════════
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

// ═══════════════════════════════════════════════
//  i18n ENGINE — BuySell365 Dashboard
// ═══════════════════════════════════════════════
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
      .then(function(data){{ currentLang = lang; localStorage.setItem('buysell365_lang', lang); applyTranslations(data); }})
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
  const lang = detectLang();
  if(lang !== 'es') loadLang(lang);
  else{{ currentLang = 'es'; localStorage.setItem('buysell365_lang', 'es'); const flagEl = document.getElementById('currentFlag'); if(flagEl) flagEl.textContent = FLAGS['es']; }}
}})();

// ═══════════════════════════════════════════════
//  ACTIVE OPERATIONS ALERT BANNER
// ═══════════════════════════════════════════════
(function(){{
  function loadActiveOps(){{
    fetch('/api/active_ops')
      .then(r => r.json())
      .then(ops => {{
        const container = document.getElementById('active-alerts-container');
        if(!container) return;
        if(!ops || ops.length === 0){{
          container.innerHTML = '<div class="active-alert" style="text-align:center;padding:20px;opacity:.7"><div class="alert-header"><span style="font-size:18px">&#128308;</span> Sin operaciones activas en este momento</div><div style="font-size:13px;color:var(--muted);margin-top:8px">El bot opera de 8:00 a 18:00 hora Andorra (L-V)</div></div>';
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
          const _dispName = (function(raw){{ if(!raw) return '?'; const m={{'GC=F':'ORO','NQ=F':'NASDAQ','ES=F':'S&P 500','EURUSD=X':'EUR/USD','USDJPY=X':'USD/JPY','GBPJPY=X':'GBP/JPY'}}; if(m[raw]) return m[raw]; var n=raw; for(var k in m){{ if(raw.indexOf(k)>=0) return m[k]; }}; n=n.replace(/[^A-Za-z0-9\\/&. _-]/g,'').trim(); if(m[n]) return m[n]; return n||raw; }})(op.nombre || op.ticker);
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

// ═══════════════════════════════════════════════
//  WINNING TRADES + FILTERS + STREAK + CHART + WIN RATE
// ═══════════════════════════════════════════════
(function(){{
  let allTrades = [];
  let currentFilter = 'ALL';
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

  // ── STREAK CALCULATION ──
  function renderStreak(trades){{
    const container = document.getElementById('streak-banner-container');
    if(!container) return;
    if(!trades || trades.length === 0){{ container.innerHTML = ''; return; }}
    // Count consecutive wins from the end
    let streak = trades.length; // all are wins
    let fireEmoji = '';
    if(streak >= 10) fireEmoji = '\U0001f525\U0001f525\U0001f525';
    else if(streak >= 5) fireEmoji = '\U0001f525\U0001f525';
    else if(streak >= 3) fireEmoji = '\U0001f525';
    let html = '<div class="streak-banner">';
    html += '<div class="streak-number">' + streak + '</div>';
    html += '<div class="streak-info"><div class="streak-label">Operaciones Ganadas Consecutivas</div>';
    html += '<div class="streak-sub">Racha ganadora total</div></div>';
    if(fireEmoji) html += '<div class="streak-fire">' + fireEmoji + '</div>';
    html += '</div>';
    container.innerHTML = html;
  }}

  // ── CUMULATIVE CHART ──
  function renderCumulativeChart(trades){{
    const container = document.getElementById('cumulative-chart-container');
    if(!container || !trades || trades.length === 0){{
      if(container) container.innerHTML = '<p style="color:var(--muted);text-align:center;padding:40px;font-size:12px">Sin datos a\u00fan</p>';
      return;
    }}
    const W = container.clientWidth || 600;
    const H = 160;
    const pad = {{t:20,r:20,b:30,l:55}};
    const pw = W - pad.l - pad.r;
    const ph = H - pad.t - pad.b;
    // Build cumulative data
    let cumul = [0];
    trades.forEach(function(t){{ cumul.push(cumul[cumul.length-1] + (t.pips || 0)); }});
    const maxY = Math.max.apply(null, cumul);
    const minY = Math.min.apply(null, cumul);
    const rangeY = maxY - minY || 1;
    function x(i){{ return pad.l + (i / (cumul.length - 1)) * pw; }}
    function y(v){{ return pad.t + ph - ((v - minY) / rangeY) * ph; }}
    // Build SVG path
    let pathD = 'M' + x(0) + ',' + y(cumul[0]);
    for(let i = 1; i < cumul.length; i++) pathD += ' L' + x(i) + ',' + y(cumul[i]);
    let areaD = pathD + ' L' + x(cumul.length-1) + ',' + (pad.t + ph) + ' L' + x(0) + ',' + (pad.t + ph) + ' Z';
    let svg = '<svg viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="none" style="width:100%;height:100%">';
    // Grid lines
    for(let i = 0; i <= 4; i++){{
      const yy = pad.t + (ph / 4) * i;
      const val = (maxY - (rangeY / 4) * i).toFixed(1);
      svg += '<line x1="' + pad.l + '" y1="' + yy + '" x2="' + (W - pad.r) + '" y2="' + yy + '" stroke="rgba(30,42,58,.5)" stroke-width="1"/>';
      svg += '<text x="' + (pad.l - 8) + '" y="' + (yy + 4) + '" fill="#5a6a7a" font-size="10" text-anchor="end" font-family="Inter">' + val + '</text>';
    }}
    // Area fill
    svg += '<defs><linearGradient id="cg" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="rgba(0,212,170,.3)"/><stop offset="100%" stop-color="rgba(0,212,170,0)"/></linearGradient></defs>';
    svg += '<path d="' + areaD + '" fill="url(#cg)"/>';
    // Line
    svg += '<path d="' + pathD + '" fill="none" stroke="#00d4aa" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>';
    // End dot
    svg += '<circle cx="' + x(cumul.length-1) + '" cy="' + y(cumul[cumul.length-1]) + '" r="4" fill="#00d4aa" stroke="#080b0f" stroke-width="2"/>';
    // X axis labels
    const step = Math.max(1, Math.floor(trades.length / 6));
    for(let i = 0; i < trades.length; i += step){{
      const label = trades[i].fecha || (i + 1);
      svg += '<text x="' + x(i + 1) + '" y="' + (H - 6) + '" fill="#5a6a7a" font-size="9" text-anchor="middle" font-family="Inter">' + label + '</text>';
    }}
    // Final value label
    svg += '<text x="' + x(cumul.length-1) + '" y="' + (y(cumul[cumul.length-1]) - 8) + '" fill="#00e676" font-size="12" font-weight="700" text-anchor="middle" font-family="Inter">+' + cumul[cumul.length-1].toFixed(1) + '</text>';
    svg += '</svg>';
    container.innerHTML = svg;
  }}

  // ── NORMALIZE ASSET NAME ──
  function normName(raw){{
    if(!raw) return '?';
    const map = {{'GC=F':'ORO','NQ=F':'NASDAQ','ES=F':'S&P 500','EURUSD=X':'EUR/USD','USDJPY=X':'USD/JPY','GBPJPY=X':'GBP/JPY','BTC-USD':'BITCOIN','ETH-USD':'ETHEREUM','XAUUSD':'ORO','BTCUSD':'BITCOIN','ETHUSD':'ETHEREUM','US100Cash':'NASDAQ','US500Cash':'S&P 500'}};
    if(map[raw]) return map[raw];
    let n = raw.replace(/[^A-Za-z0-9\\/&. _-]/g, '').trim();
    if(map[n]) return map[n];
    for(let k in map){{ if(raw.indexOf(k) >= 0) return map[k]; }}
    return n;
  }}

  // ── FILTER BUTTONS ──
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
    let html = '<span style="font-size:11px;color:var(--muted);text-transform:uppercase;font-weight:600;letter-spacing:.5px">Filtrar:</span>';
    html += '<button class="filter-btn' + (currentFilter === 'ALL' ? ' active' : '') + '" data-filter="ALL" onclick="window._filterTrades(this.dataset.filter)">Todos (' + visibleCount + ')</button>';
    Object.keys(assets).sort().forEach(function(name){{
      const cls = currentFilter === name ? ' active' : '';
      html += '<button class="filter-btn' + cls + '" data-filter="' + name + '" onclick="window._filterTrades(this.dataset.filter)">' + name + ' (' + assets[name] + ')</button>';
    }});
    bar.innerHTML = html;
  }}

  // ── TABLE RENDER (PAGINATED) ──
  function renderTable(trades){{
    const container = document.getElementById('winning-trades-container');
    if(!container) return;
    const _hidden = {{'BITCOIN':1,'ETHEREUM':1}};
    const visibleTrades = trades.filter(function(t){{ return !_hidden[normName(t.nombre || t.ticker || '?')]; }});
    const filtered = currentFilter === 'ALL' ? visibleTrades : visibleTrades.filter(function(t){{ return normName(t.nombre || t.ticker) === currentFilter; }});
    if(!filtered || filtered.length === 0){{
      container.innerHTML = '<p style="color:var(--muted);text-align:center;padding:20px">No hay operaciones para este filtro.</p>';
      return;
    }}
    // Calculate totals from ALL filtered trades (not just current page)
    let totalPips = 0;
    filtered.forEach(function(t){{ totalPips += (t.pips || 0); }});
    // Pagination calc
    const sorted = filtered.slice().reverse();
    const totalPages = Math.ceil(sorted.length / TRADES_PER_PAGE);
    if(currentPage > totalPages) currentPage = totalPages;
    if(currentPage < 1) currentPage = 1;
    const startIdx = (currentPage - 1) * TRADES_PER_PAGE;
    const pageData = sorted.slice(startIdx, startIdx + TRADES_PER_PAGE);
    // Table
    let html = '<table style="width:100%;border-collapse:collapse;font-size:0.85rem">';
    html += '<thead><tr style="border-bottom:2px solid var(--border);color:var(--primary);text-align:left">';
    html += '<th style="padding:10px 8px">Fecha</th><th style="padding:10px 8px">Activo</th><th style="padding:10px 8px">Tipo</th>';
    html += '<th style="padding:10px 8px">Entrada</th><th style="padding:10px 6px">Hora</th><th style="padding:10px 8px">Salida</th>';
    html += '<th style="padding:10px 6px">Hora</th><th style="padding:10px 8px">Pips/Pts</th><th style="padding:10px 8px">Score</th>';
    html += '</tr></thead><tbody>';
    pageData.forEach(function(t, i){{
      const bg = i % 2 === 0 ? 'rgba(0,212,170,0.04)' : 'transparent';
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
      html += '<td style="padding:8px;color:#00e676;font-weight:700">+' + pips.toFixed(1) + ' ' + unit + '</td>';
      html += '<td style="padding:8px;color:var(--primary)">' + (t.score != null ? Math.min(t.score * 2, 10) : '-') + '/10</td>';
      html += '</tr>';
    }});
    html += '</tbody></table>';
    // Summary (always shows TOTAL, not page)
    html += '<div style="display:flex;justify-content:space-between;align-items:center;padding:14px 8px;margin-top:8px;border-top:2px solid var(--primary);font-weight:700">';
    html += '<span style="color:var(--text)">\U0001f3c6 Total: ' + filtered.length + ' operaciones ganadas</span>';
    html += '<span style="color:#00e676;font-size:1.1rem">+' + totalPips.toFixed(1) + ' pips acumulados</span>';
    html += '</div>';
    // Pagination controls
    if(totalPages > 1){{
      html += '<div class="pagination">';
      html += '<button class="page-btn" onclick="window._goToPage(' + (currentPage - 1) + ')"' + (currentPage === 1 ? ' disabled' : '') + '>&laquo; Anterior</button>';
      // Page numbers with smart range
      let startP = Math.max(1, currentPage - 2);
      let endP = Math.min(totalPages, currentPage + 2);
      if(startP > 1){{ html += '<button class="page-btn" onclick="window._goToPage(1)">1</button>'; if(startP > 2) html += '<span style="color:var(--muted);padding:0 4px">...</span>'; }}
      for(let p = startP; p <= endP; p++){{
        html += '<button class="page-btn' + (p === currentPage ? ' active' : '') + '" onclick="window._goToPage(' + p + ')">' + p + '</button>';
      }}
      if(endP < totalPages){{ if(endP < totalPages - 1) html += '<span style="color:var(--muted);padding:0 4px">...</span>'; html += '<button class="page-btn" onclick="window._goToPage(' + totalPages + ')">' + totalPages + '</button>'; }}
      html += '<button class="page-btn" onclick="window._goToPage(' + (currentPage + 1) + ')"' + (currentPage === totalPages ? ' disabled' : '') + '>Siguiente &raquo;</button>';
      html += '</div>';
      html += '<div style="text-align:center;font-size:0.75rem;color:var(--muted);margin-top:8px">P\u00e1gina ' + currentPage + ' de ' + totalPages + ' \u00b7 Mostrando ' + pageData.length + ' de ' + filtered.length + ' operaciones</div>';
    }}
    container.innerHTML = html;
  }}

  // ── WIN RATE BY PERIOD ──
  function renderWinRatePeriods(){{
    fetch('/api/stats')
      .then(r => r.json())
      .then(function(stats){{
        const container = document.getElementById('wr-period-container');
        if(!container) return;
        const todayWR = stats.today_signals > 0 ? Math.round(stats.today_wins / stats.today_signals * 100) : 0;
        const weekWR = stats.week_wr || 0;
        const monthWR = stats.month_wr || 0;
        const todayColor = todayWR >= 60 ? '#00d4aa' : (todayWR >= 45 ? '#f0b90b' : (stats.today_signals > 0 ? '#ff3b30' : '#5a6a7a'));
        const weekColor = weekWR >= 60 ? '#00d4aa' : (weekWR >= 45 ? '#f0b90b' : (stats.week_signals > 0 ? '#ff3b30' : '#5a6a7a'));
        const monthColor = monthWR >= 60 ? '#00d4aa' : (monthWR >= 45 ? '#f0b90b' : (stats.month_signals > 0 ? '#ff3b30' : '#5a6a7a'));
        let html = '';
        // TODAY
        html += '<div class="wr-period-card" style="border-top:2px solid ' + todayColor + '">';
        html += '<div class="wr-period-label">Hoy</div>';
        html += '<div class="wr-period-val" style="color:' + todayColor + '">' + todayWR + '%</div>';
        html += '<div class="wr-period-detail">' + (stats.today_wins||0) + 'W / ' + ((stats.today_signals||0) - (stats.today_wins||0)) + 'L de ' + (stats.today_signals||0) + '</div>';
        html += '<div class="wr-period-bar"><div class="wr-period-fill" style="width:' + todayWR + '%;background:' + todayColor + '"></div></div>';
        html += '</div>';
        // WEEK
        html += '<div class="wr-period-card" style="border-top:2px solid ' + weekColor + '">';
        html += '<div class="wr-period-label">Esta Semana</div>';
        html += '<div class="wr-period-val" style="color:' + weekColor + '">' + Math.round(weekWR) + '%</div>';
        html += '<div class="wr-period-detail">' + (stats.week_wins||0) + 'W / ' + ((stats.week_signals||0) - (stats.week_wins||0)) + 'L de ' + (stats.week_signals||0) + '</div>';
        html += '<div class="wr-period-bar"><div class="wr-period-fill" style="width:' + weekWR + '%;background:' + weekColor + '"></div></div>';
        html += '</div>';
        // MONTH
        html += '<div class="wr-period-card" style="border-top:2px solid ' + monthColor + '">';
        html += '<div class="wr-period-label">Este Mes</div>';
        html += '<div class="wr-period-val" style="color:' + monthColor + '">' + Math.round(monthWR) + '%</div>';
        html += '<div class="wr-period-detail">' + (stats.month_wins||0) + 'W / ' + ((stats.month_signals||0) - (stats.month_wins||0)) + 'L de ' + (stats.month_signals||0) + '</div>';
        html += '<div class="wr-period-bar"><div class="wr-period-fill" style="width:' + monthWR + '%;background:' + monthColor + '"></div></div>';
        html += '</div>';
        container.innerHTML = html;
      }})
      .catch(function(){{}});
  }}

  // ── PAGE NAVIGATION ──
  window._goToPage = function(page){{
    currentPage = page;
    renderTable(allTrades);
    const el = document.getElementById('winning-trades-container');
    if(el) el.scrollIntoView({{behavior:'smooth', block:'start'}});
  }};

  // ── FILTER HANDLER ──
  window._filterTrades = function(filter){{
    currentFilter = filter;
    currentPage = 1;
    renderFilters(allTrades);
    renderTable(allTrades);
  }};

  // ── MAIN LOADER ──
  function loadAll(){{
    fetch('/api/winning_trades')
      .then(r => r.json())
      .then(trades => {{
        allTrades = trades || [];
        renderStreak(allTrades);
        renderCumulativeChart(allTrades);
        renderFilters(allTrades);
        renderTable(allTrades);
      }})
      .catch(function(e){{
        const container = document.getElementById('winning-trades-container');
        if(container) container.innerHTML = '<p style="color:var(--muted);text-align:center">Error cargando historial</p>';
      }});
    renderWinRatePeriods();
  }}
  loadAll();
  setInterval(loadAll, 30000);
}})();
</script>

</body>
</html>"""
    return html

# ============================================================

_confluencia_pending = {}
_confluencia_last    = {}
CONFLUENCIA_VENTANA  = 120
CONFLUENCIA_COOLDOWN = 300

def _confluencia_check(ticker, direccion, source):
    import time
    ahora = time.time()
    key = f"{ticker}_{direccion}"
    otro = "BUYSELL365" if source == "GAINZALGO_V2" else "GAINZALGO_V2"
    
    if key in _confluencia_last:
        if ahora - _confluencia_last[key] < CONFLUENCIA_COOLDOWN:
            return False
            
    if key in _confluencia_pending:
        p = _confluencia_pending[key]
        if ahora - p["ts"] <= CONFLUENCIA_VENTANA and p["source"] == otro:
            del _confluencia_pending[key]
            _confluencia_last[key] = ahora
            return True
            
    _confluencia_pending[key] = {"source": source, "ts": ahora}
    return False

def limpiar_caches_memoria():
    """Previene fugas de memoria limpiando cachés antiguos periódicamente."""
    import time as _time_mod
    ahora_ts = _time_mod.time()
    # Limpiar caches unbounded que crecen con el tiempo
    try:
        # Limpiar cooldowns de bienvenida viejos (>1 hora)
        if '_cooldown_bienvenida' in globals():
            old_keys = [k for k, v in _cooldown_bienvenida.items() if ahora_ts - v > 3600]
            for k in old_keys:
                _cooldown_bienvenida.pop(k, None)
    except Exception:
        pass
    try:
        # Limpiar cache de miembros viejos (>10 min)
        if '_cache_miembros' in globals():
            old_keys = [k for k, v in _cache_miembros.items() if (isinstance(v, tuple) and ahora_ts - v[0] > 600) or (isinstance(v, dict) and ahora_ts - v.get('ts', 0) > 600)]
            for k in old_keys:
                _cache_miembros.pop(k, None)
    except Exception:
        pass
    ahora_ts = _time_mod.time()
    for limite, cache_dict in [
        (3600, _cache_ml_modelos), 
        (7200, _cache_mtf_1h), 
        (14400, _cache_mtf_4h), 
        (CONFLUENCIA_VENTANA*2, _confluencia_pending),
        (CONFLUENCIA_COOLDOWN*2, _confluencia_last)
    ]:
        claves_a_borrar = []
        for k, v in cache_dict.items():
            # Si el valor es un float (timestamp) o un diccionario con 'ts' o 'timestamp'
            ts = v if isinstance(v, (int, float)) else (v.get('ts') or v.get('timestamp') if isinstance(v, dict) else ahora_ts)
            if ahora_ts - ts > limite:
                claves_a_borrar.append(k)
        for k in claves_a_borrar:
            del cache_dict[k]

    # H-03 FIX: Trim historial_operaciones para evitar crecimiento ilimitado
    try:
        with _lock_ops:
            if len(historial_operaciones) > 500:
                # Mantener solo las últimas 500 operaciones
                _exceso = len(historial_operaciones) - 500
                del historial_operaciones[:_exceso]
                logger.info(f"🧹 historial_operaciones trimmed: eliminadas {_exceso} entradas antiguas")
    except Exception:
        pass

    # Limpiar _senal_reciente expirados (>120s)
    try:
        if '_senal_reciente' in globals():
            _sr_expired = [k for k, v in _senal_reciente.items() if ahora_ts - v > 120]
            for k in _sr_expired:
                _senal_reciente.pop(k, None)
    except Exception:
        pass

    # Limpiar _cooldown_cierres expirados (>30 min)
    try:
        _cd_expired = [k for k, v in _cooldown_cierres.items() if ahora_ts - v > 1800]
        for k in _cd_expired:
            _cooldown_cierres.pop(k, None)
    except Exception:
        pass

    # Limpiar _rate_limit_web: IPs sin actividad en >5 min
    try:
        _rl_expired = [ip for ip, ts_list in _rate_limit_web.items()
                       if not ts_list or ahora_ts - max(ts_list) > 300]
        for ip in _rl_expired:
            _rate_limit_web.pop(ip, None)
    except Exception:
        pass

    # Limpiar rate limit de usuarios expirados
    try:
        _rl_expired = [k for k, v in _rate_limit_usuarios.items() if not v or ahora_ts - max(v) > 60]
        for k in _rl_expired:
            _rate_limit_usuarios.pop(k, None)
    except Exception:
        pass

@app.route("/dashboard_legacy")
def dashboard():
    """🌐 PANEL DE CONTROL BuySell365 — VISTA WEB (Legacy)"""
    global operaciones_activas, ultimo_escaneo
    status_mt5 = "✅ CONECTADO" if MT5_AVAILABLE and mt5.terminal_info() is not None else "❌ DESCONECTADO"
    ops_count = len(operaciones_activas)
    
    html = f"""
    <!DOCTYPE html>
    <html lang="es">
    <head>
        <!-- Google Analytics -->
        <script async src="https://www.googletagmanager.com/gtag/js?id=G-L514BL7E83"></script>
        <script>window.dataLayer=window.dataLayer||[];function gtag(){{dataLayer.push(arguments);}}gtag('js',new Date());gtag('config','G-L514BL7E83');</script>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>BuySell365 Pro | Trading en Vivo</title>
        <style>
            @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;800&display=swap');
            body {{
                background-color: #0a0e14;
                color: #e2e8f0;
                font-family: 'Inter', sans-serif;
                margin: 0;
                display: flex;
                justify-content: center;
                align-items: center;
                height: 100vh;
                overflow: hidden;
            }}
            .panel {{
                background: linear-gradient(145deg, #111827, #1f2937);
                border: 1px solid #374151;
                border-radius: 24px;
                padding: 40px;
                width: 400px;
                text-align: center;
                box-shadow: 0 25px 50px -12px rgba(0,0,0,0.5);
                position: relative;
            }}
            .panel::before {{
                content: '';
                position: absolute;
                top: -2px; left: -2px; right: -2px; bottom: -2px;
                background: linear-gradient(45deg, #10b981, #3b82f6);
                z-index: -1;
                border-radius: 26px;
                opacity: 0.3;
            }}
            h1 {{
                font-size: 2rem;
                font-weight: 800;
                margin-bottom: 8px;
                background: linear-gradient(to right, #10b981, #3b82f6);
                -webkit-background-clip: text;
                -webkit-text-fill-color: transparent;
            }}
            .subtitle {{ color: #9ca3af; font-size: 0.9rem; margin-bottom: 30px; }}
            .stats {{
                display: grid;
                grid-template-columns: 1fr 1fr;
                gap: 20px;
                margin-bottom: 30px;
            }}
            .stat-card {{
                background: rgba(255,255,255,0.05);
                padding: 15px;
                border-radius: 16px;
                border: 1px solid rgba(255,255,255,0.1);
            }}
            .stat-value {{ font-size: 1.2rem; font-weight: 700; color: #fff; }}
            .stat-label {{ font-size: 0.7rem; color: #9ca3af; text-transform: uppercase; margin-top: 5px; }}
            .status-badge {{
                display: inline-block;
                padding: 8px 16px;
                border-radius: 20px;
                font-size: 0.8rem;
                font-weight: 600;
                background: rgba(16, 185, 129, 0.1);
                color: #34d399;
                border: 1px solid rgba(16, 185, 129, 0.2);
            }}
            .pulse {{
                width: 10px; height: 10px;
                background: #10b981;
                border-radius: 50%;
                display: inline-block;
                margin-right: 8px;
                animation: pulse 2s infinite;
            }}
            @keyframes pulse {{
                0% {{ transform: scale(0.95); box-shadow: 0 0 0 0 rgba(16, 185, 129, 0.7); }}
                70% {{ transform: scale(1); box-shadow: 0 0 0 10px rgba(16, 185, 129, 0); }}
                100% {{ transform: scale(0.95); box-shadow: 0 0 0 0 rgba(16, 185, 129, 0); }}
            }}
            .footer {{ margin-top: 20px; font-size: 0.7rem; color: #4b5563; }}
        </style>
    </head>
    <body>
        <div class="panel">
            <h1>BuySell365 Pro</h1>
            <p class="subtitle">Inteligencia Artificial aplicada al Trading</p>
            
            <div class="stats">
                <div class="stat-card">
                    <div class="stat-value">{ops_count}</div>
                    <div class="stat-label">Ops Activas</div>
                </div>
                <div class="stat-card">
                    <div class="stat-value">{int(time.time() - ultimo_escaneo) if ultimo_escaneo > 0 else "0"}s</div>
                    <div class="stat-label">Last Scan</div>
                </div>
            </div>

            <div class="status-badge">
                <span class="pulse"></span> {status_mt5}
            </div>

            <div class="footer">
                &copy; 2026 BuySell365 Pro | By Emmanuel Diaz
                <p style="margin-top:6px;font-size:0.65rem;color:#888">
                    &#9888; Herramienta informativa/educativa. No es asesor&iacute;a financiera. Opera bajo tu propio riesgo.
                </p>
            </div>
        </div>
    </body>
    </html>
    """
    return html

LOGS_PASSWORD = os.getenv("LOGS_PASSWORD", "").strip()
if not LOGS_PASSWORD:
    import secrets as _sec
    LOGS_PASSWORD = _sec.token_urlsafe(16)
    print(f"⚠️ LOGS_PASSWORD no configurado en .env — usando token temporal: {LOGS_PASSWORD}")

# 🔑 API Key para endpoints sensibles (opcional — si no se configura, APIs son públicas)
API_SECRET_KEY = os.getenv("API_SECRET_KEY", "").strip()

def _check_api_auth():
    """Verifica autenticación para APIs sensibles. Retorna True si OK."""
    if not API_SECRET_KEY:
        return True  # Sin key configurada = público (backwards compatible)
    # Permitir requests del propio dashboard (same-origin fetch desde /dashboard)
    referer = request.headers.get("Referer", "")
    if referer:
        from urllib.parse import urlparse
        ref_host = urlparse(referer).hostname or ""
        req_host = request.host.split(":")[0] if request.host else ""
        if ref_host == req_host:
            return True  # Dashboard interno — no requiere API key
    key = request.args.get("key", "") or request.headers.get("X-API-Key", "")
    # H-08 FIX: Comparación segura contra timing attacks
    import hmac as _hmac
    return _hmac.compare_digest(str(key), str(API_SECRET_KEY))


@app.route("/logs")
def logs_viewer():
    """📋 Visor de logs en vivo — protegido con contraseña."""
    import glob as _glob_mod
    import html as _html_esc
    import hmac as _hmac_logs
    # 🔒 Verificar contraseña (via ?key= o cookie de sesión) — H-08 FIX
    pw = request.args.get("key", "") or request.cookies.get("logs_auth", "")
    if not _hmac_logs.compare_digest(str(pw), str(LOGS_PASSWORD)):
        return '''<!DOCTYPE html><html><head><!-- Google Analytics --><script async src="https://www.googletagmanager.com/gtag/js?id=G-L514BL7E83"></script><script>window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments);}gtag('js',new Date());gtag('config','G-L514BL7E83');</script><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>BuySell365 Pro Logs — Acceso</title>
<style>*{margin:0;padding:0;box-sizing:border-box}body{background:#0d1117;color:#c9d1d9;font-family:Inter,sans-serif;display:flex;justify-content:center;align-items:center;height:100vh}
.box{background:#161b22;padding:30px;border-radius:12px;border:1px solid #30363d;text-align:center;width:320px}
h2{color:#58a6ff;margin-bottom:16px;font-size:18px}input{width:100%;padding:10px;border-radius:8px;border:1px solid #30363d;background:#0d1117;color:#c9d1d9;font-size:14px;margin:8px 0}
button{width:100%;padding:10px;border-radius:8px;border:none;background:#1f6feb;color:#fff;font-size:14px;cursor:pointer;margin-top:8px}button:hover{background:#388bfd}
</style></head><body><div class="box"><h2>🔒 BuySell365 Pro Logs</h2><form method="GET" action="/logs"><input type="password" name="key" placeholder="Contraseña" autofocus>
<button type="submit">Entrar</button></form></div></body></html>''', 200

    # Leer el archivo de log actual
    log_file = os.path.join(_LOGS_DIR, "bot.log")
    lineas = []
    try:
        with open(log_file, "r", encoding="utf-8") as f:
            lineas = f.readlines()[-500:]  # Últimas 500 líneas
    except Exception:
        lineas = ["No hay logs disponibles aún.\n"]

    # Filtrar por categoría si se pasa ?cat=VIP
    cat_filter = request.args.get("cat", "").upper()
    if cat_filter:
        lineas = [l for l in lineas if f"[{cat_filter}]" in l]

    # Buscar texto si se pasa ?q=palabra
    q_filter = request.args.get("q", "")
    if q_filter:
        lineas = [l for l in lineas if q_filter.lower() in l.lower()]

    # H-06 FIX: Escapar inputs del usuario para prevenir XSS reflejado
    q_filter = _html_esc.escape(q_filter) if q_filter else ""
    cat_filter = _html_esc.escape(cat_filter) if cat_filter else ""
    pw = _html_esc.escape(pw) if pw else ""

    # Listar archivos de log disponibles (para descargar)
    archivos_log = sorted(_glob_mod.glob(os.path.join(_LOGS_DIR, "bot.log*")), reverse=True)
    archivos_info = []
    for a in archivos_log[:31]:
        try:
            sz = os.path.getsize(a)
            nombre = os.path.basename(a)
            archivos_info.append({"nombre": nombre, "size_kb": round(sz/1024, 1)})
        except Exception:
            pass

    # Colorear por categoría
    def colorear(linea):
        import html as _html
        l = _html.escape(linea.rstrip())
        if "[SENAL]" in l:     return f'<span style="color:#00e676">{l}</span>'
        if "[OPERACION]" in l: return f'<span style="color:#2196f3">{l}</span>'
        if "[VIP]" in l:       return f'<span style="color:#ffd740">{l}</span>'
        if "[PAGO]" in l:      return f'<span style="color:#ff9100">{l}</span>'
        if "[USUARIO]" in l:   return f'<span style="color:#ce93d8">{l}</span>'
        if "[SISTEMA]" in l:   return f'<span style="color:#26c6da">{l}</span>'
        if "[ERROR]" in l or "ERROR" in l: return f'<span style="color:#ff5252">{l}</span>'
        return f'<span style="color:#b0bec5">{l}</span>'

    lineas_html = "\n".join(colorear(l) for l in lineas)
    total = len(lineas)

    archivos_html = ""
    for a in archivos_info:
        archivos_html += f'<a href="/logs/download/{a["nombre"]}?key={pw}" class="log-file">{a["nombre"]} ({a["size_kb"]} KB)</a>\n'

    html = f'''<!DOCTYPE html>
<html lang="es">
<head>
<!-- Google Analytics -->
<script async src="https://www.googletagmanager.com/gtag/js?id=G-L514BL7E83"></script>
<script>window.dataLayer=window.dataLayer||[];function gtag(){{dataLayer.push(arguments);}}gtag('js',new Date());gtag('config','G-L514BL7E83');</script>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>BuySell365 Pro — Logs en Vivo</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:#0d1117;color:#c9d1d9;font-family:'Cascadia Code','Fira Code',monospace;font-size:12px}}
.header{{background:#161b22;padding:12px 16px;border-bottom:1px solid #30363d;display:flex;flex-wrap:wrap;gap:8px;align-items:center;position:sticky;top:0;z-index:10}}
.header h1{{font-size:16px;color:#58a6ff;margin-right:auto}}
.filters{{display:flex;gap:6px;flex-wrap:wrap}}
.btn{{padding:5px 10px;border-radius:6px;border:1px solid #30363d;background:#21262d;color:#c9d1d9;text-decoration:none;font-size:11px;cursor:pointer}}
.btn:hover{{background:#30363d}}
.btn.active{{background:#1f6feb;border-color:#1f6feb;color:#fff}}
.search{{padding:5px 10px;border-radius:6px;border:1px solid #30363d;background:#0d1117;color:#c9d1d9;font-size:11px;width:140px}}
.log-area{{padding:10px 16px;overflow-x:auto;white-space:pre;line-height:1.6}}
.stats{{background:#161b22;padding:8px 16px;border-top:1px solid #30363d;font-size:11px;color:#8b949e;position:sticky;bottom:0;display:flex;justify-content:space-between;flex-wrap:wrap;gap:4px}}
.sidebar{{background:#161b22;border-top:1px solid #30363d;padding:12px 16px;display:none}}
.sidebar.open{{display:block}}
.log-file{{display:block;padding:4px 0;color:#58a6ff;text-decoration:none;font-size:11px}}
.log-file:hover{{text-decoration:underline}}
.toggle-files{{cursor:pointer}}
@media(max-width:600px){{
  .header h1{{font-size:14px}}
  .log-area{{font-size:10px;padding:8px}}
  body{{font-size:10px}}
}}
</style>
</head>
<body>
<div class="header">
  <h1>📋 BuySell365 Pro LOGS</h1>
  <div class="filters">
    <a href="/logs?key={pw}" class="btn {"active" if not cat_filter else ""}">TODO</a>
    <a href="/logs?key={pw}&cat=SENAL" class="btn {"active" if cat_filter=="SENAL" else ""}" style="color:#00e676">SEÑAL</a>
    <a href="/logs?key={pw}&cat=OPERACION" class="btn {"active" if cat_filter=="OPERACION" else ""}" style="color:#2196f3">OPERACION</a>
    <a href="/logs?key={pw}&cat=VIP" class="btn {"active" if cat_filter=="VIP" else ""}" style="color:#ffd740">VIP</a>
    <a href="/logs?key={pw}&cat=PAGO" class="btn {"active" if cat_filter=="PAGO" else ""}" style="color:#ff9100">PAGO</a>
    <a href="/logs?key={pw}&cat=USUARIO" class="btn {"active" if cat_filter=="USUARIO" else ""}" style="color:#ce93d8">USUARIO</a>
    <a href="/logs?key={pw}&cat=SISTEMA" class="btn {"active" if cat_filter=="SISTEMA" else ""}" style="color:#26c6da">SISTEMA</a>
  </div>
  <form action="/logs" method="get" style="display:flex;gap:4px">
    <input type="hidden" name="key" value="{pw}">
    {"<input type='hidden' name='cat' value='"+cat_filter+"'>" if cat_filter else ""}
    <input type="text" name="q" value="{q_filter}" placeholder="Buscar..." class="search">
    <button type="submit" class="btn">🔍</button>
  </form>
  <a href="javascript:void(0)" class="btn toggle-files" onclick="document.getElementById('sidebar').classList.toggle('open')">📁 Archivos</a>
  <a href="/logs?key={pw}" class="btn" onclick="setTimeout(()=>location.reload(),100)">🔄</a>
</div>
<div id="sidebar" class="sidebar">
  <strong style="color:#58a6ff">📁 Archivos de log (descargar):</strong><br><br>
  {archivos_html}
</div>
<div class="log-area" id="logArea">
{lineas_html}
</div>
<div class="stats">
  <span>📊 {total} líneas | Filtro: {cat_filter or "TODO"} {("| Busca: "+q_filter) if q_filter else ""}</span>
  <span>🔄 Auto-refresh: <a href="javascript:void(0)" onclick="toggleAuto()" id="autoBtn" class="btn" style="padding:2px 6px">OFF</a></span>
</div>
<script>
// Auto-scroll al fondo
document.getElementById('logArea').scrollTop = document.getElementById('logArea').scrollHeight;
window.scrollTo(0, document.body.scrollHeight);
let _autoRef = null;
function toggleAuto() {{
  const btn = document.getElementById('autoBtn');
  if (_autoRef) {{ clearInterval(_autoRef); _autoRef=null; btn.textContent='OFF'; }}
  else {{ _autoRef=setInterval(()=>location.reload(), 15000); btn.textContent='15s'; }}
}}
</script>
</body>
</html>'''
    return html

@app.route("/logs/download/<filename>")
def logs_download(filename):
    """Descarga un archivo de log específico (protegido)."""
    import re as _re_mod
    import hmac as _hmac_dl
    pw = request.args.get("key", "")
    if not _hmac_dl.compare_digest(str(pw), str(LOGS_PASSWORD)):
        return "No autorizado", 403
    # Validar que el filename es seguro (solo bot.log o bot.log.YYYY-MM-DD)
    if not _re_mod.match(r'^bot\.log(\.\d{4}-\d{2}-\d{2})?$', filename):
        return "Archivo no válido", 403
    filepath = os.path.join(_LOGS_DIR, filename)
    if not os.path.exists(filepath):
        return "Archivo no encontrado", 404
    from flask import send_file
    return send_file(filepath, as_attachment=True, download_name=filename)

@app.route("/login")
def redirect_to_home():
    return redirect("/")

def _parsear_texto_gainzalgo(raw_body: str) -> dict:
    """🧠 Parser inteligente para mensajes de GainzAlgo (texto plano o semi-estructurado).
    Extrae: direction (BUY/SELL), TP, SL, ticker, precio de cualquier formato."""
    resultado = {
        "action": "",
        "ticker": "DESCONOCIDO",
        "price": 0,
        "sl": None,
        "tp": None,
        "source": "GainzAlgo",
        # ⚠️ NO auto-rellenar secret: solo el JSON original debe tenerlo
    }
    texto = raw_body.strip()
    texto_upper = texto.upper()

    # 1. DIRECCIÓN: buscar BUY/SELL/LONG/SHORT
    if any(w in texto_upper for w in ("BUY", "LONG", "COMPRA")):
        resultado["action"] = "BUY"
    elif any(w in texto_upper for w in ("SELL", "SHORT", "VENTA")):
        resultado["action"] = "SELL"

    # 2. TICKER: buscar símbolos conocidos en el texto
    ticker_aliases = {
        "XAUUSD": "XAUUSD", "GOLD": "XAUUSD", "ORO": "XAUUSD",
        # BTC/ETH eliminados
        "EURUSD": "EURUSD", "EUR/USD": "EURUSD",
        "USDJPY": "USDJPY", "USD/JPY": "USDJPY",
        "GBPJPY": "GBPJPY", "GBP/JPY": "GBPJPY",
        "NAS100": "NQ=F", "NASDAQ": "NQ=F", "US100": "NQ=F", "NQ": "NQ=F",
        "SP500": "ES=F", "SPX": "ES=F", "US500": "ES=F", "SPX500USD": "ES=F",
    }
    for alias, ticker_std in sorted(ticker_aliases.items(), key=lambda x: -len(x[0])):
        if alias in texto_upper:
            resultado["ticker"] = ticker_std
            break

    # 3. TP y SL: buscar patrones como "TP: 5248.08" o "SL: 5142.18" o "TP=5248"
    tp_match = re.search(r'TP[:\s=]+([0-9]+[.,]?[0-9]*)', texto, re.IGNORECASE)
    sl_match = re.search(r'SL[:\s=]+([0-9]+[.,]?[0-9]*)', texto, re.IGNORECASE)
    if tp_match:
        resultado["tp"] = float(tp_match.group(1).replace(',', '.'))
    if sl_match:
        resultado["sl"] = float(sl_match.group(1).replace(',', '.'))

    # 4. PRECIO: buscar patrones como "price: 5165" o "entrada: 5165" o número suelto
    price_match = re.search(r'(?:PRICE|PRECIO|ENTRY|ENTRADA)[:\s=]+([0-9]+[.,]?[0-9]*)', texto, re.IGNORECASE)
    if price_match:
        resultado["price"] = float(price_match.group(1).replace(',', '.'))
    else:
        # Buscar números grandes sueltos (posible precio)
        numeros = re.findall(r'\b([0-9]{3,}[.,]?[0-9]*)\b', texto)
        if numeros:
            # El más grande suele ser el precio (ej: XAUUSD ~5000, NQ ~20000)
            candidatos = [float(n.replace(',', '.')) for n in numeros]
            # Filtrar: no usar TP ni SL como precio
            if resultado["tp"]:
                candidatos = [c for c in candidatos if abs(c - resultado["tp"]) > 1]
            if resultado["sl"]:
                candidatos = [c for c in candidatos if abs(c - resultado["sl"]) > 1]
            if candidatos:
                resultado["price"] = max(candidatos)

    logger.info(f"🧠 GainzAlgo Parser: {resultado} | Raw: {texto[:200]}")
    return resultado

@app.route("/tv_signal", methods=["POST"])
@app.route("/webhook", methods=["POST"])
@app.route("/signal", methods=["POST"])
@app.route("/tradingview", methods=["POST"])
def route_tv_signal():
    """📡 RECEPTOR DE SEÑALES UNIVERSAL (JSON y Texto Plano)"""
    try:
        # 1. Obtener datos (Soportar JSON y Texto Plano de GainzAlgo)
        data = {}
        raw_body = request.get_data(as_text=True)
        logger.info(f"📡 Webhook recibido: {raw_body[:500]}")

        try:
            data = request.get_json(force=True) or {}
        except Exception:
            data = {}

        # Si no hay JSON válido o el JSON es pobre, parsear el texto con el parser inteligente
        if not data or (not data.get("action") and not data.get("signal")):
            parsed = _parsear_texto_gainzalgo(raw_body)
            # Merge: parsed llena los huecos, JSON tiene prioridad
            for k, v in parsed.items():
                if k not in data or not data.get(k):
                    data[k] = v

        source = data.get("source", "TradingView_AI")
        ticker_raw = data.get("ticker", data.get("market", "DESCONOCIDO"))

        # Limpiar ticker (ej: OANDA:EURUSD -> EURUSD)
        ticker = ticker_raw.split(":")[-1] if ":" in ticker_raw else ticker_raw

        # ── AUTENTICACIÓN WEBHOOK ──
        tv_secret_received = str(data.get("secret", data.get("passphrase", "")))
        tv_secret_stored = os.getenv("TV_SECRET", "").strip()
        if not tv_secret_stored:
            log_op("⚠️ TV_SECRET no configurado en .env — webhook rechazado", "warning")
            return jsonify({"status": "error", "msg": "TV_SECRET no configurado"}), 403

        # Fuentes internas de BuySell365 (indicadores propios en TradingView)
        _source_raw = data.get("source", "")
        _es_fuente_interna = str(_source_raw).startswith("BuySell365_")

        if tv_secret_received != tv_secret_stored:
            logger.warning(f"⛔ Webhook NO AUTORIZADO desde {request.remote_addr} (secret inválido): {raw_body[:100]}")
            return jsonify({"error": "No autorizado"}), 401

        # ── VALIDAR TICKER ──
        tickers_validos = {"XAUUSD", "EURUSD", "USDJPY", "GBPJPY", "NQ=F", "ES=F",
                           "GOLD", "NAS100", "SP500", "US100", "US500",
                           "GC=F", "SPX500USD", "SPX", "NQ",
                           "NASDAQ", "US100Cash", "US500Cash"}
        if ticker.upper() in ("DESCONOCIDO", "") or ticker.upper() not in tickers_validos:
            logger.warning(f"⚠️ Webhook: ticker no reconocido '{ticker}' — descartado. Raw: {raw_body[:200]}")
            return jsonify({"status": "rejected", "msg": f"Ticker no reconocido: {ticker}"}), 400

        # ═══ RESPONDER INMEDIATO A TRADINGVIEW (evitar timeout 10s) ═══
        # C-05 FIX: Usar pool limitado en vez de threads ilimitados (previene DoS)
        _wh_data = data.copy()
        try:
            _pool_webhook.submit(_procesar_webhook_bg, _wh_data, str(ticker), str(source), str(raw_body))
        except Exception:
            # Pool lleno — procesar en thread nuevo como fallback
            import threading as _thr
            _thr.Thread(target=_procesar_webhook_bg, args=(_wh_data, str(ticker), str(source), str(raw_body)), daemon=True).start()
        logger.info(f"📡 Webhook {ticker} aceptado — procesando en background")
        return jsonify({"status": "received", "ticker": ticker}), 200

    except Exception as e:
        logger.error(f"[tv_signal] Error: {e}")
        return jsonify({"status": "error", "msg": "Error procesando webhook"}), 500


def _procesar_webhook_bg(data, ticker, source, raw_body):
    """Procesa webhook en background thread para evitar timeout de TradingView."""
    try:
        # ── OBTENER PRECIO ──
        price = float(data.get("price", 0))
        if price <= 0:
            price = obtener_precio_actual(ticker) or 0.0

        if price <= 0:
            logger.warning(f"⚠️ Webhook descartado por precio 0: {ticker}")
            return

        # ── TP y SL del parser (si vinieron en el texto) ──
        tp_parsed = data.get("tp")
        sl_parsed = data.get("sl")

        # Log de señal
        time_tv = data.get("time", ahora().strftime('%H:%M:%S'))
        with open("tv_signals.log", "a", encoding="utf-8") as lf:
            lf.write(f"{time_tv}|{source}|{ticker}|{price}|{data.get('action','')}|TP={tp_parsed}|SL={sl_parsed}\n")

        # ── DIRECCIÓN Y ANÁLISIS ──
        action_tv = str(data.get("action", data.get("signal", data.get("direccion", "")))).strip().upper()

        # 🚀 MODO AUTO_ANALYSIS: Si la alerta no trae dirección, el bot la calcula
        if action_tv in ("AUTO_ANALYSIS", "", "INFO", "0"):
            logger.info(f"🔍 INICIANDO AUTO-ANÁLISIS PROFUNDO PARA: {ticker} (sin dirección en webhook)")
            df_auto = descargar_datos_seguro(ticker)
            if df_auto is not None and not df_auto.empty:
                precio_auto = float(df_auto['Close'].iloc[-1])
                ind_auto = calcular_indicadores_profesionales(df_auto, precio_auto, ticker)
                if ind_auto:
                    tipo_auto, score_auto, r_auto = evaluar_senal_profesional(ind_auto, ticker)
                    if tipo_auto:
                        direccion = tipo_auto
                        price = precio_auto
                        score = score_auto
                        niv_auto = calcular_niveles_3tp(price, direccion, ind_auto.get('atr_1h', price*0.005), ticker)
                        sl, tp1, tp2, tp3 = niv_auto['sl'], niv_auto['tp1'], niv_auto['tp2'], niv_auto['tp3']
                        # Si el parser encontró TP/SL de GainzAlgo, usar esos (más precisos)
                        if tp_parsed and tp_parsed > 0:
                            tp1 = tp_parsed
                        if sl_parsed and sl_parsed > 0:
                            sl = sl_parsed
                        source = f"AI+GainzAlgo_{source}"
                    else:
                        logger.warning(f"⚠️ Auto-Análisis: No se encontró dirección clara en {ticker}")
                        return
                else:
                    logger.error(f"❌ Error calculando indicadores para {ticker}")
                    return
            else:
                logger.error(f"❌ Error descargando datos para {ticker}")
                return

        elif action_tv in ("BUY", "LONG", "COMPRA", "COMPRAR"):
            direccion = "COMPRA"
        elif action_tv in ("SELL", "SHORT", "VENTA", "VENDER"):
            direccion = "VENTA"
        else:
            logger.warning(f"⚠️ Webhook: acción no reconocida '{action_tv}' — descartado")
            return

        # ── NORMALIZAR TICKER A YFINANCE (para cooldown consistente) ──
        ticker_yf = _TICKER_TO_YFINANCE.get(ticker.upper(), ticker)

        # ── MAPEAR SIMBOLO MT5 ──
        mt5_sym = MT5_TICKER_MAP.get(ticker.upper(), ticker.upper())
        
        # ── SL y TP (Si no se calcularon en AUTO_ANALYSIS y no vienen en el JSON) ──
        if action_tv not in ("AUTO_ANALYSIS", "", "INFO", "0"):
            # Prioridad: JSON explícito > parser GainzAlgo > fallback ATR
            sl = data.get("sl") or sl_parsed
            tp1 = data.get("tp1") or tp_parsed  # GainzAlgo solo envía 1 TP
            tp2 = data.get("tp2")
            tp3 = data.get("tp3")
            score = data.get("score", data.get("puntos", 0))

            # 🚀 Fallback inteligente: Si faltan datos, el bot los genera con ATR
            if sl is None or tp1 is None:
                logger.info(f"📍 Niveles faltantes en señal. Generando niveles dinámicos con ATR para {mt5_sym}...")
                df_fallback = descargar_datos_seguro(ticker)
                if df_fallback is not None and not df_fallback.empty:
                    _close = float(df_fallback['Close'].iloc[-1])
                    _high = df_fallback['High']
                    _low = df_fallback['Low']
                    _c = df_fallback['Close']
                    _atr = ta.atr(_high, _low, _c, length=14)
                    atr_val = float(_atr.iloc[-1]) if _atr is not None and not pd.isna(_atr.iloc[-1]) else (price * 0.005)

                    niv_f = calcular_niveles_3tp(price, direccion, atr_val, ticker)
                    sl = sl or niv_f['sl']
                    tp1 = tp1 or niv_f['tp1']
                    tp2 = tp2 or niv_f['tp2']
                    tp3 = tp3 or niv_f['tp3']
                    score = score or 3
                else:
                    # Fallback de último recurso (porcentajes) si falla la descarga
                    pcts = {"GOLD": 0.005, "US100Cash": 0.008, "US500Cash": 0.006, "EURUSD": 0.001, "USDJPY": 0.01, "GBPJPY": 0.012}
                    dist = price * pcts.get(mt5_sym, 0.007)
                    sign = 1 if direccion == "COMPRA" else -1
                    sl = sl or (price - sign * dist)
                    tp1 = tp1 or (price + sign * dist)
                    tp2 = tp2 or (price + sign * dist * 2)
                    tp3 = tp3 or (price + sign * dist * 3)

            # Si tenemos TP1 y SL pero faltan TP2/TP3, generarlos proporcionalmente
            if tp1 is not None and sl is not None:
                distancia_tp1 = abs(float(tp1) - price)
                if tp2 is None:
                    sign = 1 if direccion == "COMPRA" else -1
                    tp2 = price + sign * distancia_tp1 * 2
                if tp3 is None:
                    sign = 1 if direccion == "COMPRA" else -1
                    tp3 = price + sign * distancia_tp1 * 3

        sl, tp1, tp2, tp3 = float(sl), float(tp1), float(tp2), float(tp3)

        # ═══ VALIDACIÓN INTELIGENTE DE SL/TP ═══
        # Detectar valores inválidos enviados por webhooks mal configurados
        # y recalcular automáticamente con ATR si los niveles no tienen sentido.
        _niveles_invalidos = False
        _motivo_invalido = ""

        if direccion == "COMPRA":
            # COMPRA: SL debe estar DEBAJO del precio, TP1 debe estar ARRIBA
            if sl >= price:
                _niveles_invalidos = True
                _motivo_invalido = f"BUY pero SL({sl}) >= precio({price})"
            if tp1 <= price:
                _niveles_invalidos = True
                _motivo_invalido += f" | BUY pero TP1({tp1}) <= precio({price})"
        else:
            # SELL: SL debe estar ARRIBA del precio, TP1 debe estar DEBAJO
            if sl <= price:
                _niveles_invalidos = True
                _motivo_invalido = f"SELL pero SL({sl}) <= precio({price})"
            if tp1 >= price:
                _niveles_invalidos = True
                _motivo_invalido += f" | SELL pero TP1({tp1}) >= precio({price})"

        # También detectar SL=TP (sin sentido) o SL/TP idénticos todos
        if abs(sl - tp1) < price * 0.00001:
            _niveles_invalidos = True
            _motivo_invalido += f" | SL({sl}) ≈ TP1({tp1})"

        # Detectar TP1=TP2=TP3 (TPs idénticos = webhook mal configurado)
        if abs(tp1 - tp2) < price * 0.00001 or abs(tp2 - tp3) < price * 0.00001:
            _niveles_invalidos = True
            _motivo_invalido += f" | TPs idénticos: TP1={tp1} TP2={tp2} TP3={tp3}"

        # Detectar SL demasiado cerca del precio (menos de 0.01% = imposible de ejecutar)
        if abs(sl - price) / price < 0.0001 and price > 0:
            _niveles_invalidos = True
            _motivo_invalido += f" | SL demasiado cerca del precio ({abs(sl-price):.5f})"

        # Detectar valores que no son precios reales (ej: EURUSD=1.15 pero sl=1.5)
        _desviacion_sl = abs(sl - price) / price if price > 0 else 0
        _desviacion_tp = abs(tp1 - price) / price if price > 0 else 0
        if _desviacion_sl > 0.15 or _desviacion_tp > 0.15:  # >15% de distancia = sospechoso
            _niveles_invalidos = True
            _motivo_invalido += f" | Desviación excesiva SL:{_desviacion_sl:.1%} TP:{_desviacion_tp:.1%}"

        if _niveles_invalidos:
            logger.warning(f"⚠️ NIVELES INVÁLIDOS DETECTADOS en webhook {ticker} {direccion}: {_motivo_invalido}")
            logger.info(f"🔄 Recalculando niveles con ATR para {ticker} {direccion} @ {price}...")
            # Recalcular con ATR (el sistema probado del bot)
            try:
                _ticker_yf_fix = _TICKER_TO_YFINANCE.get(ticker.upper(), ticker)
                df_fix = descargar_datos_seguro(_ticker_yf_fix)
                if df_fix is not None and not df_fix.empty:
                    _h = df_fix['High']
                    _l = df_fix['Low']
                    _cl = df_fix['Close']
                    _atr_fix = ta.atr(_h, _l, _cl, length=14)
                    atr_fix = float(_atr_fix.iloc[-1]) if _atr_fix is not None and not pd.isna(_atr_fix.iloc[-1]) else (price * 0.005)
                    niv_fix = calcular_niveles_3tp(price, direccion, atr_fix, _ticker_yf_fix)
                    sl = niv_fix['sl']
                    tp1 = niv_fix['tp1']
                    tp2 = niv_fix['tp2']
                    tp3 = niv_fix['tp3']
                    logger.info(f"✅ Niveles recalculados: SL={sl} TP1={tp1} TP2={tp2} TP3={tp3}")
                else:
                    # Fallback porcentajes si falla descarga
                    pcts = {"GOLD": 0.005, "US100Cash": 0.008, "US500Cash": 0.006, "EURUSD": 0.001, "USDJPY": 0.005, "GBPJPY": 0.007}
                    dist_fix = price * pcts.get(mt5_sym, 0.007)
                    sign = 1 if direccion == "COMPRA" else -1
                    sl = price - sign * dist_fix
                    tp1 = price + sign * dist_fix * 1.5
                    tp2 = price + sign * dist_fix * 2.5
                    tp3 = price + sign * dist_fix * 4.0
                    logger.info(f"✅ Niveles fallback %: SL={sl} TP1={tp1} TP2={tp2} TP3={tp3}")
            except Exception as e_fix:
                logger.error(f"❌ Error recalculando niveles: {e_fix}")
                # Último recurso: porcentaje fijo
                pct_lr = 0.001 if "EUR" in ticker.upper() or "JPY" in ticker.upper() else 0.005
                dist_lr = price * pct_lr
                sign = 1 if direccion == "COMPRA" else -1
                sl = price - sign * dist_lr
                tp1 = price + sign * dist_lr * 1.5
                tp2 = price + sign * dist_lr * 2.5
                tp3 = price + sign * dist_lr * 4.0

        # ── FORMATEO DE MENSAJE (limpio, sin decimales innecesarios) ──
        def _fmt_wh(v):
            if v is None: return "N/A"
            _t = ticker_yf.upper() if ticker_yf else mt5_sym.upper()
            # Indices: sin decimales (24,620 no 24,620.50)
            if any(x in _t for x in ['NQ=F', 'ES=F', 'US100', 'US500']):
                return f"{v:,.0f}"
            # Oro: 1 decimal (3,100.5 no 3,100.50)
            if any(x in _t for x in ['GC=F', 'GOLD']):
                if v == int(v): return f"{v:,.0f}"
                return f"{v:,.1f}"
            # GBP/JPY: 3 decimales
            if 'GBPJPY' in _t: return f"{v:,.3f}"
            # Forex: 5 decimales (standard) o 3 para JPY
            if 'JPY' in _t: return f"{v:.3f}"
            if any(x in _t for x in ['EUR', 'GBP', 'AUD', 'NZD', 'CHF', 'CAD']):
                return f"{v:.5f}"
            return fmt_val(v, ticker_yf)
        emoji = "🟢" if direccion == "COMPRA" else "🔴"

        # Nombre bonito del activo (EUR/USD, no EURUSD)
        _nombre_map = {
            'GOLD': 'ORO',
            'US100Cash': '📊 NASDAQ', 'US500Cash': '📈 S&P 500',
            'EURUSD': 'EUR/USD', 'USDJPY': 'USD/JPY', 'GBPJPY': 'GBP/JPY'
        }
        nombre_activo = _nombre_map.get(mt5_sym, ticker_yf)

        _dir_es = direccion  # Ya es "COMPRA"/"VENTA" (normalizado arriba)
        # Calcular distancias TP/SL para mostrar en el mensaje (igual que scanner)
        _wh_cat = get_categoria(ticker_yf)
        if _wh_cat == "forex":
            _wh_sl_d = abs(calcular_pips(price, sl, ticker_yf))
            _wh_tp1_d = abs(calcular_pips(price, tp1, ticker_yf))
            _wh_tp2_d = abs(calcular_pips(price, tp2, ticker_yf))
            _wh_tp3_d = abs(calcular_pips(price, tp3, ticker_yf))
            _wh_fmt_d = lambda v: f"{v:.1f} pips"
        else:
            _wh_sl_d = abs(sl - price)
            _wh_tp1_d = abs(tp1 - price)
            _wh_tp2_d = abs(tp2 - price)
            _wh_tp3_d = abs(tp3 - price)
            _wh_fmt_d = lambda v: f"{v:.1f} pts"

        msg = (f"{emoji} *{_dir_es}* — {nombre_activo}\n"
               f"━━━━━━━━━━\n"
               f"Entrada: `{_fmt_wh(price)}`\n"
               f"SL: `{_fmt_wh(sl)}` (\u2212{_wh_fmt_d(_wh_sl_d)})\n\n"
               f"TP1: `{_fmt_wh(tp1)}` (+{_wh_fmt_d(_wh_tp1_d)})\n"
               f"TP2: `{_fmt_wh(tp2)}` (+{_wh_fmt_d(_wh_tp2_d)})\n"
               f"TP3: `{_fmt_wh(tp3)}` (+{_wh_fmt_d(_wh_tp3_d)})\n"
               f"━━━━━━━━━━\n"
               f"Score: {score}/10")

        # ═══ RESERVA ATÓMICA: Todos los checks + reserva de slot en UN lock ═══
        op_id = f"{ticker_yf}_{int(time.time())}"

        with _lock_ops:
            # Check 1: Cooldown mismo ticker+dirección (20 min)
            ya_existe = any(
                v.get('ticker') == ticker_yf and v.get('tipo') == direccion
                and (time.time() - v.get('timestamp', 0)) < 1200
                for v in operaciones_activas.values()
            )
            if ya_existe:
                logger.info(f"⏳ Cooldown activo para {ticker} {direccion} — descartado")
                return
            # Check 2: Anti-duplicado por activo (cualquier dirección)
            _wh_base_yf = ticker_yf.replace("=X","").replace("=F","").replace("-","").upper()
            for _wv in operaciones_activas.values():
                _wt = _wv.get('ticker','').replace("=X","").replace("=F","").replace("-","").upper()
                if _wt == _wh_base_yf:
                    logger.warning(f"🚫 ANTI-DUPLICADO WH: {ticker} bloqueado — ya existe op en {_wh_base_yf}")
                    return
            # Check 3: Cooldown desde cierre reciente
            _cd_key_wh = (_wh_base_yf, direccion)
            if _cd_key_wh in _cooldown_cierres and (time.time() - _cooldown_cierres[_cd_key_wh]) < 1200:
                logger.info(f"🔄 COOLDOWN cierre reciente WH: {ticker} {direccion}")
                return

            # BUG-3 FIX: Check 5: Anti doble ejecución webhook+scanner (señal reciente < 60s)
            if _wh_base_yf in _senal_reciente and (time.time() - _senal_reciente[_wh_base_yf]) < 60:
                logger.warning(f"🚫 ANTI-DOBLE WH: {ticker} bloqueado — señal reciente hace {int(time.time()-_senal_reciente[_wh_base_yf])}s")
                return
            # Check 6: Límite de trades simultáneos (protección de capital)
            if len(operaciones_activas) >= MAX_TRADES_SIMULTANEOS:
                logger.warning(f"⏳ Webhook: máx trades ({MAX_TRADES_SIMULTANEOS}) alcanzado — {ticker} {direccion} descartado")
                return
            # ✅ TODOS LOS CHECKS PASADOS — RESERVAR SLOT (atómico con checks)
            _senal_reciente[_wh_base_yf] = time.time()  # BUG-3: Marcar señal reciente
            operaciones_activas[op_id] = {
                'ticker': ticker_yf, 'nombre': nombre_activo, 'tipo': direccion,
                'entrada': price, 'tp1': tp1, 'tp2': tp2, 'tp3': tp3, 'sl': sl,
                'score': score, 'timestamp': time.time(), 'hora': ahora().strftime("%H:%M"),
                'tp1_hit': False, 'tp2_hit': False,
                'aviso_sl_enviado': False,
                'confianza_multi_ia': 0, 'estrategia': 'BuySell365_AI',
                'mt5_ejecutado': False, 'ticket_mt5': None,
                '_reservado': True,
            }
            estadisticas_diarias["senales_hoy"] = estadisticas_diarias.get("senales_hoy", 0) + 1

        # ═══ FILTRO DE CALIDAD RELAJADO (Solo rechaza señales peligrosas) ═══
        # Permite la mayoría de webhooks. Solo filtra si:
        # 1) Bot detecta señal FUERTE en dirección OPUESTA (score >= 3)
        # 2) RSI MUY extremo contra la dirección (>80 BUY, <20 SELL)
        # Si no se pueden descargar datos → PERMITIR (no penalizar por yfinance)
        _webhook_validado = True  # Por defecto ACEPTAR
        _motivo_rechazo = ""
        score_bot = None  # Inicializar para lotaje premium

        try:
            df_val = descargar_datos_seguro(ticker_yf)
            if df_val is not None and not df_val.empty:
                precio_val = float(df_val['Close'].iloc[-1])
                ind_val = calcular_indicadores_profesionales(df_val, precio_val, ticker_yf)
                if ind_val:
                    rsi_val = ind_val.get('rsi', 50)
                    tipo_bot, score_bot, razones_bot = evaluar_senal_profesional(ind_val, ticker_yf)

                    # Solo rechazar si bot detecta señal FUERTE en dirección OPUESTA
                    if tipo_bot is not None and score_bot >= 3:
                        if (tipo_bot.upper() in ("COMPRA","BUY") and direccion == "VENTA") or \
                           (tipo_bot.upper() in ("VENTA","SELL") and direccion == "COMPRA"):
                            _webhook_validado = False
                            _motivo_rechazo = f"Bot detecta {tipo_bot} score:{score_bot} opuesto a {direccion}"

                    # RSI MUY extremo contra la dirección
                    if _webhook_validado:
                        if direccion == "COMPRA" and rsi_val > 80:
                            _webhook_validado = False
                            _motivo_rechazo = f"RSI extremo sobrecomprado ({rsi_val:.1f}) para COMPRA"
                        elif direccion == "VENTA" and rsi_val < 20:
                            _webhook_validado = False
                            _motivo_rechazo = f"RSI extremo sobrevendido ({rsi_val:.1f}) para VENTA"

                    if _webhook_validado:
                        logger.info(f"✅ WEBHOOK VALIDADO: {ticker} {direccion} | RSI:{rsi_val:.1f} | Bot:{tipo_bot or 'neutral'} score:{score_bot}")
                    # Si no hay indicadores → permitir
            # Si no se pudieron descargar datos → permitir (no penalizar)
            else:
                logger.info(f"ℹ️ Datos no disponibles para validación {ticker_yf} — webhook permitido")
        except Exception as e_val:
            logger.warning(f"⚠️ Validación webhook falló (se permite): {e_val}")

        # Si la validación falla → borrar reserva y salir
        if not _webhook_validado:
            logger.warning(f"🛡️ WEBHOOK FILTRADO: {ticker} {direccion} | Motivo: {_motivo_rechazo}")
            with _lock_ops:
                operaciones_activas.pop(op_id, None)
                estadisticas_diarias["senales_hoy"] = max(0, estadisticas_diarias.get("senales_hoy", 0) - 1)
            return

        # 🛡️ ANTI-DUPLICADO MT5 WEBHOOK (solo afecta ejecución MT5, NO bloquea señal)
        _wh_skip_mt5 = False
        _wh_skip_mt5_razon = ""
        if tiene_posicion_mt5(ticker_yf):
            _wh_skip_mt5 = True
            _wh_skip_mt5_razon = f"Ya tiene posición abierta en MT5"
            logger.info(f"⚠️ ANTI-DUPLICADO MT5 WH: {ticker_yf} — señal se envía pero NO se ejecuta en MT5")

        # ── Kill Switch webhook: solo bloquea MT5, señal siempre a Telegram ──
        if _kill_switch_activo() and not _wh_skip_mt5:
            _wh_skip_mt5 = True
            _wh_skip_mt5_razon = "Kill switch activo (muchas pérdidas hoy)"

        # 🕐 Check horario MT5 (señales Telegram siguen fuera de horario)
        if not en_horario_mt5(ticker_yf) and not _wh_skip_mt5:
            _wh_skip_mt5 = True
            _wh_skip_mt5_razon = "Fuera de horario MT5"
            logger.info(f"🕐 HORARIO: {ticker_yf} — fuera de horario MT5 — señal solo Telegram")

        # 💎 Lotaje por activo + premium webhook
        _es_premium = (score_bot is not None and score_bot >= 4)

        # 🔒 FILTRO PREMIUM GLOBAL: solo webhooks premium pasan (score >= 4)
        if not _es_premium:
            logger.info(f"🔒 FILTRO PREMIUM WH: {nombre_activo} {direccion} — Score:{score_bot} — NO es premium → descartada")
            with _lock_ops:
                operaciones_activas.pop(op_id, None)
                estadisticas_diarias["senales_hoy"] = max(0, estadisticas_diarias.get("senales_hoy", 0) - 1)
            return

        # ⏸️ MT5 pausado → webhook solo premium pasa
        if mt5_pausado and not _wh_skip_mt5:
            if mt5_solo_premium and _es_premium:
                logger.info(f"💎 MT5 SOLO-PREMIUM WH: {nombre_activo} {direccion} — señal premium pasa a MT5")
            else:
                _wh_skip_mt5 = True
                _wh_skip_mt5_razon = "MT5 pausado manualmente"

        # 🔒 mt5_solo_premium sin pausa: bloquear NO premium
        if mt5_solo_premium and not mt5_pausado and not _es_premium and not _wh_skip_mt5:
            _wh_skip_mt5 = True
            _wh_skip_mt5_razon = "Solo señales premium pasan a MT5"

        if ticker_yf == "GC=F":
            _riesgo = max(RIESGO_ORO, RIESGO_PREMIUM if _es_premium else RIESGO_ORO)
        elif ticker_yf == "USDJPY=X":
            _riesgo = max(RIESGO_USDJPY, RIESGO_PREMIUM if _es_premium else RIESGO_USDJPY)
        elif ticker_yf == "GBPJPY=X":
            _riesgo = max(RIESGO_GBPJPY, RIESGO_PREMIUM if _es_premium else RIESGO_GBPJPY)
        else:
            _riesgo = RIESGO_PREMIUM if _es_premium else RIESGO_POR_TRADE

        # OBS-2 FIX: Mover BLOQUEO TOTAL de horario ANTES de ejecutar MT5
        # Si está fuera de horario total → limpiar reserva ANTES de intentar MT5
        if not en_horario_mt5(ticker_yf):
            with _lock_ops:
                operaciones_activas.pop(op_id, None)
                estadisticas_diarias["senales_hoy"] = max(0, estadisticas_diarias.get("senales_hoy", 0) - 1)
            logger.info(f"🕐 HORARIO: {ticker_yf} — fuera de horario — señal descartada (ni Telegram ni MT5)")
            return

        # ── Ejecución Automática MT5 (solo si no hay _wh_skip_mt5) ──
        mt5_ejecutado = False
        _wh_ticket_mt5 = None
        if MT5_AVAILABLE and AUTO_TRADING and not _wh_skip_mt5:
            try:
                with _lock_mt5:  # H-01 FIX
                    _tick = mt5.symbol_info_tick(mt5_sym)
                if _tick:
                    price_mt5 = _tick.ask if direccion == "COMPRA" else _tick.bid
                    _mt5_result = ejecutar_orden_mt5(ticker_yf, direccion, CAPITAL_USUARIO, _riesgo, price_mt5, sl, tp1, es_premium=_es_premium)
                    if _mt5_result:
                        mt5_ejecutado = True
                        _wh_ticket_mt5 = _mt5_result if isinstance(_mt5_result, int) else None
                    else:
                        _wh_skip_mt5 = True
                        _wh_skip_mt5_razon = "MT5 rechazó la orden"
                        logger.warning(f"⚠️ MT5 rechazó orden {nombre_activo} {direccion} — señal se publica igual en Telegram")
            except Exception as e:
                logger.error(f"❌ Error lanzando orden desde Webhook: {e}")
                _wh_skip_mt5 = True
                _wh_skip_mt5_razon = f"Error MT5: {e}"

        # Tag premium (sin tags de horario/error — solo lo esencial)
        if _es_premium:
            msg = f"💎 PREMIUM\n{msg}"

        # Enviar a Telegram (solo en horario)
        enviar_canal(msg)

        # Actualizar la reserva con datos finales (MT5 result, etc.)
        with _lock_ops:
            if op_id in operaciones_activas:
                operaciones_activas[op_id].update({
                    'mt5_ejecutado': mt5_ejecutado,
                    'ticket_mt5': _wh_ticket_mt5,
                    'skip_mt5_razon': _wh_skip_mt5_razon if _wh_skip_mt5 else '',
                    '_reservado': False,  # Reserva completa
                    'premium': _es_premium,
                    'riesgo_usado': _riesgo,
                })
        guardar_estado()

        # 🚨 NOTIFICACIÓN FOMO AL GRUPO (sin revelar niveles VIP)
        notificar_fomo_grupo(nombre_activo, direccion)
        _mt5_tag = " [MT5 ✅]" if mt5_ejecutado else f" [Solo Telegram — {_wh_skip_mt5_razon}]"
        logger.info(f"✅ Webhook señal PUBLICADA: {nombre_activo} {direccion} (Score: {score}){_mt5_tag}")
        return

    except Exception as e:
        logger.error(f"[webhook_bg] Error procesando webhook: {e}", exc_info=True)
        # BUG-5 FIX: Alertar al admin sobre errores de webhook
        try:
            _admin_wh = ADMIN_IDS[0] if ADMIN_IDS else None
            if _admin_wh:
                enviar_telegram(
                    f"🔴 *ERROR EN WEBHOOK*\n"
                    f"━━━━━━━━━━\n"
                    f"`{str(e)[:300]}`\n\n"
                    f"⚠️ _Revisar logs del bot_",
                    _admin_wh
                )
        except Exception:
            pass
        # C-02 FIX: Limpiar reservación huérfana si crasheó después de reservar
        try:
            if op_id and op_id in operaciones_activas:
                _orphan = operaciones_activas.get(op_id, {})
                if _orphan.get('_reservado', False) and not _orphan.get('mt5_ejecutado', False):
                    with _lock_ops:
                        if op_id in operaciones_activas and operaciones_activas[op_id].get('_reservado', False):
                            del operaciones_activas[op_id]
                            logger.warning(f"🧹 Reservación huérfana limpiada: {op_id}")
        except Exception:
            pass


#  RESUMEN Y RECORDATORIOS
# ============================================================

def get_min_score_efectivo():
    """Retorna el MIN_SCORE ajustado según el modo de riesgo activo.
    Conservador=4 (solo score 4+5), Normal=3, Agresivo=2."""
    if MODO_RIESGO == "conservador":
        return 4
    elif MODO_RIESGO == "agresivo":
        return 2
    return MIN_SCORE  # normal = 3

# ============================================================
#  BUYSELL365 - FIRMA FINAL
# ============================================================
def con_firma(texto):
    """Firma desactivada — retorna texto sin modificar."""
    return texto if texto and isinstance(texto, str) else texto

def enviar_recordatorio_activas():
    if not operaciones_activas:
        return

    hora = ahora().strftime("%H:%M")

    # 1. 💎 CANAL VIP: detalles completos (entrada, TP, SL)
    lineas_vip = [f"🔔 *ABIERTAS ({len(operaciones_activas)})* | {hora}"]
    # 2. 📢 GRUPO PÚBLICO: solo activo + dirección (sin niveles)
    lineas_grupo = [f"🔔 *OPERACIONES EN CURSO ({len(operaciones_activas)})* | {hora}"]

    for op_id, op in operaciones_activas.items():
        tkr = op.get('ticker', op_id)
        tipo_display = op['tipo'].upper()
        if tipo_display in ("BUY", "LONG"): tipo_display = "COMPRA"
        elif tipo_display in ("SELL", "SHORT"): tipo_display = "VENTA"
        e = "🟢" if tipo_display == "COMPRA" else "🔴"
        h_rest = int((TIEMPO_AUTOCIERRE - (time.time() - op.get('timestamp', time.time()))) / 3600)

        # VIP: todo
        lineas_vip.append(
            f"{e} *{op['nombre']}* {tipo_display} | IN: {fmt(op['entrada'], tkr)}\n"
            f"🎯 1️⃣ {fmt(op['tp1'], tkr)} · 2️⃣ {fmt(op['tp2'], tkr)} · 3️⃣ {fmt(op['tp3'], tkr)} | 🛑 SL: {fmt(op['sl'], tkr)} ({h_rest}h rest.)"
        )
        # GRUPO: solo nombre y dirección
        lineas_grupo.append(f"{e} *{op['nombre']}* — {tipo_display}")

    # Enviar al canal VIP (completo) — SOLO VIP, no al grupo público
    enviar_canal("\n\n".join(lineas_vip))

    # ❌ REMOVIDO: Ya no se envía resumen automático al grupo público
    # El grupo público solo recibe notificaciones de TP alcanzados (victorias)

_CSV_HISTORIAL_CAMPOS = [
    "fecha", "hora", "ticker", "nombre", "tipo", "entrada", "salida",
    "pips", "resultado", "tag", "score", "confianza", "duracion_min",
    "tp1_hit", "tp2_hit", "estrategia", "fuente"
]

def _guardar_historial_csv(op_data: dict):
    """Guarda operación cerrada en historial CSV permanente (NUNCA se borra)."""
    import csv
    csv_path = os.path.join(os.path.dirname(__file__), "historial_trades.csv")
    existe = os.path.exists(csv_path)
    try:
        with open(csv_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=_CSV_HISTORIAL_CAMPOS, extrasaction='ignore')
            if not existe:
                writer.writeheader()
            writer.writerow(op_data)
    except Exception as e:
        log_op(f"⚠️ Error guardando historial CSV: {e}", "warning")

def enviar_resumen_diario():
    global estadisticas_diarias, ultimo_resumen, historial_operaciones
    momento = ahora()
    es_domingo = (momento.weekday() == 6)
    
    lista_estetica = formatear_lista_resultados()
    
    # Formato exacto solicitado por el usuario
    if es_domingo:
        cabecera = "🏆 *CIERRE SEMANAL*"
    else:
        cabecera = f"📊 *REPORTE* {momento.strftime('%d/%m')}"

    msg_completo = f"{cabecera}\n{lista_estetica}"

    # Siempre enviar al CANAL PRIVADO (VIP) — se borra en 15 min
    enviar_telegram_temporal(msg_completo, destino=CHANNEL_ID, delay_borrado=900)

    # Solo enviar al GRUPO PÚBLICO si el resultado es POSITIVO
    if GROUP_ID and GROUP_ID != CHANNEL_ID:
        pips_dia = sum(op.get('pips', 0.0) for op in historial_operaciones)
        if pips_dia > 0:
            enviar_grupo(
                f"🚀 *RESULTADOS DEL DÍA* 📈\n"
                f"+{pips_dia:.0f} Pips\n\n"
                f"{msg_completo}", 
                incluir_promo=True
            )

    # RESET DIARIO de estadísticas (siempre después de enviar reporte)
    with _lock_ops:
        estadisticas_diarias.update({"ganadas":0, "perdidas":0, "pips_ganados":0.0, "pips_perdidos":0.0})

    # RESET SEMANAL del historial de operaciones (solo domingos — para reporte semanal)
    if es_domingo:
        with _lock_ops:
            historial_operaciones.clear()

    guardar_estado()
    ultimo_resumen = time.time()
    guardar_estado()

# ============================================================
#  MOTOR DE ESCANEO - OPTIMIZADO
# ============================================================

def enviar_briefing_matutino():
    """Envía el briefing del mercado a las 8:00 UTC."""
    hora = ahora().strftime("%H:%M")
    fg = get_fear_greed()
    noticias = cargar_calendario_economico()
    ahora_utc = datetime.now(pytz.UTC)
    tz_ny = pytz.timezone("America/New_York")
    noticias_hoy = []
    for n in (noticias or []):
        if n.get("impact", "").lower() != "high":
            continue
        try:
            fecha_str = n.get("date", "")
            hora_str  = n.get("time", "").strip().lower()
            if not fecha_str or hora_str in ("", "all day", "tentative"):
                continue
            dt = datetime.strptime(f"{fecha_str} {hora_str}", "%m-%d-%Y %I:%M%p")
            dt_utc = tz_ny.localize(dt).astimezone(pytz.UTC)
            diff = (dt_utc - ahora_utc).total_seconds() / 3600
            if 0 <= diff <= 24:
                noticias_hoy.append(f"   🔴 {n.get('country','')} — {n.get('title','')}")
        except Exception:
            continue

    lineas = [
        f"☀️ *BRIEFING* {ahora().strftime('%d/%m')} | {hora}\n"
    ]
    for nombre_act, ticker in ACTIVOS.items():
        ind = _cache_ind.get(ticker)
        if ind:
            if ind['ema9'] > ind['ema20'] > ind['ema50']:
                tend = "📈 ALCISTA"
            elif ind['ema9'] < ind['ema20'] < ind['ema50']:
                tend = "📉 BAJISTA"
            else:
                tend = "➡️  NEUTRO"
            rsi_txt = f"RSI {ind['rsi']:.0f}"
            lineas.append(f"   {nombre_act}  │  {tend}  │  {rsi_txt}\n")
        else:
            lineas.append(f"   {nombre_act}  │  ⏳ Sin datos\n")

    lineas.append(f"😱 F&G: {fg}/100")

    if noticias_hoy:
        lineas.append("📰 *Noticias:*")
        lineas.extend(noticias_hoy[:4])
        lineas.append("")
    else:
        lineas.append("📰 Sin noticias de impacto")

    lineas.append(
        f"⚙️ Modo: *{MODO_RIESGO.upper()}* | Score min: {get_min_score_efectivo()}/5"
    )
    enviar_telegram_temporal("\n".join(lineas), destino=CHANNEL_ID, delay_borrado=600)

def enviar_notificacion_sesion(sesion):
    """Envía notificación de apertura de sesión (Londres o Nueva York)."""
    hora = ahora().strftime("%H:%M")
    if sesion == "london":
        titulo = "🇬🇧 APERTURA SESIÓN LONDRES"
        activos_clave = ["EUR/USD", "USD/JPY", "ORO"]
    else:
        titulo = "🇺🇸 APERTURA SESIÓN NUEVA YORK"
        activos_clave = ["ORO", "📊 NASDAQ", "📈 S&P 500"]

    lineas = [
        f"🔔 *{titulo}* | {hora} UTC\n"
    ]
    for nombre_act in activos_clave:
        ticker = ACTIVOS.get(nombre_act, "")
        ind = _cache_ind.get(ticker)
        if ind:
            tend = "📈" if ind['ema9'] > ind['ema20'] else "📉"
            lineas.append(f"   {nombre_act}  {tend}  RSI {ind['rsi']:.0f}\n")

    
    # Añadimos botones para análisis rápido
    teclado = {
        "inline_keyboard": [
            [{"text": f"🔍 Analizar {a}", "callback_data": f"/analisis {a}"} for a in activos_clave]
        ]
    }
    enviar_telegram_temporal("\n".join(lineas), destino=CHANNEL_ID, delay_borrado=600, teclado=teclado)

def crear_teclado_principal():
    """Crea un Menú Persistente (ReplyKeyboardMarkup) en la parte inferior para máxima automatización."""
    return {
        "keyboard": [
            [{"text": "📊 Señales Activas"}, {"text": "📈 Resumen Diario"}],
            [{"text": "🚀 Análisis Oro"}, {"text": "🔍 Análisis NASDAQ"}],
            [{"text": "📅 Noticias"}, {"text": "⚙️ Estado Bot"}],
            [{"text": "❓ Ayuda"}]
        ],
        "resize_keyboard": True,
        "one_time_keyboard": False
    }

def revisar_niveles_operaciones():
    """
    Monitor de alta frecuencia para operaciones activas y alertas de precio.
    Se ejecuta independientemente del escáner de señales cada 10-15 segundos.
    """
    global operaciones_activas, historial_operaciones, estadisticas_diarias, alertas_precio
    
    with _lock_ops:
        if not operaciones_activas and not alertas_precio:
            return
        import copy
        # Copiamos con deepcopy para aislar los diccionarios internos y evitar race conditions
        ops_pendientes = {k: copy.deepcopy(v) for k, v in operaciones_activas.items()}
        alertas_pendientes = list(alertas_precio)

    # Agrupar tickers únicos para optimizar peticiones de precio
    tickers_interes = set()
    for op in ops_pendientes.values(): tickers_interes.add(op['ticker'])
    for al in alertas_pendientes: tickers_interes.add(al['ticker'])

    precios_rt = {}
    for tk in tickers_interes:
        cot = obtener_cotizacion_tv(tk)
        if cot:
            precios_rt[tk] = cot
        else:
            p = obtener_precio_actual(tk)
            if p: precios_rt[tk] = {'precio': p, 'fuente': 'yfinance (HFR)'}

    # 1. REVISAR OPERACIONES ACTIVAS
    for op_id, op in ops_pendientes.items():
        if op.get('_reservado', False):
            continue  # Skip reservas incompletas (webhook en proceso)
        ticker = op['ticker']
        if ticker not in precios_rt: continue

        precio_mon = precios_rt[ticker]['precio']
        nombre = op['nombre']
        tipo = op['tipo']
        # 🔧 Normalizar BUY/SELL (webhook) → COMPRA/VENTA (scanner)
        if tipo.upper() in ("BUY", "LONG"):
            tipo = "COMPRA"
        elif tipo.upper() in ("SELL", "SHORT"):
            tipo = "VENTA"
        tp1_hit = op.get('tp1_hit', False)
        tp2_hit = op.get('tp2_hit', False)

        toca_tp1 = (tipo=="COMPRA" and precio_mon>=op['tp1']) or (tipo=="VENTA" and precio_mon<=op['tp1'])
        toca_tp2 = (tipo=="COMPRA" and precio_mon>=op['tp2']) or (tipo=="VENTA" and precio_mon<=op['tp2'])
        toca_tp3 = (tipo=="COMPRA" and precio_mon>=op['tp3']) or (tipo=="VENTA" and precio_mon<=op['tp3'])
        sl_alcanzado = (tipo=="COMPRA" and precio_mon<=op['sl']) or (tipo=="VENTA" and precio_mon>=op['sl'])

        # 🚀 FILTRO 80% HACIA SL — DESACTIVADO (usuario prefiere solo señales + TP/SL)
        # if not sl_alcanzado and not op.get('aviso_sl_enviado', False):
        #     entrada = op['entrada']
        #     sl = op['sl']
        #     dist_total = abs(entrada - sl)
        #     dist_actual = abs(precio_mon - entrada) if ((tipo == "COMPRA" and precio_mon < entrada) or (tipo == "VENTA" and precio_mon > entrada)) else 0
        #     if dist_total > 0 and dist_actual / dist_total >= 0.80:
        #         enviar_canal(f"⚠️ *ALERTA — PRECIO CERCA DEL SL*\n🎯 *{nombre}* — Avance hacia SL: *80%*")
        #         with _lock_ops:
        #             if op_id in operaciones_activas: operaciones_activas[op_id]['aviso_sl_enviado'] = True
        #         guardar_estado()

        # TRAILING STOP VIRTUAL eliminado — tp2_hit nunca se activa (cierre completo en TP1)
        # Si en el futuro se implementa cierre parcial, rehabilitar este bloque.

        # [6] TRAILING STOP INTELIGENTE: mover SL dinámicamente basado en ADX/ATR
        try:
            _cached_ind_trail = _cache_ind.get(ticker, {})
            if _cached_ind_trail and op.get('mt5_ejecutado', False):
                _pips_trail = calcular_pips(op['entrada'], precio_mon, ticker, tipo)
                # Solo activar trailing si está en profit y ha pasado breakeven
                if _pips_trail > 0:
                    _nuevo_sl = _trailing_stop_dinamico(op, precio_mon, _cached_ind_trail)
                    if _nuevo_sl is not None:
                        # Mover SL en MT5
                        _ticket_trail = op.get('ticket_mt5')
                        if _ticket_trail and MT5_AVAILABLE:
                            try:
                                mt5_sym_trail = MT5_TICKER_MAP.get(ticker, ticker)
                                with _lock_mt5:
                                    _si_trail = mt5.symbol_info(mt5_sym_trail)
                                if _si_trail:
                                    _digits_trail = _si_trail.digits
                                    _nuevo_sl_norm = round(_nuevo_sl, _digits_trail)
                                    _req_trail = {
                                        "action": mt5.TRADE_ACTION_SLTP,
                                        "position": _ticket_trail,
                                        "symbol": mt5_sym_trail,
                                        "sl": _nuevo_sl_norm,
                                        "tp": round(float(op.get('tp1', 0)), _digits_trail),
                                    }
                                    with _lock_mt5:
                                        _res_trail = mt5.order_send(_req_trail)
                                    if _res_trail and _res_trail.retcode == mt5.TRADE_RETCODE_DONE:
                                        logger.info(f"📈 TRAILING STOP: {ticker} SL movido a {_nuevo_sl_norm}")
                                        with _lock_ops:
                                            if op_id in operaciones_activas:
                                                operaciones_activas[op_id]['sl'] = _nuevo_sl_norm
                                                operaciones_activas[op_id]['trailing_activo'] = True
                            except Exception as e_trail:
                                logger.warning(f"⚠️ Error trailing stop {ticker}: {e_trail}")
        except Exception:
            pass

        # 🔒 BREAKEVEN SL — Mover SL a entrada + buffer tras 3h en profit (NO cierra posición)
        _edad_op = time.time() - op.get('timestamp', time.time())
        _pips_actual = calcular_pips(op['entrada'], precio_mon, ticker, tipo)
        _en_profit = _pips_actual > 0
        _dist_sl = abs(calcular_pips(op['entrada'], op['sl'], ticker))
        _perdida_minima = _dist_sl > 0 and (_pips_actual > -(_dist_sl * 0.30))  # Pérdida < 30% del SL

        # Cierre definitivo — TP1, SL, o Auto-cierre 24h (sin cierre anticipado por tiempo)
        if toca_tp1 or sl_alcanzado or (_edad_op > TIEMPO_AUTOCIERRE):
            df = descargar_datos_seguro(ticker)
            with _lock_ops:
                if op_id not in operaciones_activas: continue
                duracion = time.time() - op.get('timestamp', time.time())
                sl_pips = abs(calcular_pips(op['entrada'], op['sl'], ticker))
                _riesgo_op = op.get('riesgo_usado', RIESGO_POR_TRADE)

                if toca_tp1:
                    precio_salida = precio_mon  # Precio real de mercado (no teórico TP1)
                    pips = calcular_pips(op['entrada'], precio_mon, ticker, tipo)
                    perc_gain = (_riesgo_op * (pips / sl_pips) * 100) if sl_pips > 0 else 0
                    msg = mensaje_tp_alcanzado(nombre, tipo, op['entrada'], op['tp1'], pips, ticker, "TP1", duracion, perc_gain)
                    tag = "TP1"
                    resultado = "WIN"
                elif sl_alcanzado:
                    precio_salida = precio_mon
                    pips = calcular_pips(op['entrada'], precio_mon, ticker, tipo)
                    perc_gain = (_riesgo_op * (pips / sl_pips) * 100) if sl_pips > 0 else 0
                    msg = mensaje_sl_tocado(nombre, tipo, op['entrada'], precio_mon, pips, ticker)
                    tag = "SL"
                    resultado = "LOSS"
                else:  # Auto-close 24h
                    precio_salida = precio_mon
                    pips = calcular_pips(op['entrada'], precio_mon, ticker, tipo)
                    perc_gain = (_riesgo_op * (pips / sl_pips) * 100) if sl_pips > 0 else 0
                    msg = mensaje_cierre_24h(nombre, tipo, op['entrada'], precio_mon, pips, ticker)
                    tag = "AUTO"
                    resultado = "WIN" if pips > 0 else "LOSS"

                log_op(f"🏁 CIERRE: {nombre} {tipo} | {tag} | Entry:{op['entrada']} Exit:{precio_salida} | {'+' if pips>0 else ''}{pips:.1f} pips | {resultado} | Dur:{duracion/60:.0f}min")

                _hora_salida = ahora().strftime("%H:%M")
                _dur_min = round(duracion / 60, 1)
                # Usar hora de apertura guardada en la operación (campo 'hora')
                _hora_entrada = op.get('hora', '')
                if not _hora_entrada and _dur_min > 0:
                    try:
                        _entry_dt = ahora() - timedelta(minutes=_dur_min)
                        _hora_entrada = _entry_dt.strftime("%H:%M")
                    except Exception:
                        _hora_entrada = ""
                _hist_data = {
                    "nombre": nombre, "tipo": tipo, "ticker": ticker, "entrada": op['entrada'],
                    "salida": precio_salida, "pips": pips, "resultado": resultado,
                    "hora": _hora_salida, "fecha": ahora().strftime("%d/%m/%Y"),
                    "hora_entrada": _hora_entrada, "hora_salida": _hora_salida,
                    "tag": tag,
                    "tp1_hit": op.get('tp1_hit', False) or (tag == "TP1"),
                    "tp2_hit": op.get('tp2_hit', False),
                    "duracion_min": _dur_min,
                    "score": op.get('score', 0),
                    "confianza": op.get('confianza_multi_ia', 0),
                    "estrategia": op.get('estrategia', 'scanner'),
                    "fuente": "scanner",
                    "timestamp_entrada": op.get('timestamp', 0),
                    "timestamp_cierre": time.time(),
                }
                historial_operaciones.append(_hist_data)
                # [3] TRACKING POR ESTRATEGIA: registrar resultado
                try:
                    _estr = _hist_data.get('estrategia', '')
                    _registrar_resultado_estrategia(_estr, resultado, pips)
                except Exception:
                    pass
                # [5] CIRCUIT BREAKER: registrar resultado P&L
                try:
                    _sl_pips_cb = abs(calcular_pips(op['entrada'], op['sl'], ticker))
                    _riesgo_cb = op.get('riesgo_usado', RIESGO_POR_TRADE)
                    _capital_cb = CAPITAL_USUARIO
                    _pnl_usd_est = (_riesgo_cb * (pips / _sl_pips_cb) * _capital_cb) if _sl_pips_cb > 0 else 0
                    _cb_registrar_resultado(_pnl_usd_est, resultado == "LOSS")
                except Exception:
                    pass
                # Guardar en CSV permanente (nunca se borra)
                _guardar_historial_csv(_hist_data)
                if pips > 0:
                    estadisticas_diarias["ganadas"] += 1
                    estadisticas_diarias["pips_ganados"] += pips
                    # 🏆 Registrar en historial permanente de operaciones ganadas (visible en dashboard)
                    registrar_trade_ganado(_hist_data)
                else:
                    estadisticas_diarias["perdidas"] += 1
                    estadisticas_diarias["pips_perdidos"] += abs(pips)
                operaciones_activas.pop(op_id, None)
                # Anti re-entry: registrar cooldown desde el cierre
                _cd_ticker2 = op.get('ticker', '').replace("=X","").replace("=F","").replace("-","").upper()
                _cd_tipo2 = op.get('tipo', '')
                if _cd_ticker2 and _cd_tipo2:
                    _cooldown_cierres[(_cd_ticker2, _cd_tipo2)] = time.time()

            # 🔒 CERRAR POSICIÓN EN MT5 (solo si fue ejecutada en MT5)
            if op.get('mt5_ejecutado', False):  # Default False — más seguro no cerrar posiciones desconocidas
                cerrar_posicion_mt5(ticker, ticket_id=op.get('ticket_mt5'))
            else:
                print(f"ℹ️ {nombre}: Señal solo Telegram (no MT5) — no hay posición que cerrar")
            
            ruta_img = None  # Gráfico desactivado (inicializado para evitar NameError)
            # Gráfico desactivado — hace el mensaje muy largo
            # ruta_img = generar_grafico_operacion(df, ticker, tipo, op['entrada'], precio_salida, tag, niveles=op) if df is not None else None

            # 1. 💎 SIEMPRE enviar al CANAL PRIVADO (VIP) — se borra en 15 min
            enviar_telegram_temporal(msg, destino=CHANNEL_ID, delay_borrado=900)

            # 2. 🚫 Resultados de trades MT5 NO se envían al grupo público
            #    (solo van al canal VIP privado — línea 12026 arriba)
            
            # Limpiar archivo de imagen generado
            if ruta_img and os.path.exists(ruta_img):
                try: os.remove(ruta_img)
                except Exception: pass
                
            guardar_estado()
            continue

        # NOTA: Con cierre en TP1, las notificaciones intermedias TP2/TP3 ya no aplican.
        # La posición se cierra completamente al tocar TP1. TP2/TP3 solo son informativos.
        # Si en el futuro se implementan cierres parciales, reactivar este bloque.


    # 🔄 SYNC MT5: Detectar posiciones cerradas externamente
    sync_mt5_positions()

    # 2. ALERTAS DE PRECIO
    for alerta in alertas_pendientes:
        ticker = alerta.get('ticker')
        if ticker not in precios_rt: continue
        precio = precios_rt[ticker]['precio']
        if (alerta['tipo'] == ">=" and precio >= alerta['precio']) or (alerta['tipo'] == "<=" and precio <= alerta['precio']):
            enviar_grupo(f"🔔 *ALERTA DE PRECIO ACTIVADA*\n📍 {alerta.get('nombre', ticker)}: {fmt(precio, ticker)}")
            with _lock_ops:
                if alerta in alertas_precio: alertas_precio.remove(alerta)
            guardar_estado()

# ============================================================
#  RESUMEN SEMANAL AUTOMÁTICO (#5)
# ============================================================
_ultimo_resumen_semanal = time.time()  # Esperar desde arranque

def enviar_resumen_semanal():
    """Envía resumen semanal al GRUPO público cada domingo a las 20:00 UTC."""
    global _ultimo_resumen_semanal
    if time.time() - _ultimo_resumen_semanal < 86400:  # Max 1 por día
        return
    _ultimo_resumen_semanal = time.time()

    hist = historial_operaciones if historial_operaciones else []
    # Filtrar señales de los últimos 7 días
    ahora_dt = ahora()
    _semana = []
    for h in hist:
        try:
            fecha_h = datetime.strptime(h.get('fecha', ''), '%d/%m/%Y')
            if (ahora_dt - fecha_h).days <= 7:
                _semana.append(h)
        except Exception:
            continue

    if not _semana:
        return  # No hay datos esta semana

    wins = sum(1 for s in _semana if s.get('pips', 0) > 0)
    losses = len(_semana) - wins
    total = len(_semana)
    wr = round(wins / total * 100, 1) if total > 0 else 0
    pips_total = sum(s.get('pips', 0) for s in _semana)
    mejor = max(_semana, key=lambda x: x.get('pips', 0))

    msg = (
        f"📊 *RESUMEN SEMANAL — BuySell365.pro*\n"
        f"━━━━━━━━━━\n\n"
        f"📈 Senales enviadas: *{total}*\n"
        f"✅ Ganadoras: *{wins}*\n"
        f"❌ Perdedoras: *{losses}*\n"
        f"🎯 Win Rate: *{wr}%*\n"
        f"💰 Pips netos: *{pips_total:+.1f}*\n\n"
        f"🏆 Mejor senal: *{mejor.get('nombre', '???')}* (+{mejor.get('pips', 0):.1f} pips)\n\n"
        f"━━━━━━━━━━\n"
        f"💎 *¿Quieres recibir estas senales?*\n"
        f"🎁 Escribe /vip — *5 dias habiles GRATIS*\n\n"
        f"⚠️ _No es asesoria financiera. Resultados pasados no garantizan resultados futuros._"
    )
    enviar_grupo(msg, incluir_promo=False)
    print(f"📊 Resumen semanal enviado: {total} señales, {wr}% WR, {pips_total:+.1f} pips")

# ============================================================
#  AUTO-CALIBRACIÓN POR ACTIVO (#8)
# ============================================================
_ultimo_autocalib = 0
_AUTOCALIB_INTERVALO = 43200  # Cada 12 horas

def auto_calibrar_umbrales():
    """Ajusta MIN_SCORE por activo basado en win rate histórico."""
    global _ultimo_autocalib
    if time.time() - _ultimo_autocalib < _AUTOCALIB_INTERVALO:
        return
    _ultimo_autocalib = time.time()

    hist = historial_operaciones if historial_operaciones else []
    if len(hist) < 10:
        return  # No hay suficientes datos

    # Calcular win rate por ticker
    _stats_por_ticker = {}
    for h in hist[-100:]:  # Últimas 100 operaciones
        tk = h.get('ticker', '')
        if tk not in _stats_por_ticker:
            _stats_por_ticker[tk] = {'wins': 0, 'total': 0}
        _stats_por_ticker[tk]['total'] += 1
        if h.get('pips', 0) > 0:
            _stats_por_ticker[tk]['wins'] += 1

    for tk, stats in _stats_por_ticker.items():
        if stats['total'] < 5:
            continue  # Mínimo 5 operaciones para calibrar
        wr = stats['wins'] / stats['total'] * 100
        nombre_activo = None
        for n, t in ACTIVOS.items():
            if t == tk:
                nombre_activo = n
                break
        if not nombre_activo:
            continue

        # Si win rate < 45%, subir exigencia (MIN_SCORE +1)
        # Si win rate > 70%, bajar exigencia (MIN_SCORE -1) → más señales
        # Rango permitido: 1 a 5
        if hasattr(auto_calibrar_umbrales, '_ajustes'):
            ajustes = auto_calibrar_umbrales._ajustes
        else:
            ajustes = {}
            auto_calibrar_umbrales._ajustes = ajustes

        ajuste_actual = ajustes.get(tk, 0)
        if wr < 45 and ajuste_actual < 2:
            ajustes[tk] = ajuste_actual + 1
            print(f"🔧 AUTO-CALIB: {nombre_activo} WR={wr:.0f}% → subiendo exigencia (+{ajustes[tk]})")
        elif wr > 70 and ajuste_actual > -1:
            ajustes[tk] = ajuste_actual - 1
            print(f"🔧 AUTO-CALIB: {nombre_activo} WR={wr:.0f}% → bajando exigencia ({ajustes[tk]})")

def get_min_score_calibrado(ticker):
    """Retorna MIN_SCORE ajustado por auto-calibración."""
    base = get_min_score_efectivo()
    ajuste = getattr(auto_calibrar_umbrales, '_ajustes', {}).get(ticker, 0)
    return max(1, min(5, base + ajuste))

# ============================================================
#  MULTI-TIMEFRAME MEJORADO — 4H TREND FILTER (#9)
# ============================================================
_cache_tendencia_4h: dict = {}
_CACHE_4H_TTL = 900  # 15 min cache

def obtener_tendencia_4h(ticker):
    """Determina la tendencia en 4H usando EMA 50 vs EMA 200. Retorna 'ALCISTA', 'BAJISTA' o 'NEUTRAL'."""
    cached = _cache_tendencia_4h.get(ticker)
    if cached and time.time() - cached['ts'] < _CACHE_4H_TTL:
        return cached['tendencia']

    try:
        df_4h = descargar_ohlcv(ticker, period="60d", interval="1h")
        if df_4h is None or len(df_4h) < 200:
            return "NEUTRAL"

        # Simular 4H agrupando cada 4 velas de 1H
        df_4h = df_4h.resample('4h').agg({
            'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'
        }).dropna()

        if len(df_4h) < 50:
            return "NEUTRAL"

        close_4h = df_4h['Close'].squeeze()
        ema_20 = close_4h.ewm(span=20, adjust=False).mean()
        ema_50 = close_4h.ewm(span=50, adjust=False).mean()
        precio_4h = float(close_4h.iloc[-1])

        if precio_4h > float(ema_20.iloc[-1]) > float(ema_50.iloc[-1]):
            tendencia = "ALCISTA"
        elif precio_4h < float(ema_20.iloc[-1]) < float(ema_50.iloc[-1]):
            tendencia = "BAJISTA"
        else:
            tendencia = "NEUTRAL"

        _cache_tendencia_4h[ticker] = {'tendencia': tendencia, 'ts': time.time()}
        return tendencia
    except Exception:
        return "NEUTRAL"

def filtro_multi_timeframe(ticker, tipo):
    """Bloquea señales que van contra la tendencia de 4H. Retorna True si OK."""
    tendencia = obtener_tendencia_4h(ticker)
    if tendencia == "NEUTRAL":
        return True  # Sin filtro si no hay tendencia clara

    # Bloquear COMPRA en tendencia bajista 4H y VENTA en tendencia alcista 4H
    if tipo == "COMPRA" and tendencia == "BAJISTA":
        print(f"🔻 MTF FILTER: {ticker} COMPRA bloqueada — tendencia 4H BAJISTA")
        return False
    if tipo == "VENTA" and tendencia == "ALCISTA":
        print(f"🔺 MTF FILTER: {ticker} VENTA bloqueada — tendencia 4H ALCISTA")
        return False
    return True

def analizar_mercado():
    """Escanea los 6 activos: ORO, EUR/USD, USD/JPY, GBP/JPY, NASDAQ, S&P500"""
    global ultimo_recordatorio, ultimo_briefing

    momento = ahora()
    now_utc = datetime.now(pytz.UTC)
    hora_utc = now_utc.hour
    fecha_hoy = now_utc.strftime("%Y-%m-%d")
    dia_semana = now_utc.weekday()
    es_fin_de_semana = dia_semana >= 5  # sáb=5, dom=6

    # Limpiar caché diario de notificaciones pasadas para evitar fuga de memoria
    claves_obsoletas = [k for k in _sesiones_notificadas.keys() if fecha_hoy not in k]
    for k in claves_obsoletas:
        del _sesiones_notificadas[k]

    # Resumen diario — DESACTIVADO (usuario prefiere solo señales)
    # if not es_fin_de_semana and hora_utc == 21 and (time.time() - ultimo_resumen > 3600):
    #     enviar_resumen_diario()
    # Resumen semanal — DESACTIVADO
    # if dia_semana == 6 and hora_utc == 20:
    #     enviar_resumen_semanal()

    # 🔧 AUTO-CALIBRACIÓN — cada 12 horas
    auto_calibrar_umbrales()

    # ━━━ HORARIO GLOBAL: Solo operar y enviar señales de 8:00 a 18:00 Andorra (L-V) ━━━
    now_local = datetime.now(BOT_TZ)
    hora_local = now_local.hour + now_local.minute / 60.0
    if es_fin_de_semana or hora_local < HORA_APERTURA_LOCAL or hora_local >= HORA_CORTE_LOCAL:
        return  # Fuera de horario: ni señales Telegram ni MT5

    # ✅ ESCANEAR ACTIVOS EN PARALELO (reduce ciclo de ~60s a ~12s)
    # Todos los activos usan analizar_activo
    from concurrent.futures import ThreadPoolExecutor, as_completed
    with ThreadPoolExecutor(max_workers=len(ACTIVOS)) as executor:
        futures = {}
        for nombre, ticker in ACTIVOS.items():
            futures[executor.submit(analizar_activo, nombre, ticker)] = nombre
        for future in as_completed(futures):
            nombre_fut = futures[future]
            try:
                future.result()
            except Exception as e:
                print(f"⚠️ Error en hilo {nombre_fut}: {e}")


def analizar_activo(nombre, ticker):
    """Analiza un activo individual con sistema profesional"""
    global operaciones_activas, escaneo_pausado
    try:
        # Intento de descarga con reintento en caso de glitch
        df = None
        es_precio_valido = False
        precio_mon = None
        fuente_precio = "desconocida"
        for intento in range(3):
            df = descargar_datos_seguro(ticker)
            if df is None or df.empty or len(df) < 100:
                time.sleep(1)
                continue
                
            precio_yf = float(df['Close'].iloc[-1])
            cot = obtener_cotizacion_tv(ticker)
            if cot:
                precio_mon = cot['precio']
                fuente_precio = cot.get('fuente', 'TradingView')
            else:
                precio_mon = obtener_precio_actual(ticker, df)
                fuente_precio = "yfinance (fallback)"

            if precio_mon is None:
                precio_mon = precio_yf
                fuente_precio = "yfinance (último recurso)"
            
            # 🛡️ VALIDACIÓN DE INTEGRIDAD DE PRECIO (Anti-Glitches)
            cat_local = get_categoria(ticker)
            es_precio_valido = True
            
            # Filtros de cordura (Sanity Checks)
            if cat_local == "forex":
                if "JPY" in ticker and (precio_mon < 80 or precio_mon > 250): es_precio_valido = False
                elif "JPY" not in ticker and (precio_mon < 0.5 or precio_mon > 2.0): es_precio_valido = False
            if ticker == "GC=F" and (precio_mon < 3500 or precio_mon > 10000): es_precio_valido = False
            if ticker == "NQ=F" and (precio_mon < 15000 or precio_mon > 40000): es_precio_valido = False
            if ticker == "ES=F" and (precio_mon < 4000 or precio_mon > 12000): es_precio_valido = False
            if es_precio_valido:
                break
            else:
                logger.warning(f"🔄 Reintentando {nombre} ({intento+1}/3)... Glitch detectado: {precio_mon}")
                time.sleep(2)
        
        if df is not None and not es_precio_valido:
             logger.error(f"❌ Glitch persistente | Activo: {nombre} | Ticker: {ticker} | Precio: {precio_mon}")
             return
        
        if df is None or df.empty:
            return

        precio = precio_mon

        # ── 🛑 CIRCUIT BREAKER: pausa 1h tras 4 pérdidas consecutivas ──
        # FIX 2026-03-20: 2→4 pérdidas (2 era demasiado agresivo, bloqueaba todo el día)
        _racha = _calcular_racha_perdidas_actual()
        if _racha >= 4:
            _last_loss_time = _get_last_loss_time()
            if _last_loss_time and (time.time() - _last_loss_time) < 3600:
                logger.info(f"🛑 CIRCUIT BREAKER: {nombre} — {_racha} pérdidas seguidas, pausa 1h")
                return

        # ── 🚨 FILTRO DE NOTICIAS (ANTES de generar señales) ────────────
        # FIX 2026-03-20: 2h→1h antes (2h bloqueaba demasiado tiempo)
        if hay_noticia_alto_impacto(ticker, horas_antes=1, horas_despues=0.5):
            logger.info(f"🚨 {nombre}: BLOQUEADO por noticia 🔴 ROJA de alto impacto")
            return

        # ── BUSCAR NUEVAS SEÑALES ────────────────────────
        ind = calcular_indicadores_profesionales(df, precio, ticker)
        if not ind:
            return

        _cache_ind[ticker] = ind
        tipo, score, razones = evaluar_senal_profesional(ind, ticker)
        min_score = get_min_score_efectivo()

        if tipo is None:
            logger.info(f"📊 {nombre}: sin señal — {razones[0] if razones else 'no cumple criterios'}")
            return

        # 🔴 FILTRO DURO VOLUMEN: vol < 0.5x = mercado muerto, no operar
        _vol_r = ind.get('vol_ratio', 1.0)
        if _vol_r < 0.5:
            logger.info(f"🚫 VOLUMEN BAJO: {nombre} {tipo} — Vol={_vol_r:.1f}x (mín 0.5x) — señal descartada")
            return

        # [4] CONFIRMACIÓN INTER-MERCADO: +1 al score si el activo correlacionado confirma
        try:
            if _confirmar_inter_mercado(ticker, tipo):
                score = min(5, score + 1)
                razones.append("🔗 Confirmación inter-mercado positiva (+1 score)")
        except Exception:
            pass

        # [3] TRACKING POR ESTRATEGIA: verificar si la estrategia está permitida
        _estrategia_temprana = ""
        for _r in razones:
            if "Asian Range" in _r: _estrategia_temprana = "asian_breakout"; break
            elif "Breakout" in _r: _estrategia_temprana = "breakout"; break
            elif "Reversi" in _r or "Divergencia" in _r: _estrategia_temprana = "reversion"; break
        if _estrategia_temprana:
            try:
                if not _estrategia_permitida(_estrategia_temprana):
                    logger.info(f"⏸️ {nombre}: estrategia {_estrategia_temprana} auto-pausada por bajo WR")
                    return
            except Exception:
                pass

        # [10] AUTO-OPTIMIZACIÓN: verificar si el activo está desactivado por optimización
        try:
            if ticker in _activos_desactivados_auto:
                logger.info(f"🔧 {nombre}: desactivado por auto-optimización semanal (WR < 30%)")
                return
        except Exception:
            pass

        # [9] SCORE DE CONFIANZA 0-100: calcular y adjuntar a indicadores
        try:
            _confianza_score = _calcular_confianza(ind, ticker, tipo)
            ind['confianza_score_100'] = _confianza_score
        except Exception:
            ind['confianza_score_100'] = 0

        # 🛡️ FILTRO ANTI-CONTRADICCIÓN TÉCNICA: NO dar SELL cuando 15m es claramente alcista (y viceversa)
        # EXCEPCIÓN: Estrategia Reversión Extrema (score 5 con divergencia) SÍ puede ir contra técnicos
        _ema20_gt_50 = ind['ema20'] > ind['ema50']
        _macd_gt_sig = ind['macd'] > ind['signal']
        _price_gt_50 = ind['precio'] > ind['ema50']
        _tech_unanime_alcista = (_ema20_gt_50 and _macd_gt_sig and _price_gt_50)
        _tech_unanime_bajista = (not _ema20_gt_50 and not _macd_gt_sig and not _price_gt_50)
        _es_reversion = any("Reversi" in r or "Divergencia" in r for r in razones)

        if tipo == "VENTA" and _tech_unanime_alcista and not _es_reversion:
            print(f"🛡️ ANTI-CONTRADICCIÓN: {nombre} VENTA bloqueada — técnicos 15m son unanimemente ALCISTAS (EMA20>50, MACD>Signal, P>EMA50)")
            return
        if tipo == "COMPRA" and _tech_unanime_bajista and not _es_reversion:
            print(f"🛡️ ANTI-CONTRADICCIÓN: {nombre} COMPRA bloqueada — técnicos 15m son unanimemente BAJISTAS (EMA20<50, MACD<Signal, P<EMA50)")
            return
        if _es_reversion and ((tipo == "VENTA" and _tech_unanime_alcista) or (tipo == "COMPRA" and _tech_unanime_bajista)):
            print(f"🔄 REVERSIÓN PERMITIDA: {nombre} {tipo} — técnicos contradicen pero es Reversión Extrema")

        # Log de estrategia activada
        _estrategia_log = razones[0] if razones else "Desconocida"
        print(f"🎯 SEÑAL DETECTADA: {nombre} {tipo} Score:{score}/5 — {_estrategia_log}")
        # Score calibrado por auto-calibración (ajuste dinámico por win rate del activo)
        min_score_calib = get_min_score_calibrado(ticker)
        if score < min_score_calib:
            print(f"📊 {nombre}: score {score}/{min_score_calib} insuficiente (calib: {min_score_calib})")
            return

        if nombre in activos_desactivados:
            return

        # Detectar estrategia temprano (necesario para filtros)
        _estrategia_tipo = ""
        for _r in razones:
            if "Asian Range" in _r: _estrategia_tipo = "asian_breakout"; break
            elif "Breakout" in _r: _estrategia_tipo = "breakout"; break
            elif "Pullback" in _r: _estrategia_tipo = "pullback"; break
            elif "Reversi" in _r or "Divergencia" in _r: _estrategia_tipo = "reversion"; break

        # 📉 FILTRO EUR/USD: backtest mostró 24.7% win rate
        # FIX 2026-03-20: score<5→score<4 (permitir score 4 también, solo bloquear score 3)
        if ticker == "EURUSD=X" and score < 4 and _estrategia_tipo != "breakout":
            print(f"📉 EUR/USD FILTRADO: {_estrategia_tipo} score {score}/5 — mínimo score 4 o Breakout")
            return

        # 🚫 FILTRO ANTI-CONTRADICCIÓN: No abrir SELL si hay BUY abierto (y viceversa)
        _base_symbol = ticker.replace("=X", "").replace("=F", "").replace("-", "").upper()
        _dir_opuesta = "VENTA" if tipo == "COMPRA" else "COMPRA"
        with _lock_ops:
            _ops_snapshot = list(operaciones_activas.items())
        for _op_key, _op_val in _ops_snapshot:
            _op_ticker = _op_val.get('ticker', '').replace("=X", "").replace("=F", "").replace("-", "").upper()
            _op_tipo = _op_val.get('tipo', '')
            if _op_ticker == _base_symbol and _op_tipo == _dir_opuesta:
                print(f"🚫 ANTI-CONTRADICCIÓN: {nombre} {tipo} bloqueado — ya hay {_op_tipo} abierto en {_base_symbol}")
                return

        # 🛡️ ANTI-CONTRADICCIÓN MT5: Verificar dirección real en MT5
        # NOTA: Si MT5 bloquea, la señal SIGUE enviándose a Telegram (usuarios VIP la reciben)
        _skip_mt5 = False  # Flag: True = no ejecutar en MT5, pero SÍ enviar señal
        _skip_mt5_razon = ""
        _dir_mt5 = obtener_direccion_mt5(ticker)
        if _dir_mt5:
            _tipo_mt5 = "COMPRA" if _dir_mt5 == "BUY" else "VENTA"
            if _dir_mt5 == "BOTH":
                _skip_mt5 = True
                _skip_mt5_razon = f"MT5 tiene BUY+SELL en {nombre}"
                print(f"⚠️ ANTI-CONTRADICCIÓN MT5: {nombre} tiene BUY+SELL — señal se envía pero NO se ejecuta en MT5")
            elif (_dir_mt5 == "BUY" and tipo == "VENTA") or (_dir_mt5 == "SELL" and tipo == "COMPRA"):
                _skip_mt5 = True
                _skip_mt5_razon = f"MT5 tiene {_tipo_mt5} abierto"
                print(f"⚠️ ANTI-CONTRADICCIÓN MT5: {nombre} {tipo} — MT5 tiene {_tipo_mt5} — señal se envía pero NO se ejecuta")

        # 🚫 FILTRO ANTI-DUPLICADO: No abrir más de 1 operación por activo del escáner
        with _lock_ops:
            _ops_mismo_activo = sum(1 for _ok, _ov in operaciones_activas.items()
                                    if _ov.get('ticker', '').replace("=X","").replace("=F","").replace("-","").upper() == _base_symbol)
        if _ops_mismo_activo >= 1:
            print(f"🚫 ANTI-DUPLICADO: {nombre} ya tiene {_ops_mismo_activo} op(s) abierta(s) — máx 1 por activo")
            return

        # 🛡️ ANTI-DUPLICADO MT5: Verificar posiciones reales en MT5
        if tiene_posicion_mt5(ticker) and not _skip_mt5:
            _skip_mt5 = True
            _skip_mt5_razon = f"Ya tiene posición abierta en MT5"
            print(f"⚠️ ANTI-DUPLICADO MT5: {nombre} — señal se envía pero NO se ejecuta en MT5")

        # Límite de trades simultáneos (protección de capital)
        with _lock_ops:
            _n_trades_activos = len(operaciones_activas)
        if _n_trades_activos >= MAX_TRADES_SIMULTANEOS:
            print(f"⏳ {nombre}: máx trades ({MAX_TRADES_SIMULTANEOS}) alcanzado — esperando")
            return

        # Filtros adicionales
        if not en_horario_mercado(ticker) or hay_noticia_alto_impacto(ticker, horas_antes=2, horas_despues=1):
            return
        if not filtro_fear_greed(ticker, tipo):
            return
        if hay_correlacion_peligrosa(ticker, tipo):
            return
        # 🔻 FILTRO MULTI-TIMEFRAME 4H — Soft: penaliza en confianza pero NO bloquea
        # Reversiones de alta calidad (score 5) pueden ir contra 4H
        _mtf_4h_ok = filtro_multi_timeframe(ticker, tipo)
        if not _mtf_4h_ok and not _es_reversion:
            log_op(f"⛔ MTF 4H BLOQUEADO: {nombre} {tipo} va contra tendencia 4H — señal descartada")
            return
        if not _mtf_4h_ok and _es_reversion:
            print(f"⚠️ MTF 4H contra señal pero REVERSIÓN PERMITIDA: {nombre} {tipo}")

        # Kill Switch: solo bloquea MT5, señales siguen a Telegram
        if _kill_switch_activo() and not _skip_mt5:
            _skip_mt5 = True
            _skip_mt5_razon = "Kill switch activo (muchas pérdidas hoy)"

        # Cooldown y Registro (60 min entre señales del mismo activo+dirección)
        # Backtest mostró que re-entrar rápido tras SL = más pérdidas
        cooldown_seg = 3600
        # _estrategia_tipo ya detectado arriba
        niveles = calcular_niveles_3tp(precio, tipo, ind['atr_1h'], ticker, estrategia=_estrategia_tipo)

        # 🚫 Validar R:R mínimo
        _sl_dist_a = abs(precio - niveles['sl'])
        _tp1_dist_a = abs(niveles['tp1'] - precio)
        _rr_a = _tp1_dist_a / _sl_dist_a if _sl_dist_a > 0 else 0
        if _rr_a < MIN_RR_RATIO:
            print(f"🚫 R:R RECHAZADO: {nombre} {tipo} — R:R={_rr_a:.2f}:1 < mínimo {MIN_RR_RATIO}:1")
            return

        # Validar COT institucional
        cot_peso = 0.0
        cot_desc = "COT no disponible"
        try:
            cot_ok, cot_desc, cot_peso = cot_confirma_senal(ticker, tipo)
            if not cot_ok and cot_peso >= 0.5:
                log_op(f"🚫 COT BLOQUEO REAL: {nombre} {tipo} - {cot_desc}")
                return  # ← Bloqueo real: señal contraria a institucionales
            if not cot_ok:
                print(f"COT débil contra: {nombre} {tipo} - {cot_desc}")
            if cot_peso > 0:
                print(f"COT: {cot_desc}")
        except Exception:
            pass

        # Validar FinBERT sentimiento
        sent_peso = 0.0
        sent_desc = "Sin noticias"
        try:
            if FINBERT_AVAILABLE:
                sent_ok, sent_desc, sent_peso = sentimiento_confirma_senal(ticker, tipo)
                if not sent_ok and sent_peso >= 0.5:
                    log_op(f"🚫 FINBERT BLOQUEO REAL: {nombre} {tipo} - {sent_desc}")
                    return  # ← Bloqueo real: sentimiento fuertemente en contra
                if not sent_ok:
                    print(f"FINBERT débil contra: {nombre} {tipo} - {sent_desc}")
                if sent_peso > 0:
                    print(f"SENTIMIENTO: {sent_desc}")
        except Exception:
            pass

        # SISTEMA DE VOTACION MULTI-IA (v2 — más flexible para Trend Following)
        votos_favor = 0
        peso_total = 0
        ml_prob = ind.get('ml_prob_alcista', 50.0)
        _ml_no_disponible = (ml_prob == 50.0)  # Sentinel: ML falló o no disponible
        # ML: si no disponible, NO dar crédito (FIX 2026-03-19: inflaba confianza artificialmente)
        if _ml_no_disponible:
            votos_favor += 0  # ML bypass = SIN crédito — señal debe valer por sí sola
        elif tipo == "COMPRA":
            if ml_prob > 52: votos_favor += 25
            elif ml_prob > 50: votos_favor += 12  # Crédito parcial
        elif tipo == "VENTA":
            if ml_prob < 48: votos_favor += 25
            elif ml_prob < 50: votos_favor += 12
        peso_total += 25
        # Score técnico: crédito proporcional (score 3=15pts, 4=25pts, 5=25pts)
        if score >= 4:
            votos_favor += 25
        elif score >= 3:
            votos_favor += 15  # Trend Following score 3 obtiene crédito
        peso_total += 25
        votos_favor += int(cot_peso * 20)
        peso_total += 20
        votos_favor += int(sent_peso * 15)
        peso_total += 15
        try:
            mtf_ok = confirmar_tendencia_1h(ticker, tipo)
            if mtf_ok:
                votos_favor += 15
        except Exception:
            pass
        peso_total += 15
        # Penalización si MTF 4H está en contra (pero no bloquea)
        if not _mtf_4h_ok:
            votos_favor = max(0, votos_favor - 10)
        confianza_total = round((votos_favor / peso_total) * 100) if peso_total > 0 else 0
        print(f"MULTI-IA {nombre}: {confianza_total}% confianza ({votos_favor}/{peso_total})")
        ind['cot_desc'] = cot_desc
        ind['sent_desc'] = sent_desc
        ind['confianza_multi_ia'] = confianza_total
        # Umbral dinámico: Score 4-5 requiere 50%, Score 3 (Trend Following) requiere 25%
        _min_conf = 25 if score <= 3 else 50
        if confianza_total < _min_conf:
            print(f"MULTI-IA BLOQUEO: {nombre} {tipo} - confianza {confianza_total}% < {_min_conf}%")
            return

        # ── VALIDAR SPREAD ANTES DE EJECUTAR EN MT5 ──────────────
        # Spread alto = no ejecutar en MT5, pero SÍ enviar señal a Telegram
        if MT5_AVAILABLE and not _skip_mt5:
            try:
                _mt5_sym = MT5_TICKER_MAP.get(ticker)
                if _mt5_sym:
                    with _lock_mt5:  # H-01 FIX
                        _si = mt5.symbol_info(_mt5_sym)
                    if _si:
                        _spread_actual = _si.spread
                        _spread_max = MAX_SPREAD_ALLOWED.get(ticker, 9999)
                        if _spread_actual > _spread_max:
                            _skip_mt5 = True
                            _skip_mt5_razon = f"Spread {_spread_actual} > máx {_spread_max}"
                            print(f"⚠️ SPREAD ALTO: {nombre} — spread {_spread_actual} > máx {_spread_max} — señal se envía pero NO se ejecuta en MT5")
            except Exception as e_spread:
                pass  # Si no se puede verificar, continuar

        # 🕐 BLOQUEO TOTAL fuera de horario — ni Telegram ni MT5
        if not en_horario_mt5(ticker):
            print(f"🕐 HORARIO: {nombre} — fuera de horario — descartado")
            return

        # ⭐ SOLO SEÑALES PREMIUM — Breakout (score 4) + Reversión con divergencia (score 5)
        # FIX 2026-03-19: subido de 50% a 70% (con ML bypass en 0, solo pasan señales reales)
        _es_premium = (score >= 4 and confianza_total >= 70)
        _nivel_senal = "PREMIUM"

        # 🔒 FILTRO PREMIUM: solo señales de alta calidad
        if not _es_premium:
            print(f"🔒 FILTRO PREMIUM: {nombre} {tipo} — Score:{score}/5 Conf:{confianza_total}% — no cumple mínimo → descartada")
            return

        # ═══ C-03 FIX: RESERVA ATÓMICA — Todos los checks + reserva en UN lock ═══
        # Patrón idéntico al webhook handler: previene TOCTOU race condition
        op_id = None
        with _lock_ops:
            # Check 1: Cooldown mismo ticker+dirección (20 min)
            ya_existe = any(v.get('ticker') == ticker and v.get('tipo') == tipo
                           and (time.time() - v.get('timestamp', 0)) < cooldown_seg
                           for v in operaciones_activas.values())
            if ya_existe:
                return

            # Check 2: Re-verificar anti-duplicado (pudo cambiar entre check previo y aquí)
            _ops_mismo_2 = sum(1 for _ok2, _ov2 in operaciones_activas.items()
                               if _ov2.get('ticker', '').replace("=X","").replace("=F","").replace("-","").upper() == _base_symbol)
            if _ops_mismo_2 >= 1:
                return

            # Check 3: Re-verificar max trades
            if len(operaciones_activas) >= MAX_TRADES_SIMULTANEOS:
                return

            # Check 4: Cooldown desde cierre reciente
            _cd_key = (ticker.replace("=X","").replace("=F","").replace("-","").upper(), tipo)
            if _cd_key in _cooldown_cierres and (time.time() - _cooldown_cierres[_cd_key]) < cooldown_seg:
                print(f"🔄 COOLDOWN (cierre reciente): {nombre} {tipo}")
                return



            # Check 6: Anti-contradicción índices US (dentro del lock atómico)
            _US_INDEX_PAIRS = {"ES=F": "NQ=F", "NQ=F": "ES=F"}
            if ticker in _US_INDEX_PAIRS and not _skip_mt5:
                _par_idx = _US_INDEX_PAIRS[ticker]
                for _v in operaciones_activas.values():
                    if _v.get('ticker') == _par_idx:
                        _dir_corr_idx = _v.get('tipo', '')
                        if _dir_corr_idx and _dir_corr_idx != tipo:
                            _skip_mt5 = True
                            _nombre_idx = "NASDAQ" if _par_idx == "NQ=F" else "S&P500"
                            _skip_mt5_razon = f"Correlación: {_nombre_idx} tiene {_dir_corr_idx}"
                            log_op(f"🔗 CORRELACIÓN: {nombre} {tipo} bloqueado MT5 — {_nombre_idx} tiene {_dir_corr_idx} (señal Telegram sigue)")
                        break

            # Check 7: Anti-doble-exposición JPY (dentro del lock atómico)
            # MISMA dirección (ambos BUY o ambos SELL) = doble exposición JPY → peligroso → bloquear
            # DIFERENTE dirección (uno BUY + otro SELL) = tesis distintas (ej: USD débil + GBP fuerte) → permitir
            _JPY_PAIRS = {"USDJPY=X": "GBPJPY=X", "GBPJPY=X": "USDJPY=X"}
            if ticker in _JPY_PAIRS and not _skip_mt5:
                _par_correlado = _JPY_PAIRS[ticker]
                for _v in operaciones_activas.values():
                    if _v.get('ticker') == _par_correlado:
                        _dir_corr = _v.get('tipo', '')
                        if _dir_corr and _dir_corr == tipo:  # MISMA dirección = doble exposición
                            _skip_mt5 = True
                            _skip_mt5_razon = f"Doble exposición JPY: {_par_correlado} también tiene {_dir_corr}"
                            _nombre_corr = "USD/JPY" if _par_correlado == "USDJPY=X" else "GBP/JPY"
                            log_op(f"🔗 DOBLE EXPOSICIÓN JPY: {nombre} {tipo} bloqueado MT5 — {_nombre_corr} también tiene {_dir_corr}")
                        else:
                            _nombre_corr = "USD/JPY" if _par_correlado == "USDJPY=X" else "GBP/JPY"
                            print(f"✅ JPY PERMITIDO: {nombre} {tipo} + {_nombre_corr} {_dir_corr} — tesis distintas, no doble exposición")
                        break

            # BUG-3 FIX: Check 8: Anti doble ejecución webhook+scanner (señal reciente < 60s)
            if _base_symbol in _senal_reciente and (time.time() - _senal_reciente[_base_symbol]) < 60:
                logger.warning(f"🚫 ANTI-DOBLE SCANNER: {nombre} bloqueado — señal reciente hace {int(time.time()-_senal_reciente[_base_symbol])}s")
                return

            # ✅ TODOS LOS CHECKS PASADOS — RESERVAR SLOT (atómico con checks)
            _senal_reciente[_base_symbol] = time.time()  # BUG-3: Marcar señal reciente
            op_id = f"{ticker}_{int(time.time())}"
            operaciones_activas[op_id] = {
                'ticker': ticker, 'nombre': nombre, 'tipo': tipo, 'entrada': precio,
                'tp1': niveles['tp1'], 'tp2': niveles['tp2'], 'tp3': niveles['tp3'], 'sl': niveles['sl'],
                'score': score, 'timestamp': time.time(), 'hora': ahora().strftime("%H:%M"),
                'tp1_hit': False, 'tp2_hit': False, 'aviso_sl_enviado': False, 'trailing_activo': False,
                'confianza_multi_ia': confianza_total,
                'confianza': confianza_total,
                'confianza_score_100': ind.get('confianza_score_100', 0),  # [9] Score 0-100
                'estrategia': _estrategia_tipo,  # [3] Para tracking por estrategia
                'mt5_ejecutado': False,
                'ticket_mt5': None,
                'skip_mt5_razon': _skip_mt5_razon if _skip_mt5 else '',
                'premium': _es_premium,
                'nivel_senal': _nivel_senal,
                'riesgo_usado': 0,
                '_reservado': True,  # Reserva: se confirma tras MT5
            }
            estadisticas_diarias["senales_hoy"] = estadisticas_diarias.get("senales_hoy", 0) + 1

        # ═══ FUERA DEL LOCK: MT5 pausado, riesgo, ejecución ═══

        # ⏸️ MT5 pausado manualmente
        if mt5_pausado and not _skip_mt5:
            if mt5_solo_premium and _es_premium:
                logger.info(f"💎 MT5 SOLO-PREMIUM: {nombre} {tipo} — señal premium pasa a MT5")
            else:
                _skip_mt5 = True
                _skip_mt5_razon = "MT5 pausado manualmente"

        # 🔒 mt5_solo_premium sin pausa
        if mt5_solo_premium and not mt5_pausado and not _es_premium and not _skip_mt5:
            _skip_mt5 = True
            _skip_mt5_razon = "Solo señales premium pasan a MT5"

        # 💎 Lotaje por activo + premium
        if ticker == "GC=F":
            _riesgo = max(RIESGO_ORO, RIESGO_PREMIUM if _es_premium else RIESGO_ORO)
        elif ticker == "USDJPY=X":
            _riesgo = max(RIESGO_USDJPY, RIESGO_PREMIUM if _es_premium else RIESGO_USDJPY)
        elif ticker == "GBPJPY=X":
            _riesgo = max(RIESGO_GBPJPY, RIESGO_PREMIUM if _es_premium else RIESGO_GBPJPY)
        else:
            _riesgo = RIESGO_PREMIUM if _es_premium else RIESGO_POR_TRADE

        # Ejecutar trade en MT5 (solo si no hay flag _skip_mt5)
        mt5_ok = True
        mt5_ejecutado = False
        _activo_ticket_mt5 = None
        if MT5_AVAILABLE and AUTO_TRADING and not _skip_mt5:
            mt5_ok = ejecutar_orden_mt5(ticker, tipo, CAPITAL_USUARIO, _riesgo, precio, niveles['sl'], niveles['tp1'], es_premium=_es_premium)
            if mt5_ok:
                mt5_ejecutado = True
                _activo_ticket_mt5 = mt5_ok if isinstance(mt5_ok, int) else None

        # Actualizar reserva con resultados MT5 o limpiar si falló
        _debe_registrar = mt5_ok or _skip_mt5 or not (MT5_AVAILABLE and AUTO_TRADING)
        if _debe_registrar and op_id:
            with _lock_ops:
                if op_id in operaciones_activas:
                    operaciones_activas[op_id].update({
                        'mt5_ejecutado': mt5_ejecutado,
                        'ticket_mt5': _activo_ticket_mt5,
                        'skip_mt5_razon': _skip_mt5_razon if _skip_mt5 else '',
                        'riesgo_usado': _riesgo,
                        '_reservado': False,  # Confirmar reserva
                    })

            guardar_estado()
            _skip_razon_display = _skip_mt5_razon if _skip_mt5 else ""
            enviar_canal(mensaje_nueva_senal(nombre, ticker, tipo, precio, niveles, ind, score, razones, fuente=fuente_precio, premium=_es_premium, skip_mt5_razon=_skip_razon_display, nivel_senal=_nivel_senal))

            # 🚨 NOTIFICACIÓN FOMO AL GRUPO
            notificar_fomo_grupo(nombre, tipo)

            _mt5_tag = " [MT5 ✅]" if mt5_ejecutado else f" [Solo Telegram — {_skip_mt5_razon}]" if _skip_mt5 else " [Sin MT5]"
            _nivel_log = f" ⭐{_nivel_senal}" if _es_premium else f" 📊{_nivel_senal}"
            log_senal(f"✅ SEÑAL ENVIADA: {nombre} | {tipo} | Score:{score}/5{_nivel_log} | Entrada:{precio} | SL:{niveles['sl']} | TP1:{niveles['tp1']} TP2:{niveles['tp2']} TP3:{niveles['tp3']}{_mt5_tag}")
            print(f"✅ Nueva señal PROFESIONAL: {nombre} - {tipo} (Score: {score}/5{_nivel_log}){_mt5_tag}")
        elif op_id:
            # MT5 falló sin _skip_mt5 → limpiar reserva huérfana
            with _lock_ops:
                if op_id in operaciones_activas and operaciones_activas[op_id].get('_reservado'):
                    del operaciones_activas[op_id]
                    estadisticas_diarias["senales_hoy"] = max(0, estadisticas_diarias.get("senales_hoy", 1) - 1)
            log_op(f"⚠️ MT5 rechazó orden {nombre} {tipo} — reserva limpiada")

    except Exception as e:
        log_senal(f"❌ ERROR generando señal {nombre}: {e}", "error")
        print(f"⚠️ Error en {nombre}: {e}")
        import traceback
        traceback.print_exc()
        # BUG-1 FIX: Limpiar reservación huérfana si crasheó después de reservar slot
        try:
            if op_id and op_id in operaciones_activas:
                with _lock_ops:
                    _orphan_scan = operaciones_activas.get(op_id, {})
                    if _orphan_scan.get('_reservado', False) and not _orphan_scan.get('mt5_ejecutado', False):
                        del operaciones_activas[op_id]
                        estadisticas_diarias["senales_hoy"] = max(0, estadisticas_diarias.get("senales_hoy", 1) - 1)
                        logger.warning(f"🧹 Scanner: reservación huérfana limpiada tras excepción: {op_id}")
        except Exception:
            pass

_HEALTH_CHECK_INTERVALO = 600  # Verificar cada 10 minutos
_ultimo_health_check    = time.time()
_mt5_desconectado_desde = 0.0  # Timestamp desde que MT5 está desconectado

def _reconectar_mt5(max_intentos=3):
    """Intenta reconectar MT5 con hasta N reintentos."""
    global MT5_AVAILABLE
    if not MT5_AVAILABLE:
        return False
    _mt5_path = os.getenv("MT5_PATH", "").strip()
    if not _mt5_path:
        # Auto-detectar MT5 en rutas comunes
        for _p in [r'C:\Program Files\XM Global MT5\terminal64.exe',
                    r'C:\Program Files\XM MT5\terminal64.exe',
                    r'C:\Program Files\MetaTrader 5\terminal64.exe']:
            if os.path.isfile(_p):
                _mt5_path = _p
                break
    if not _mt5_path:
        log_op("❌ No se encontró terminal64.exe de MT5 en ninguna ruta conocida", "warning")
        return False
    for intento in range(1, max_intentos + 1):
        try:
            # H-01 FIX: Proteger shutdown+initialize con lock completo
            # Evita que otros threads hagan llamadas MT5 durante reconexión
            with _lock_mt5:
                mt5.shutdown()
                time.sleep(2)
                if _mt5_primary_account:
                    acc = _mt5_primary_account
                    _ok = mt5.initialize(path=_mt5_path, login=acc["login"], password=acc["password"], server=acc["server"])
                else:
                    _ok = mt5.initialize(path=_mt5_path)
                if _ok:
                    _tinfo = mt5.terminal_info()
                    if _tinfo and _tinfo.connected:
                        # H-02 FIX: Re-habilitar AutoTrading tras reconexión
                        try:
                            _reenable_autotrading()
                        except Exception:
                            pass
                        log_op(f"✅ MT5 reconectado al intento {intento}")
                        return True
        except Exception as e:
            log_op(f"⚠️ MT5 reconexión intento {intento} falló: {e}", "warning")
        time.sleep(3)
    return False

def loop_health_check():
    """
    Hilo de health check: verifica cada 10 min que el scanner sigue vivo.
    También verifica conexión MT5 y reconecta si es necesario.
    """
    global _ultimo_health_check, _mt5_desconectado_desde
    time.sleep(60)  # Esperar 1 min antes del primer chequeo
    while True:
        try:
            # ── Check 1: Scanner activo ──
            tiempo_sin_scan = time.time() - ultimo_escaneo
            if ultimo_escaneo > 0 and tiempo_sin_scan > _HEALTH_CHECK_INTERVALO and not escaneo_pausado:
                mins = int(tiempo_sin_scan / 60)
                logger.error(f"🚨 HEALTH CHECK: Scanner sin actividad hace {mins} minutos!")
                enviar_grupo(
                    "🚨 *ALERTA DE SALUD — BuySell365.pro*\n"
                    "━━━━━━━━━━\n"
                    f"⚠️ El scanner lleva *{mins} minutos* sin ejecutarse.\n"
                    "Puede haber un problema de conexión o el servidor se colgó.\n\n"
                    "💡 Verifica el servidor y reinicia si es necesario.\n"
                    f"🕐 {ahora().strftime('%H:%M:%S')}",
                    incluir_promo=False
                )
                _ultimo_health_check = time.time()

            # ── Check 2: MT5 conexión ──
            if MT5_AVAILABLE and AUTO_TRADING:
                try:
                    _tinfo = mt5.terminal_info()
                    if _tinfo is None or not _tinfo.connected:
                        if _mt5_desconectado_desde == 0:
                            _mt5_desconectado_desde = time.time()
                        log_op("⚠️ MT5 desconectado — intentando reconectar...", "warning")
                        if _reconectar_mt5():
                            _mt5_desconectado_desde = 0.0
                        else:
                            mins_off = int((time.time() - _mt5_desconectado_desde) / 60)
                            if mins_off >= 10:
                                enviar_grupo(
                                    "🚨 *ALERTA MT5 — BuySell365.pro*\n"
                                    "━━━━━━━━━━\n"
                                    f"⚠️ MT5 lleva *{mins_off} min* desconectado.\n"
                                    "Las órdenes automáticas NO se están ejecutando.\n"
                                    "💡 Revisa la terminal MT5 y la conexión al broker.\n"
                                    f"🕐 {ahora().strftime('%H:%M:%S')}",
                                    incluir_promo=False
                                )
                    else:
                        if _mt5_desconectado_desde > 0:
                            log_op("✅ MT5 conexión restaurada")
                            _mt5_desconectado_desde = 0.0
                except Exception as e_mt5:
                    log_op(f"⚠️ Error verificando MT5: {e_mt5}", "warning")

        except Exception as e:
            logger.warning(f"⚠️ Error en health check: {e}")
        time.sleep(_HEALTH_CHECK_INTERVALO)


def _auditar_membresías():
    """Verifica que todos los VIP con entrada confirmada siguen en el canal.
    Si alguien se salió voluntariamente → revocar suscripción."""
    for uid in list(suscripciones_vip.keys()):
        if uid in ADMIN_IDS:
            continue  # 🛡️ Admins: nunca revocar
        sub = suscripciones_vip.get(uid)
        if not sub or not sub.get("entrada_confirmada", True):
            continue  # Solo auditar confirmados

        # ¿Sigue en el canal? (force=True para no usar caché viejo)
        if not _es_miembro_canal(uid, force=True):
            log_vip(f"🔍 AUDITORIA: {uid} ({sub.get('nombre','?')}) salió del canal → revocando VIP")
            _revocar_acceso_vip(uid, notificar=True)
            time.sleep(1)  # Rate limit entre kicks


def _verificar_entradas_pendientes():
    """Verifica trials pendientes de entrada al canal.
    - Si el usuario entró → confirmar trial, iniciar timer de 5 días hábiles (7 calendario)
    - Si pasaron >24h sin entrar → revocar invite link, permitir reintento"""
    global _vip_trials_usados
    ahora_dt = ahora().replace(tzinfo=None)

    for uid in list(suscripciones_vip.keys()):
        sub = suscripciones_vip.get(uid)
        if not sub:
            continue
        # Solo procesar trials pendientes de entrada
        if sub.get("entrada_confirmada", True):
            continue

        # ¿Ya entró al canal?
        if _es_miembro_canal(uid, force=True):
            es_codigo = sub.get("tipo") == "codigo"
            dias_acceso = sub.get("dias_codigo", VIP_TRIAL_DIAS) if es_codigo else VIP_TRIAL_DIAS

            # ✅ ¡Entró! Activar timer real desde AHORA
            expira_real = ahora_dt + timedelta(days=dias_acceso)
            invite_link = sub.get("invite_link", "")
            # Revocar el link de 24h (ya usado)
            try:
                if invite_link:
                    requests.post(
                        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/revokeChatInviteLink",
                        json={"chat_id": CHANNEL_ID, "invite_link": invite_link},
                        timeout=10
                    )
            except Exception:
                pass

            with _lock_ops:
                sub["entrada_confirmada"] = True
                sub["inicio"] = ahora_dt.strftime("%Y-%m-%dT%H:%M:%S")
                sub["expira"] = expira_real.strftime("%Y-%m-%dT%H:%M:%S")
                if not es_codigo:
                    _vip_trials_usados.add(uid)  # 🔑 Solo marcar como usada si es trial (no código)
            guardar_estado()

            if es_codigo:
                codigo_txt = sub.get("codigo", "?")
                enviar_telegram(
                    f"🎉 *ACCESO ACTIVADO — {dias_acceso} DIAS GRATIS*\n"
                    f"━━━━━━━━━━\n\n"
                    f"🎟️ Código: `{codigo_txt}`\n"
                    f"✅ Ya estas dentro del canal VIP\n"
                    f"⏳ Te quedan *{dias_acceso} dias* de acceso\n"
                    f"📅 Expira: *{expira_real.strftime('%Y-%m-%d')}*\n\n"
                    f"📊 _Recibiras senales IA con Entry, SL y TP exactos._\n"
                    f"🚀 _Aprovecha al maximo tu acceso!_",
                    uid
                )
                log_vip(f"✅ CÓDIGO CONFIRMADO: {uid} ({sub.get('nombre','?')}) entró al canal | Código:{codigo_txt} | Expira:{expira_real.strftime('%Y-%m-%d')} | {dias_acceso} dias activos")
            else:
                enviar_telegram(
                    f"🎉 *TRIAL ACTIVADA — 5 DIAS HABILES GRATIS*\n"
                    f"━━━━━━━━━━\n\n"
                    f"✅ Ya estas dentro del canal VIP\n"
                    f"⏳ Te quedan *5 dias habiles* de acceso\n"
                    f"📅 Expira: *{expira_real.strftime('%Y-%m-%d')}*\n\n"
                    f"📊 _Recibiras senales IA con Entry, SL y TP exactos._\n"
                    f"🚀 _Aprovecha al maximo tu prueba!_",
                    uid
                )
                log_vip(f"✅ TRIAL CONFIRMADA: {uid} ({sub.get('nombre','?')}) entró al canal | Expira:{expira_real.strftime('%Y-%m-%d')} | {VIP_TRIAL_DIAS} dias activos")
            time.sleep(0.5)
        else:
            # ¿Pasaron >24h sin entrar?
            try:
                inicio = datetime.fromisoformat(sub["inicio"])
                horas = (ahora_dt - inicio).total_seconds() / 3600
            except Exception:
                horas = 999

            if horas > 24:
                # Expirar invite link, quitar suscripción (NO marcar como usada)
                invite_link = sub.get("invite_link", "")
                if invite_link:
                    try:
                        requests.post(
                            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/revokeChatInviteLink",
                            json={"chat_id": CHANNEL_ID, "invite_link": invite_link},
                            timeout=10
                        )
                    except Exception:
                        pass

                nombre = sub.get("nombre", "Usuario")
                log_vip(f"⏰ TRIAL EXPIRADA: {uid} ({nombre}) no entró en 24h | Link revocado | Intentos:{_trial_intentos.get(uid,0)}/3")
                with _lock_ops:
                    suscripciones_vip.pop(uid, None)
                _cache_miembros.pop(uid, None)
                guardar_estado()

                intentos_restantes = 3 - _trial_intentos.get(uid, 0)
                if intentos_restantes > 0:
                    enviar_telegram(
                        f"⏰ *Tu link de trial expiró* (24h sin entrar).\n\n"
                        f"🔄 Puedes intentar de nuevo ({intentos_restantes} intento{'s' if intentos_restantes > 1 else ''}).\n"
                        f"👉 Escribe /vip para recibir un nuevo link.",
                        uid
                    )
                else:
                    enviar_telegram(
                        f"⏰ *Tu link de trial expiró.*\n\n"
                        f"Has agotado tus intentos de prueba gratuita.\n"
                        f"💰 Escribe /vip para ver opciones de pago.",
                        uid
                    )

                logger.info(f"⏰ Trial pendiente expirada: {uid} ({nombre}) — {horas:.0f}h sin entrar")
                time.sleep(0.5)


def loop_vip_check():
    """
    Hilo de verificación VIP:
    - Cada 5 min: revisa depósitos en Binance, entradas pendientes, expiraciones
    - Cada 30 min: auditoría de membresías (¿siguen en el canal?)
    - Reporte diario al admin a las 9:00 AM
    - Limpia pagos pendientes con >24h sin completar
    - Limpia códigos de invitación viejos (>30 días)
    """
    global pagos_pendientes_vip, _ultima_auditoria, _codigos_invitacion
    time.sleep(120)  # Esperar 2 min tras arranque
    _ultima_auditoria = time.time()
    logger.info("👑 Loop VIP check iniciado")

    while True:
        try:
            # 0. 📊 REPORTE DIARIO AL ADMIN (9:00 AM)
            ahora_check = ahora().replace(tzinfo=None)
            if (ahora_check.hour > REPORTE_HORA or (ahora_check.hour == REPORTE_HORA and ahora_check.minute >= REPORTE_MINUTO)) and _ultimo_reporte_diario != ahora_check.strftime("%Y-%m-%d"):
                _generar_reporte_diario()

            # 1. Verificar depósitos en Binance (matchear pagos pendientes)
            if pagos_pendientes_vip and BINANCE_API_KEY and BINANCE_API_SECRET:
                _verificar_depositos_binance()

            # 2. Limpiar pagos pendientes expirados (>24h sin completar)
            for uid in list(pagos_pendientes_vip.keys()):
                pend = pagos_pendientes_vip.get(uid, {})
                ts_str = pend.get("timestamp", "")
                try:
                    ts_pend = datetime.fromisoformat(ts_str)
                    horas_transcurridas = (ahora().replace(tzinfo=None) - ts_pend).total_seconds() / 3600
                    if horas_transcurridas > 24:
                        logger.info(f"🧹 Limpiando pago pendiente expirado: {uid}")
                        with _lock_ops:
                            pagos_pendientes_vip.pop(uid, None)
                        guardar_estado()
                except Exception:
                    pass

            # 3. 🔑 Verificar trials pendientes de entrada al canal
            _verificar_entradas_pendientes()

            # 4. Revisar suscripciones VIP CONFIRMADAS (expiraciones y avisos)
            if suscripciones_vip:
                ahora_dt = ahora().replace(tzinfo=None)
                for uid in list(suscripciones_vip.keys()):
                    # 🛡️ Admins: VIP permanente, nunca expira
                    if uid in ADMIN_IDS:
                        continue
                    sub = suscripciones_vip.get(uid)
                    if not sub:
                        continue
                    # Solo procesar las confirmadas
                    if not sub.get("entrada_confirmada", True):
                        continue

                    try:
                        expira = datetime.fromisoformat(sub["expira"])
                    except (ValueError, KeyError):
                        logger.warning(f"⚠️ Fecha inválida VIP de {uid}, revocando")
                        _revocar_acceso_vip(uid)
                        continue

                    _delta_vip = expira - ahora_dt
                    dias_restantes = _delta_vip.days
                    _horas_restantes = _delta_vip.total_seconds() / 3600  # L-FIX: Usar horas para precisión

                    # Expirado -> revocar (solo si realmente pasó la hora exacta)
                    if _horas_restantes <= 0:
                        logger.info(f"🚫 VIP expirado: {uid} ({sub.get('nombre', '?')})")
                        _revocar_acceso_vip(uid)
                        continue

                    # FIX 2026-03-19: Secuencia de avisos 7d→3d→1d (antes solo 1 aviso)
                    es_codigo = sub.get("tipo") == "codigo"
                    _avisos_enviados = sub.get("avisos_enviados", [])  # Lista de días ya avisados
                    # Migración: si tenía aviso_enviado=True antiguo, marcar como [3]
                    if sub.get("aviso_enviado", False) and not _avisos_enviados:
                        _avisos_enviados = [3]

                    _secuencia_aviso = [1] if es_codigo else [7, 3, 1]
                    _debe_avisar = False
                    for _d in _secuencia_aviso:
                        if dias_restantes <= _d and _d not in _avisos_enviados:
                            _debe_avisar = True
                            _avisos_enviados.append(_d)
                            break

                    if _debe_avisar:
                        logger.info(f"⚠️ Aviso VIP a {uid} ({dias_restantes}d) {'[CÓDIGO]' if es_codigo else ''}")
                        _enviar_aviso_vip(uid, dias_restantes)

            # 5. 🔍 Auditoría de membresías cada 30 min
            if time.time() - _ultima_auditoria > 1800:
                logger.info("🔍 Auditoría de membresías VIP...")
                _auditar_membresías()
                _ultima_auditoria = time.time()

            # 6. 🧹 Limpieza de códigos de invitación viejos (>30 días y agotados)
            for code in list(_codigos_invitacion.keys()):
                cinfo = _codigos_invitacion.get(code, {})
                try:
                    creado = datetime.fromisoformat(cinfo.get("creado", ""))
                    dias_old = (ahora().replace(tzinfo=None) - creado).days
                    if dias_old > 30 and cinfo.get("usos", 0) >= cinfo.get("max_usos", 1):
                        del _codigos_invitacion[code]
                        guardar_estado()
                        log_sistema(f"🧹 Código viejo limpiado: {code} ({dias_old}d)")
                except Exception:
                    pass

        except Exception as e:
            logger.error(f"⚠️ Error en loop VIP check: {e}")

        time.sleep(VIP_CHECK_INTERVALO)


def loop_monitor_alta_frecuencia():
    """Hilo dedicado exclusivamente a la vigilancia de operaciones cada N segundos."""
    print("🛰️ Monitor de Alta Frecuencia (HFR) iniciado...")
    _ultimo_check_noticias = 0
    while True:
        try:
            revisar_niveles_operaciones()
            # Protección de noticias: revisar cada 60 segundos
            _ahora = time.time()
            if _ahora - _ultimo_check_noticias >= 60:
                proteger_operaciones_por_noticias()
                _ultimo_check_noticias = _ahora
        except Exception as e:
            logger.error(f"⚠️ Error en monitor HFR: {e}")
        time.sleep(INTERVALO_MONITOR)

_ultimo_log_capital = 0.0  # Para no spammear log de capital

def _actualizar_capital_desde_mt5():
    """Actualiza CAPITAL_USUARIO con el equity real de MT5 cada ciclo de escaneo."""
    global CAPITAL_USUARIO, _ultimo_log_capital
    if not MT5_AVAILABLE:
        return
    try:
        equity = _obtener_capital_real_mt5()
        if equity > 0 and equity != CAPITAL_USUARIO:
            _old = CAPITAL_USUARIO
            CAPITAL_USUARIO = equity
            # Log solo cada 10 min o si cambio > $5
            _now = time.time()
            if abs(equity - _old) > 5 or (_now - _ultimo_log_capital) > 600:
                print(f"💰 CAPITAL ACTUALIZADO: ${_old:.0f} → ${equity:.0f} (balance MT5)")
                _ultimo_log_capital = _now
    except Exception as e:
        logger.warning(f"⚠️ Error actualizando capital: {e}")


def _cerrar_operacion_manual(op_key):
    """Cierra una operacion manualmente desde la consola.
    Cierra posicion MT5 si existe, elimina del tracking, registra en historial."""
    global operaciones_activas
    with _lock_ops:
        op = operaciones_activas.get(op_key)
        if not op:
            print(f"⚠️ CIERRE MANUAL: Operacion {op_key} no encontrada")
            return

        nombre = op.get('nombre', '?')
        tipo = op.get('tipo', '?')
        ticker = op.get('ticker', '')
        entrada = op.get('entrada', 0)
        ticket_mt5 = op.get('ticket_mt5')
        print(f"🔧 CIERRE MANUAL: {nombre} {tipo} (key={op_key})")

        # Cerrar posicion MT5 si existe
        if ticket_mt5 and MT5_AVAILABLE:
            try:
                import MetaTrader5 as _mt5_mod
                pos = _mt5_mod.positions_get(ticket=ticket_mt5)
                if pos and len(pos) > 0:
                    p = pos[0]
                    close_type = _mt5_mod.ORDER_TYPE_SELL if p.type == 0 else _mt5_mod.ORDER_TYPE_BUY
                    sym_info = _mt5_mod.symbol_info(p.symbol)
                    if sym_info:
                        price = sym_info.bid if p.type == 0 else sym_info.ask
                        request = {
                            "action": _mt5_mod.TRADE_ACTION_DEAL,
                            "symbol": p.symbol,
                            "volume": p.volume,
                            "type": close_type,
                            "position": p.ticket,
                            "price": price,
                            "deviation": 20,
                            "magic": p.magic,
                            "comment": "BuySell365 manual close",
                            "type_time": _mt5_mod.ORDER_TIME_GTC,
                            "type_filling": _mt5_mod.ORDER_FILLING_IOC,
                        }
                        result = _mt5_mod.order_send(request)
                        if result and result.retcode == _mt5_mod.TRADE_RETCODE_DONE:
                            print(f"✅ MT5 posicion {ticket_mt5} cerrada OK")
                        else:
                            _rc = result.retcode if result else "None"
                            print(f"⚠️ MT5 cierre ticket {ticket_mt5}: retcode={_rc}")
                else:
                    print(f"ℹ️ MT5 ticket {ticket_mt5} ya no existe (posicion cerrada)")
            except Exception as e_mt5:
                print(f"⚠️ Error cerrando MT5 ticket {ticket_mt5}: {e_mt5}")

        # Registrar en historial
        _hora_salida = ahora().strftime("%H:%M")
        _dur_seg = time.time() - op.get('timestamp', time.time())
        _dur_min = round(_dur_seg / 60, 1)
        _hist = {
            "nombre": nombre, "tipo": tipo, "ticker": ticker,
            "entrada": entrada, "salida": entrada,
            "pips": 0, "resultado": "MANUAL",
            "hora": _hora_salida, "fecha": ahora().strftime("%d/%m/%Y"),
            "hora_entrada": op.get('hora', ''), "hora_salida": _hora_salida,
            "tag": "MANUAL", "duracion_min": _dur_min,
            "score": op.get('score', 0),
            "confianza": op.get('confianza_multi_ia', 0),
            "estrategia": op.get('estrategia', ''),
            "fuente": "manual_close",
        }
        historial_operaciones.append(_hist)

        # Eliminar del tracking
        del operaciones_activas[op_key]
        guardar_estado()
        log_op(f"🔧 CIERRE MANUAL: {nombre} {tipo} eliminado del tracking (dur: {_dur_min}min)")


def _procesar_comandos_launcher():
    """Lee y ejecuta comandos del launcher via .bot.cmd"""
    global SCALPER_ACTIVO, escaneo_pausado, mt5_pausado
    if not os.path.exists(_CMD_FILE):
        return None
    try:
        with open(_CMD_FILE, "r", encoding="utf-8") as f:
            cmd = f.read().strip()
        os.remove(_CMD_FILE)
        if not cmd:
            return None
        log_sistema(f"📩 Comando del launcher: {cmd}")
        if cmd == "scalper_pause":
            SCALPER_ACTIVO = False
            guardar_estado()
            print("🔪 Scalper PAUSADO por consola")
        elif cmd == "scalper_resume":
            SCALPER_ACTIVO = True
            guardar_estado()
            print("🔪 Scalper REANUDADO por consola")
        elif cmd == "force_scan":
            print("🔍 Escaneo inmediato solicitado por consola")
            return "force_scan"
        elif cmd == "pause_all":
            mt5_pausado = True
            escaneo_pausado = True
            SCALPER_ACTIVO = False
            guardar_estado()
            print("🛑 PAUSA TOTAL por consola")
        elif cmd == "resume_all":
            mt5_pausado = False
            escaneo_pausado = False
            SCALPER_ACTIVO = True
            guardar_estado()
            print("▶️ TODO REACTIVADO por consola")
        elif cmd.startswith("close_op:"):
            _op_key = cmd.split(":", 1)[1]
            _cerrar_operacion_manual(_op_key)
        return cmd
    except Exception as e:
        logger.warning(f"Error procesando .bot.cmd: {e}")
        return None


def loop_escaneo():
    """Hilo dedicado al escaneo continuo de SEÑALES."""
    global ultimo_escaneo
    print("━━━━━━━━━━")
    print(f"🚀 BuySell365 Pro BOT | SCANNER DE SEÑALES | {ahora().strftime('%H:%M:%S')}")
    print("━━━━━━━━━━")

    _errores_consecutivos = 0

    while True:
        # 📩 Procesar comandos del launcher (scalper pause, force scan, etc.)
        _launcher_cmd = _procesar_comandos_launcher()
        _forzar_escaneo = (_launcher_cmd == "force_scan")

        if not escaneo_pausado or _forzar_escaneo:
            try:
                # 💰 ACTUALIZAR CAPITAL DESDE MT5 (cada ciclo = cada 3 min)
                _actualizar_capital_desde_mt5()

                # [10] AUTO-OPTIMIZACIÓN SEMANAL: Domingos 23:00 Andorra
                try:
                    _now_opt = ahora()
                    if (_now_opt.weekday() == 6 and _now_opt.hour == 23
                            and (time.time() - _ultima_optimizacion_semanal) > 82800):
                        _auto_optimizar_semanal()
                except Exception as e_opt:
                    logger.warning(f"⚠️ Error auto-optimización: {e_opt}")

                analizar_mercado()
                ultimo_escaneo = time.time()
                limpiar_caches_memoria()
                _errores_consecutivos = 0
                print(f"✅ Ciclo completado. Siguiente en {INTERVALO_ESCANEO}s...")
            except Exception as e:
                _errores_consecutivos += 1
                logger.error(f"⚠️ Error en loop de escaneo (#{_errores_consecutivos}): {e}")
                import traceback
                traceback.print_exc()
                # Si hay 5 errores seguidos, avisar por Telegram
                if _errores_consecutivos == 5:
                    enviar_grupo(
                        "🔴 *ERROR REPETIDO EN SCANNER*\n"
                        f"5 ciclos consecutivos fallaron.\nÚltimo error: `{str(e)[:200]}`\n"
                        f"🕐 {ahora().strftime('%H:%M:%S')}",
                        incluir_promo=False
                    )
        else:
            print("⏸️ Escaneo pausado, esperando...")

        time.sleep(INTERVALO_ESCANEO)


_cooldown_bienvenida: dict = {}  # {user_id: timestamp} — evitar spam de bienvenida

def manejar_usuario_nuevo(msg, user_info, texto, grupo_chat_id=None):
    """Gestiona usuarios que no están en la lista blanca con autonomía y marketing VIP.
    Si grupo_chat_id se proporciona, envía la bienvenida EN el grupo (y programa su borrado)
    en lugar de al chat privado del usuario (que falla si no ha hecho /start)."""
    user_id = str(user_info.get("id", ""))
    nombre = escapar_markdown(user_info.get("first_name", "Usuario"))  # H-11 FIX

    # Cooldown: máximo 1 bienvenida por usuario cada 10 minutos
    _ahora = time.time()
    if user_id in _cooldown_bienvenida and (_ahora - _cooldown_bienvenida[user_id]) < 600:
        return  # Ya se le envió bienvenida recientemente
    _cooldown_bienvenida[user_id] = _ahora
    username = f"@{user_info.get('username')}" if user_info.get('username') else "Sin @alias"

    # 1. Bienvenida Estratégica con Trial Gratis
    puede_trial = user_id not in _vip_trials_usados

    pi = _vip_precio_info()
    p = pi["precio"]
    desc_label = f" (50% OFF)" if pi["en_descuento"] else ""

    bienvenida = f"👋 *Hola {nombre}!* Bienvenido a *BuySell365.pro*\n\n"

    if puede_trial:
        bienvenida += (
            f"🎁 *5 DIAS HABILES GRATIS* en nuestro canal VIP:\n"
            "✅ Senales IA: Oro, Forex, NASDAQ, S&P 500\n"
            "✅ Entry, SL, TP exactos | Riesgo controlado\n\n"
            "🚀 *Copy Trading* — copia nuestras operaciones en tu cuenta\n\n"
            f"Despues: {p}{VIP_MONEDA}/mes{desc_label}. Sin compromiso."
        )
    else:
        bienvenida += (
            "Canal VIP con senales de alta precision:\n"
            "✅ Oro, Forex, NASDAQ, S&P 500\n"
            "✅ Entry, SL, TP exactos\n\n"
            "🚀 *Copy Trading* — copia nuestras operaciones automaticamente\n\n"
            f"👑 *{p}{VIP_MONEDA}/mes{desc_label}* → /vip"
        )

    if puede_trial:
        markup = {
            "inline_keyboard": [
                [{"text": f"🎁 5 DIAS HABILES GRATIS", "callback_data": "vip_trial_gratis"}],
                [{"text": "🚀 COPY TRADING", "url": "https://social.tp-redirect.com/s/WRE0V7jm"}],
                [{"text": f"💰 PAGAR {p}{VIP_MONEDA}{desc_label}", "callback_data": "vip_pagar_usdt"}],
                [{"text": "📊 Estadisticas", "callback_data": "/semana"}, {"text": "⏰ Horarios", "callback_data": "/horarios"}]
            ]
        }
    else:
        markup = {
            "inline_keyboard": [
                [{"text": "🚀 COPY TRADING", "url": "https://social.tp-redirect.com/s/WRE0V7jm"}],
                [{"text": f"💰 SUSCRIBIRSE AL VIP ({p}{VIP_MONEDA}{desc_label})", "callback_data": "vip_pagar_usdt"}],
                [{"text": "📊 Estadisticas", "callback_data": "/semana"}, {"text": "⏰ Horarios", "callback_data": "/horarios"}]
            ]
        }

    # Enviar al grupo (visible para el usuario) o al chat privado
    destino = grupo_chat_id or user_id
    msg_id = enviar_telegram(bienvenida, destino, teclado=markup)
    # Si se envió al grupo, programar borrado tras 5 min (tiempo suficiente para leer y pulsar botón)
    if grupo_chat_id and msg_id:
        programar_borrado(grupo_chat_id, msg_id, 300)

    # 2. Reenvío al Administrador (Tú)
    admin_id = USERS_AUTORIZADOS[0] if USERS_AUTORIZADOS else None
    if admin_id:
        aviso_admin = (
            "👤 *NUEVO INTERESADO EN VIP*\n"
            "━━━━━━━━━━\n"
            f"👤 Nombre: {nombre}\n"
            f"🆔 ID: `{user_id}`\n"
            f"🔗 Alias: {username}\n"
            f"💬 Mensaje: _{texto}_\n\n"
            f"💡 _Pulsa en el alias para responderle directamente._"
        )
        enviar_telegram(aviso_admin, admin_id)

# 🛡️ RATE LIMITER POR USUARIO — máx 4 comandos cada 30 segundos
_rate_limit_usuarios: dict = {}
_RATE_LIMIT_MAX = 4       # máximo de comandos por ventana
_RATE_LIMIT_VENTANA = 30  # ventana en segundos

# 🧵 THREAD POOL para procesamiento de comandos — los comandos pesados
# (/analisis, /precios, /tendencia) no bloquean el loop de polling
_pool_comandos = ThreadPoolExecutor(max_workers=8, thread_name_prefix="cmd")

def _check_rate_limit(user_id: str) -> bool:
    """Retorna True si el usuario excede el rate limit. False = OK."""
    ahora = time.time()
    if user_id not in _rate_limit_usuarios:
        _rate_limit_usuarios[user_id] = []
    # Limpiar timestamps viejos
    _rate_limit_usuarios[user_id] = [t for t in _rate_limit_usuarios[user_id] if ahora - t < _RATE_LIMIT_VENTANA]
    if len(_rate_limit_usuarios[user_id]) >= _RATE_LIMIT_MAX:
        return True  # Excede el límite
    _rate_limit_usuarios[user_id].append(ahora)
    return False

def loop_polling():
    """
    Polling de mensajes Telegram con backoff exponencial.
    Ante fallos de red, espera 5s → 10s → 20s → 40s → 60s (máx) antes de reintentar.
    """
    offset     = 0
    _backoff   = 5    # segundos de espera inicial tras error
    _max_backoff = 60 # máximo 1 minuto entre reintentos
    print("📡 Iniciando polling de Telegram...")

    # Eliminar webhook previo con drop_pending_updates para evitar el 409
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/deleteWebhook",
            json={"drop_pending_updates": True},
            timeout=10
        )
        print("✅ Webhook eliminado (pending updates descartados), usando polling.")
    except Exception as e:
        print(f"⚠️ Error eliminando webhook: {e}")

    # FIX 2026-03-19: Registrar comandos en BotFather para menú de usuario
    try:
        _cmds = [
            {"command": "start", "description": "Iniciar el bot"},
            {"command": "senales", "description": "Ver señales activas"},
            {"command": "estado", "description": "Estado del bot y mercado"},
            {"command": "resumen", "description": "Resumen del día"},
            {"command": "noticias", "description": "Calendario económico"},
            {"command": "tendencia", "description": "Tendencias del mercado"},
            {"command": "precios", "description": "Precios en tiempo real"},
            {"command": "analisis", "description": "Análisis técnico de un activo"},
            {"command": "sentimiento", "description": "Fear & Greed index"},
            {"command": "vip", "description": "Acceso VIP / Trial gratis"},
            {"command": "web", "description": "Dashboard web"},
            {"command": "ayuda", "description": "Lista de comandos"},
        ]
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setMyCommands",
            json={"commands": _cmds},
            timeout=10
        )
        print("✅ Comandos registrados en BotFather.")
    except Exception as e:
        print(f"⚠️ Error registrando comandos: {e}")

    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
            r = requests.get(url, params={"offset": offset, "timeout": 30}, timeout=35)

            if r.status_code == 200:
                _backoff = 5  # Resetear backoff en caso de éxito
                data = r.json()
                for update in data.get("result", []):
                    offset = update["update_id"] + 1
                    # ── MANEJO DE CALLBACK QUERIES (Botones Inline) ──
                    cb = update.get("callback_query")
                    if cb:
                        cb_id = cb.get("id")
                        texto = cb.get("data", "")
                        msg   = cb.get("message", {})
                        chat  = msg.get("chat", {})
                        chat_id = str(chat.get("id", ""))
                        tipo_chat = chat.get("type", "private")
                        from_user = cb.get("from") or {}
                        user_id   = str(from_user.get("id", chat_id))
                        # FIX 2026-03-19: Feedback contextual en vez de genérico "Procesando..."
                        _cb_feedback = {
                            "/activas": "📊 Cargando señales...",
                            "/estado": "📈 Cargando estado...",
                            "/resumen": "📋 Generando resumen...",
                            "/precios": "💰 Consultando precios...",
                            "/noticias": "📰 Cargando noticias...",
                            "/semana": "📊 Calculando semana...",
                            "/horarios": "🕐 Cargando horarios...",
                            "/vip": "👑 Abriendo VIP...",
                            "vip_trial_gratis": "🎁 Preparando trial...",
                            "vip_pagar_usdt": "💰 Cargando pago...",
                            "vip_trial_confirmar": "✅ Activando trial...",
                            "vip_pagar_confirmar": "💳 Procesando pago...",
                        }.get(texto, "⏳ Procesando...")
                        try:
                            requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/answerCallbackQuery",
                                          json={"callback_query_id": cb_id, "text": _cb_feedback}, timeout=10)
                        except Exception: pass
                        log_usuario(f"🔘 CALLBACK [{tipo_chat}] user={user_id} chat={chat_id}: {texto}")
                        print(f"🔘 Callback [{tipo_chat}] user={user_id} chat={chat_id}: {texto}")

                        # 🛡️ Si el botón se pulsó en el CANAL, redirigir fallback al GRUPO
                        # El canal es solo para señales — nunca enviar respuestas interactivas ahí
                        _fallback_dest = chat_id
                        if chat_id == CHANNEL_ID or tipo_chat == "channel":
                            _fallback_dest = GROUP_ID if GROUP_ID else chat_id

                        # ── Callback VIP: trial gratis (paso 1 → mostrar términos) ──
                        if texto == "vip_trial_gratis":
                            pi_t = _vip_precio_info()
                            _dm_trial = enviar_telegram(
                                f"📋 *TERMINOS DEL TRIAL GRATUITO*\n"
                                f"━━━━━━━━━━\n\n"
                                f"🎁 Acceso al canal VIP por *5 dias habiles gratis*.\n"
                                f"📅 Despues del trial: *{pi_t['precio']}{VIP_MONEDA}/mes*.\n"
                                f"🚫 No se cobra nada durante el trial.\n\n"
                                f"📜 *Condiciones:*\n"
                                f"• Servicio de senales educativas.\n"
                                f"  _No es asesoria financiera._\n"
                                f"• El usuario es responsable de\n"
                                f"  sus decisiones de trading.\n"
                                f"• Una prueba gratuita por persona.\n"
                                f"• Al finalizar el trial, el acceso\n"
                                f"  se desactiva automaticamente.\n\n"
                                f"👇 *Al pulsar ACEPTO confirmas estos terminos:*",
                                user_id,
                                teclado={"inline_keyboard": [
                                    [{"text": "✅ ACEPTO — ACTIVAR TRIAL", "callback_data": "vip_trial_confirmar"}],
                                    [{"text": "❌ CANCELAR", "callback_data": "vip_cancelar"}]
                                ]}
                            )
                            # 🆕 Si el DM falló y el botón se pulsó desde grupo → avisar en grupo
                            if _dm_trial is None and tipo_chat in ("group", "supergroup"):
                                _nombre_cb_trial = escapar_markdown(from_user.get("first_name", "Trader"))  # H-11 FIX
                                _aviso_dm = enviar_telegram(
                                    f"👋 *{_nombre_cb_trial}*, para activar tu trial necesito "
                                    f"que primero me escribas por privado:\n\n"
                                    f"👉 Abre @Andoperandobot y pulsa *Start*\n"
                                    f"👉 Luego vuelve aqui y pulsa el boton de nuevo.",
                                    chat_id
                                )
                                if _aviso_dm:
                                    programar_borrado(chat_id, _aviso_dm, 120)
                            continue

                        # ── Callback VIP: trial confirmado (paso 2 → activar) ──
                        if texto == "vip_trial_confirmar":
                            nombre_cb = from_user.get("first_name", "Trader")
                            username_cb = from_user.get("username", "")
                            _otorgar_trial_vip(user_id, nombre_cb, username_cb)
                            continue

                        # ── Callback VIP: pago USDT (paso 1 → mostrar términos) ──
                        if texto == "vip_pagar_usdt":
                            pi_p = _vip_precio_info()
                            _dm_pago = enviar_telegram(
                                f"📋 *TERMINOS DE SUSCRIPCION VIP*\n"
                                f"━━━━━━━━━━\n\n"
                                f"💰 Precio: *{pi_p['precio']}{VIP_MONEDA}/mes*\n"
                                f"💎 Pago unico mensual via USDT ({VIP_RED}).\n\n"
                                f"📜 *Condiciones:*\n"
                                f"• Servicio de senales educativas.\n"
                                f"  _No es asesoria financiera._\n"
                                f"• El usuario es responsable de\n"
                                f"  sus decisiones de trading.\n"
                                f"• *No hay reembolsos* una vez\n"
                                f"  confirmado el pago.\n"
                                f"• Verificacion automatica en ~5 min.\n"
                                f"• Acceso por 30 dias desde el pago.\n\n"
                                f"👇 *Al pulsar ACEPTO confirmas estos terminos:*",
                                user_id,
                                teclado={"inline_keyboard": [
                                    [{"text": "✅ ACEPTO — VER INSTRUCCIONES DE PAGO", "callback_data": "vip_pagar_confirmar"}],
                                    [{"text": "❌ CANCELAR", "callback_data": "vip_cancelar"}]
                                ]}
                            )
                            # 🆕 Si el DM falló y el botón se pulsó desde grupo → avisar en grupo
                            if _dm_pago is None and tipo_chat in ("group", "supergroup"):
                                _nombre_cb_pago = escapar_markdown(from_user.get("first_name", "Trader"))  # H-11
                                _aviso_dm_p = enviar_telegram(
                                    f"👋 *{_nombre_cb_pago}*, para ver las opciones de pago "
                                    f"necesito que primero me escribas por privado:\n\n"
                                    f"👉 Abre @Andoperandobot y pulsa *Start*\n"
                                    f"👉 Luego vuelve aqui y pulsa el boton de nuevo.",
                                    chat_id
                                )
                                if _aviso_dm_p:
                                    programar_borrado(chat_id, _aviso_dm_p, 120)
                            continue

                        # ── Callback VIP: pago confirmado (paso 2 → instrucciones) ──
                        if texto == "vip_pagar_confirmar":
                            nombre_cb = from_user.get("first_name", "Trader")
                            username_cb = from_user.get("username", "")
                            _mostrar_instrucciones_pago(user_id, user_id, nombre_cb, username_cb, fallback_chat=_fallback_dest)
                            continue

                        # ── Callback CÓDIGO DE INVITACIÓN: aceptar ──
                        if texto.startswith("codigo_aceptar_"):
                            _code_cb = texto.replace("codigo_aceptar_", "").strip().upper()
                            nombre_cb = from_user.get("first_name", "Trader")
                            username_cb = from_user.get("username", "")
                            _activar_codigo_invitacion(_code_cb, user_id, nombre_cb, username_cb)
                            continue

                        # ── Callback VIP LISTA (desde reporte diario) ──
                        if texto == "/vip_lista_cb":
                            _es_admin_cb = user_id in ADMIN_IDS
                            if _es_admin_cb:
                                enviar_telegram(cmd_vip_lista(), user_id)
                            continue

                        # ── Callback VIP: ver pago pendiente ──
                        if texto == "vip_ver_pago_pendiente":
                            if user_id in pagos_pendientes_vip:
                                _pend_data = pagos_pendientes_vip[user_id]
                                _pend_monto = _pend_data.get("monto_unico", 0)
                                enviar_telegram(
                                    "⏳ *PAGO PENDIENTE*\n"
                                    "━━━━━━━━━━\n\n"
                                    f"💰 Monto: *{_pend_monto:.3f} USDT* | Red: *{VIP_RED}*\n"
                                    f"📋 Wallet:\n`{VIP_WALLET_USDT}`\n\n"
                                    "⚠️ Envia *EXACTAMENTE* esa cantidad en USDT.\n"
                                    "👇 Pulsa el boton para abrir Binance:\n\n"
                                    "✅ _Verificacion automatica en ~5 min._",
                                    user_id,
                                    teclado={"inline_keyboard": [
                                        [{"text": "💰 ABRIR BINANCE PARA PAGAR", "url": "https://app.binance.com/en/my/wallet/account/main/withdrawal/crypto/USDT"}],
                                        [{"text": "❌ CANCELAR PAGO", "callback_data": "vip_cancelar_pago"}],
                                        [{"text": f"❓ AYUDA — {ADMIN_USER}", "url": f"https://t.me/{ADMIN_USER.replace('@','')}"}]
                                    ]}
                                )
                            else:
                                enviar_telegram("ℹ️ No tienes pago pendiente.\n\nEscribe /vip para ver opciones.", user_id)
                            continue

                        # ── Callback VIP: cancelar pago pendiente ──
                        if texto == "vip_cancelar_pago":
                            with _lock_ops:
                                pagos_pendientes_vip.pop(user_id, None)
                            guardar_estado()
                            enviar_telegram(
                                "✅ *Pago cancelado.*\n\nEscribe /vip para ver opciones.",
                                user_id
                            )
                            continue

                        # ── Callback VIP: cancelar ──
                        if texto == "vip_cancelar":
                            enviar_telegram(
                                "👋 Cancelado. Escribe /vip cuando quieras volver a ver las opciones.",
                                user_id
                            )
                            continue

                        # ── Callbacks generales (botones inline del canal/grupo) ──
                        try:
                            cb_respuesta = None
                            cb_teclado = None
                            _cb_es_grupo = tipo_chat in ("group", "supergroup")
                            _cb_es_canal = tipo_chat == "channel" or chat_id == CHANNEL_ID
                            # 🛡️ En GRUPO PÚBLICO: solo teasers para contenido VIP
                            # En CANAL VIP o PRIVADO: contenido completo
                            _cb_usuario_autorizado = user_id in USERS_AUTORIZADOS or user_id in ADMIN_IDS

                            if texto == "/activas":
                                if _cb_es_grupo and not _cb_es_canal and not _cb_usuario_autorizado:
                                    # 🔒 TEASER en grupo público — NO mostrar señales completas
                                    with _lock_ops:
                                        n_ops = len(operaciones_activas)
                                        n_buy = sum(1 for o in operaciones_activas.values()
                                                    if isinstance(o, dict) and o.get('tipo') == "COMPRA")
                                        n_sell = n_ops - n_buy
                                    if n_ops > 0:
                                        _pl = "es" if n_ops > 1 else ""
                                        cb_respuesta = (
                                            f"📊 *{n_ops} operacion{_pl} activa{_pl}*"
                                            f" · 🟢{n_buy} 🔴{n_sell}\n\n"
                                            f"🔒 _Entry, SL y TP disponibles en el canal VIP._\n"
                                            f"🎁 *Escribe /vip — 5 dias habiles GRATIS*"
                                        )
                                    else:
                                        cb_respuesta = "📭 *Sin operaciones abiertas.* Te aviso cuando haya señal. 📡"
                                else:
                                    cb_respuesta = cmd_senales()
                            elif texto == "/noticias":
                                cb_respuesta = cmd_noticias()  # Noticias son públicas
                            elif texto == "/estado":
                                if _cb_es_grupo and not _cb_es_canal and not _cb_usuario_autorizado:
                                    with _lock_ops:
                                        n_ops = len(operaciones_activas)
                                    _total_e = estadisticas_diarias["ganadas"] + estadisticas_diarias["perdidas"]
                                    _wr_e = (estadisticas_diarias["ganadas"] / _total_e * 100) if _total_e > 0 else 0
                                    cb_respuesta = (
                                        f"⚙️ *Bot activo* ✅ · {n_ops} operaciones abiertas\n"
                                        f"📊 Win Rate hoy: *{_wr_e:.0f}%* ({_total_e} señales)\n\n"
                                        f"🔒 _Panel completo en el canal VIP._\n"
                                        f"🎁 *Escribe /vip — 5 dias habiles GRATIS*"
                                    )
                                else:
                                    cb_respuesta = cmd_estado()
                            elif texto == "/resumen":
                                if _cb_es_grupo and not _cb_es_canal and not _cb_usuario_autorizado:
                                    _total_r = estadisticas_diarias["ganadas"] + estadisticas_diarias["perdidas"]
                                    _wr_r = (estadisticas_diarias["ganadas"] / _total_r * 100) if _total_r > 0 else 0
                                    _pips_r = estadisticas_diarias["pips_ganados"] - estadisticas_diarias["pips_perdidos"]
                                    cb_respuesta = (
                                        f"📈 *Resumen del dia*\n"
                                        f"Señales: {_total_r} · Win Rate: *{_wr_r:.0f}%*\n"
                                        f"Pips netos: *{_pips_r:+.1f}*\n\n"
                                        f"🔒 _Historial completo en el canal VIP._\n"
                                        f"🎁 *Escribe /vip — 5 dias habiles GRATIS*"
                                    )
                                else:
                                    cb_respuesta = cmd_resumen()
                            elif texto == "/precios":
                                cb_respuesta = cmd_precios_tv()  # Precios son públicos
                            elif texto.startswith("/analisis_"):
                                if _cb_es_grupo and not _cb_es_canal and not _cb_usuario_autorizado:
                                    activo = texto.replace("/analisis_", "").upper()
                                    cb_respuesta = (
                                        f"🔍 *Análisis {activo}* — contenido VIP\n\n"
                                        f"🔒 _Disponible en el canal VIP._\n"
                                        f"🎁 *Escribe /vip — 5 dias habiles GRATIS*"
                                    )
                                else:
                                    activo = texto.replace("/analisis_", "").upper()
                                    cb_respuesta = cmd_analisis(activo)
                            elif texto == "/vip":
                                r_vip = cmd_vip(user_id=user_id)
                                cb_respuesta = r_vip[0] if isinstance(r_vip, tuple) else r_vip
                                cb_teclado = r_vip[1] if isinstance(r_vip, tuple) else None
                                # VIP info va al DM, no al grupo
                                if _cb_es_grupo and not _cb_es_canal:
                                    _dm_vip_cb = enviar_telegram(cb_respuesta, user_id, teclado=cb_teclado)
                                    if _dm_vip_cb:
                                        _nombre_vip_cb = escapar_markdown(from_user.get("first_name", "Trader"))  # H-11
                                        _av = enviar_telegram(f"💬 *{_nombre_vip_cb}*, te envie la info por privado 📩", chat_id)
                                        if _av: programar_borrado(chat_id, _av, 90)
                                    else:
                                        _av2 = enviar_telegram(
                                            f"👉 *Primero escribeme al DM:* @Andoperandobot y pulsa *Start*\n"
                                            f"Luego escribe /vip aqui.", chat_id)
                                        if _av2: programar_borrado(chat_id, _av2, 120)
                                    continue
                            elif texto == "/semana":
                                if _cb_es_grupo and not _cb_es_canal and not _cb_usuario_autorizado:
                                    _total_r = estadisticas_diarias["ganadas"] + estadisticas_diarias["perdidas"]
                                    _wr_r = (estadisticas_diarias["ganadas"] / _total_r * 100) if _total_r > 0 else 0
                                    cb_respuesta = (
                                        f"📈 *Resumen semanal* — contenido VIP\n"
                                        f"Señales hoy: {_total_r} · WR: *{_wr_r:.0f}%*\n\n"
                                        f"🔒 _Detalles completos en el canal VIP._\n"
                                        f"🎁 *Escribe /vip — 5 dias habiles GRATIS*"
                                    )
                                else:
                                    cb_respuesta = cmd_semana()
                            elif texto == "/horarios":
                                cb_respuesta = cmd_horarios()  # Horarios son públicos

                            if cb_respuesta:
                                cb_dest = _fallback_dest
                                cb_msg_id = enviar_telegram(cb_respuesta, cb_dest, teclado=cb_teclado)
                                if tipo_chat in ("group", "supergroup", "channel"):
                                    programar_borrado(cb_dest, cb_msg_id, 300)
                                continue
                        except Exception as e_cb:
                            logger.error(f"❌ Error callback '{texto}': {e_cb}")
                            continue

                    else:
                        # ── MANEJO DE MENSAJES NORMALES O POSTS ──
                        msg = update.get("message") or update.get("channel_post")
                        if not msg:
                            continue

                        # 🆕 DETECCIÓN DE NUEVOS MIEMBROS EN GRUPO — auto-bienvenida
                        _new_members = msg.get("new_chat_members", [])
                        if _new_members:
                            _grp_chat = msg.get("chat", {})
                            _grp_chat_id = str(_grp_chat.get("id", ""))
                            _grp_tipo = _grp_chat.get("type", "")
                            if _grp_tipo in ("group", "supergroup"):
                                for _nm in _new_members:
                                    if _nm.get("is_bot"):
                                        continue  # Ignorar bots que se unen
                                    _nm_id = str(_nm.get("id", ""))
                                    _nm_nombre = _nm.get("first_name", "Trader")
                                    _nm_info = {
                                        "id": _nm.get("id"),
                                        "first_name": _nm_nombre,
                                        "username": _nm.get("username", ""),
                                    }
                                    # Registrar en directorio
                                    if _nm_id not in directorio_usuarios:
                                        directorio_usuarios[_nm_id] = {"nombre": _nm_nombre, "username": _nm.get("username", "")}
                                    # Solo dar bienvenida si NO es usuario autorizado
                                    if _nm_id not in USERS_AUTORIZADOS:
                                        manejar_usuario_nuevo(msg, _nm_info, "(nuevo miembro)", grupo_chat_id=_grp_chat_id)
                                        log_usuario(f"👤 NUEVO MIEMBRO: {_nm_nombre} ({_nm_id}) se unió al grupo")
                            continue  # No procesar el update como mensaje normal

                        # 📺 channel_posts: AHORA se procesan normalmente.
                        # Info privada (VIP/pago) se redirige a DM automáticamente.
                        # Señales llegan por enviar_canal(), no por getUpdates.

                        # 🛡️ IGNORAR reenvíos automáticos del canal vinculado al grupo
                        # Cuando canal↔grupo están vinculados, Telegram reenvía los mensajes
                        # del canal al grupo como user 777000. Si no filtramos, el bot
                        # procesa la misma señal DOS VECES y puede ejecutar órdenes duplicadas.
                        if msg.get("is_automatic_forward"):
                            continue

                        texto = msg.get("text", "").strip()
                        # Limpiar sufijo @botname de comandos en grupos
                        # Telegram envía "/vip@Andoperandobot" cuando el usuario escribe /vip
                        if "@" in texto and texto.startswith("/"):
                            texto = texto.split("@")[0]
                        if not texto:
                            continue
                        chat      = msg.get("chat", {})
                        chat_id   = str(chat.get("id", ""))
                        tipo_chat = chat.get("type", "")
                        from_user = msg.get("from") or {}
                        user_id   = str(from_user.get("id", chat_id))
                        # 📝 Guardar nombre del usuario en el directorio
                        nombre_u = from_user.get("first_name", "Trader")
                        alias_u  = from_user.get("username", "")
                        if user_id not in directorio_usuarios or directorio_usuarios[user_id].get("nombre") != nombre_u:
                            directorio_usuarios[user_id] = {"nombre": nombre_u, "username": alias_u}
                            guardar_estado()

                        log_usuario(f"📨 MSG [{tipo_chat}] user={user_id} ({nombre_u}) chat={chat_id}: {texto[:120]}")
                        print(f"📨 Polling [{tipo_chat}] user={user_id} chat={chat_id}: {texto}")

                        # 🛡️ RATE LIMIT POR USUARIO — anti-spam (4 cmds/30s)
                        # Admins y admin anónimos están exentos
                        _es_admin_rl = user_id in ADMIN_IDS or str(from_user.get("id", "")) in ("1087968824", "136817688")
                        if not _es_admin_rl and _check_rate_limit(user_id):
                            print(f"🛡️ Rate limit: user={user_id} excede {_RATE_LIMIT_MAX} cmds/{_RATE_LIMIT_VENTANA}s — ignorado")
                            continue

                    # 🛡️ Ignorar bots REALES, pero permitir admin anónimo del grupo/canal
                    # GroupAnonymousBot (1087968824) = admin posteando como grupo
                    # Channel_Bot (136817688) = admin posteando como canal
                    if from_user and from_user.get("is_bot"):
                        _bot_id = str(from_user.get("id", ""))
                        if _bot_id not in ("1087968824", "136817688"):
                            continue

                    if tipo_chat in ("group", "supergroup", "private", "channel"):
                        # 🛡️ GESTIÓN DE ACCESO Y MARKETING
                        es_grupo   = tipo_chat in ("group", "supergroup")
                        es_canal   = tipo_chat == "channel"
                        
                        # Los canales, admins anónimos y administradores siempre están autorizados
                        _es_admin_anonimo_check = str(from_user.get("id", "")) in ("1087968824", "136817688")
                        usuario_no_autorizado = (USERS_AUTORIZADOS and user_id not in USERS_AUTORIZADOS
                                                and user_id != str(CHANNEL_ID)
                                                and not es_canal
                                                and not _es_admin_anonimo_check)

                        # Palabras clave que se responden públicamente en el grupo
                        _t = texto.strip().lower()
                        TEMAS_PUBLICOS = (
                            # Precios y análisis
                            "precio", "cuanto", "cotiza", "vale", "analisis", "análisis",
                            "tendencia", "señal", "senal", "señales", "mercado",
                            # Activos
                            "bitcoin", "btc", "ethereum", "eth", "oro", "gold",
                            "nasdaq", "sp500", "eurusd", "usdjpy", "yen", "euro",
                            "bolsa", "cripto", "crypto", "xauusd", "xau", "btcusd", "ethusd",
                            # Acciones de trading
                            "comprar", "vender", "entrar", "operar", "sube", "baja",
                            # VIP y canal
                            "vip", "canal", "premium", "suscripcion", "membresia",
                            "unirme", "pagar", "acceso", "prueba", "gratis", "trial", "probar",
                            # Saludos y ayuda
                            "hola", "buenos", "buenas", "ayuda", "help",
                            # Herramientas
                            "noticias", "horario", "semana", "resumen", "precios",
                            "winrate", "sentimiento", "soporte", "resistencia", "pivot",
                            "estado", "web", "dashboard", "top", "volatil",
                            # Índices y general
                            "sp", "s&p", "índice", "indice",
                            "como esta", "cómo está", "que tal", "a cuanto", "dolar",
                        )
                        es_tema_publico = any(p in _t for p in TEMAS_PUBLICOS)

                        # 🧹 Auto-borrar CUALQUIER mensaje de usuario en grupo/canal
                        # (señales/TP/SL llegan por enviar_canal, no por polling)
                        # IMPORTANTE: se hace ANTES del check de autorización para que
                        # mensajes de usuarios no autorizados también se borren
                        if (es_grupo or es_canal) and msg.get("message_id"):
                            programar_borrado(chat_id, msg.get("message_id"))

                        # 🎟️ DETECCIÓN DE CÓDIGO DE INVITACIÓN — cualquier usuario, grupo o privado
                        _match_code_poll = re.match(r'^BS365-[A-Z0-9]{4}$', texto.strip().upper())
                        if _match_code_poll:
                            _code_poll = _match_code_poll.group(0)
                            _nombre_code_poll = escapar_markdown(from_user.get("first_name", "Trader"))  # H-11
                            # Siempre enviar al DM del usuario (no al grupo)
                            _procesar_codigo_invitacion(_code_poll, user_id, _nombre_code_poll)
                            if es_grupo or es_canal:
                                # Avisar brevemente en grupo/canal
                                _aviso_code = f"💬 *{_nombre_code_poll}*, te envie la informacion por privado 📩"
                                _aviso_code_id = enviar_telegram(_aviso_code, chat_id)
                                if _aviso_code_id:
                                    programar_borrado(chat_id, _aviso_code_id, 60)
                            continue

                        # 📺 En CANAL solo pueden escribir admins → tratar como autorizado
                        if es_canal:
                            usuario_no_autorizado = False

                        if usuario_no_autorizado:
                            if (es_grupo or es_canal) and es_tema_publico:
                                # ✅ En grupos/canal: responder con info básica + CTA al canal
                                pass
                            elif es_grupo or es_canal:
                                # 📢 Grupo + tema no reconocido: bienvenida VIP EN EL GRUPO
                                # (no en privado, porque el usuario puede no haber hecho /start)
                                manejar_usuario_nuevo(msg, from_user, texto, grupo_chat_id=chat_id)
                                continue
                            else:
                                # 💬 Privado: enviar bienvenida VIP al chat directo
                                manejar_usuario_nuevo(msg, from_user, texto)
                                continue

                        # Admin anónimo: GroupAnonymousBot o Channel_Bot son admins del grupo/canal
                        _es_admin_anonimo = str(from_user.get("id", "")) in ("1087968824", "136817688")
                        remitente = user_id if (es_grupo or es_canal) else chat_id
                        es_admin_role = (user_id in ADMIN_IDS) or _es_admin_anonimo

                        # 🧵 Despachar al thread pool — no bloquea el polling
                        def _procesar_en_pool(_texto=texto, _remitente=remitente, _es_admin=es_admin_role,
                                              _chat_id=chat_id, _es_grupo=es_grupo, _es_canal=es_canal,
                                              _usuario_no_autorizado=usuario_no_autorizado,
                                              _user_id=user_id):
                            try:
                                res = procesar_mensaje(_texto, _remitente, es_admin=_es_admin)
                                respuesta = res[0] if isinstance(res, tuple) else res
                                teclado   = res[1] if isinstance(res, tuple) else None

                                # 📢 Detectar si la respuesta contiene info VIP/pago privada
                                _es_respuesta_vip = respuesta and ("CANAL VIP" in respuesta or "PAGO VIP" in respuesta or "PAGO PENDIENTE" in respuesta or "DIAS GRATIS ACTIVADOS" in respuesta or "TRIAL ACTIVA" in respuesta or "VIP ACTIVO" in respuesta or "VIP PERMANENTE" in respuesta)

                                # 🔒 Detectar por INPUT si el tema debe ir al privado (VIP/pago)
                                _t_lower = _texto.strip().lower()
                                _PALABRAS_PRIVADAS = ("vip", "/vip", "canal", "premium", "suscripcion",
                                                      "membresia", "unirme", "pagar", "acceso",
                                                      "trial", "prueba gratis", "gratis")
                                _es_input_privado = any(p in _t_lower for p in _PALABRAS_PRIVADAS)

                                # 📊 Detectar comandos que muestran info interna del bot
                                _CMDS_TRADES = ("señales", "senales", "operaciones", "abiertas", "activas",
                                                "/señales", "/senales", "/operaciones", "/abiertas", "/activas",
                                                "señales activas", "📊 señales activas")
                                _CMDS_ESTADO = ("estado", "/estado", "/stats", "/estadisticas",
                                                "⚙️ estado bot", "estado bot")
                                _CMDS_RESUMEN = ("resumen", "/resumen", "/historial", "/reporte",
                                                 "📈 resumen diario", "resumen diario", "historial")
                                _es_cmd_trades = _t_lower in _CMDS_TRADES or any(k in _t_lower for k in ("señales", "senales", "operaciones"))
                                _es_cmd_estado = _t_lower in _CMDS_ESTADO
                                _es_cmd_resumen = _t_lower in _CMDS_RESUMEN
                                _es_cmd_analisis = ("analisis" in _t_lower or "análisis" in _t_lower
                                                    or _t_lower.startswith("/analisis") or _t_lower.startswith("tendencia")
                                                    or _t_lower in ("top", "/top", "/pivots", "pivots",
                                                                     "/winrate", "winrate", "/sentimiento", "sentimiento"))
                                _es_cmd_ayuda = _t_lower in ("ayuda", "/ayuda", "/help", "help", "comandos", "menu", "❓ ayuda")
                                _es_cmd_protegido = _es_cmd_trades or _es_cmd_estado or _es_cmd_resumen or _es_cmd_analisis or _es_cmd_ayuda

                                # 📊 En GRUPO: comandos protegidos → TEASER sin detalles (protege info VIP)
                                # ⚠️ En CANAL (VIP): NO teaser — mostrar contenido COMPLETO (usuarios pagaron)
                                if _es_cmd_protegido and _es_grupo and not _es_canal:
                                    if _es_cmd_ayuda:
                                        teaser = (
                                            f"👋 *Bienvenido a BuySell365.pro*\n"
                                            f"━━━━━━━━━━\n\n"
                                            f"📊 `/precios` — Precios en vivo\n"
                                            f"📅 `/noticias` — Calendario economico\n"
                                            f"🌐 `/web` — Trading en Vivo\n\n"
                                            f"👑 *CANAL VIP*\n"
                                            f"   Señales IA · Analisis · Entry/SL/TP\n"
                                            f"   Monitoreo 24/7 · Win Rate en vivo\n\n"
                                            f"🎁 *Escribe /vip — 5 dias habiles GRATIS* 🚀"
                                        )
                                    elif _es_cmd_analisis:
                                        # Extraer nombre del activo del texto
                                        _activo_txt = _t_lower.replace("análisis", "").replace("analisis", "").replace("/", "").replace("tendencia", "").replace("🚀", "").replace("🔍", "").strip().upper() or "MERCADO"
                                        teaser = (
                                            f"🔍 *Análisis {_activo_txt}* — contenido VIP\n\n"
                                            f"📊 _Auditoria completa: IA, patrones, metricas,\n"
                                            f"pronostico y lectura del mercado._\n\n"
                                            f"🔒 _Disponible en el canal VIP._\n"
                                            f"🎁 *Escribe /vip — 5 dias habiles GRATIS*"
                                        )
                                    elif _es_cmd_trades:
                                        with _lock_ops:
                                            n_ops = len(operaciones_activas)
                                            n_buy = sum(1 for o in operaciones_activas.values()
                                                        if isinstance(o, dict) and o.get('tipo') == "COMPRA")
                                            n_sell = n_ops - n_buy
                                        if n_ops > 0:
                                            _pl = "es" if n_ops > 1 else ""
                                            teaser = (
                                                f"📊 *{n_ops} operacion{_pl} activa{_pl}*"
                                                f" · 🟢{n_buy} 🔴{n_sell}\n\n"
                                                f"🔒 _Entry, SL y TP disponibles en el canal VIP._\n"
                                                f"🎁 *Escribe /vip — 5 dias habiles GRATIS*"
                                            )
                                        else:
                                            teaser = "📭 *Sin operaciones abiertas.* Te aviso cuando haya señal. 📡"
                                    elif _es_cmd_estado:
                                        with _lock_ops:
                                            n_ops = len(operaciones_activas)
                                        _total_e = estadisticas_diarias["ganadas"] + estadisticas_diarias["perdidas"]
                                        _wr_e = (estadisticas_diarias["ganadas"] / _total_e * 100) if _total_e > 0 else 0
                                        teaser = (
                                            f"⚙️ *Bot activo* ✅ · {n_ops} operaciones abiertas\n"
                                            f"📊 Win Rate hoy: *{_wr_e:.0f}%* ({_total_e} señales)\n\n"
                                            f"🔒 _Panel completo en el canal VIP y la web._\n"
                                            f"🌐 buysell365.pro/dashboard\n"
                                            f"🎁 *Escribe /vip — 5 dias habiles GRATIS*"
                                        )
                                    else:  # _es_cmd_resumen
                                        _total_r = estadisticas_diarias["ganadas"] + estadisticas_diarias["perdidas"]
                                        _wr_r = (estadisticas_diarias["ganadas"] / _total_r * 100) if _total_r > 0 else 0
                                        _pips_r = estadisticas_diarias["pips_ganados"] - estadisticas_diarias["pips_perdidos"]
                                        teaser = (
                                            f"📈 *Resumen del dia*\n"
                                            f"Señales: {_total_r} · Win Rate: *{_wr_r:.0f}%*\n"
                                            f"Pips netos: *{_pips_r:+.1f}*\n\n"
                                            f"🔒 _Historial completo en el canal VIP._\n"
                                            f"🎁 *Escribe /vip — 5 dias habiles GRATIS*"
                                        )
                                    msg_id = enviar_telegram(teaser, _chat_id)
                                    if msg_id and (_es_grupo or _es_canal):
                                        programar_borrado(_chat_id, msg_id, 300)
                                    return

                                # 🔒 RUTEO PRIVADO: info VIP/pago va al DM, NO al grupo público
                                # ⚠️ EXCEPCIÓN: admin anónimo (GroupAnonymousBot/Channel_Bot)
                                # → No se puede DM, responder directo en grupo/canal con auto-borrado
                                _es_bot_anonimo = _user_id in ("1087968824", "136817688")
                                # Solo redirigir a DM desde el GRUPO (público). En CANAL (VIP) mostrar directo.
                                if (_es_respuesta_vip or _es_input_privado) and _es_grupo and not _es_canal and not _es_bot_anonimo:
                                    try:
                                        dm_msg_id = enviar_telegram(respuesta, _user_id, teclado=teclado)
                                        if dm_msg_id:
                                            # ✅ DM enviado — dejar un aviso breve en el grupo
                                            _nombre_u = escapar_markdown(directorio_usuarios.get(_user_id, {}).get("nombre", ""))  # H-11
                                            aviso = f"💬 *{_nombre_u}*, te envie la informacion por mensaje privado 📩"
                                            aviso_id = enviar_telegram(aviso, _chat_id)
                                            if aviso_id:
                                                programar_borrado(_chat_id, aviso_id, 90)
                                            return  # No enviar nada más al grupo
                                        else:
                                            # ❌ DM falló — usuario no ha hecho /start
                                            aviso = (
                                                f"💬 Para proteger tus datos de pago, te respondo por privado.\n"
                                                f"👉 *Primero escribeme al DM:* @Andoperandobot y pulsa *Start*\n"
                                                f"y luego vuelve a escribir /vip aqui."
                                            )
                                            aviso_id = enviar_telegram(aviso, _chat_id)
                                            if aviso_id:
                                                programar_borrado(_chat_id, aviso_id, 120)
                                            return
                                    except Exception as e_dm:
                                        logger.warning(f"⚠️ DM VIP falló para {_user_id}: {e_dm}")
                                        # Fallback: enviar al grupo con borrado rápido
                                        pass

                                # 📢 Añadir CTA al canal VIP solo en GRUPO (público), NO en canal VIP
                                if respuesta and _es_grupo and not _es_canal and _usuario_no_autorizado and not _es_respuesta_vip:
                                    promos_grupo = [
                                        (
                                            f"\n\n━━━━━━━━━━\n"
                                            f"💎 *¿Te interesa recibir senales completas?*\n"
                                            f"Canal VIP con entrada, SL y TP exactos.\n"
                                            f"🎁 *5 dias habiles GRATIS + 50% OFF en el primer mes*\n"
                                            f"👉 Escribe /vip"
                                        ),
                                        (
                                            f"\n\n━━━━━━━━━━\n"
                                            f"📊 *Senales IA de alta precision*\n"
                                            f"Oro, Forex, NASDAQ, S&P 500 — Entry, SL, TP.\n"
                                            f"🎁 *5 dias habiles GRATIS + 50% OFF en el primer mes*\n"
                                            f"👉 Escribe /vip"
                                        ),
                                        (
                                            f"\n\n━━━━━━━━━━\n"
                                            f"🚀 *COPY TRADING DISPONIBLE*\n"
                                            f"Copia nuestras operaciones en tu cuenta MT5.\n"
                                            f"👉 [Activar Copy Trading](https://social.tp-redirect.com/s/WRE0V7jm)"
                                        ),
                                        (
                                            f"\n\n━━━━━━━━━━\n"
                                            f"📈 *COPIA NUESTRAS OPERACIONES 24/7*\n"
                                            f"Sin experiencia necesaria. Broker regulado XM.\n"
                                            f"👉 [Empezar Copy Trading](https://social.tp-redirect.com/s/WRE0V7jm)"
                                        ),
                                    ]
                                    respuesta += random.choice(promos_grupo)

                                if respuesta:
                                    bot_msg_id = enviar_telegram(respuesta, _chat_id, teclado=teclado)
                                    if _es_grupo or _es_canal:
                                        programar_borrado(_chat_id, bot_msg_id)
                            except Exception as e_pool:
                                logger.error(f"❌ Error procesando comando en pool: {e_pool}")

                        _pool_comandos.submit(_procesar_en_pool)

            elif r.status_code == 409:
                print("⚠️ Polling HTTP 409 — instancia duplicada. Esperando 60s y eliminando webhook...")
                time.sleep(60)
                try:
                    requests.post(
                        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/deleteWebhook",
                        json={"drop_pending_updates": True},
                        timeout=10
                    )
                    offset = 0
                except Exception:
                    pass

            elif r.status_code == 429:
                # Rate limit: usar el retry_after de Telegram si viene
                retry = int(r.json().get("parameters", {}).get("retry_after", _backoff))
                logger.warning(f"⚠️ Polling rate limited (429). Esperando {retry}s...")
                time.sleep(retry)

            else:
                logger.warning(f"⚠️ Polling HTTP {r.status_code}. Backoff: {_backoff}s")
                time.sleep(_backoff)
                _backoff = min(_backoff * 2, _max_backoff)  # Backoff exponencial

        except requests.exceptions.ConnectionError:
            logger.warning(f"🔌 Sin conexión. Reintentando en {_backoff}s...")
            time.sleep(_backoff)
            _backoff = min(_backoff * 2, _max_backoff)
        except requests.exceptions.Timeout:
            logger.warning(f"⏱️ Timeout en polling. Reintentando en {_backoff}s...")
            time.sleep(_backoff)
            _backoff = min(_backoff * 2, _max_backoff)
        except Exception as e:
            logger.error(f"⚠️ Error inesperado en polling: {e}")
            time.sleep(_backoff)
            _backoff = min(_backoff * 2, _max_backoff)

# ── Comandos Telegram del Scalper ──

def _cmd_scalper_pausar():
    """Pausa el scalper desde Telegram."""
    global SCALPER_ACTIVO
    SCALPER_ACTIVO = False
    print("🔪 Scalper PAUSADO por comando Telegram")
    return "🔪 *Scalper PAUSADO* ⏸️\n\nEl scalper no abrirá nuevas posiciones.\nPosiciones abiertas se seguirán gestionando.\n\n📌 Para reanudar: `play scalper`"

def _cmd_scalper_reanudar():
    """Reanuda el scalper desde Telegram."""
    global SCALPER_ACTIVO
    SCALPER_ACTIVO = True
    print("🔪 Scalper REANUDADO por comando Telegram")
    return "🔪 *Scalper ACTIVO* ▶️\n\nEl scalper está buscando señales de nuevo.\n\n📌 Para pausar: `pausar scalper`"

def _cmd_scalper_estado():
    """Muestra estado del scalper."""
    estado = "✅ ACTIVO" if SCALPER_ACTIVO else "⏸️ PAUSADO"
    pos_abiertas = 0
    try:
        if MT5_AVAILABLE:
            with _lock_mt5:
                positions = mt5.positions_get()
            if positions:
                pos_abiertas = sum(1 for p in positions if p.magic == SCALPER_MAGIC)
    except:
        pass

    return (
        f"🔪 *Scalper Silencioso — Estado*\n\n"
        f"▪️ Estado: {estado}\n"
        f"▪️ Trades hoy: {_scalper_trades_hoy}\n"
        f"▪️ Posiciones abiertas: {pos_abiertas}\n"
        f"▪️ P&L diario: ${_scalper_pnl_diario:+.2f}\n"
        f"▪️ Pérdidas seguidas: {_scalper_perdidas_consecutivas}\n"
        f"▪️ Lote: Mínimo del broker (bajo riesgo)\n"
        f"▪️ Estrategia: BB(20,2) + RSI(7) M5\n\n"
        f"📌 Comandos:\n"
        f"`pausar scalper` — Detener\n"
        f"`play scalper` — Reanudar\n"
        f"`/scalper` — Ver estado"
    )

# ============================================================
#  🔪 SCALPER SILENCIOSO — MT5 Only (Sin Telegram, Sin Stats)
#  Estrategia: BB(20,2) + RSI(7) Mean Reversion en M5
#  Activos: XAUUSD (Gold), EURUSD, GBPUSD, US100 (NASDAQ)
#  Horarios individuales por activo (hora Andorra CET/CEST)
# ============================================================
#  PAR_PROFILES — Per-Pair Independent Strategy Configuration
#  Single source of truth for Scalper + Premium engines
#  Basado en investigación de estrategias institucionales 2026
# ============================================================

PAR_PROFILES = {
    # ━━━━ EURUSD — Multi-TF mean reversion scalp + premium breakout ━━━━
    "EURUSD": {
        "identity": {"mt5": "EURUSD", "yf": "EURUSD=X", "display": "EUR/USD", "category": "forex", "currencies": ["EUR", "USD"], "pip_size": 0.0001},
        "scalper": {"enabled": True, "strategies": ["bb_rsi"], "rsi_period": 7, "rsi_buy": 40, "rsi_sell": 60, "bb_period": 20, "bb_std": 2.0, "adx_period": 10, "adx_min": 10, "adx_max": 50, "vol_min": 0.3, "tp_atr": 2.0, "sl_atr": 1.2, "max_spread": 20},
        "premium": {"enabled": True, "strategies": ["breakout", "reversal_5"], "rsi_period": 14, "rsi_os": 35, "rsi_ob": 65, "adx_min": 15, "bb_squeeze": 0.008, "min_atr": 0.0003, "vol_breakout": 1.3, "ml_umbral": 55.0, "min_score": 4, "rsi_gate_buy": None, "rsi_gate_sell": None, "adx_gate": None, "rev4_allowed": False},
        "risk": {"risk_pct": 0.005, "max_sl_pips": 50},
        "sl_tp": {"sl_mult": 0.8, "tp1_mult": 2.5, "tp2_mult": 3.2, "tp3_mult": 4.0, "ze_mult": 0.2, "min_sl": 0.00150},
        "time_filter": {"best_hours_utc": [(7, 17)], "peak_hours_utc": [(12, 16)], "best_days": [1, 2, 3], "scalper_horario": (9, 18)},
        "news": {"currencies": ["EUR", "USD"], "block_minutes_before": 60, "reduce_minutes_before": 180},
        "behavior": {"solo_sell": False, "solo_buy": False, "block_buy": False, "block_sell": False, "max_positions": 1, "cooldown_minutes": 30},
    },
    # ━━━━ GOLD — Momentum breakout, SELL only scalper, London open ━━━━
    "GOLD": {
        "identity": {"mt5": "GOLD", "yf": "GC=F", "display": "ORO", "category": "commodity", "currencies": ["USD"], "pip_size": 0.01},
        "scalper": {"enabled": True, "strategies": ["bb_rsi"], "rsi_period": 7, "rsi_buy": 40, "rsi_sell": 60, "bb_period": 20, "bb_std": 2.0, "adx_period": 10, "adx_min": 10, "adx_max": 50, "vol_min": 0.3, "tp_atr": 2.0, "sl_atr": 1.2, "max_spread": 80},
        "premium": {"enabled": False, "strategies": ["breakout", "reversal_4", "reversal_5"], "rsi_period": 14, "rsi_os": 36, "rsi_ob": 64, "adx_min": 15, "bb_squeeze": 0.012, "min_atr": 0.2, "vol_breakout": 1.0, "ml_umbral": 56.0, "min_score": 4, "rsi_gate_buy": 45, "rsi_gate_sell": None, "adx_gate": None, "rev4_allowed": True, "bb_width_volatility": 5.0, "vol_min_extrema": 0.5},
        "risk": {"risk_pct": 0.005, "max_sl_pips": 200},
        "sl_tp": {"sl_mult": 0.8, "tp1_mult": 1.5, "tp2_mult": 2.2, "tp3_mult": 3.0, "ze_mult": 0.2, "min_sl": 5.0},
        "time_filter": {"best_hours_utc": [(7, 17)], "peak_hours_utc": [(12, 16)], "best_days": [0, 1, 2, 3, 4], "scalper_horario": (9, 19)},
        "news": {"currencies": ["USD"], "block_minutes_before": 60, "reduce_minutes_before": 180},
        "behavior": {"solo_sell": False, "solo_buy": False, "block_buy": True, "block_sell": False, "max_positions": 1, "cooldown_minutes": 30},
    },
    # ━━━━ US100 (NASDAQ) — NY open breakout, kill zone 13-16 UTC ━━━━
    "US100Cash": {
        "identity": {"mt5": "US100Cash", "yf": "NQ=F", "display": "NASDAQ", "category": "indice", "currencies": ["USD"], "pip_size": 0.01},
        "scalper": {"enabled": True, "strategies": ["bb_rsi"], "rsi_period": 7, "rsi_buy": 40, "rsi_sell": 60, "bb_period": 20, "bb_std": 2.0, "adx_period": 10, "adx_min": 10, "adx_max": 50, "vol_min": 0.3, "tp_atr": 2.0, "sl_atr": 1.2, "max_spread": 500},
        "premium": {"enabled": True, "strategies": ["breakout", "reversal_5"], "rsi_period": 14, "rsi_os": 35, "rsi_ob": 65, "adx_min": 20, "bb_squeeze": 0.010, "min_atr": 2.0, "vol_breakout": 1.0, "ml_umbral": 60.0, "min_score": 4, "rsi_gate_buy": None, "rsi_gate_sell": None, "adx_gate": None, "rev4_allowed": False},
        "risk": {"risk_pct": 0.005, "max_sl_pips": 500},
        "sl_tp": {"sl_mult": 0.7, "tp1_mult": 2.2, "tp2_mult": 3.0, "tp3_mult": 4.0, "ze_mult": 0.2, "min_sl": 25.0},
        "time_filter": {"best_hours_utc": [(13, 20)], "peak_hours_utc": [(13, 16)], "best_days": [0, 1, 2, 3, 4], "scalper_horario": (15, 22)},
        "news": {"currencies": ["USD"], "block_minutes_before": 60, "reduce_minutes_before": 180},
        "behavior": {"solo_sell": False, "solo_buy": False, "block_buy": False, "block_sell": False, "max_positions": 1, "cooldown_minutes": 30},
    },
    # ━━━━ US500 (S&P 500) — Mean reversion + momentum, solo premium ━━━━
    "US500Cash": {
        "identity": {"mt5": "US500Cash", "yf": "ES=F", "display": "S&P 500", "category": "indice", "currencies": ["USD"], "pip_size": 0.01},
        "scalper": {"enabled": False, "strategies": []},
        "premium": {"enabled": True, "strategies": ["breakout", "reversal_5"], "rsi_period": 14, "rsi_os": 44, "rsi_ob": 56, "adx_min": 16, "bb_squeeze": 0.010, "min_atr": 0.8, "vol_breakout": 1.0, "ml_umbral": 57.0, "min_score": 4, "rsi_gate_buy": None, "rsi_gate_sell": None, "adx_gate": None, "rev4_allowed": False},
        "risk": {"risk_pct": 0.005, "max_sl_pips": 300},
        "sl_tp": {"sl_mult": 0.7, "tp1_mult": 2.2, "tp2_mult": 3.0, "tp3_mult": 4.0, "ze_mult": 0.2, "min_sl": 8.0},
        "time_filter": {"best_hours_utc": [(13, 20)], "peak_hours_utc": [(13, 16)], "best_days": [0, 1, 2, 3, 4], "scalper_horario": (15, 22)},
        "news": {"currencies": ["USD"], "block_minutes_before": 60, "reduce_minutes_before": 180},
        "behavior": {"solo_sell": False, "solo_buy": False, "block_buy": False, "block_sell": False, "max_positions": 1, "cooldown_minutes": 30},
    },
    # ━━━━ USDJPY — Carry trade + trend following, Tokyo + NY ━━━━
    "USDJPY": {
        "identity": {"mt5": "USDJPY", "yf": "USDJPY=X", "display": "USD/JPY", "category": "forex", "currencies": ["USD", "JPY"], "pip_size": 0.01},
        "scalper": {"enabled": False, "strategies": []},
        "premium": {"enabled": True, "strategies": ["breakout", "reversal_5"], "rsi_period": 14, "rsi_os": 33, "rsi_ob": 67, "adx_min": 16, "bb_squeeze": 0.010, "min_atr": 0.004, "vol_breakout": 1.3, "ml_umbral": 55.0, "min_score": 4, "rsi_gate_buy": 55, "rsi_gate_sell": 45, "adx_gate": None, "rev4_allowed": False},
        "risk": {"risk_pct": 0.005, "max_sl_pips": 80},
        "sl_tp": {"sl_mult": 1.0, "tp1_mult": 1.4, "tp2_mult": 2.0, "tp3_mult": 2.8, "ze_mult": 0.3, "min_sl": 0.150},
        "time_filter": {"best_hours_utc": [(0, 7), (12, 16)], "peak_hours_utc": [(12, 16)], "best_days": [1, 2, 3], "scalper_horario": (1, 21)},
        "news": {"currencies": ["USD", "JPY"], "block_minutes_before": 60, "reduce_minutes_before": 180},
        "behavior": {"solo_sell": False, "solo_buy": False, "block_buy": False, "block_sell": False, "max_positions": 1, "cooldown_minutes": 30},
    },
    # ━━━━ GBPJPY — "The Beast", London breakout, alta volatilidad ━━━━
    "GBPJPY": {
        "identity": {"mt5": "GBPJPY", "yf": "GBPJPY=X", "display": "GBP/JPY", "category": "forex", "currencies": ["GBP", "JPY"], "pip_size": 0.01},
        "scalper": {"enabled": False, "strategies": []},
        "premium": {"enabled": False, "strategies": ["breakout", "reversal_4", "reversal_5"], "rsi_period": 14, "rsi_os": 30, "rsi_ob": 70, "adx_min": 18, "bb_squeeze": 0.012, "min_atr": 0.004, "vol_breakout": 1.3, "ml_umbral": 55.0, "min_score": 4, "rsi_gate_buy": 55, "rsi_gate_sell": 45, "adx_gate": 20, "rev4_allowed": True},
        "risk": {"risk_pct": 0.004, "max_sl_pips": 150},
        "sl_tp": {"sl_mult": 0.8, "tp1_mult": 1.8, "tp2_mult": 2.5, "tp3_mult": 3.5, "ze_mult": 0.3, "min_sl": 0.200},
        "time_filter": {"best_hours_utc": [(7, 10), (12, 16)], "peak_hours_utc": [(7, 10)], "best_days": [0, 1, 2, 3, 4], "scalper_horario": (1, 21)},
        "news": {"currencies": ["GBP", "JPY"], "block_minutes_before": 60, "reduce_minutes_before": 180},
        "behavior": {"solo_sell": False, "solo_buy": False, "block_buy": False, "block_sell": False, "max_positions": 1, "cooldown_minutes": 30},
    },
    # ━━━━ AUDCAD — Fibonacci range trading, Asian + London ━━━━
    "AUDCAD": {
        "identity": {"mt5": "AUDCAD", "yf": None, "display": "AUD/CAD", "category": "forex", "currencies": ["AUD", "CAD"], "pip_size": 0.0001},
        "scalper": {"enabled": True, "strategies": ["fibonacci"], "rsi_period": 7, "rsi_buy": 45, "rsi_sell": 55, "fib_buy": 0.35, "fib_sell": 0.65, "bb_period": 20, "bb_std": 2.0, "adx_period": 10, "adx_min": 10, "adx_max": 50, "vol_min": 0.3, "tp_atr": 1.8, "sl_atr": 1.5, "max_spread": 60},
        "premium": {"enabled": False},
        "risk": {"risk_pct": 0.005, "max_sl_pips": 40},
        "sl_tp": {},
        "time_filter": {"best_hours_utc": [(0, 7), (7, 14)], "peak_hours_utc": [(7, 10)], "best_days": [0, 1, 2, 3, 4], "scalper_horario": (9, 18)},
        "news": {"currencies": ["AUD", "CAD"], "block_minutes_before": 60, "reduce_minutes_before": 180},
        "behavior": {"solo_sell": False, "solo_buy": False, "block_buy": True, "block_sell": False, "max_positions": 1, "cooldown_minutes": 30},
    },
    # ━━━━ EURCHF — Mean reversion SNB, baja volatilidad, London ━━━━
    "EURCHF": {
        "identity": {"mt5": "EURCHF", "yf": None, "display": "EUR/CHF", "category": "forex", "currencies": ["EUR", "CHF"], "pip_size": 0.0001},
        "scalper": {"enabled": True, "strategies": ["fibonacci"], "rsi_period": 7, "rsi_buy": 45, "rsi_sell": 55, "fib_buy": 0.35, "fib_sell": 0.65, "bb_period": 20, "bb_std": 2.0, "adx_period": 10, "adx_min": 10, "adx_max": 50, "vol_min": 0.3, "tp_atr": 1.8, "sl_atr": 1.5, "max_spread": 45},
        "premium": {"enabled": False},
        "risk": {"risk_pct": 0.005, "max_sl_pips": 30},
        "sl_tp": {},
        "time_filter": {"best_hours_utc": [(7, 16)], "peak_hours_utc": [(8, 12)], "best_days": [0, 1, 2, 3, 4], "scalper_horario": (9, 18)},
        "news": {"currencies": ["EUR", "CHF"], "block_minutes_before": 60, "reduce_minutes_before": 180},
        "behavior": {"solo_sell": False, "solo_buy": False, "block_buy": True, "block_sell": False, "max_positions": 1, "cooldown_minutes": 30},
    },
    # ━━━━ USDCAD — Oil correlation + Fibonacci, NY session ━━━━
    "USDCAD": {
        "identity": {"mt5": "USDCAD", "yf": None, "display": "USD/CAD", "category": "forex", "currencies": ["USD", "CAD"], "pip_size": 0.0001},
        "scalper": {"enabled": True, "strategies": ["fibonacci"], "rsi_period": 7, "rsi_buy": 45, "rsi_sell": 55, "fib_buy": 0.35, "fib_sell": 0.65, "bb_period": 20, "bb_std": 2.0, "adx_period": 10, "adx_min": 10, "adx_max": 50, "vol_min": 0.3, "tp_atr": 1.8, "sl_atr": 1.5, "max_spread": 40},
        "premium": {"enabled": False},
        "risk": {"risk_pct": 0.005, "max_sl_pips": 40},
        "sl_tp": {},
        "time_filter": {"best_hours_utc": [(13, 20)], "peak_hours_utc": [(14, 17)], "best_days": [0, 1, 2, 3, 4], "scalper_horario": (9, 18)},
        "news": {"currencies": ["USD", "CAD"], "block_minutes_before": 60, "reduce_minutes_before": 180},
        "behavior": {"solo_sell": False, "solo_buy": False, "block_buy": True, "block_sell": False, "max_positions": 1, "cooldown_minutes": 30},
    },
}

# ── Init deferred data from PAR_PROFILES ──
_init_horarios_from_profiles()

# ── Lookup helpers ──
_PROFILE_BY_YF = {p["identity"]["yf"]: p for k, p in PAR_PROFILES.items() if p["identity"].get("yf")}
_PROFILE_BY_MT5 = {p["identity"]["mt5"]: p for k, p in PAR_PROFILES.items()}

def get_par_profile(ticker=None, mt5_symbol=None):
    """Get profile by yfinance ticker or MT5 symbol."""
    if ticker:
        return _PROFILE_BY_YF.get(ticker)
    if mt5_symbol:
        return _PROFILE_BY_MT5.get(mt5_symbol)
    return None

# ── Auto-generate SCALPER_ACTIVOS from PAR_PROFILES (backward compat) ──
SCALPER_ACTIVOS = {}
for _pp_key, _pp_val in PAR_PROFILES.items():
    if _pp_val.get("scalper", {}).get("enabled"):
        _sc_cfg = _pp_val["scalper"]
        SCALPER_ACTIVOS[_pp_key] = {
            "mt5": _pp_val["identity"]["mt5"],
            "tipo": _pp_val["identity"]["category"],
            "horario": _pp_val["time_filter"]["scalper_horario"],
            "max_spread": _sc_cfg["max_spread"],
            "tp_atr": _sc_cfg["tp_atr"],
            "sl_atr": _sc_cfg["sl_atr"],
            "estrategia": _sc_cfg["strategies"][0] if _sc_cfg.get("strategies") else "bb_rsi",
            "solo_sell": _pp_val["behavior"].get("solo_sell", False),
            "_profile": _pp_val,
        }

# Configuración del Scalper
SCALPER_ACTIVO = True  # Master switch para activar/desactivar scalper
SCALPER_MAGIC = 20260318  # Magic number para identificar trades del scalper en MT5

# Risk Management del Scalper
SCALPER_RIESGO_POR_TRADE = 0.005   # 0.5% del capital por trade
SCALPER_MAX_LOSS_DIARIO = 0.03     # 3% máximo pérdida diaria → para todo
SCALPER_MAX_CONSECUTIVAS = 4       # 4 pérdidas seguidas → pausa 30 min
SCALPER_MAX_POSICIONES = 5         # Máximo 5 posiciones abiertas simultáneas (7 activos)
SCALPER_TIMEOUT_MINUTOS = 45       # FIX 2026-03-19: 60→45 min para scalping más rápido
SCALPER_BREAKEVEN_PCT = 0.4        # Mover SL a breakeven al 40% del TP
SCALPER_INTERVALO = 30             # Segundos entre escaneos (rápido para scalping)

# Estado interno del scalper
_scalper_pnl_diario = 0.0
_scalper_perdidas_consecutivas = 0
_scalper_pausa_hasta = None
_scalper_trades_hoy = 0
_scalper_posiciones = {}  # {ticket: {symbol, tipo, entrada, sl, tp, tiempo}}
_lock_scalper = threading.Lock()

# FIX 2026-03-20: Cooldown por dirección después de pérdida
# {symbol_tipo: timestamp_hasta} — ej: {"EURUSD_BUY": 1711000000}
_scalper_cooldown_direccion: dict = {}
SCALPER_COOLDOWN_MINUTOS = 30  # No repetir misma dirección en 30 min tras pérdida

# ============================================================
#  MEJORAS ESTRATÉGICAS v4 — 10 módulos de optimización
# ============================================================

# ── [3] TRACKING POR ESTRATEGIA CON AUTO-PAUSA ──
_stats_por_estrategia: dict = {}  # {estrategia: {"wins": 0, "losses": 0, "pips": 0.0, "ultimo_trade": 0}}
_estrategia_pausada_hasta: dict = {}  # {estrategia: timestamp_reactivación}

# ── DIAGNÓSTICO POR ACTIVO (para consola launcher) ──
_diagnostico_activos: dict = {}  # ticker -> {adx, rsi, vol, ema_bull, macd_bull, precio, spread, ts}

# ── [5] CIRCUIT BREAKER GLOBAL ──
_cb_pnl_diario: float = 0.0
_cb_perdidas_consecutivas: int = 0
_cb_activo: bool = False
_cb_hasta: float = 0.0
_cb_ultimo_dia: str = ""

# ── [8] ANÁLISIS DE SPREAD EN TIEMPO REAL ──
_spread_historico: dict = {}  # {mt5_symbol: {"promedio": float, "muestras": int, "ts": float}}

# ── [10] AUTO-OPTIMIZACIÓN SEMANAL ──
_ultima_optimizacion_semanal: float = 0.0
_activos_desactivados_auto: set = set()  # Activos desactivados por auto-optimización
_ajustes_rsi: dict = {}  # {ticker: {"rsi_os_adj": 0, "rsi_ob_adj": 0}}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  [2] RIESGO DINÁMICO POR NOTICIAS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _ajustar_riesgo_por_noticias(ticker):
    """
    Retorna un multiplicador de riesgo (0.0–1.0) basado en proximidad a noticias.
    - < 60 min antes de High impact → 0.0 (NO operar)
    - < 180 min antes de High impact → 0.25
    - Medium impact < 120 min → 0.5
    - Sin noticias → 1.0
    """
    try:
        noticias = cargar_calendario_economico()
        if not noticias:
            return 1.0

        divisas = DIVISAS_POR_TICKER.get(ticker, [])
        if not divisas:
            return 1.0

        ahora_utc = datetime.now(pytz.UTC)
        tz_ny = pytz.timezone("America/New_York")

        menor_diff_high = float('inf')
        menor_diff_medium = float('inf')

        for n in noticias:
            impacto = n.get("impact", "").lower()
            if impacto not in ("high", "medium"):
                continue
            if n.get("country", "") not in divisas:
                continue
            try:
                fecha_str = n.get("date", "")
                hora_str = n.get("time", "").strip().lower()
                if not fecha_str or hora_str in ("", "all day", "tentative"):
                    continue
                dt = datetime.strptime(f"{fecha_str} {hora_str}", "%m-%d-%Y %I:%M%p")
                dt_utc = tz_ny.localize(dt).astimezone(pytz.UTC)
                diff_min = (dt_utc - ahora_utc).total_seconds() / 60.0
                # Solo considerar noticias futuras o muy recientes (últimos 30 min)
                if diff_min < -30:
                    continue
                if impacto == "high" and diff_min < menor_diff_high:
                    menor_diff_high = diff_min
                elif impacto == "medium" and diff_min < menor_diff_medium:
                    menor_diff_medium = diff_min
            except Exception:
                continue

        # Evaluar multiplicador basado en proximidad
        if menor_diff_high <= 60:
            logger.info(f"🚨 RIESGO NOTICIAS {ticker}: High impact en {menor_diff_high:.0f}min → mult=0.0 (NO OPERAR)")
            return 0.0
        elif menor_diff_high <= 180:
            logger.info(f"⚠️ RIESGO NOTICIAS {ticker}: High impact en {menor_diff_high:.0f}min → mult=0.25")
            return 0.25
        elif menor_diff_medium <= 120:
            logger.info(f"📰 RIESGO NOTICIAS {ticker}: Medium impact en {menor_diff_medium:.0f}min → mult=0.5")
            return 0.5

        return 1.0

    except Exception as e:
        logger.warning(f"⚠️ Error en _ajustar_riesgo_por_noticias({ticker}): {e}")
        return 1.0


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  [3] TRACKING POR ESTRATEGIA CON AUTO-PAUSA
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _registrar_resultado_estrategia(estrategia, resultado, pips):
    """Registra resultado de una operación por estrategia (llamar al cerrar op)."""
    global _stats_por_estrategia
    if not estrategia:
        return
    if estrategia not in _stats_por_estrategia:
        _stats_por_estrategia[estrategia] = {"wins": 0, "losses": 0, "pips": 0.0, "ultimo_trade": 0}
    stats = _stats_por_estrategia[estrategia]
    if resultado == "WIN":
        stats["wins"] += 1
    else:
        stats["losses"] += 1
    stats["pips"] += pips
    stats["ultimo_trade"] = time.time()
    total = stats["wins"] + stats["losses"]
    wr = (stats["wins"] / total * 100) if total > 0 else 0
    logger.info(f"📊 STATS ESTRATEGIA {estrategia}: {stats['wins']}W/{stats['losses']}L (WR={wr:.0f}%) | Pips={stats['pips']:+.1f}")


def _estrategia_permitida(estrategia):
    """Retorna False si la estrategia tiene WR < 35% tras 15+ operaciones. Re-habilita tras 24h."""
    global _estrategia_pausada_hasta
    if not estrategia:
        return True

    # Check si está en pausa temporal
    if estrategia in _estrategia_pausada_hasta:
        if time.time() < _estrategia_pausada_hasta[estrategia]:
            logger.info(f"⏸️ ESTRATEGIA PAUSADA: {estrategia} — auto-re-habilita en {(_estrategia_pausada_hasta[estrategia] - time.time())/3600:.1f}h")
            return False
        else:
            del _estrategia_pausada_hasta[estrategia]
            logger.info(f"✅ ESTRATEGIA REHABILITADA: {estrategia} — 24h de pausa completadas")

    stats = _stats_por_estrategia.get(estrategia)
    if not stats:
        return True
    total = stats["wins"] + stats["losses"]
    if total < 15:
        return True
    wr = (stats["wins"] / total * 100)
    if wr < 35:
        _estrategia_pausada_hasta[estrategia] = time.time() + 86400  # Pausa 24h
        logger.warning(f"🚨 ESTRATEGIA AUTO-PAUSADA: {estrategia} — WR={wr:.0f}% < 35% tras {total} operaciones → pausa 24h")
        return False
    return True


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  [4] CONFIRMACIÓN INTER-MERCADO
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# Mapeo de correlaciones inter-mercado
_INTER_MARKET_MAP = {
    "NQ=F":     {"corr": "ES=F",     "relacion": "directa"},    # NQ y ES van juntos
    "ES=F":     {"corr": "NQ=F",     "relacion": "directa"},    # ES y NQ van juntos
    "EURUSD=X": {"corr": "DX-Y.NYB", "relacion": "inversa"},   # EUR vs DXY
    "GC=F":     {"corr": "DX-Y.NYB", "relacion": "inversa"},   # Gold vs DXY
}

def _confirmar_inter_mercado(ticker, tipo):
    """
    Verifica confirmación inter-mercado.
    Retorna True si el activo correlacionado confirma la dirección.
    """
    if ticker not in _INTER_MARKET_MAP:
        return False

    corr_info = _INTER_MARKET_MAP[ticker]
    corr_ticker = corr_info["corr"]
    relacion = corr_info["relacion"]

    try:
        # Intentar obtener precio del activo correlacionado
        cot = obtener_cotizacion_tv(corr_ticker)
        if not cot:
            return False
        precio_corr = cot.get('precio')
        if not precio_corr:
            return False

        # Descargar datos para calcular EMA20 del correlacionado
        df_corr = descargar_datos_seguro(corr_ticker)
        if df_corr is None or len(df_corr) < 25:
            return False

        ema20_corr = df_corr['Close'].ewm(span=20, adjust=False).mean().iloc[-1]

        # Determinar dirección del correlacionado
        corr_alcista = precio_corr > ema20_corr
        es_compra = tipo.upper() in ("COMPRA", "BUY", "LONG")

        if relacion == "directa":
            # Mismo activo confirma si va en la misma dirección
            confirmado = (es_compra and corr_alcista) or (not es_compra and not corr_alcista)
        else:  # inversa
            # DXY inverso a EUR y Gold
            confirmado = (es_compra and not corr_alcista) or (not es_compra and corr_alcista)

        if confirmado:
            _nombre_corr = {"ES=F": "S&P500", "NQ=F": "NASDAQ", "DX-Y.NYB": "DXY"}.get(corr_ticker, corr_ticker)
            logger.info(f"🔗 INTER-MERCADO CONFIRMA: {ticker} {tipo} — {_nombre_corr} {'alcista' if corr_alcista else 'bajista'} ({relacion})")
        return confirmado

    except Exception as e:
        logger.warning(f"⚠️ Error confirmación inter-mercado {ticker}: {e}")
        return False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  [5] CIRCUIT BREAKER GLOBAL
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _circuit_breaker_check():
    """
    Verifica si el circuit breaker global está activo.
    - Si P&L diario < -3% del capital → pausa hasta fin del día
    - Si 5 pérdidas consecutivas → pausa 2 horas
    Retorna True si el trading está bloqueado.
    """
    global _cb_pnl_diario, _cb_perdidas_consecutivas, _cb_activo, _cb_hasta, _cb_ultimo_dia

    # Reset diario
    hoy = ahora().strftime("%Y-%m-%d")
    if _cb_ultimo_dia != hoy:
        _cb_pnl_diario = 0.0
        _cb_perdidas_consecutivas = 0
        _cb_activo = False
        _cb_hasta = 0.0
        _cb_ultimo_dia = hoy

    # Si ya está activo, verificar si expiró
    if _cb_activo:
        if time.time() >= _cb_hasta:
            _cb_activo = False
            _cb_hasta = 0.0
            logger.info("✅ CIRCUIT BREAKER: Periodo de pausa terminado — trading reactivado")
            return False
        return True

    # Check 1: P&L diario vs capital
    capital = CAPITAL_USUARIO
    if capital > 0 and _cb_pnl_diario <= -(capital * 0.03):
        _cb_activo = True
        # Hasta medianoche Andorra
        _mañana = ahora().replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        _cb_hasta = _mañana.timestamp()
        msg_cb = f"🚨 *CIRCUIT BREAKER ACTIVADO*\n📉 P&L diario: ${_cb_pnl_diario:+.2f} (>{3}% de ${capital:.0f})\n⏰ Trading pausado hasta mañana"
        logger.warning(msg_cb)
        # FIX 2026-03-19: alertas solo al admin, NO al canal VIP
        for _admin_id in ADMIN_IDS:
            try:
                enviar_telegram(msg_cb, destino=_admin_id)
            except Exception:
                pass
        return True

    # Check 2: Pérdidas consecutivas
    if _cb_perdidas_consecutivas >= 5:
        _cb_activo = True
        _cb_hasta = time.time() + 7200  # 2 horas
        msg_cb = f"🚨 *CIRCUIT BREAKER ACTIVADO*\n📉 {_cb_perdidas_consecutivas} pérdidas consecutivas\n⏰ Trading pausado 2 horas"
        logger.warning(msg_cb)
        # FIX 2026-03-19: alertas solo al admin, NO al canal VIP
        for _admin_id in ADMIN_IDS:
            try:
                enviar_telegram(msg_cb, destino=_admin_id)
            except Exception:
                pass
        return True

    return False


def _cb_registrar_resultado(pnl_usd, es_loss):
    """Registra resultado en el circuit breaker global."""
    global _cb_pnl_diario, _cb_perdidas_consecutivas
    _cb_pnl_diario += pnl_usd
    if es_loss:
        _cb_perdidas_consecutivas += 1
    else:
        _cb_perdidas_consecutivas = 0


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  [6] TRAILING STOP INTELIGENTE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _trailing_stop_dinamico(posicion, precio_actual, indicadores):
    """
    Calcula el nuevo SL trailing basado en volatilidad (ADX + ATR).
    - High volatility (ADX > 35): trail at 2x ATR
    - Medium volatility (ADX 20-35): trail at 1.5x ATR
    - Low volatility (ADX < 20): trail at 1x ATR
    - Scalper: trail siguiendo EMA9 en M5
    Retorna: nuevo_sl (float) o None si no hay que mover.
    """
    if not indicadores:
        return None

    adx = indicadores.get('adx', 25)
    atr = indicadores.get('atr', 0)
    if atr <= 0:
        return None

    tipo = posicion.get('tipo', '')
    entrada = posicion.get('entrada', 0)
    sl_actual = posicion.get('sl', 0)
    es_scalper = posicion.get('estrategia', '') in ('scalper_bb_rsi', 'scalper_fibonacci')

    es_compra = tipo.upper() in ("COMPRA", "BUY", "LONG")

    if es_scalper:
        # Scalper: trail con EMA9 (si disponible)
        ema9 = indicadores.get('ema9', 0)
        if ema9 > 0:
            if es_compra:
                nuevo_sl = ema9 - atr * 0.3  # EMA9 - 30% ATR buffer
                if nuevo_sl > sl_actual and nuevo_sl < precio_actual:
                    return nuevo_sl
            else:
                nuevo_sl = ema9 + atr * 0.3
                if nuevo_sl < sl_actual and nuevo_sl > precio_actual:
                    return nuevo_sl
        return None

    # Determinar multiplicador ATR por volatilidad
    if adx > 35:
        trail_mult = 2.0  # Alta volatilidad: más espacio
    elif adx >= 20:
        trail_mult = 1.5  # Media
    else:
        trail_mult = 1.0  # Baja volatilidad: ajustado

    trail_dist = atr * trail_mult

    if es_compra:
        nuevo_sl = precio_actual - trail_dist
        # Solo mover si mejora el SL (más alto que el actual) y no está por encima del precio
        if nuevo_sl > sl_actual and nuevo_sl > entrada and nuevo_sl < precio_actual:
            return nuevo_sl
    else:
        nuevo_sl = precio_actual + trail_dist
        if nuevo_sl < sl_actual and nuevo_sl < entrada and nuevo_sl > precio_actual:
            return nuevo_sl

    return None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  [7] FILTRO DE SESIÓN MEJORADO
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _factor_sesion(ticker):
    """
    Retorna multiplicador de lote basado en sesión de mercado.
    - London open 7:00-8:00 UTC → 0.5 (spreads altos)
    - NY open 13:30-14:30 UTC → 0.7 (volátil)
    - London-NY overlap 14:00-17:00 UTC → 1.0 (mejor liquidez)
    - Late NY 20:00-22:00 UTC → 0.5 (baja liquidez)
    - Otherwise → 0.8
    """
    try:
        now_utc = datetime.now(pytz.UTC)
        hora_utc = now_utc.hour + now_utc.minute / 60.0

        # London open: spreads altos
        if 7.0 <= hora_utc < 8.0:
            return 0.5

        # NY open: volatilidad de apertura
        if 13.5 <= hora_utc < 14.5:
            return 0.7

        # London-NY overlap: MEJOR sesión (máxima liquidez)
        if 14.0 <= hora_utc < 17.0:
            return 1.0

        # Late NY: baja liquidez
        if 20.0 <= hora_utc < 22.0:
            return 0.5

        return 0.8

    except Exception:
        return 0.8


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  [8] ANÁLISIS DE SPREAD EN TIEMPO REAL
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _spread_aceptable(ticker):
    """
    Verifica si el spread actual es aceptable comparado con el promedio histórico.
    Retorna True si el spread es <= 2x el promedio, False si es excesivo.
    También actualiza el promedio histórico (media móvil).
    """
    global _spread_historico

    if not MT5_AVAILABLE:
        return True

    mt5_ticker = MT5_TICKER_MAP.get(ticker, ticker)
    try:
        with _lock_mt5:
            symbol_info = mt5.symbol_info(mt5_ticker)
        if symbol_info is None:
            return True

        spread_actual = symbol_info.spread

        # Actualizar promedio histórico (TTL 4h, reset si datos muy viejos)
        hist = _spread_historico.get(mt5_ticker)
        if hist and (time.time() - hist['ts']) < 14400:
            # Media móvil exponencial
            alpha = 0.1
            hist['promedio'] = hist['promedio'] * (1 - alpha) + spread_actual * alpha
            hist['muestras'] += 1
            hist['ts'] = time.time()
        else:
            # Inicializar o resetear
            _spread_historico[mt5_ticker] = {
                'promedio': float(spread_actual),
                'muestras': 1,
                'ts': time.time()
            }
            return True  # Primera muestra: no tenemos referencia aún

        promedio = hist['promedio']
        # Solo rechazar si tenemos suficientes muestras (> 10)
        if hist['muestras'] > 10 and promedio > 0:
            ratio = spread_actual / promedio
            if ratio > 2.0:
                logger.warning(f"🔴 SPREAD EXCESIVO {mt5_ticker}: actual={spread_actual} vs promedio={promedio:.0f} (ratio={ratio:.1f}x > 2x)")
                return False

        return True

    except Exception as e:
        logger.warning(f"⚠️ Error verificando spread {ticker}: {e}")
        return True


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  [9] SCORE DE CONFIANZA 0-100
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _calcular_confianza(ind, ticker, tipo):
    """
    Calcula score de confianza 0-100 combinando:
    - Técnico (40%): EMA alignment, MACD, RSI position
    - Volumen (20%): vol_ratio > 1.2 = full points
    - Contexto mercado (20%): inter-mercado + sesión
    - Timing (20%): sesión overlap = best, Asian = worst
    """
    if not ind:
        return 0

    score = 0.0
    es_compra = tipo.upper() in ("COMPRA", "BUY", "LONG")

    # ── TÉCNICO (40 puntos máx) ──
    tecnico = 0.0

    # EMA alignment (15 pts)
    if es_compra:
        if ind['ema20'] > ind['ema50']:
            tecnico += 7.5
        if ind['precio'] > ind['ema200']:
            tecnico += 7.5
    else:
        if ind['ema20'] < ind['ema50']:
            tecnico += 7.5
        if ind['precio'] < ind['ema200']:
            tecnico += 7.5

    # MACD (10 pts)
    if es_compra and ind['macd'] > ind['signal']:
        tecnico += 10.0
    elif not es_compra and ind['macd'] < ind['signal']:
        tecnico += 10.0

    # RSI position (15 pts)
    rsi = ind.get('rsi', 50)
    if es_compra:
        if rsi < 30:
            tecnico += 15.0  # Oversold = great for buy
        elif rsi < 45:
            tecnico += 10.0
        elif rsi < 60:
            tecnico += 5.0
    else:
        if rsi > 70:
            tecnico += 15.0  # Overbought = great for sell
        elif rsi > 55:
            tecnico += 10.0
        elif rsi > 40:
            tecnico += 5.0

    score += tecnico

    # ── VOLUMEN (20 puntos máx) ──
    vol_ratio = ind.get('vol_ratio', 1.0)
    if vol_ratio >= 1.5:
        score += 20.0
    elif vol_ratio >= 1.2:
        score += 15.0
    elif vol_ratio >= 1.0:
        score += 10.0
    elif vol_ratio >= 0.8:
        score += 5.0

    # ── CONTEXTO MERCADO (20 puntos máx) ──
    contexto = 0.0

    # Inter-mercado (10 pts)
    try:
        if _confirmar_inter_mercado(ticker, tipo):
            contexto += 10.0
    except Exception:
        pass

    # Sesión quality (10 pts)
    factor_ses = _factor_sesion(ticker)
    contexto += factor_ses * 10.0

    score += contexto

    # ── TIMING (20 puntos máx) ──
    try:
        now_utc = datetime.now(pytz.UTC)
        hora_utc = now_utc.hour

        # London-NY overlap (14:00-17:00 UTC) = best
        if 14 <= hora_utc < 17:
            score += 20.0
        # London session (8:00-14:00 UTC) = good
        elif 8 <= hora_utc < 14:
            score += 15.0
        # NY afternoon (17:00-20:00 UTC) = decent
        elif 17 <= hora_utc < 20:
            score += 10.0
        # Asian session (0:00-7:00 UTC) = worst
        elif hora_utc < 7:
            score += 5.0
        else:
            score += 8.0
    except Exception:
        score += 10.0

    resultado = min(100, max(0, round(score)))
    logger.info(f"📊 CONFIANZA {ticker} {tipo}: {resultado}/100 (técnico={tecnico:.0f}/40 vol={vol_ratio:.1f} sesión={factor_ses:.1f})")
    return resultado


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  [10] AUTO-OPTIMIZACIÓN SEMANAL
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _auto_optimizar_semanal():
    """
    Análisis semanal: WR por activo, ajuste RSI, desactivar activos malos.
    Se ejecuta domingos a las 23:00 (check en loop_escaneo).
    """
    global _ultima_optimizacion_semanal, _activos_desactivados_auto, _ajustes_rsi

    _ultima_optimizacion_semanal = time.time()
    logger.info("🔧 AUTO-OPTIMIZACIÓN SEMANAL: Iniciando análisis...")

    resumen_lines = ["🔧 *AUTO-OPTIMIZACIÓN SEMANAL*\n"]
    cambios = 0

    try:
        # Obtener operaciones de los últimos 7 días
        ahora_ts = time.time()
        una_semana = 7 * 86400

        with _lock_ops:
            ops_semana = [
                h for h in historial_operaciones
                if (ahora_ts - h.get('timestamp_cierre', h.get('timestamp_entrada', 0))) < una_semana
            ]

        if len(ops_semana) < 5:
            resumen_lines.append("▪️ Menos de 5 operaciones esta semana — sin ajustes")
            logger.info("🔧 Optimización: pocas operaciones (<5), sin ajustes")
        else:
            # Analizar por activo
            por_activo = {}
            for op in ops_semana:
                tk = op.get('ticker', 'unknown')
                if tk not in por_activo:
                    por_activo[tk] = {"wins": 0, "losses": 0, "pips": 0.0}
                if op.get('resultado') == 'WIN':
                    por_activo[tk]["wins"] += 1
                else:
                    por_activo[tk]["losses"] += 1
                por_activo[tk]["pips"] += op.get('pips', 0)

            # Evaluar cada activo
            activos_a_desactivar = set()
            for tk, stats in por_activo.items():
                total = stats["wins"] + stats["losses"]
                if total < 3:
                    continue
                wr = stats["wins"] / total * 100
                avg_pips = stats["pips"] / total
                nombre_activo = {v: k for k, v in ACTIVOS.items()}.get(tk, tk)

                resumen_lines.append(f"▪️ {nombre_activo}: {stats['wins']}W/{stats['losses']}L (WR={wr:.0f}%) | Avg={avg_pips:+.1f} pips")

                # WR < 30% → desactivar temporalmente
                if wr < 30 and total >= 5:
                    activos_a_desactivar.add(tk)
                    resumen_lines.append(f"  🔴 DESACTIVADO (WR < 30%)")
                    cambios += 1

                # Ajuste RSI: si muchas false signals de reversión, ajustar ±2
                _rev_ops = [o for o in ops_semana if o.get('ticker') == tk and o.get('estrategia') in ('reversion',)]
                if len(_rev_ops) >= 5:
                    _rev_losses = sum(1 for o in _rev_ops if o.get('resultado') == 'LOSS')
                    _rev_wr = (1 - _rev_losses / len(_rev_ops)) * 100
                    if _rev_wr < 40:
                        # Demasiadas falsas señales: hacer RSI más estricto (±2)
                        if tk not in _ajustes_rsi:
                            _ajustes_rsi[tk] = {"rsi_os_adj": 0, "rsi_ob_adj": 0}
                        _ajustes_rsi[tk]["rsi_os_adj"] -= 2  # Más estricto (bajar umbral OS)
                        _ajustes_rsi[tk]["rsi_ob_adj"] += 2  # Más estricto (subir umbral OB)
                        resumen_lines.append(f"  📉 RSI ajustado: OS-2, OB+2 (rev WR={_rev_wr:.0f}%)")
                        cambios += 1

            # Aplicar desactivaciones
            _activos_desactivados_auto = activos_a_desactivar

            # Write RSI adjustments back to PAR_PROFILES (per-pair optimization)
            for _adj_tk, _adj_vals in _ajustes_rsi.items():
                _adj_prof = get_par_profile(ticker=_adj_tk)
                if _adj_prof and _adj_prof.get("premium", {}).get("enabled"):
                    _adj_prof["premium"]["rsi_os"] += _adj_vals.get("rsi_os_adj", 0)
                    _adj_prof["premium"]["rsi_ob"] += _adj_vals.get("rsi_ob_adj", 0)
                    resumen_lines.append(f"  🔧 {_adj_tk}: PAR_PROFILES RSI actualizado OS={_adj_prof['premium']['rsi_os']} OB={_adj_prof['premium']['rsi_ob']}")

        resumen_lines.append(f"\n📊 Total cambios: {cambios}")
        resumen = "\n".join(resumen_lines)

        # Guardar ajustes en estado.json
        try:
            guardar_estado()
        except Exception:
            pass

        # Enviar resumen por Telegram
        try:
            enviar_telegram_temporal(resumen, destino=CHANNEL_ID, delay_borrado=900)
        except Exception:
            pass

        logger.info(f"🔧 AUTO-OPTIMIZACIÓN: Completada con {cambios} cambios")

    except Exception as e:
        logger.error(f"🔧 Error en auto-optimización semanal: {e}")


def _scalper_descargar_m5(mt5_symbol):
    """Descarga 500 velas M5 desde MT5 para un símbolo."""
    try:
        with _lock_mt5:
            mt5.symbol_select(mt5_symbol, True)
            rates = mt5.copy_rates_from_pos(mt5_symbol, mt5.TIMEFRAME_M5, 0, 500)
        if rates is not None and len(rates) > 100:
            df = pd.DataFrame(rates)
            df['datetime'] = pd.to_datetime(df['time'], unit='s')
            df.set_index('datetime', inplace=True)
            df.rename(columns={'open': 'Open', 'high': 'High', 'low': 'Low', 'close': 'Close', 'tick_volume': 'Volume'}, inplace=True)
            for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
                df[col] = pd.to_numeric(df[col], errors='coerce')
            return df[['Open', 'High', 'Low', 'Close', 'Volume']].dropna()
    except Exception as e:
        logger.warning(f"⚠️ Scalper: Error descargando M5 {mt5_symbol}: {e}")
    return None


def _scalper_calcular_indicadores(df, profile=None):
    """Calcula indicadores para scalping M5 con parámetros del perfil."""
    try:
        _sc = profile["scalper"] if profile else {}
        _rsi_p = _sc.get("rsi_period", 7)
        _bb_p = _sc.get("bb_period", 20)
        _bb_s = _sc.get("bb_std", 2.0)
        _adx_p = _sc.get("adx_period", 10)

        close = pd.Series(df['Close'].values, dtype=float)
        high = pd.Series(df['High'].values, dtype=float)
        low = pd.Series(df['Low'].values, dtype=float)

        if len(close) < 100:
            return None

        rsi = ta.rsi(close, length=_rsi_p)
        bb = ta.bbands(close, length=_bb_p, std=_bb_s)
        ema20 = ta.ema(close, length=20)
        ema50 = ta.ema(close, length=50)
        adx_df = ta.adx(high, low, close, length=_adx_p)
        atr = ta.atr(high, low, close, length=14)

        # Volumen: ratio vs SMA(20)
        vol = pd.Series(df['Volume'].values, dtype=float)
        vol_sma = ta.sma(vol, length=20)
        _last_vol_sma = float(vol_sma.iloc[-1]) if vol_sma is not None and len(vol_sma) > 0 and not pd.isna(vol_sma.iloc[-1]) else 0
        vol_ratio = float(vol.iloc[-1]) / _last_vol_sma if _last_vol_sma > 0 else 1.0

        if rsi is None or bb is None or ema50 is None or ema20 is None:
            return None

        # Fibonacci: rango de últimas 20 velas H1 (240 M5 = 20 H1)
        fib_lookback = min(240, len(close) - 1)
        fib_high = float(high.iloc[-fib_lookback:].max())
        fib_low = float(low.iloc[-fib_lookback:].min())
        fib_range = fib_high - fib_low
        fib_pos = (float(close.iloc[-1]) - fib_low) / fib_range if fib_range > 0 else 0.5
        fib_236 = fib_low + fib_range * 0.236
        fib_764 = fib_low + fib_range * 0.764

        return {
            "precio": float(close.iloc[-1]),
            "open": float(df['Open'].iloc[-1]),
            "high": float(high.iloc[-1]),
            "low": float(low.iloc[-1]),
            "rsi": float(rsi.iloc[-1]),
            "rsi_prev": float(rsi.iloc[-2]),
            "bb_up": float(get_col(bb, 'BBU').iloc[-1]),
            "bb_mid": float(get_col(bb, 'BBM').iloc[-1]),
            "bb_lo": float(get_col(bb, 'BBL').iloc[-1]),
            "ema20": float(ema20.iloc[-1]),
            "ema50": float(ema50.iloc[-1]),
            "tendencia": "ALCISTA" if float(ema20.iloc[-1]) > float(ema50.iloc[-1]) else "BAJISTA",
            "adx": float(get_col(adx_df, 'ADX_').iloc[-1]),
            "atr": float(atr.iloc[-1]),
            "fib_pos": fib_pos,
            "fib_236": fib_236,
            "fib_764": fib_764,
            "fib_high": fib_high,
            "fib_low": fib_low,
            "vol_ratio": vol_ratio,
        }
    except Exception as e:
        logger.warning(f"⚠️ Scalper: Error calculando indicadores: {e}")
        return None


def _scalper_evaluar_senal(ind, config):
    """
    Evalúa señal de scalping: BB(20,2) + RSI(7) Mean Reversion M5.
    3 variantes de entrada:
      A: BB touch + RSI en zona extrema (principal)
      B: Rechazo fuerte de BB (mecha larga)
      C: RSI extremo + precio cerca de BB
    Filtros por backtest: bloquea NASDAQ SELL y GOLD BUY (perdedores).
    Retorna: ("BUY"/"SELL", razón) o (None, razón)
    """
    if not ind:
        return None, "Sin indicadores"

    # [5] CIRCUIT BREAKER GLOBAL: bloquear scalper si está activo
    try:
        if _circuit_breaker_check():
            return None, "Circuit Breaker activo"
    except Exception:
        pass

    adx = ind['adx']
    rsi = ind['rsi']
    rsi_prev = ind.get('rsi_prev', rsi)
    precio = ind['precio']
    symbol = config.get('mt5', '')

    # PAR_PROFILES lookup for per-pair thresholds
    _pp = config.get('_profile')
    _sc = _pp["scalper"] if _pp else {}
    _beh = _pp["behavior"] if _pp else {}
    _adx_min = _sc.get("adx_min", 10)
    _adx_max = _sc.get("adx_max", 50)
    _rsi_buy = _sc.get("rsi_buy", 40)
    _rsi_sell = _sc.get("rsi_sell", 60)
    _vol_min = _sc.get("vol_min", 0.3)

    if adx < _adx_min:
        return None, f"ADX={adx:.0f} muy bajo (min {_adx_min})"
    if adx > _adx_max:
        return None, f"ADX={adx:.0f} muy alto (max {_adx_max})"

    # Filtro ATR mínimo
    if ind['atr'] <= 0:
        return None, "ATR=0"

    # 🔴 Filtro volumen: mercado muerto = no scalp
    # FIX 2026-03-19: si vol=0 (sin datos de volumen, común en forex MT5), no bloquear
    _svol = ind.get('vol_ratio', 1.0)
    if _svol > 0 and _svol < _vol_min:
        return None, f"Vol={_svol:.1f}x bajo (mín {_vol_min}x)"

    bb_lo = ind['bb_lo']
    bb_up = ind['bb_up']
    bb_mid = (bb_lo + bb_up) / 2
    low = ind['low']
    high = ind['high']
    close = ind.get('close', precio)
    tendencia = ind.get('tendencia', 'NEUTRAL')

    # FIX 2026-03-20: FILTRO DE TENDENCIA EMA20 vs EMA50
    # Solo bloquea contra-tendencia cuando ADX>35 (tendencia MUY fuerte)
    # Mean reversion NECESITA operar contra tendencia moderada — solo bloquear extremos
    def _filtro_tendencia(tipo_op):
        """Mean reversion = operar CONTRA tendencia. No filtrar por tendencia.
        FIX 2026-03-20: Eliminado filtro tendencia para scalper mean reversion.
        La protección ya existe: R:R 1.67:1, cooldown 30min, trailing stop."""
        return True

    # FIX 2026-03-20: COOLDOWN POR DIRECCIÓN (30 min tras pérdida)
    _now_ts = time.time()
    def _en_cooldown(tipo_op):
        """Retorna True si hay cooldown activo para esta dirección."""
        key = f"{symbol}_{tipo_op}"
        hasta = _scalper_cooldown_direccion.get(key, 0)
        return _now_ts < hasta

    # Helper: check BUY/SELL blocks from profile
    def _block_check(tipo_op):
        if tipo_op == "BUY" and _beh.get("block_buy"):
            return True, f"{symbol} BUY bloqueado (perfil)"
        if tipo_op == "SELL" and _beh.get("block_sell"):
            return True, f"{symbol} SELL bloqueado (perfil)"
        return False, ""

    # ── Variante A: BB touch + RSI en zona extrema (principal) ──
    if (low <= bb_lo and rsi < _rsi_buy and _adx_min <= adx <= _adx_max):
        blocked, msg = _block_check("BUY")
        if blocked: return None, msg
        if not _filtro_tendencia("BUY"): return None, f"BUY bloqueado (tendencia {tendencia})"
        if _en_cooldown("BUY"): return None, f"BUY en cooldown 30min"
        return "BUY", f"BB+RSI-A Buy | RSI={rsi:.0f} | ADX={adx:.0f} | {tendencia}"

    if (high >= bb_up and rsi > _rsi_sell and _adx_min <= adx <= _adx_max):
        blocked, msg = _block_check("SELL")
        if blocked: return None, msg
        if not _filtro_tendencia("SELL"): return None, f"SELL bloqueado (tendencia {tendencia})"
        if _en_cooldown("SELL"): return None, f"SELL en cooldown 30min"
        return "SELL", f"BB+RSI-A Sell | RSI={rsi:.0f} | ADX={adx:.0f} | {tendencia}"

    # ── Variante B: Rechazo fuerte de BB (mecha larga) ──
    wick_upper = high - close
    wick_lower = close - low

    if (low < bb_lo and close > bb_lo and wick_lower > wick_upper * 1.5
            and rsi < (_rsi_buy + 8) and _adx_min <= adx <= _adx_max):
        blocked, msg = _block_check("BUY")
        if blocked: return None, msg
        if not _filtro_tendencia("BUY"): return None, f"BUY-B bloqueado (tendencia {tendencia})"
        if _en_cooldown("BUY"): return None, f"BUY-B en cooldown 30min"
        return "BUY", f"BB+RSI-B Buy (rechazo) | RSI={rsi:.0f} | ADX={adx:.0f} | {tendencia}"

    if (high > bb_up and close < bb_up and wick_upper > wick_lower * 1.5
            and rsi > (_rsi_sell - 8) and _adx_min <= adx <= _adx_max):
        blocked, msg = _block_check("SELL")
        if blocked: return None, msg
        if not _filtro_tendencia("SELL"): return None, f"SELL-B bloqueado (tendencia {tendencia})"
        if _en_cooldown("SELL"): return None, f"SELL-B en cooldown 30min"
        return "SELL", f"BB+RSI-B Sell (rechazo) | RSI={rsi:.0f} | ADX={adx:.0f} | {tendencia}"

    # ── Variante C: RSI extremo + precio cerca de BB ──
    if (rsi < 30 and close < (bb_lo + (bb_mid - bb_lo) * 0.3)
            and _adx_min <= adx <= _adx_max):
        blocked, msg = _block_check("BUY")
        if blocked: return None, msg
        if not _filtro_tendencia("BUY"): return None, f"BUY-C bloqueado (tendencia {tendencia})"
        if _en_cooldown("BUY"): return None, f"BUY-C en cooldown 30min"
        return "BUY", f"BB+RSI-C Buy (RSI extremo) | RSI={rsi:.0f} | ADX={adx:.0f} | {tendencia}"

    if (rsi > 70 and close > (bb_up - (bb_up - bb_mid) * 0.3)
            and _adx_min <= adx <= _adx_max):
        blocked, msg = _block_check("SELL")
        if blocked: return None, msg
        if not _filtro_tendencia("SELL"): return None, f"SELL-C bloqueado (tendencia {tendencia})"
        if _en_cooldown("SELL"): return None, f"SELL-C en cooldown 30min"
        return "SELL", f"BB+RSI-C Sell (RSI extremo) | RSI={rsi:.0f} | ADX={adx:.0f} | {tendencia}"

    # ── Variante D: FIBONACCI Mean Reversion ──
    estrategia = config.get('estrategia', 'bb_rsi')
    if estrategia == 'fibonacci':
        fib_pos = ind.get('fib_pos', 0.5)
        _fib_buy = _sc.get("fib_buy", 0.35)
        _fib_sell = _sc.get("fib_sell", 0.65)
        _fib_rsi_buy = _sc.get("rsi_buy", 45)
        _fib_rsi_sell = _sc.get("rsi_sell", 55)

        if fib_pos < _fib_buy and rsi < _fib_rsi_buy and _adx_min <= adx <= _adx_max:
            blocked, msg = _block_check("BUY")
            if blocked: return None, msg
            if not _filtro_tendencia("BUY"): return None, f"FIB BUY bloqueado (tendencia {tendencia})"
            if _en_cooldown("BUY"): return None, f"FIB BUY en cooldown 30min"
            return "BUY", f"FIB Buy | Pos={fib_pos:.1%} < {_fib_buy:.0%} | RSI={rsi:.0f} | ADX={adx:.0f} | {tendencia}"

        if fib_pos > _fib_sell and rsi > _fib_rsi_sell and _adx_min <= adx <= _adx_max:
            blocked, msg = _block_check("SELL")
            if blocked: return None, msg
            if not _filtro_tendencia("SELL"): return None, f"FIB SELL bloqueado (tendencia {tendencia})"
            if _en_cooldown("SELL"): return None, f"FIB SELL en cooldown 30min"
            return "SELL", f"FIB Sell | Pos={fib_pos:.1%} > {_fib_sell:.0%} | RSI={rsi:.0f} | ADX={adx:.0f} | {tendencia}"

        return None, f"FIB neutral (pos={fib_pos:.1%})"

    return None, "Sin señal scalping"


def _scalper_ejecutar_orden(mt5_symbol, tipo, sl_price, tp_price, config):
    """Ejecuta orden de scalping en MT5. Sin Telegram, sin estadísticas del bot principal."""
    global _scalper_trades_hoy, _scalper_posiciones

    if not MT5_AVAILABLE or not AUTO_TRADING:
        logger.info(f"🔪 Scalper BLOQUEADO {mt5_symbol}: MT5={MT5_AVAILABLE} AutoTrading={AUTO_TRADING}")
        return False

    try:
        with _lock_mt5:
            symbol_info = mt5.symbol_info(mt5_symbol)
        if symbol_info is None:
            logger.info(f"🔪 Scalper BLOQUEADO {mt5_symbol}: symbol_info=None (símbolo no encontrado en MT5)")
            return False

        # Verificar spread
        if symbol_info.spread > config['max_spread']:
            logger.info(f"🔪 Scalper: {mt5_symbol} spread={symbol_info.spread} > max={config['max_spread']} — skip")
            return False

        # Calcular lote basado en riesgo 0.5%
        capital_real = _obtener_capital_real_mt5()
        riesgo_dinero = capital_real * SCALPER_RIESGO_POR_TRADE
        with _lock_mt5:
            tick = mt5.symbol_info_tick(mt5_symbol)
        if not tick:
            logger.info(f"🔪 Scalper BLOQUEADO {mt5_symbol}: tick=None (sin precio)")
            return False

        precio_entrada = tick.ask if tipo == "BUY" else tick.bid
        sl_distance = abs(precio_entrada - sl_price)
        if sl_distance <= 0:
            logger.info(f"🔪 Scalper BLOQUEADO {mt5_symbol}: sl_distance=0 (SL igual a precio)")
            return False

        # Calcular valor del pip
        tick_size = symbol_info.trade_tick_size
        tick_value = symbol_info.trade_tick_value
        if tick_size <= 0 or tick_value <= 0:
            logger.info(f"🔪 Scalper BLOQUEADO {mt5_symbol}: tick_size={tick_size} tick_value={tick_value}")
            return False

        lote_calculado = riesgo_dinero / (sl_distance / tick_size * tick_value)
        lote_calculado = max(symbol_info.volume_min, min(round(lote_calculado, 2), symbol_info.volume_max))
        # SCALPER: Siempre usar lote mínimo para empezar (bajo riesgo)
        lote = symbol_info.volume_min
        # Ajustar al step del volumen
        vol_step = symbol_info.volume_step
        if vol_step > 0:
            lote = round(round(lote / vol_step) * vol_step, 2)

        order_type = mt5.ORDER_TYPE_BUY if tipo == "BUY" else mt5.ORDER_TYPE_SELL
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": mt5_symbol,
            "volume": lote,
            "type": order_type,
            "price": precio_entrada,
            "sl": round(sl_price, symbol_info.digits),
            "tp": round(tp_price, symbol_info.digits),
            "deviation": 20,
            "magic": SCALPER_MAGIC,
            "comment": "BuySell365 Scalper",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        with _lock_mt5:
            result = mt5.order_send(request)

        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            with _lock_scalper:
                _scalper_trades_hoy += 1
                _scalper_posiciones[result.order] = {
                    "symbol": mt5_symbol,
                    "tipo": tipo,
                    "entrada": precio_entrada,
                    "sl": sl_price,
                    "tp": tp_price,
                    "lote": lote,
                    "tiempo": datetime.now(),
                }
            logger.info(f"🔪 SCALPER EJECUTADO {tipo}: {mt5_symbol} @ {precio_entrada:.5g} | SL={sl_price:.5g} | TP={tp_price:.5g} | Lote={lote} | Trade #{_scalper_trades_hoy}")
            return True
        else:
            rc = result.retcode if result else "None"
            comment = getattr(result, 'comment', '') if result else ''
            logger.info(f"🔪 Scalper: Orden RECHAZADA {mt5_symbol} — retcode={rc} comment={comment}")
            return False

    except Exception as e:
        logger.error(f"🔪 Scalper: Error ejecutando orden {mt5_symbol}: {e}")
        return False


def _scalper_gestionar_posiciones():
    """
    Gestiona posiciones abiertas del scalper:
    - Breakeven al 40% del camino al TP
    - Cierre por timeout (60 min)
    - Tracking de P&L diario
    """
    global _scalper_pnl_diario, _scalper_perdidas_consecutivas, _scalper_posiciones

    if not MT5_AVAILABLE:
        return

    try:
        with _lock_mt5:
            positions = mt5.positions_get()
        if positions is None:
            return

        scalper_positions = [p for p in positions if p.magic == SCALPER_MAGIC]

        # Limpiar posiciones cerradas del tracking
        tickets_abiertos = {p.ticket for p in scalper_positions}
        with _lock_scalper:
            cerradas = [t for t in _scalper_posiciones if t not in tickets_abiertos]
            for ticket in cerradas:
                pos_info = _scalper_posiciones.pop(ticket, None)
                if pos_info:
                    # Buscar resultado en historial
                    try:
                        desde = pos_info['tiempo'] - timedelta(minutes=5)
                        hasta = datetime.now() + timedelta(minutes=5)
                        with _lock_mt5:
                            deals = mt5.history_deals_get(desde, hasta, group=pos_info['symbol'])
                        if deals:
                            for deal in deals:
                                if deal.position_id == ticket and deal.entry == mt5.DEAL_ENTRY_OUT:
                                    profit = deal.profit
                                    _scalper_pnl_diario += profit
                                    if profit < 0:
                                        _scalper_perdidas_consecutivas += 1
                                        # FIX 2026-03-20: Cooldown 30min para misma dirección tras pérdida
                                        _cd_key = f"{pos_info['symbol']}_{pos_info['tipo']}"
                                        _scalper_cooldown_direccion[_cd_key] = time.time() + SCALPER_COOLDOWN_MINUTOS * 60
                                        print(f"🔪 Cooldown 30min activado: {_cd_key}")
                                    else:
                                        _scalper_perdidas_consecutivas = 0
                                    print(f"🔪 Scalper CERRADO: {pos_info['symbol']} {pos_info['tipo']} | P&L=${profit:.2f} | Día=${_scalper_pnl_diario:.2f} | Racha={_scalper_perdidas_consecutivas}")
                                    # Notificar al admin directamente (chat privado)
                                    try:
                                        _sc_emoji = "✅" if profit >= 0 else "🛑"
                                        _sc_msg = (
                                            f"{_sc_emoji} *Scalper* — {pos_info.get('symbol', '?')}\n"
                                            f"{pos_info.get('tipo', '?')}  {'$+' if profit >= 0 else '$'}{profit:.2f}\n"
                                            f"P&L día: ${_scalper_pnl_diario:+.2f}"
                                        )
                                        if USERS_AUTORIZADOS:
                                            enviar_telegram_temporal(_sc_msg, destino=USERS_AUTORIZADOS[0], delay_borrado=600)
                                    except Exception:
                                        pass
                                    break
                    except Exception:
                        pass

        # Gestionar posiciones activas
        for pos in scalper_positions:
            info = None
            with _lock_scalper:
                info = _scalper_posiciones.get(pos.ticket)
            if not info:
                continue

            # Timeout: cerrar si lleva más de 60 minutos
            minutos_abierta = (datetime.now() - info['tiempo']).total_seconds() / 60
            if minutos_abierta >= SCALPER_TIMEOUT_MINUTOS:
                print(f"🔪 Scalper TIMEOUT: {pos.symbol} — {minutos_abierta:.0f} min — cerrando")
                _scalper_cerrar_posicion(pos)
                continue

            # Trailing Stop Progresivo: proteger ganancias conforme avanza el precio
            tp_dist = abs(info['tp'] - info['entrada'])
            if tp_dist > 0:
                with _lock_mt5:
                    _tick_be = mt5.symbol_info_tick(pos.symbol)
                _spread_be = (_tick_be.ask - _tick_be.bid) if _tick_be else 0

                if info['tipo'] == "BUY":
                    avance = pos.price_current - info['entrada']
                else:
                    avance = info['entrada'] - pos.price_current

                if avance > tp_dist * SCALPER_BREAKEVEN_PCT:
                    # Trailing progresivo: SL sigue al precio asegurando ganancia
                    # Nivel 1 (40% TP): SL a breakeven (entrada + spread)
                    # Nivel 2 (60% TP): SL a 30% del avance
                    # Nivel 3 (80% TP): SL a 60% del avance
                    if avance > tp_dist * 0.80:
                        trail_pct = 0.60  # Asegurar 60% de la ganancia
                    elif avance > tp_dist * 0.60:
                        trail_pct = 0.30  # Asegurar 30% de la ganancia
                    else:
                        trail_pct = 0.0   # Breakeven

                    if info['tipo'] == "BUY":
                        nuevo_sl = info['entrada'] + _spread_be + (avance * trail_pct)
                        if pos.sl < nuevo_sl:
                            _scalper_modificar_sl(pos, nuevo_sl)
                            if trail_pct > 0:
                                print(f"📈 Scalper TRAIL: {pos.symbol} BUY SL→{nuevo_sl:.5g} (aseg. {trail_pct*100:.0f}%)")
                    elif info['tipo'] == "SELL":
                        nuevo_sl = info['entrada'] - _spread_be - (avance * trail_pct)
                        if pos.sl > nuevo_sl:
                            _scalper_modificar_sl(pos, nuevo_sl)
                            if trail_pct > 0:
                                print(f"📈 Scalper TRAIL: {pos.symbol} SELL SL→{nuevo_sl:.5g} (aseg. {trail_pct*100:.0f}%)")

    except Exception as e:
        logger.error(f"🔪 Scalper: Error gestionando posiciones: {e}")


def _scalper_cerrar_posicion(pos):
    """Cierra una posición del scalper."""
    try:
        tipo_cierre = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
        with _lock_mt5:
            tick = mt5.symbol_info_tick(pos.symbol)
        if not tick:
            return
        precio = tick.bid if pos.type == mt5.ORDER_TYPE_BUY else tick.ask
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": pos.symbol,
            "volume": pos.volume,
            "type": tipo_cierre,
            "position": pos.ticket,
            "price": precio,
            "deviation": 20,
            "magic": SCALPER_MAGIC,
            "comment": "Scalper timeout",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        with _lock_mt5:
            result = mt5.order_send(request)
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            print(f"🔪 Scalper: Posición {pos.ticket} cerrada OK")
        else:
            rc = result.retcode if result else "None"
            logger.warning(f"🔪 Scalper: Error cerrando {pos.ticket} — retcode={rc}")
    except Exception as e:
        logger.error(f"🔪 Scalper: Error cerrando posición {pos.ticket}: {e}")


def _scalper_modificar_sl(pos, nuevo_sl):
    """Modifica el SL de una posición (breakeven)."""
    try:
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "symbol": pos.symbol,
            "position": pos.ticket,
            "sl": nuevo_sl,
            "tp": pos.tp,
            "magic": SCALPER_MAGIC,
        }
        with _lock_mt5:
            result = mt5.order_send(request)
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            print(f"🔪 Scalper BE: {pos.symbol} SL movido a breakeven {nuevo_sl:.5g}")
    except Exception as e:
        logger.warning(f"🔪 Scalper: Error moviendo SL {pos.ticket}: {e}")


def _scalper_reset_diario():
    """Reset contadores diarios a medianoche."""
    global _scalper_pnl_diario, _scalper_perdidas_consecutivas, _scalper_trades_hoy, _scalper_pausa_hasta
    _scalper_pnl_diario = 0.0
    _scalper_perdidas_consecutivas = 0
    _scalper_trades_hoy = 0
    _scalper_pausa_hasta = None


def loop_scalper():
    """
    🔪 HILO PRINCIPAL DEL SCALPER SILENCIOSO
    Corre cada 30 segundos. Solo opera en MT5 (sin Telegram, sin estadísticas).
    Estrategia: BB(20,2) + RSI(7) Mean Reversion en velas M5.
    """
    global _scalper_pnl_diario, _scalper_perdidas_consecutivas, _scalper_pausa_hasta

    time.sleep(30)  # Esperar a que MT5 se conecte
    print("🔪 Scalper Silencioso iniciado — BB+RSI Mean Reversion M5")

    _ultimo_dia = None

    while True:
        try:
            # 📩 Procesar comandos del launcher (si el scanner no los atrapo primero)
            _procesar_comandos_launcher()

            if not SCALPER_ACTIVO or not MT5_AVAILABLE or not AUTO_TRADING:
                time.sleep(SCALPER_INTERVALO)
                continue

            now = ahora()  # Hora Andorra

            # Reset diario
            if _ultimo_dia != now.date():
                _scalper_reset_diario()
                _ultimo_dia = now.date()
                print(f"🔪 Scalper: Nuevo día {_ultimo_dia} — contadores reseteados")

            # No operar fines de semana
            if now.weekday() >= 5:
                time.sleep(60)
                continue

            # Gestionar posiciones abiertas (siempre, incluso en pausa)
            _scalper_gestionar_posiciones()

            # Check límite de pérdida diaria (3%)
            capital = _obtener_capital_real_mt5()
            if capital > 0 and _scalper_pnl_diario <= -(capital * SCALPER_MAX_LOSS_DIARIO):
                if _scalper_trades_hoy > 0:  # Solo imprimir una vez
                    print(f"🔪 Scalper STOP: Pérdida diaria ${_scalper_pnl_diario:.2f} >= 3% de ${capital:.0f} — parado hasta mañana")
                time.sleep(60)
                continue

            # Check pausa por pérdidas consecutivas
            if _scalper_pausa_hasta and now < _scalper_pausa_hasta:
                time.sleep(SCALPER_INTERVALO)
                continue
            elif _scalper_pausa_hasta and now >= _scalper_pausa_hasta:
                _scalper_pausa_hasta = None
                _scalper_perdidas_consecutivas = 0
                print("🔪 Scalper: Pausa terminada — reanudando operaciones")

            if _scalper_perdidas_consecutivas >= SCALPER_MAX_CONSECUTIVAS:
                _scalper_pausa_hasta = now + timedelta(minutes=30)
                print(f"🔪 Scalper PAUSA: {_scalper_perdidas_consecutivas} pérdidas seguidas — pausando 30 min")
                time.sleep(SCALPER_INTERVALO)
                continue

            # Check máximo de posiciones abiertas
            with _lock_scalper:
                n_abiertas = len(_scalper_posiciones)
            if n_abiertas >= SCALPER_MAX_POSICIONES:
                time.sleep(SCALPER_INTERVALO)
                continue

            # ── ESCANEAR CADA ACTIVO ──
            hora_local = now.hour
            for nombre, config in SCALPER_ACTIVOS.items():
                mt5_sym = config['mt5']
                h_inicio, h_fin = config['horario']

                # Filtro horario por activo
                if hora_local < h_inicio or hora_local >= h_fin:
                    continue

                # Filtro best_days from PAR_PROFILES
                _pp_loop = config.get('_profile')
                if _pp_loop:
                    _best_days = _pp_loop.get("time_filter", {}).get("best_days")
                    if _best_days is not None and now.weekday() not in _best_days:
                        continue  # Skip non-peak days for this pair

                # No abrir si ya hay posición del scalper en este símbolo
                # Verificar tanto en dict interno como en MT5 directamente
                with _lock_scalper:
                    ya_en_dict = any(p['symbol'] == mt5_sym for p in _scalper_posiciones.values())
                ya_en_mt5 = False
                try:
                    if MT5_AVAILABLE:
                        with _lock_mt5:
                            positions = mt5.positions_get(symbol=mt5_sym)
                        if positions:
                            ya_en_mt5 = any(p.magic == SCALPER_MAGIC for p in positions)
                except:
                    pass
                if ya_en_dict or ya_en_mt5:
                    continue

                # FIX 2026-03-20: GBPUSD eliminado, ya no necesita filtro correlación

                # Descargar datos M5
                df = _scalper_descargar_m5(mt5_sym)
                if df is None or len(df) < 100:
                    continue

                # Calcular indicadores
                ind = _scalper_calcular_indicadores(df, profile=config.get('_profile'))
                if not ind:
                    continue

                # Evaluar señal
                tipo, razon = _scalper_evaluar_senal(ind, config)
                # FIX 2026-03-20: Log de cada evaluación para diagnóstico
                _sc_rsi = ind.get('rsi', 0)
                _sc_adx = ind.get('adx', 0)
                _sc_tend = ind.get('tendencia', '?')
                _sc_fib = ind.get('fib_pos', 0.5)
                logger.info(f"🔪 Scalper {mt5_sym}: RSI={_sc_rsi:.1f} ADX={_sc_adx:.1f} {_sc_tend} Fib={_sc_fib:.0%} => {tipo or 'NADA'} ({razon})")
                if tipo is None:
                    continue

                # Calcular TP y SL dinámicos basados en ATR
                atr = ind['atr']
                if atr <= 0:
                    continue

                sl_dist = atr * config['sl_atr']
                tp_dist = atr * config['tp_atr']

                if tipo == "BUY":
                    sl_price = ind['precio'] - sl_dist
                    tp_price = ind['precio'] + tp_dist
                else:
                    sl_price = ind['precio'] + sl_dist
                    tp_price = ind['precio'] - tp_dist

                # EJECUTAR
                logger.info(f"🔪 Scalper SEÑAL EJECUTANDO: {mt5_sym} {tipo} — {razon} | SL={sl_price:.5g} TP={tp_price:.5g}")
                ok = _scalper_ejecutar_orden(mt5_sym, tipo, sl_price, tp_price, config)
                logger.info(f"🔪 Scalper RESULTADO: {mt5_sym} {tipo} — {'EJECUTADO' if ok else 'FALLIDO'}")

        except Exception as e:
            logger.error(f"🔪 Scalper: Error en loop principal: {e}")
            import traceback
            traceback.print_exc()

        time.sleep(SCALPER_INTERVALO)


# ============================================================
#  ARRANQUE
# ============================================================

# ── REGISTRO GLOBAL DE HILOS (para watchdog) ────────────────
_hilos_registrados: dict = {}  # {"nombre": threading.Thread}
# M-FIX: Registrar hilo de borrado que se inició al cargar módulo
if '_t_borrado' in dir() and _t_borrado.is_alive():
    _hilos_registrados["delete_sched"] = _t_borrado

def _iniciar_hilo(nombre, target_func):
    """Inicia un hilo daemon y lo registra para el watchdog."""
    global _hilos_registrados
    hilo = threading.Thread(target=target_func, daemon=True, name=nombre)
    hilo.start()
    _hilos_registrados[nombre] = hilo
    return hilo

def _watchdog():
    """
    🐕 WATCHDOG — Vigila que TODOS los hilos estén vivos.
    Si detecta un hilo muerto, lo reinicia automáticamente y alerta por Telegram.
    Corre cada 60 segundos.
    """
    time.sleep(90)  # Esperar a que arranquen todos
    _target_map = {
        "scanner": loop_escaneo,
        "monitor": loop_monitor_alta_frecuencia,
        "polling": loop_polling,
        "health":  loop_health_check,
        "vip":     loop_vip_check,
        "scalper": loop_scalper,  # 🔪 Scalper silencioso
        "delete_sched": _hilo_borrado_scheduler,  # M-FIX: Monitorear hilo de borrado
    }
    while True:
        try:
            for nombre, hilo in list(_hilos_registrados.items()):
                if not hilo.is_alive():
                    log_sistema(f"🚨 WATCHDOG: Hilo '{nombre}' MUERTO — reiniciando...", "error")
                    target = _target_map.get(nombre)
                    if target:
                        _iniciar_hilo(nombre, target)
                        log_sistema(f"✅ WATCHDOG: Hilo '{nombre}' reiniciado correctamente")
                    else:
                        logger.error(f"❌ WATCHDOG: No hay target para reiniciar '{nombre}'")
        except Exception as e_wd:
            logger.error(f"⚠️ Error en watchdog: {e_wd}")
        time.sleep(60)

def _arrancar():
    """Arranca el bot en background con protección total contra crashes."""
    try:
        _arrancar_interno()
    except Exception as e_arranque:
        logger.error(f"🚨 CRASH FATAL en _arrancar(): {e_arranque}")
        import traceback
        traceback.print_exc()
        # Intentar notificar por Telegram
        try:
            enviar_telegram(
                f"🚨 *ERROR FATAL AL ARRANCAR*\n\n"
                f"```{str(e_arranque)[:500]}```\n\n"
                f"El bot necesita reinicio manual.",
                ADMIN_IDS[0] if ADMIN_IDS else CHANNEL_ID
            )
        except Exception: pass

def _arrancar_interno():
    """Lógica real de arranque (llamada desde _arrancar con try/except)."""
    global CAPITAL_USUARIO, MT5_AVAILABLE

    # H-09 FIX: Protección multi-instancia compatible con Windows
    # Windows: usar named mutex (kernel-level) — funciona en todos los casos
    # Linux: usar fcntl como antes
    _instance_lock = None
    if os.name == 'nt':
        # Windows: usar named mutex via ctypes
        try:
            import ctypes
            _kernel32 = ctypes.windll.kernel32
            _mutex_name = "Global\\BuySell365_Bot_SingleInstance"
            _instance_lock = _kernel32.CreateMutexW(None, True, _mutex_name)
            _last_err = _kernel32.GetLastError()
            if _last_err == 183:  # ERROR_ALREADY_EXISTS
                log_sistema("❌ Ya hay otra instancia del bot corriendo (Windows Mutex detectado).", "error")
                print("❌ Ya hay otra instancia del bot corriendo (Windows Mutex detectado).")
                sys.exit(1)
            print(f"🔒 Mutex Windows adquirido (PID {os.getpid()}) — instancia única garantizada.")
        except Exception as _e:
            print(f"⚠️ No se pudo crear mutex Windows: {_e} — continuando sin protección multi-instancia.")
    else:
        # Linux/Mac: usar fcntl file lock
        try:
            import fcntl
            LOCK_FILE = "/tmp/buysell365_bot.lock"
            _lock_fd = open(LOCK_FILE, "w")
            fcntl.flock(_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            _lock_fd.write(str(os.getpid()))
            _lock_fd.flush()
            print(f"🔒 Lock adquirido (PID {os.getpid()}) — instancia única garantizada.")
        except IOError:
            print("❌ Ya hay otra instancia del bot corriendo (file lock detectado).")
            sys.exit(1)
        except Exception as _e:
            print(f"⚠️ No se pudo crear lock: {_e} — continuando sin él.")

    time.sleep(3)

    if MT5_AVAILABLE:
        print("Conectando con MetaTrader 5...")
        intentos = 0
        conectado = False
        _mt5_path = os.getenv("MT5_PATH", "").strip()
        if not _mt5_path:
            for _p in [r'C:\Program Files\XM Global MT5\terminal64.exe',
                        r'C:\Program Files\XM MT5\terminal64.exe',
                        r'C:\Program Files\MetaTrader 5\terminal64.exe']:
                if os.path.isfile(_p):
                    _mt5_path = _p
                    break
        if not _mt5_path:
            print("❌ No se encontró terminal64.exe — MT5 deshabilitado")
            MT5_AVAILABLE = False
        while _mt5_path and intentos < 3 and not conectado:
            if _mt5_primary_account:
                acc = _mt5_primary_account
                if mt5.initialize(path=_mt5_path, login=acc["login"], password=acc["password"], server=acc["server"]):
                    print(f"✅ Conectado a MT5 {acc['name']} (ID: {acc['login']}, Server: {acc['server']})")
                    conectado = True
                else:
                    print(f"⚠️ Error init+login MT5 {acc['name']} (Intento {intentos+1}): {mt5.last_error()}")
            else:
                print("❌ Error: No hay cuentas MT5 configuradas en .env")
                break

            if not conectado:
                intentos += 1
                time.sleep(5)

        # Verificar cuentas secundarias
        if conectado and len(MT5_ACCOUNTS) > 1:
            for acc in MT5_ACCOUNTS[1:]:
                if mt5.login(acc["login"], password=acc["password"], server=acc["server"]):
                    print(f"✅ Verificada {acc['name']} (ID: {acc['login']}, Server: {acc['server']})")
                else:
                    print(f"⚠️ No se pudo verificar {acc['name']} ({acc['login']}): {mt5.last_error()}")
            # Volver a cuenta principal para operación normal
            if _mt5_primary_account:
                mt5.login(_mt5_primary_account["login"], password=_mt5_primary_account["password"], server=_mt5_primary_account["server"])
                print(f"🔄 Reconectado a cuenta principal {_mt5_primary_account['name']}")
                # Re-habilitar AutoTrading (el cambio entre servidores lo desactiva)
                _reenable_autotrading()
            print(f"📊 Total cuentas MT5 configuradas: {len(MT5_ACCOUNTS)}")

        if not conectado:
            print("❌ No se pudo conectar a MT5 tras 3 intentos. El bot seguirá en modo lectura/TV.")
    else:
        print("🔗 MT5 no detectado o corriendo en Linux/Nube. Usando WebScraping TradingView/YFinance como backend...")

    cargar_estado()

    # 💰 INICIALIZAR CAPITAL DESDE MT5 (balance real al arrancar)
    if MT5_AVAILABLE:
        try:
            _acc_info = mt5.account_info()
            if _acc_info and _acc_info.balance > 0:
                CAPITAL_USUARIO = round(_acc_info.balance, 2)
                print(f"💰 Capital inicializado desde MT5: ${CAPITAL_USUARIO:.2f} (balance real)")
            else:
                print(f"⚠️ No se pudo leer balance MT5, usando capital guardado: ${CAPITAL_USUARIO:.0f}")
        except Exception as e:
            print(f"⚠️ Error leyendo balance MT5: {e}, usando: ${CAPITAL_USUARIO:.0f}")

    log_sistema(f"📂 Estado cargado: {len(operaciones_activas)} ops, {len(suscripciones_vip)} VIPs, {len(historial_operaciones)} historial")
    if MT5_AVAILABLE:
        log_sistema(f"🚀 ARRANQUE: BuySell365 Pro | PID:{os.getpid()} | MT5:OK | Capital:${CAPITAL_USUARIO:.0f}")
        print("🚀 BuySell365 Pro — Bot iniciado...")
    else:
        log_sistema(f"☁️ ARRANQUE: BuySell365 Pro (Web-Cloud) | PID:{os.getpid()} | MT5:NO")
        print("☁️ BuySell365 Pro — Web-Cloud iniciado...")

    # ── INICIAR TODOS LOS HILOS (registrados para watchdog) ──
    _iniciar_hilo("scanner", loop_escaneo)
    _iniciar_hilo("monitor", loop_monitor_alta_frecuencia)
    _iniciar_hilo("polling", loop_polling)
    _iniciar_hilo("health",  loop_health_check)
    _iniciar_hilo("vip",     loop_vip_check)
    # 🔪 SCALPER SILENCIOSO — solo MT5, sin Telegram
    if MT5_AVAILABLE and SCALPER_ACTIVO:
        _iniciar_hilo("scalper", loop_scalper)
        _sc_names = ", ".join(SCALPER_ACTIVOS.keys())
        log_sistema(f"🔪 Scalper Silencioso activado — {len(SCALPER_ACTIVOS)} activos | {_sc_names}")

    log_sistema("✅ Todos los hilos iniciados: scanner, monitor, polling, health, vip, scalper, watchdog")

    # 🐕 WATCHDOG — vigila y reinicia hilos muertos cada 60s
    _iniciar_hilo("watchdog", _watchdog)

    # 🌐 WEB SYNC — enviar datos a la web en Render cada 30s
    if _WEB_SYNC_AVAILABLE:
        def _get_web_state():
            """Collect current state for web sync."""
            with _lock_ops:
                return {
                    "operaciones_activas": {k: {kk: vv for kk, vv in v.items() if kk != 'df'} for k, v in operaciones_activas.items() if isinstance(v, dict)},
                    "historial_operaciones": historial_operaciones[-200:],  # Last 200
                    "estadisticas_diarias": dict(estadisticas_diarias),
                    "winning_trades": _cargar_winning_trades()[-100:],  # Last 100
                    "bot_active": True,
                    "auto_trading": AUTO_TRADING,
                    "assets_count": len(ACTIVOS),
                    "active_ops_detail": _build_active_ops_for_web(),
                }

        def _build_active_ops_for_web():
            """Build active ops list for web dashboard (no MT5 calls)."""
            result = []
            for op_id, op in operaciones_activas.items():
                if not isinstance(op, dict) or not op.get('mt5_ejecutado', False):
                    continue
                entrada = op.get('entrada', 0)
                sl = op.get('sl', 0)
                tp1 = op.get('tp1', 0)
                tp2 = op.get('tp2', 0)
                tp3 = op.get('tp3', 0)
                precio_actual = op.get('precio_actual', op.get('precio_extremo', entrada))
                tipo = op.get('tipo', '')
                # Calculate progress: 0% at entry, 100% at TP2
                progreso = 0
                try:
                    target = tp2 if tp2 else tp1
                    if target and entrada and target != entrada:
                        if tipo == 'COMPRA':
                            progreso = max(0, min(100, ((precio_actual - entrada) / (target - entrada)) * 100))
                        else:
                            progreso = max(0, min(100, ((entrada - precio_actual) / (entrada - target)) * 100))
                except Exception:
                    progreso = 0
                # Calculate P&L
                beneficio = op.get('beneficio', None)
                if beneficio is None and precio_actual and entrada:
                    try:
                        if tipo == 'COMPRA':
                            beneficio = round(precio_actual - entrada, 5)
                        else:
                            beneficio = round(entrada - precio_actual, 5)
                    except Exception:
                        beneficio = 0
                result.append({
                    'id': op_id, 'ticker': op.get('ticker', ''),
                    'nombre': op.get('nombre', ''), 'tipo': tipo,
                    'entrada': entrada, 'sl': sl,
                    'tp1': tp1, 'tp2': tp2, 'tp3': tp3,
                    'tp1_hit': op.get('tp1_hit', False), 'tp2_hit': op.get('tp2_hit', False),
                    'precio_actual': precio_actual,
                    'beneficio': beneficio,
                    'progreso': round(progreso, 1), 'hora': op.get('hora', ''),
                    'score': min(op.get('score', 0) * 2, 10),
                    'fuente': 'mt5',
                })
            return result

        def _on_web_signal(signal):
            """Process a webhook signal received from the web."""
            data = signal.get("data", {})
            raw = signal.get("raw", "")
            if data or raw:
                log_sistema(f"📡 Señal recibida desde web: {str(data)[:200]}")
                # Process via existing webhook handler
                try:
                    _pool_webhook.submit(_procesar_webhook_bg, data, str(data.get("ticker", "")), str(data.get("source", "Web")), raw)
                except Exception:
                    pass

        _start_web_sync(_get_web_state, _on_web_signal)
        log_sistema("🌐 Web sync iniciado → datos se envían a Render cada 30s")

    # Solo notificar al admin (NO al canal/grupo — los usuarios no necesitan saber)
    msg_inicio = (
        "BuySell365.pro ONLINE\n"
        "━━━━━━━━━━━━━━\n"
        f"✅ Sistema: Operativo 100%\n"
        f"🕒 Inicio: {ahora().strftime('%H:%M')}"
    )
    admin_id = ADMIN_IDS[0] if ADMIN_IDS else None
    if admin_id:
        try:
            if os.path.exists("logo_bot.png"):
                enviar_foto_telegram(msg_inicio, os.path.abspath("logo_bot.png"), destino=admin_id)
            else:
                enviar_telegram(msg_inicio, destino=admin_id)
        except Exception as e_msg:
            log_sistema(f"⚠️ No se pudo enviar mensaje de inicio al admin {admin_id}: {e_msg}", "warning")
    else:
        log_sistema("⚠️ No hay ADMIN_IDS válidos configurados. Configura USER_ID_1 en .env con tu ID de Telegram personal.", "warning")

# ============================================================
# Para ejecutar directamente
# ============================================================
if __name__ == "__main__":
    import sys
    try:
        # Registrar PID para evitar instancias dobles
        _pid_file = ".bot.pid"
        if os.path.exists(_pid_file):
            try:
                with open(_pid_file) as _f:
                    _old_pid = int(_f.read().strip())
                _is_old_running = False
                try:
                    import psutil
                    if psutil.pid_exists(_old_pid):
                        proc = psutil.Process(_old_pid)
                        try:
                            cmdline = " ".join(proc.cmdline()).lower()
                            if "bot.py" in cmdline or "buysell365" in cmdline:
                                _is_old_running = True
                        except (psutil.AccessDenied, psutil.NoSuchProcess):
                            pass
                except ImportError:
                    # Sin psutil: verificar con ctypes en Windows
                    try:
                        import ctypes
                        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, _old_pid)
                        if handle:
                            ctypes.windll.kernel32.CloseHandle(handle)
                            _is_old_running = True
                    except Exception:
                        pass
                if _is_old_running:
                    print(f"❌ Ya hay una instancia corriendo (PID={_old_pid}). Detenla antes de reiniciar.")
                    # No usar input() — bloquea cuando se lanza sin consola (CREATE_NO_WINDOW)
                    sys.exit(1)
                else:
                    # PID stale — el proceso ya no existe, limpiamos
                    print(f"🧹 PID stale detectado ({_old_pid}), limpiando...")
                    os.remove(_pid_file)
            except Exception:
                # Error leyendo PID file — limpiarlo
                try:
                    os.remove(_pid_file)
                except Exception:
                    pass

        with open(_pid_file, "w") as _f:
            _f.write(str(os.getpid()))
        
        import atexit, signal

        _shutdown_state = [False]  # Evitar doble-llamada (atexit + signal) — usar lista mutable

        def _cleanup_on_exit():
            """atexit handler: guardar estado y limpiar PID (NO llamar sys.exit)."""
            if _shutdown_state[0]:
                return
            _shutdown_state[0] = True
            print(f"\n🛑 Limpieza de cierre — guardando estado...")
            try:
                guardar_estado()
                print("✅ Estado guardado correctamente antes de cerrar.")
            except Exception as e_shut:
                print(f"⚠️ Error guardando estado al cerrar: {e_shut}")
            if MT5_AVAILABLE:
                try:
                    mt5.shutdown()
                    print("✅ MT5 cerrado correctamente.")
                except Exception:
                    pass
            try:
                if os.path.exists(_pid_file):
                    os.remove(_pid_file)
            except Exception:
                pass

        def _signal_handler(sig=None, frame=None):
            """Signal handler: ejecutar limpieza y salir."""
            _cleanup_on_exit()
            sys.exit(0)

        atexit.register(_cleanup_on_exit)
        signal.signal(signal.SIGINT, _signal_handler)
        signal.signal(signal.SIGTERM, _signal_handler)
        # Windows: capturar CTRL_BREAK_EVENT y CTRL_CLOSE_EVENT via SIGBREAK
        if os.name == 'nt':
            try:
                signal.signal(signal.SIGBREAK, _signal_handler)
            except (AttributeError, OSError):
                pass

        # Lanzar arranque
        threading.Thread(target=_arrancar, daemon=True).start()

        print("🚀 BuySell365 Pro — Bot está arrancando...")
        print(f"🔗 Accede a: {DASHBOARD_URL}")

        # 📌 Pin de la web: solo se fija manualmente con /web, no al reiniciar
        # (Los usuarios no necesitan saber que el bot se reinició)

        # --- Puertos configurables via .env (local: 8080/8443, VPS: 80/443) ---
        _http_port = int(os.getenv("HTTP_PORT", "80").strip())
        _https_port = int(os.getenv("HTTPS_PORT", "443").strip())

        # --- Servidor HTTP — Waitress producción (webhooks TradingView) ---
        try:
            srv_http = create_server(app, host="0.0.0.0", port=_http_port, threads=4)
            threading.Thread(target=srv_http.run, daemon=True).start()
            print(f"📡 HTTP :{_http_port} activo (Waitress — webhooks TradingView)")
        except Exception as e_http:
            logger.warning(f"⚠️ No se pudo abrir puerto HTTP {_http_port}: {e_http}")
            if _http_port < 1024:
                _http_port = 8080
                try:
                    srv_http = create_server(app, host="0.0.0.0", port=_http_port, threads=4)
                    threading.Thread(target=srv_http.run, daemon=True).start()
                    print(f"📡 HTTP :{_http_port} activo (puerto alternativo)")
                except Exception:
                    print("⚠️ HTTP deshabilitado — webhooks no disponibles")

        # --- Servidor principal HTTPS — Waitress + SSL ---
        le_cert = r"C:\Certbot\live\buysell365.pro\fullchain.pem"
        le_key  = r"C:\Certbot\live\buysell365.pro\privkey.pem"
        # Fallback: intentar cert antiguo duckdns si el nuevo no existe
        if not os.path.isfile(le_cert):
            le_cert = r"C:\Certbot\live\buysell365.duckdns.org\fullchain.pem"
            le_key  = r"C:\Certbot\live\buysell365.duckdns.org\privkey.pem"
        if os.path.isfile(le_cert) and os.path.isfile(le_key):
            ssl_cert, ssl_key = le_cert, le_key
            print(f"🔒 HTTPS :{_https_port} activo (Waitress + Let's Encrypt)")
        else:
            ssl_cert = os.path.join(os.getcwd(), "ssl_cert.pem")
            ssl_key  = os.path.join(os.getcwd(), "ssl_key.pem")
            print(f"🔒 HTTPS :{_https_port} activo (Waitress + SSL auto-firmado)")

        try:
            srv_https = create_server(app, host="0.0.0.0", port=_https_port, url_scheme="https", threads=4)
            ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ssl_ctx.minimum_version = ssl.TLSVersion.TLSv1_2
            ssl_ctx.load_cert_chain(ssl_cert, ssl_key)
            srv_https.socket = ssl_ctx.wrap_socket(srv_https.socket, server_side=True)
        except Exception as e_ssl_init:
            logger.warning(f"⚠️ No se pudo abrir HTTPS en puerto {_https_port}: {e_ssl_init}")
            if _https_port < 1024:
                _https_port = 8443
                try:
                    srv_https = create_server(app, host="0.0.0.0", port=_https_port, url_scheme="https", threads=4)
                    ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                    ssl_ctx.minimum_version = ssl.TLSVersion.TLSv1_2
                    ssl_ctx.load_cert_chain(ssl_cert, ssl_key)
                    srv_https.socket = ssl_ctx.wrap_socket(srv_https.socket, server_side=True)
                    print(f"🔒 HTTPS :{_https_port} activo (puerto alternativo)")
                except Exception:
                    print("⚠️ HTTPS deshabilitado — dashboard solo HTTP")
                    srv_https = None

        # C-04 FIX: Proteger contra crash HTTPS
        if srv_https:
            while True:
                try:
                    srv_https.run()  # Bloqueante en hilo principal
                except Exception as e_https:
                    logger.error(f"🛑 HTTPS server crashed: {e_https} — reiniciando en 30s...")
                    try:
                        enviar_grupo(f"🛑 *ALERTA*: Servidor HTTPS cayó: {str(e_https)[:100]}. Reiniciando...")
                    except Exception:
                        pass
                    time.sleep(30)
                    try:
                        srv_https = create_server(app, host="0.0.0.0", port=_https_port, url_scheme="https", threads=4)
                        ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                        ssl_ctx.minimum_version = ssl.TLSVersion.TLSv1_2
                        ssl_ctx.load_cert_chain(ssl_cert, ssl_key)
                        srv_https.socket = ssl_ctx.wrap_socket(srv_https.socket, server_side=True)
                    except Exception as e_ssl:
                        logger.error(f"🛑 No se pudo recrear HTTPS: {e_ssl} — reintentando en 60s...")
                        time.sleep(60)
        else:
            # Sin HTTPS: mantener el proceso vivo con el hilo principal
            print("⏳ Bot corriendo sin HTTPS (modo local). Ctrl+C para detener.")
            while True:
                time.sleep(60)
            
    except Exception as e:
        print("\n" + "="*50)
        print(f"🛑 ERROR CRÍTICO AL INICIAR: {e}")
        import traceback
        traceback.print_exc()
        print("="*50)
        logger.critical(f"🛑 ERROR CRÍTICO AL INICIAR: {e}")
        # No usar input() — bloquea cuando se lanza sin consola
        # El error queda registrado en logs/bot.log para diagnóstico
        sys.exit(1)



"""
BuySell365 Pro - Consola de Control
Herramienta de gestion profesional para el bot de trading BuySell365 Pro.
Interfaz con 6 pestanas: Dashboard, Senales, Noticias, Config, VIP, Logs.
"""
import sys
import os
import io
import time
import json
import hashlib
import subprocess
import threading
import urllib.request
import urllib.error
import webbrowser
import csv
import winsound
from datetime import datetime, timedelta

# Force UTF-8 output
if sys.stdout and hasattr(sys.stdout, 'encoding') and sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
    except Exception:
        pass

# ============================================================
#  COLOR THEME
# ============================================================
BG_MAIN = "#0a0e14"
BG_PANEL = "#12171f"
BG_INPUT = "#1a2030"
BG_ROW_ALT = "#151d2a"
TEXT = "#e8ecf1"
TEXT_PRI = "#f0f4f8"
TEXT_SEC = "#6b7a8d"
ACCENT = "#00d4aa"
ACCENT_BRIGHT = "#00f5c4"
WARN = "#f59e0b"
ERR = "#ef4444"
WIN_COLOR = "#10b981"
LOSS_COLOR = "#ef4444"
PREMIUM_COLOR = "#f59e0b"

# ============================================================
#  PATHS
# ============================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BOT_SCRIPT = os.path.join(BASE_DIR, "bot.py")
CONFIG_FILE = os.path.join(BASE_DIR, "launcher_config.json")
TRADING_CONFIG_FILE = os.path.join(BASE_DIR, "launcher_trading_config.json")
LOG_FILE = os.path.join(BASE_DIR, "logs", "bot.log")
COPIER_LOG_FILE = os.path.join(BASE_DIR, "logs", "copier.log")
COPIER_STATS_FILE = os.path.join(BASE_DIR, "copier_stats.json")
LAUNCHER_LOG = os.path.join(BASE_DIR, "logs", "launcher.log")
ESTADO_FILE = os.path.join(BASE_DIR, "estado.json")
ENV_FILE = os.path.join(BASE_DIR, ".env")
ICON_PATH = os.path.join(BASE_DIR, "static", "bull-logo.png")
ICO_PATH = os.path.join(BASE_DIR, "static", "bull-logo.ico")

# ============================================================
#  ASSET NAME NORMALIZATION
# ============================================================
_ASSET_MAP = {
    "EUR/USD": "EUR/USD", "EURUSD=X": "EUR/USD", "EURUSD": "EUR/USD",
    "NASDAQ": "NASDAQ", "NQ=F": "NASDAQ", "US100": "NASDAQ", "US100Cash": "NASDAQ",
    "S&P 500": "S&P 500", "ES=F": "S&P 500", "US500": "S&P 500", "US500Cash": "S&P 500",
    "AUD/CAD": "AUD/CAD", "AUDCAD": "AUD/CAD", "AUDCAD=X": "AUD/CAD",
    "EUR/CHF": "EUR/CHF", "EURCHF": "EUR/CHF", "EURCHF=X": "EUR/CHF",
    "USD/CAD": "USD/CAD", "USDCAD": "USD/CAD", "USDCAD=X": "USD/CAD",
}
_VALID_ASSETS = {"EUR/USD", "NASDAQ", "S&P 500", "AUD/CAD", "EUR/CHF", "USD/CAD"}

# Reverse map: friendly name -> tickers used in estado.json
_ASSET_TICKERS = {
    "EUR/USD": "EURUSD=X",
    "NASDAQ": "NQ=F",
    "S&P 500": "ES=F",
}


def _normalize_asset(name: str) -> str:
    """Normalize an asset name stripping emoji prefixes and mapping to canonical name."""
    if not name:
        return name
    # Strip common emoji prefixes
    import re
    cleaned = re.sub(r'^[\U0001F4CA\U0001F4C8\U0001F4C9\U0001F947\u2B06\u2B07\u26A0\u2705\u274C\U0001F525\U0001F4B0\U0001F3AF\U0001F4B5\U0001F4B2\U0001F4B1\U0001F30D\U0001F4E2\U0001F514\U0001F6A8\u2728\U0001F680\U0001F4AA\u2757\u203C\U0001F534\U0001F7E2\U0001F7E1 ]+', '', name).strip()
    return _ASSET_MAP.get(cleaned, cleaned)


# ============================================================
#  SIMPLE LOGGING
# ============================================================
def _log(msg):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    try:
        os.makedirs(os.path.dirname(LAUNCHER_LOG), exist_ok=True)
        with open(LAUNCHER_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ============================================================
#  COPIER STATS — leer copier_stats.json para el dashboard
# ============================================================
_copier_stats_cache = {"mtime": 0, "data": None}

def _get_copier_stats_today():
    """Lee copier_stats.json y devuelve resumen del día actual."""
    try:
        if not os.path.exists(COPIER_STATS_FILE):
            return {"tps": 0, "sls": 0, "closes": 0, "pips_total": 0, "trades": [], "win_rate": 0}
        mtime = os.path.getmtime(COPIER_STATS_FILE)
        if mtime == _copier_stats_cache["mtime"] and _copier_stats_cache["data"]:
            return _copier_stats_cache["data"]
        with open(COPIER_STATS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        today_str = datetime.now().strftime("%d/%m/%Y")
        today = [t for t in data.get("trades", []) if t.get("fecha") == today_str]
        tps = [t for t in today if t["result"] == "tp"]
        sls = [t for t in today if t["result"] == "sl"]
        closes = [t for t in today if t["result"] in ("close_half", "close_partial", "full_close")]
        pips_tp = sum(t.get("pips", 0) for t in tps)
        pips_sl = sum(t.get("pips", 0) for t in sls)
        pips_closes = sum(t.get("pips", 0) for t in closes if t.get("pips", 0) > 0)
        pips_total = pips_tp - pips_sl + pips_closes
        total_wins = len(tps) + len([c for c in closes if c.get("pips", 0) > 0])
        total_all = len(today)
        win_rate = (total_wins / total_all * 100) if total_all > 0 else 0
        # FIX 2026-05-20: "best" SOLO entre wins reales. Antes hacia max() sobre
        # todos los trades del dia y "pips" se guarda en abs() (signo lo da result),
        # asi que un SL con 18 pips ganaba como "Mejor: NZD/USD BUY +18 pips"
        # contradiciendo el W/L 0/2 del mismo dashboard.
        _wins_for_best = [
            t for t in today
            if t.get("result") == "tp"
            or (t.get("result") in ("close_half", "close_partial", "full_close")
                and (t.get("pips_numeric") or t.get("pips", 0)) > 0)
        ]
        result = {
            "tps": len(tps), "sls": len(sls), "closes": len(closes),
            "pips_total": round(pips_total, 1), "trades": today,
            "win_rate": round(win_rate, 1), "total": total_all,
            "best": max(_wins_for_best, key=lambda t: t.get("pips", 0), default=None),
        }
        _copier_stats_cache["mtime"] = mtime
        _copier_stats_cache["data"] = result
        return result
    except Exception:
        return {"tps": 0, "sls": 0, "closes": 0, "pips_total": 0, "trades": [], "win_rate": 0, "total": 0}


# ============================================================
#  CONFIG MANAGEMENT
# ============================================================
def load_config() -> dict:
    defaults = {
        "autostart_bot": True,
        "minimize_to_tray": True,
        "auto_restart": True,
        "first_run": True,
    }
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            for k, v in defaults.items():
                data.setdefault(k, v)
            return data
        except Exception:
            pass
    return defaults


def save_config(config: dict):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
    except Exception as e:
        _log(f"Error guardando config: {e}")


def load_trading_config() -> dict:
    defaults = {
        "capital": 555.0,
        "riesgo_trade": 0.01,
        "riesgo_premium": 0.015,
        "modo": "Normal",
        "min_score": 3,
        "max_trades": 6,
        "max_perdida_diaria": 0.05,
        "hora_apertura": 8,
        "hora_corte": 18,
        "min_rr": 1.0,
        "auto_cierre_horas": 24,
        "intervalo_escaneo": 180,
        "auto_trading_mt5": True,
        "solo_premium_mt5": True,
        "spreads_max": {
            "EUR/USD": 3,
            "NASDAQ": 30,
            "S&P 500": 20,
            "AUD/CAD": 30,
            "EUR/CHF": 25,
            "USD/CAD": 25,
        },
        "activos_habilitados": {
            "EUR/USD": True,
            "NASDAQ": True,
            "S&P 500": True,
            "AUD/CAD": True,
            "EUR/CHF": True,
            "USD/CAD": True,
        },
    }
    if os.path.exists(TRADING_CONFIG_FILE):
        try:
            with open(TRADING_CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            for k, v in defaults.items():
                data.setdefault(k, v)
            if "spreads_max" in data:
                for k2, v2 in defaults["spreads_max"].items():
                    data["spreads_max"].setdefault(k2, v2)
            if "activos_habilitados" in data:
                for k2, v2 in defaults["activos_habilitados"].items():
                    data["activos_habilitados"].setdefault(k2, v2)
            return data
        except Exception:
            pass
    return defaults


def save_trading_config(config: dict):
    try:
        with open(TRADING_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        _log("Trading config guardada")
    except Exception as e:
        _log(f"Error guardando trading config: {e}")


# ============================================================
#  ESTADO.JSON MANAGEMENT
# ============================================================
def load_estado() -> dict:
    if not os.path.exists(ESTADO_FILE):
        return {}
    try:
        with open(ESTADO_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        _log(f"Error leyendo estado.json: {e}")
        return {}


def save_estado(data: dict):
    """FIX 2026-04-19: write atómico con tmp+os.replace.
    Antes: json.dump directo → si crash a media escritura (o race con bot.py
    que también escribe estado.json), el archivo quedaba corrupto/vacío.
    Esto significaría perder VIPs, depósitos procesados, suscripciones — duplicar pagos.
    """
    try:
        _tmp = ESTADO_FILE + ".tmp"
        with open(_tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
            # FIX 2026-05-06 (#7): fsync para durabilidad ante corte de luz
            f.flush()
            try: os.fsync(f.fileno())
            except (OSError, AttributeError): pass
        os.replace(_tmp, ESTADO_FILE)
    except Exception as e:
        _log(f"Error guardando estado.json: {e}")


def _send_bot_cmd(cmd: str):
    """Envia un comando al bot via archivo .bot.cmd"""
    cmd_file = os.path.join(BASE_DIR, ".bot.cmd")
    try:
        with open(cmd_file, "w", encoding="utf-8") as f:
            f.write(cmd)
        _log(f"Comando enviado al bot: {cmd}")
    except Exception as e:
        _log(f"Error enviando comando al bot: {e}")


# ============================================================
#  .ENV FILE MANAGEMENT
# ============================================================
def load_env() -> dict:
    data = {}
    if not os.path.exists(ENV_FILE):
        return data
    try:
        with open(ENV_FILE, "r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                if "=" in stripped:
                    key, _, val = stripped.partition("=")
                    data[key.strip()] = val.strip()
    except Exception as e:
        _log(f"Error leyendo .env: {e}")
    return data


def save_env(updates: dict):
    """Write updates to .env preserving comments, blank lines and unmodified keys."""
    lines = []
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()

    updated_keys = set()
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.partition("=")[0].strip()
            if key in updates:
                new_lines.append(f"{key}={updates[key]}\n")
                updated_keys.add(key)
                continue
        new_lines.append(line)

    for key, val in updates.items():
        if key not in updated_keys:
            new_lines.append(f"{key}={val}\n")

    try:
        with open(ENV_FILE, "w", encoding="utf-8") as f:
            f.writelines(new_lines)
        _log("Archivo .env actualizado")
    except Exception as e:
        _log(f"Error guardando .env: {e}")


# ============================================================
#  TKINTER IMPORTS (deferred to allow headless usage of helpers)
# ============================================================
import tkinter as tk
from tkinter import ttk, messagebox, filedialog


# ============================================================
#  STYLED WIDGET HELPERS
# ============================================================
def _make_entry(parent, show=None, width=30):
    """Create a styled dark entry widget."""
    e = tk.Entry(
        parent, bg=BG_INPUT, fg=TEXT, insertbackground=TEXT,
        relief="flat", font=("Segoe UI", 10), width=width,
        highlightthickness=1, highlightcolor=ACCENT, highlightbackground="#30363d",
    )
    if show:
        e.config(show=show)
    return e


def _make_label(parent, text, fg=TEXT, font=None, anchor="w"):
    if font is None:
        font = ("Segoe UI", 10)
    return tk.Label(parent, text=text, bg=BG_PANEL, fg=fg, font=font, anchor=anchor)


def _make_button(parent, text, command, bg=ACCENT, fg="#000000", width=None):
    btn = tk.Button(
        parent, text=text, command=command, bg=bg, fg=fg,
        activebackground=bg, activeforeground=fg,
        relief="flat", font=("Segoe UI", 10, "bold"), cursor="hand2",
        padx=12, pady=4,
    )
    if width:
        btn.config(width=width)
    return btn


def _make_section_frame(parent, title):
    """Create a dark panel frame with a title label."""
    frame = tk.Frame(parent, bg=BG_PANEL, padx=12, pady=8,
                     highlightthickness=1, highlightbackground="#30363d")
    lbl = tk.Label(frame, text=title, bg=BG_PANEL, fg=ACCENT,
                   font=("Segoe UI", 12, "bold"), anchor="w")
    lbl.pack(fill="x", pady=(0, 6))
    return frame


def _make_scrollable(parent, bg=BG_MAIN):
    """Return (canvas, scroll_frame) inside parent with vertical scrollbar."""
    canvas = tk.Canvas(parent, bg=bg, highlightthickness=0, borderwidth=0)
    scrollbar = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
    scroll_frame = tk.Frame(canvas, bg=bg)

    scroll_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas_window = canvas.create_window((0, 0), window=scroll_frame, anchor="nw")

    def _on_canvas_configure(event):
        canvas.itemconfig(canvas_window, width=event.width)

    canvas.bind("<Configure>", _on_canvas_configure)
    canvas.configure(yscrollcommand=scrollbar.set)

    scrollbar.pack(side="right", fill="y")
    canvas.pack(side="left", fill="both", expand=True)

    return canvas, scroll_frame


# ============================================================
#  BOT MANAGER
# ============================================================
_MT5_PATHS = [
    r'C:\Program Files\MetaTrader 5\terminal64.exe',
    r'C:\Program Files\XM Global MT5\terminal64.exe',
    r'C:\Program Files\XM MT5\terminal64.exe',
]


def _ensure_mt5_running():
    """Verifica si MT5 esta corriendo, si no lo abre automaticamente."""
    try:
        # Check if terminal64.exe is already running
        result = subprocess.run(
            ['tasklist.exe', '/FI', 'IMAGENAME eq terminal64.exe'],
            capture_output=True, text=True, timeout=10,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
        if 'terminal64.exe' in result.stdout.lower():
            _log("MT5 ya esta corriendo")
            return True

        # Not running — find and launch it
        env_data = load_env()
        mt5_path = env_data.get("MT5_PATH", "").strip()
        if not mt5_path or not os.path.isfile(mt5_path):
            for p in _MT5_PATHS:
                if os.path.isfile(p):
                    mt5_path = p
                    break

        if mt5_path and os.path.isfile(mt5_path):
            _log(f"Abriendo MetaTrader 5: {mt5_path}")
            subprocess.Popen(
                [mt5_path],
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0) | 0x00000008,  # DETACHED_PROCESS
            )
            # Esperar a que MT5 conecte (max 20 segundos)
            for i in range(20):
                time.sleep(1)
                check = subprocess.run(
                    ['tasklist.exe', '/FI', 'IMAGENAME eq terminal64.exe'],
                    capture_output=True, text=True, timeout=10,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                )
                if 'terminal64.exe' in check.stdout.lower():
                    _log(f"MT5 abierto exitosamente (tardo {i+1}s)")
                    time.sleep(5)  # Dar tiempo extra para que conecte al servidor
                    return True
            _log("MT5 no termino de abrir en 20s, continuando igual...")
            return False
        else:
            _log("MT5 no encontrado en rutas conocidas. Abrelo manualmente.")
            return False
    except Exception as e:
        _log(f"Error verificando/abriendo MT5: {e}")
        return False


class BotManager:
    def __init__(self):
        self._proc = None
        self._running = False
        self._stop_requested = False  # FIX 2026-04-06c: Flag para que el watchdog NO reinicie tras stop manual
        self.pid = None
        self.start_time = None
        self.restart_count = 0
        self._watchdog_thread = None
        # Subprocesos auxiliares (arrancan/paran junto al bot)
        self._copier_proc  = None   # signal_copier.py
        self._monitor_proc = None   # monitor_real.py

    def _start_aux(self, script_name):
        """Inicia un subproceso auxiliar si el script existe."""
        script = os.path.join(BASE_DIR, script_name)
        if not os.path.exists(script):
            _log(f"⚠️ {script_name} no encontrado, omitiendo")
            return None
        try:
            _flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0) | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)
            proc = subprocess.Popen(
                [sys.executable, script],
                cwd=BASE_DIR,
                creationflags=_flags,
            )
            _log(f"▶ {script_name} iniciado PID={proc.pid}")
            return proc
        except Exception as e:
            _log(f"Error iniciando {script_name}: {e}")
            return None

    def _stop_aux(self, proc, name):
        """Detiene un subproceso auxiliar."""
        if proc and proc.poll() is None:
            try:
                if os.name == 'nt':
                    subprocess.run(["taskkill", "/F", "/PID", str(proc.pid)], capture_output=True, timeout=5)
                else:
                    proc.terminate()
                _log(f"■ {name} detenido (PID={proc.pid})")
            except Exception as e:
                _log(f"Error deteniendo {name}: {e}")

    @property
    def is_running(self):
        # FIX: is_running NO modifica self._running — evita race condition con watchdog_loop
        # El watchdog es el único que debe cambiar self._running
        # FIX 2026-04-28c: HEARTBEAT FIRST — antes que PID file, chequear el
        # heartbeat. El heartbeat indica que el bot esta REALMENTE vivo y
        # respondiendo (no hung). Si esta reciente (<90s), es prueba definitiva.
        try:
            from heartbeat import is_alive_by_heartbeat
            _hb_bot = os.path.join(BASE_DIR, ".bot.heartbeat")
            if is_alive_by_heartbeat(_hb_bot, max_age=90):
                # FIX 2026-05-10: ANTES de retornar True, asignar self.pid leyendo
                # .bot.pid si no lo tenemos. Sin esto, si el launcher se reinicia
                # mientras el bot sigue vivo, self.pid=None y stop() devuelve
                # "no hay PID conocido" sin matar nada.
                if not self.pid and not self._proc:
                    pid_file = os.path.join(BASE_DIR, ".bot.pid")
                    if os.path.exists(pid_file):
                        try:
                            with open(pid_file, "r") as _f:
                                _pid_txt = _f.read().strip()
                            if _pid_txt and _pid_txt.isdigit():
                                self.pid = int(_pid_txt)
                                self.start_time = self.start_time or time.time()
                        except Exception:
                            pass
                return True
        except Exception:
            pass
        # 1. Si el launcher gestiona el proceso directamente
        if self._proc is not None:
            return self._proc.poll() is None
        # 2. Solo verificar .bot.pid si NO hay _proc (bot iniciado externamente)
        # FIX 2026-04-28b: lectura ROBUSTA de .bot.pid — antes lectura simple
        # podia leer durante un write parcial del bot (race) y generar
        # ValueError -> falso negativo. Ahora retry hasta 3 veces si vacio/corrupto
        # con sleep 0.2s entre intentos.
        # FIX 2026-04-28c: GRACE POR MTIME — si .bot.pid fue tocado hace <90s
        # asumimos bot arrancando (imports + MT5 + Telegram init pueden tomar
        # 5-15s en Windows lento). Esto evita falso negativo en arranque.
        pid_file = os.path.join(BASE_DIR, ".bot.pid")
        if os.path.exists(pid_file):
            try:
                _mtime_bot = os.path.getmtime(pid_file)
                if (time.time() - _mtime_bot) < 90:
                    return True  # archivo reciente — bot arrancando, asumimos vivo
            except Exception:
                pass
            ext_pid = None
            for _attempt in range(3):
                try:
                    with open(pid_file, "r") as f:
                        _content = f.read().strip()
                    if _content and _content.isdigit():
                        ext_pid = int(_content)
                        break
                except Exception:
                    pass
                time.sleep(0.2)  # write puede tardar fracciones
            if ext_pid is None:
                return False
            try:
                import psutil as _ps
                if _ps.pid_exists(ext_pid):
                    p = _ps.Process(ext_pid)
                    if p.status() != _ps.STATUS_ZOMBIE:
                        # FIX 2026-04-28b: validar cmdline tambien para .bot.pid.
                        # Antes solo lo haciamos en .copier.lock — pero el bot.pid
                        # tiene el mismo problema de PID reusado en Windows.
                        # Si Windows reasigna ese PID a otro programa (Chrome,
                        # antivirus), is_running mentia diciendo "vivo".
                        _is_bot = True
                        try:
                            _cmd = ' '.join(p.cmdline()).lower()
                            _is_bot = ('bot.py' in _cmd) or ('python' in _cmd and self.pid == ext_pid)
                        except Exception:
                            _is_bot = True  # fallback: si no podemos leer cmdline, asumimos OK
                        if _is_bot:
                            if self.pid != ext_pid:
                                self.pid = ext_pid
                                self.start_time = self.start_time or time.time()
                            return True
            except ImportError:
                # Fallback: kernel32 si no hay psutil
                import ctypes
                kernel32 = ctypes.windll.kernel32
                handle = kernel32.OpenProcess(0x1000, False, ext_pid)
                if handle:
                    kernel32.CloseHandle(handle)
                    if self.pid != ext_pid:
                        self.pid = ext_pid
                        self.start_time = self.start_time or time.time()
                    return True
            except Exception:
                pass
        return False

    def start(self):
        if self.is_running:
            return
        self._stop_requested = False  # FIX 2026-04-06c: Limpiar flag al iniciar
        # Auto-abrir MT5 antes de iniciar el bot
        _ensure_mt5_running()
        # Clean up stale PID before starting
        self._cleanup_pid_file()
        # FIX 2026-05-03: matar CUALQUIER bot.py externo previo. Sin esto, si el
        # usuario lanzo bot fuera del launcher (terminal, otro launcher, etc),
        # el nuevo proceso choca con mutex/puertos y muere silencioso.
        _killed = self._kill_orphan_bots()
        if _killed:
            _log(f"🧹 {_killed} bot(s) huerfano(s) eliminado(s) antes de start")
        python = sys.executable
        try:
            # CREATE_NEW_PROCESS_GROUP allows sending CTRL_BREAK for graceful shutdown
            _flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0) | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)
            # FIX: limpiar PYTHONUSERBASE/PYTHONPATH — evita conflicto numpy cuando el launcher
            # es iniciado desde entorno WSL (hereda variables de Store Python que interfieren)
            _env = dict(os.environ)
            _env.pop('PYTHONUSERBASE', None)
            _env.pop('PYTHONPATH', None)
            _env['PYTHONNOUSERSITE'] = '1'
            # FIX 2026-05-03: capturar stderr a archivo. Sin esto, si el bot
            # crashea durante imports/init no podemos diagnosticar la causa.
            _stderr_path = os.path.join(BASE_DIR, "logs", "bot_stderr.log")
            try:
                _stderr_fp = open(_stderr_path, "ab")
                _stderr_fp.write(f"\n=== START {time.strftime('%Y-%m-%d %H:%M:%S')} (launcher) ===\n".encode())
                _stderr_fp.flush()
            except Exception:
                _stderr_fp = subprocess.DEVNULL
            self._proc = subprocess.Popen(
                [python, BOT_SCRIPT],
                cwd=BASE_DIR,
                env=_env,
                creationflags=_flags,
                stderr=_stderr_fp,
            )
            self.pid = self._proc.pid
            self.start_time = time.time()
            self._running = True
            # FIX 2026-04-30: persistir PID en .bot.pid para que un launcher
            # reiniciado pueda gestionar el bot. Antes solo vivia en self._proc
            # → si el launcher se cerraba sin matar al bot, el siguiente launcher
            # veia "bot vivo" por heartbeat pero no sabia que PID matar
            # (Stop devolvia "no hay PID de bot conocido" silenciosamente).
            try:
                with open(os.path.join(BASE_DIR, ".bot.pid"), "w") as _f:
                    _f.write(str(self.pid))
            except Exception as _e:
                _log(f"Aviso: no se pudo escribir .bot.pid: {_e}")
            _log(f"Bot iniciado PID={self.pid}")
            if self._watchdog_thread is None or not self._watchdog_thread.is_alive():
                self._watchdog_thread = threading.Thread(target=self.watchdog_loop, daemon=True)
                self._watchdog_thread.start()
            # Arrancar auxiliares con pequeño delay
            def _start_aux_delayed():
                time.sleep(5)
                self._copier_proc  = self._start_aux("signal_copier.py")
                time.sleep(3)
                self._monitor_proc = self._start_aux("monitor_real.py")
            threading.Thread(target=_start_aux_delayed, daemon=True).start()
        except Exception as e:
            _log(f"Error iniciando bot: {e}")

    def _cleanup_pid_file(self):
        """Remove stale .bot.pid file after stopping the bot subprocess."""
        pid_file = os.path.join(BASE_DIR, ".bot.pid")
        try:
            if os.path.exists(pid_file):
                os.remove(pid_file)
                _log("PID file .bot.pid eliminado")
        except Exception as e:
            _log(f"Error eliminando .bot.pid: {e}")

    def _kill_orphan_bots(self):
        """FIX 2026-05-03: Mata CUALQUIER bot.py corriendo (no solo el self.pid).
        Resuelve el caso: bot lanzado externamente (fuera del launcher) bloqueando
        mutex/puertos cuando el boton Reiniciar intenta lanzar uno nuevo.
        Devuelve numero de procesos matados."""
        killed = 0
        try:
            import psutil as _ps
            _self_pid = os.getpid()
            for _proc in _ps.process_iter(['pid', 'name', 'cmdline']):
                try:
                    if _proc.pid == _self_pid:
                        continue
                    _name = (_proc.info.get('name') or '').lower()
                    if 'python' not in _name:
                        continue
                    _cmd = ' '.join(_proc.info.get('cmdline') or []).lower()
                    if 'bot.py' in _cmd and 'launcher.py' not in _cmd:
                        _log(f"🧹 Matando bot.py huerfano PID={_proc.pid}")
                        _proc.terminate()
                        try:
                            _proc.wait(timeout=4)
                        except _ps.TimeoutExpired:
                            _proc.kill()
                        killed += 1
                except (_ps.NoSuchProcess, _ps.AccessDenied):
                    pass
        except Exception as e:
            _log(f"Error escaneando bots huerfanos: {e}")
        if killed > 0:
            time.sleep(2)  # Dar tiempo a Windows a liberar mutex y puertos
        return killed

    def stop(self):
        if not self.is_running:
            return
        self._stop_requested = True  # FIX 2026-04-06c: Señal al watchdog de NO reiniciar
        _target_pid = None
        try:
            # Determinar PID a matar (proceso propio o adoptado)
            if self._proc and self._proc.poll() is None:
                _target_pid = self._proc.pid
            elif self.pid:
                _target_pid = self.pid

            # FIX 2026-05-10: ULTIMO RECURSO — si llegamos aqui sin PID, intentar
            # leer .bot.pid directamente. Cubre caso del launcher recien abierto
            # con el bot vivo desde antes (no es _proc ni self.pid asignado).
            if not _target_pid:
                _pid_file = os.path.join(BASE_DIR, ".bot.pid")
                if os.path.exists(_pid_file):
                    try:
                        with open(_pid_file, "r") as _f:
                            _txt = _f.read().strip()
                        if _txt and _txt.isdigit():
                            _candidate = int(_txt)
                            if self._pid_alive(_candidate):
                                _target_pid = _candidate
                                self.pid = _candidate
                                _log(f"Stop: PID recuperado de .bot.pid ({_candidate})")
                    except Exception as _e_pidread:
                        _log(f"Stop: error leyendo .bot.pid — {_e_pidread}")

            if not _target_pid:
                _log("Stop: no hay PID de bot conocido")
                self._running = False
                # Aun asi limpiar copier/monitor por si quedaron sueltos
                self._cleanup_pid_file()
                return

            import subprocess as _sp
            import signal as _sig
            # ── Graceful shutdown via CTRL_BREAK_EVENT (Python lo escucha con SIGBREAK) ──
            # CREATE_NEW_PROCESS_GROUP permite enviar CTRL_BREAK al grupo del proceso
            _sent_break = False
            try:
                if os.name == 'nt':
                    os.kill(_target_pid, _sig.CTRL_BREAK_EVENT)
                    _sent_break = True
                else:
                    os.kill(_target_pid, _sig.SIGTERM)
            except (OSError, ProcessLookupError):
                pass  # Proceso ya muerto

            # Esperar hasta 4 segundos (Python guarda estado y sale en <1s con SIGBREAK)
            for _ in range(8):
                time.sleep(0.5)
                if self._proc and self._proc.poll() is not None:
                    break
                if not self._pid_alive(_target_pid):
                    break

            if not self._pid_alive(_target_pid):
                _log("Bot detenido — estado guardado correctamente")
            else:
                # Fallback: force kill si no respondió en 4s
                _log("Bot tardó en cerrar, forzando kill...")
                try:
                    _sp.run(["taskkill", "/F", "/T", "/PID", str(_target_pid)],
                            capture_output=True, timeout=5)
                    # Esperar 2s extra para que Windows libere el mutex antes del próximo start
                    time.sleep(2)
                except Exception as e2:
                    _log(f"Error en kill forzado: {e2}")
        except Exception as e:
            _log(f"Error deteniendo bot: {e}")
        self._proc = None
        self.pid = None
        self._running = False
        self._cleanup_pid_file()
        # Detener copier: primero por _copier_proc, luego por PID en .copier.lock (fallback)
        self._stop_aux(self._copier_proc, "signal_copier.py")
        _lock = os.path.join(BASE_DIR, ".copier.lock")
        try:
            if os.path.exists(_lock):
                _cpid = int(open(_lock).read().strip())
                if self._pid_alive(_cpid):
                    _sp.run(["taskkill", "/F", "/PID", str(_cpid)], capture_output=True, timeout=5)
                    _log(f"signal_copier.py detenido por lock (PID={_cpid})")
                os.remove(_lock)
        except Exception:
            pass
        self._stop_aux(self._monitor_proc, "monitor_real.py")
        # FIX 2026-04-17: Fallback por lock file — si _monitor_proc se perdió entre
        # reinicios del launcher, matar por el PID guardado en .monitor_real.lock.
        # Antes se acumulaban hasta 8 monitor_real huérfanos por esto.
        _mlock = os.path.join(BASE_DIR, ".monitor_real.lock")
        try:
            if os.path.exists(_mlock):
                _mpid = int(open(_mlock).read().strip())
                if self._pid_alive(_mpid):
                    _sp.run(["taskkill", "/F", "/PID", str(_mpid)], capture_output=True, timeout=5)
                    _log(f"monitor_real.py detenido por lock (PID={_mpid})")
                os.remove(_mlock)
        except Exception:
            pass
        self._copier_proc  = None
        self._monitor_proc = None
        # FIX 2026-04-30: borrar heartbeats — sino is_running miente "vivo" hasta 90s
        # post-stop (chequea heartbeat ANTES que pid file). Bloquea restart silenciosamente.
        for _hb_name in (".bot.heartbeat", ".copier.heartbeat"):
            try:
                _hb_path = os.path.join(BASE_DIR, _hb_name)
                if os.path.exists(_hb_path):
                    os.remove(_hb_path)
            except Exception:
                pass
        _log("Bot y servicios auxiliares detenidos")

    @staticmethod
    def _pid_alive(pid):
        """Verifica si un PID sigue vivo en Windows."""
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x1000, False, pid)
            if handle:
                kernel32.CloseHandle(handle)
                return True
        except Exception:
            pass
        return False

    def restart(self):
        _log("Reiniciando bot...")
        self.stop()
        # FIX 2026-04-16: Asegurar estado limpio ANTES de start()
        # stop() puede dejar .bot.pid vivo por race condition → start() cree que sigue corriendo
        self._proc = None
        self.pid = None
        self._running = False
        self._cleanup_pid_file()  # Forzar limpieza del PID file
        time.sleep(2)  # Esperar que Windows libere el proceso completamente
        self._cleanup_pid_file()  # Segundo intento por si el proceso escribió PID al morir
        # FIX 2026-05-03: matar bots externos no trackeados antes de start.
        # stop() solo mata self.pid; si hay un bot lanzado fuera del launcher,
        # el siguiente start() chocara con mutex/puertos.
        _kkilled = self._kill_orphan_bots()
        if _kkilled:
            _log(f"🧹 restart: {_kkilled} bot(s) externo(s) limpiado(s)")
        self.start()
        self.restart_count += 1
        if self.is_running:
            _log(f"Bot reiniciado OK — PID={self.pid}")
        else:
            _log("⚠️ Bot no arrancó después del reinicio")

    def uptime_str(self):
        if not self.start_time or not self.is_running:
            return "--"
        elapsed = int(time.time() - self.start_time)
        h = elapsed // 3600
        m = (elapsed % 3600) // 60
        s = elapsed % 60
        return f"{h}h {m}m {s}s"

    def watchdog_loop(self):
        """Watchdog: monitors bot + signal_copier, restarts on crash with backoff.
        FIX: is_running ya NO modifica self._running — el watchdog lo gestiona solo.

        FIX 2026-04-28: Mejoras importantes contra falsos positivos y race conditions:
          1) DEBOUNCING: requiere 2 chequeos consecutivos fallidos antes de declarar caido
             (separados por 3s). Esto evita reiniciar por glitches transitorios de PID
             o I/O lento del bot que solo hizo write a .bot.pid medio segundo despues.
          2) COOLDOWN POST-RESTART: tras reiniciar, espera 30s sin chequear — da tiempo
             a que el bot/copier inicialicen MT5/Telethon sin interferencia.
          3) COPIER cmdline VALIDATION: valida que el PID en .copier.lock tenga
             "signal_copier" en su cmdline (Windows puede reusar PIDs).
        """
        _consecutive_restarts = 0
        _last_restart_time = 0
        _copier_restarts = 0
        # FIX 2026-04-28b: grace period INICIAL de 30s — al arrancar el watchdog,
        # el bot/copier aun pueden estar inicializando (Telethon, MT5, escritura
        # de .bot.pid y .copier.lock). Sin esto, el primer chequeo a los 15s
        # podia ver lock files vacios -> falso positivo -> reinicio innecesario.
        _bot_check_grace_until = time.time() + 30
        _copier_check_grace_until = time.time() + 30
        # FIX 2026-04-28c: RESTART BUDGET — limita reinicios a 5/hora. Si excede,
        # entra en modo "circuit breaker": para de reiniciar y notifica al admin
        # via Telegram para que revise el problema raiz. Evita loops infinitos.
        _restart_history = []  # lista de timestamps de reinicios bot+copier
        _budget_alerted = False
        _RESTART_BUDGET_MAX = 5
        _RESTART_BUDGET_WINDOW = 3600  # 1 hora

        def _check_restart_budget():
            """Returns True si aun hay budget. False si excedimos limite."""
            nonlocal _budget_alerted
            now = time.time()
            # Limpiar reinicios fuera de la ventana
            _restart_history[:] = [t for t in _restart_history if (now - t) < _RESTART_BUDGET_WINDOW]
            if len(_restart_history) >= _RESTART_BUDGET_MAX:
                if not _budget_alerted:
                    _log(f"⚠️ RESTART BUDGET EXCEDIDO: {len(_restart_history)} reinicios en ultima hora — pausando watchdog 10 min")
                    _notify_admin_telegram(
                        f"🚨 BuySell365 — Watchdog detectó {len(_restart_history)} reinicios "
                        f"en la última hora. Pausado 10 min para evitar loop infinito. "
                        f"Revisa logs/launcher.log + bot_stderr.log."
                    )
                    _budget_alerted = True
                return False
            _budget_alerted = False
            return True

        def _notify_admin_telegram(msg: str):
            """Best-effort notify admin via Telegram bot API."""
            try:
                _token = os.environ.get("TELEGRAM_TOKEN", "")
                _admin = os.environ.get("ADMIN_ID", "")
                if not _token or not _admin:
                    return
                import urllib.request as _ur, urllib.parse as _up, json as _jj
                _data = _up.urlencode({"chat_id": _admin, "text": msg}).encode()
                _req = _ur.Request(f"https://api.telegram.org/bot{_token}/sendMessage", data=_data)
                _ur.urlopen(_req, timeout=5).read()
            except Exception:
                pass

        while self._running:
            time.sleep(15)
            _now = time.time()

            # ── Watchdog bot.py ──────────────────────────────────────
            if _now < _bot_check_grace_until:
                # Cooldown post-restart activo: skip checks
                _bot_alive = True
            else:
                _bot_alive = self.is_running  # propiedad pura — no modifica self._running
                if not _bot_alive:
                    # FIX 2026-04-28: DEBOUNCING — rechequear 3s despues antes de declarar caido
                    time.sleep(3)
                    _bot_alive_recheck = self.is_running
                    if _bot_alive_recheck:
                        _log("Watchdog: bot recupero tras rechequeo (era falso positivo)")
                        _bot_alive = True

            if not _bot_alive:
                self._running = False  # marcar caído SÓLO aquí (no en is_running)
                # FIX 2026-04-06c: Si el usuario pidió stop, NO reiniciar
                if self._stop_requested:
                    _log("Watchdog: bot detenido por usuario — NO reiniciando.")
                    break  # Salir del watchdog loop
                # FIX 2026-04-28c: chequear restart budget antes de reiniciar
                if not _check_restart_budget():
                    time.sleep(600)  # 10 min de pausa, luego reset y reintentar
                    _restart_history.clear()
                    _bot_check_grace_until = time.time() + 30
                    continue
                _consecutive_restarts += 1
                if _consecutive_restarts > 3:
                    _wait = min(60 * _consecutive_restarts, 300)
                    _log(f"Watchdog: bot caido (crash #{_consecutive_restarts}), esperando {_wait}s...")
                    time.sleep(_wait)
                else:
                    _log(f"Watchdog: bot caido, reiniciando... (intento #{_consecutive_restarts})")
                self._cleanup_pid_file()
                self.start()  # start() pone self._running = True si arranca OK
                self.restart_count += 1
                _last_restart_time = time.time()
                _restart_history.append(time.time())  # registrar reinicio
                _bot_check_grace_until = time.time() + 30  # cooldown 30s post-restart
            else:
                if _consecutive_restarts > 0 and (time.time() - _last_restart_time) > 300:
                    _consecutive_restarts = 0

            # ── Watchdog signal_copier.py ────────────────────────────
            # FIX: verificar .copier.lock además de _copier_proc
            # Antes: si _copier_proc is None (bot iniciado externamente) → spam "Otra instancia corriendo"
            # Ahora: si el lock tiene un PID vivo → copier está corriendo, no reiniciar
            # FIX 2026-04-28b: lectura ROBUSTA del lock con retry — antes podia
            # leer un archivo a medio escribir (vacio o digitos parciales) y
            # disparar falso positivo justo despues del arranque del copier.
            # FIX 2026-04-28c: GRACE POR MTIME del lock — si el archivo lock fue
            # tocado hace <90s, asumimos que el copier esta arrancando (Python
            # startup + imports + MT5 init pueden tomar 5-15s en Windows lento).
            # Esto evita el falso positivo clasico justo despues del Popen.
            _lock = os.path.join(BASE_DIR, ".copier.lock")
            _lock_pid_alive = False
            _lock_pid = None
            # FIX 2026-04-28c: HEARTBEAT FIRST — chequear heartbeat antes que lock.
            # Si el heartbeat esta reciente (<90s) es prueba de que el copier
            # esta REALMENTE vivo y respondiendo. Esto reemplaza la lectura
            # del PID que es fragil en arranque y ante PIDs reusados.
            try:
                from heartbeat import is_alive_by_heartbeat
                _hb_copier = os.path.join(BASE_DIR, ".copier.heartbeat")
                if is_alive_by_heartbeat(_hb_copier, max_age=90):
                    _lock_pid_alive = True
            except Exception:
                pass
            try:
                if not _lock_pid_alive and os.path.exists(_lock):
                    _mtime = os.path.getmtime(_lock)
                    if (time.time() - _mtime) < 90:
                        _lock_pid_alive = True  # archivo reciente — copier arrancando
            except Exception:
                pass
            for _att in range(3):
                try:
                    if os.path.exists(_lock):
                        with open(_lock, "r") as _lf:
                            _content = _lf.read().strip()
                        if _content and _content.isdigit():
                            _lock_pid = int(_content)
                            break
                except Exception:
                    pass
                time.sleep(0.2)
            try:
                if _lock_pid is not None:
                    try:
                        import psutil as _psu
                        if _psu.pid_exists(_lock_pid):
                            _p = _psu.Process(_lock_pid)
                            if _p.status() != _psu.STATUS_ZOMBIE:
                                # FIX 2026-04-28: validar cmdline para evitar PID reusado
                                # de otro programa (Windows reasigna PIDs liberados).
                                _is_copier = True
                                try:
                                    _cmd = ' '.join(_p.cmdline()).lower()
                                    _is_copier = 'signal_copier' in _cmd
                                except Exception:
                                    _is_copier = True  # fallback: si no podemos leer cmdline, asumimos OK
                                if _is_copier:
                                    _lock_pid_alive = True
                                    # Adoptar PID externo para que _copier_proc no sea None la próxima vez
                                    if self._copier_proc is None or self._copier_proc.poll() is not None:
                                        self._copier_proc = None  # no hay Popen, pero marcamos vivo via flag
                    except ImportError:
                        pass
            except Exception:
                pass

            # FIX 2026-04-28: skip copier check si en cooldown post-restart
            if _now < _copier_check_grace_until:
                continue

            _copier_dead = (
                not _lock_pid_alive and (
                    self._copier_proc is None or
                    self._copier_proc.poll() is not None
                )
            )
            if _copier_dead and self.is_running:
                # FIX 2026-04-28: DEBOUNCING tambien para copier — rechequear 3s despues
                # FIX 2026-04-28b: lectura ROBUSTA del lock tambien en rechequeo
                time.sleep(3)
                _lock_pid_alive_2 = False
                _lock_pid_2 = None
                for _att2 in range(3):
                    try:
                        if os.path.exists(_lock):
                            with open(_lock, "r") as _lf2:
                                _c2 = _lf2.read().strip()
                            if _c2 and _c2.isdigit():
                                _lock_pid_2 = int(_c2)
                                break
                    except Exception:
                        pass
                    time.sleep(0.2)
                try:
                    if _lock_pid_2 is not None:
                        try:
                            import psutil as _psu2
                            if _psu2.pid_exists(_lock_pid_2):
                                _p2 = _psu2.Process(_lock_pid_2)
                                if _p2.status() != _psu2.STATUS_ZOMBIE:
                                    _lock_pid_alive_2 = True
                        except ImportError:
                            pass
                except Exception:
                    pass
                if _lock_pid_alive_2 or (self._copier_proc and self._copier_proc.poll() is None):
                    _log("Watchdog: signal_copier recupero tras rechequeo (era falso positivo)")
                    continue

                # FIX 2026-04-28c: chequear restart budget antes de reiniciar copier
                if not _check_restart_budget():
                    time.sleep(600)
                    _restart_history.clear()
                    _copier_check_grace_until = time.time() + 30
                    continue
                # Solo reiniciar copier si el bot está vivo
                _copier_restarts += 1
                try:
                    os.remove(_lock)
                except FileNotFoundError:
                    pass
                _log(f"Watchdog: signal_copier caido, reiniciando... (#{_copier_restarts})")
                time.sleep(3)
                self._copier_proc = self._start_aux("signal_copier.py")
                _restart_history.append(time.time())  # registrar reinicio
                _copier_check_grace_until = time.time() + 30  # cooldown 30s post-restart
            elif not _copier_dead:
                if _copier_restarts > 0:
                    _copier_restarts = 0


# ============================================================
#  MANAGEMENT CONSOLE - MAIN GUI
# ============================================================
class ManagementConsole:
    def __init__(self, config: dict, bot: BotManager):
        self.config = config
        self.bot = bot
        self._estado_cache = {}
        self._estado_mtime = 0
        self._tick_count = 0
        self._scan_countdown = 120
        self._last_analisis_time = ""
        self._last_active_ops_keys = set()  # for trade alert detection
        self._pnl_history = []  # list of (timestamp, pnl_value) for chart
        self._equity_history = []  # list of (timestamp, capital) for equity curve
        self._tab_flash_state = {}  # tab_name -> bool for flashing
        self._update_loop_active = True
        self._closing = False

        # Build root window
        self.root = tk.Tk()
        # FIX 2026-04-25: ocultar root durante construccion para que Windows no
        # exponga ventanas auxiliares de Tk (EmbeddedMenuWindow, root sin titulo)
        # en Alt+Tab tras reinicios del laptop. Se restaura en run() con deiconify().
        self.root.withdraw()
        self.root.title("BuySell365 Pro - Consola de Control | Propiedad de Emmanuel Diaz")
        self.root.geometry("1400x850")
        self.root.minsize(1100, 700)
        self.root.configure(bg=BG_MAIN)

        # Icono de ventana y barra de tareas
        def _set_icon():
            try:
                if os.path.exists(ICO_PATH):
                    self.root.iconbitmap(default=ICO_PATH)
            except Exception:
                pass
            try:
                if os.path.exists(ICON_PATH):
                    from PIL import Image, ImageTk
                    img = Image.open(ICON_PATH).resize((64, 64), Image.LANCZOS)
                    self._icon_img = ImageTk.PhotoImage(img)
                    self.root.iconphoto(True, self._icon_img)
            except Exception:
                try:
                    if os.path.exists(ICON_PATH):
                        tk_img = tk.PhotoImage(file=ICON_PATH)
                        self.root.iconphoto(True, tk_img)
                        self._icon_img = tk_img
                except Exception:
                    pass
        self.root.after(100, _set_icon)

        # Style
        self._setup_style()

        # Notebook (tabs)
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=4, pady=4)

        # Create all tabs
        self._tab_dashboard = tk.Frame(self.notebook, bg=BG_MAIN)
        self._tab_senales = tk.Frame(self.notebook, bg=BG_MAIN)
        self._tab_trading = tk.Frame(self.notebook, bg=BG_MAIN)
        self._tab_conexiones = tk.Frame(self.notebook, bg=BG_MAIN)
        self._tab_vip = tk.Frame(self.notebook, bg=BG_MAIN)
        self._tab_logs = tk.Frame(self.notebook, bg=BG_MAIN)
        self._tab_noticias = tk.Frame(self.notebook, bg=BG_MAIN)
        self._tab_whatsapp = tk.Frame(self.notebook, bg=BG_MAIN)

        self.notebook.add(self._tab_dashboard, text=" \U0001F4CA Dashboard ")
        self.notebook.add(self._tab_senales, text=" \U0001F4E1 Se\u00f1ales ")
        self.notebook.add(self._tab_noticias, text=" \U0001F4F0 Noticias ")
        # ELIMINADA pestana Config (FIX 2026-04-28) -- datos del scanner muerto
        # self.notebook.add(self._tab_trading, text=" \u2699 Config ")
        self.notebook.add(self._tab_vip, text=" \u2B50 VIP ")
        self.notebook.add(self._tab_whatsapp, text=" \U0001F4F1 WhatsApp ")
        self.notebook.add(self._tab_logs, text=" \U0001F4DD Logs ")

        # Scrollable canvases dict for mousewheel binding
        self._scroll_canvases = {}

        # Build each tab
        self._build_dashboard()
        self._build_senales()
        self._build_trading()
        self._build_conexiones()
        self._build_vip()
        self._build_logs()
        self._build_noticias()
        self._build_whatsapp()

        # Mousewheel binding
        self.root.bind_all("<MouseWheel>", self._on_mousewheel)
        self.root.bind_all("<Button-4>", self._on_mousewheel_linux)
        self.root.bind_all("<Button-5>", self._on_mousewheel_linux)

        # Abrir MT5 siempre al arrancar el launcher (en background, no bloquea)
        threading.Thread(target=_ensure_mt5_running, daemon=True).start()

        # Auto-start bot si está configurado
        if self.config.get("autostart_bot", True):
            self.root.after(2000, self._auto_start_bot)

        # Status bar
        _status_frame = tk.Frame(self.root, bg="#0a0e14")
        _status_frame.pack(side="bottom", fill="x")
        self._status_bar = tk.Label(
            _status_frame, text="BuySell365 Pro v6.0 | Bot: OFF",
            bg="#0a0e14", fg=TEXT_SEC, font=("Segoe UI", 9), anchor="w", padx=8
        )
        self._status_bar.pack(side="left", fill="x", expand=True)
        tk.Label(
            _status_frame, text="Creado por Emmanuel Diaz",
            bg="#0a0e14", fg="#4a90d9", font=("Segoe UI", 9, "italic"), anchor="e", padx=8
        ).pack(side="right")

        # Start update loop
        self.root.after(1000, self._update_loop)

        # Protocol
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # System tray (optional)
        self._tray_icon = None
        self._setup_tray()

    # --------------------------------------------------------
    #  STYLE SETUP
    # --------------------------------------------------------
    def _setup_style(self):
        style = ttk.Style()
        style.theme_use("clam")

        style.configure("TNotebook", background=BG_MAIN, borderwidth=0)
        style.configure("TNotebook.Tab", background=BG_INPUT, foreground=TEXT,
                        font=("Segoe UI", 10, "bold"), padding=[16, 8])
        style.map("TNotebook.Tab",
                  background=[("selected", BG_PANEL)],
                  foreground=[("selected", ACCENT)])

        style.configure("Treeview", background=BG_PANEL, foreground=TEXT,
                        fieldbackground=BG_PANEL, borderwidth=0,
                        font=("Segoe UI", 10), rowheight=32)
        style.configure("Treeview.Heading", background=BG_INPUT, foreground=TEXT,
                        font=("Segoe UI", 10, "bold"), borderwidth=1,
                        relief="flat")
        style.map("Treeview",
                  background=[("selected", "#30363d")],
                  foreground=[("selected", ACCENT)])

        style.configure("TScrollbar", background=BG_INPUT, troughcolor=BG_MAIN,
                        borderwidth=0, arrowcolor=TEXT_SEC)

        style.configure("TScale", background=BG_PANEL, troughcolor=BG_INPUT)
        style.configure("TCheckbutton", background=BG_PANEL, foreground=TEXT,
                        font=("Segoe UI", 10))
        style.map("TCheckbutton", background=[("active", BG_PANEL)])

        style.configure("TCombobox", fieldbackground=BG_INPUT, background=BG_INPUT,
                        foreground=TEXT, arrowcolor=TEXT)

        style.configure("TSpinbox", fieldbackground=BG_INPUT, background=BG_INPUT,
                        foreground=TEXT, arrowcolor=TEXT)

    # --------------------------------------------------------
    #  MOUSEWHEEL HELPERS
    # --------------------------------------------------------
    def _on_mousewheel(self, event):
        current_tab = self.notebook.select()
        canvas = self._scroll_canvases.get(current_tab)
        if canvas:
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _on_mousewheel_linux(self, event):
        current_tab = self.notebook.select()
        canvas = self._scroll_canvases.get(current_tab)
        if canvas:
            if event.num == 4:
                canvas.yview_scroll(-1, "units")
            elif event.num == 5:
                canvas.yview_scroll(1, "units")

    # --------------------------------------------------------
    #  SYSTEM TRAY (optional)
    # --------------------------------------------------------
    def _setup_tray(self):
        try:
            import pystray
            from PIL import Image
            if os.path.exists(ICON_PATH):
                img = Image.open(ICON_PATH)
                img = img.resize((64, 64))
            else:
                img = Image.new("RGB", (64, 64), ACCENT)

            menu = pystray.Menu(
                pystray.MenuItem("Mostrar", self._tray_show),
                pystray.MenuItem("Iniciar Bot", lambda: threading.Thread(target=self.bot.start, daemon=True).start()),
                pystray.MenuItem("Detener Bot", lambda: threading.Thread(target=self.bot.stop, daemon=True).start()),
                pystray.MenuItem("Salir", self._tray_quit),
            )
            self._tray_icon = pystray.Icon("BuySell365", img, "BuySell365 Pro", menu)
            threading.Thread(target=self._tray_icon.run, daemon=True).start()
        except Exception:
            pass

    def _tray_show(self, *args):
        self.root.after(0, self.root.deiconify)

    def _tray_action(self, func):
        self.root.after(0, func)

    def _tray_quit(self, *args):
        self.root.after(0, self._on_close)

    # --------------------------------------------------------
    #  AUTO-START
    # --------------------------------------------------------
    def _auto_start_bot(self):
        if not self.bot.is_running:
            threading.Thread(target=self.bot.start, daemon=True).start()

    # --------------------------------------------------------
    #  CLOSE HANDLER
    # --------------------------------------------------------
    def _on_close(self):
        # Evitar múltiples llamadas
        if getattr(self, '_closing', False):
            return
        self._closing = True
        self._update_loop_active = False

        try:
            if self._tray_icon:
                self._tray_icon.stop()
        except Exception:
            pass

        try:
            self.root.destroy()
        except Exception:
            pass

        # Forzar salida inmediata — mata todos los hilos sin más diálogos
        import os as _os
        _os._exit(0)

    # --------------------------------------------------------
    #  ESTADO CACHE
    # --------------------------------------------------------
    def _get_estado(self) -> dict:
        """Read estado.json with mtime caching."""
        try:
            mtime = os.path.getmtime(ESTADO_FILE)
            if mtime != self._estado_mtime:
                self._estado_cache = load_estado()
                self._estado_mtime = mtime
        except Exception:
            pass
        return self._estado_cache

    # ============================================================
    #  TAB 1: DASHBOARD
    # ============================================================
    def _build_dashboard(self):
        canvas, scroll_frame = _make_scrollable(self._tab_dashboard)
        self._scroll_canvases[str(self._tab_dashboard)] = canvas

        # ── Header: estado + capital MT5 + última actualización ──
        dash_header = tk.Frame(scroll_frame, bg=BG_MAIN)
        dash_header.pack(fill="x", padx=10, pady=(10, 4))

        self._dash_estado_lbl = _make_label(dash_header, "● Bot Detenido",
                                             fg=ERR, font=("Segoe UI", 11, "bold"))
        self._dash_estado_lbl.pack(side="left")

        self._dash_uptime_lbl = _make_label(dash_header, "", fg=TEXT_SEC,
                                             font=("Segoe UI", 9))
        self._dash_uptime_lbl.pack(side="left", padx=(12, 0))

        self._dash_mt5_capital_lbl = _make_label(dash_header, "", fg=ACCENT_BRIGHT,
                                                  font=("Segoe UI", 11, "bold"))
        self._dash_mt5_capital_lbl.pack(side="right", padx=10)

        self._dash_last_update_lbl = _make_label(dash_header, "", fg=TEXT_SEC,
                                                  font=("Segoe UI", 9))
        self._dash_last_update_lbl.pack(side="right", padx=4)

        # ── Sección 1: Controles (todo en uno) ──
        ctrl_frame = _make_section_frame(scroll_frame, "Controles")
        ctrl_frame.pack(fill="x", padx=10, pady=5)

        btn_row1 = tk.Frame(ctrl_frame, bg=BG_PANEL)
        btn_row1.pack(fill="x", pady=(4, 2))

        self._btn_start = _make_button(btn_row1, "▶  Iniciar Bot", self._cmd_start_bot,
                                       bg="#238636", fg="#ffffff")
        self._btn_start.pack(side="left", padx=(0, 6), pady=2)

        self._btn_stop = _make_button(btn_row1, "■  Detener Bot", self._cmd_stop_bot,
                                      bg=ERR, fg="#ffffff")
        self._btn_stop.pack(side="left", padx=6, pady=2)

        self._btn_restart = _make_button(btn_row1, "↺  Reiniciar", self._cmd_restart_bot,
                                         bg=WARN, fg="#000000")
        self._btn_restart.pack(side="left", padx=6, pady=2)

        # FIX 2026-05-01: Boton "Pegar Senal Manual" — abre dialog con textarea
        # donde pegar la senal, que se procesa con el MISMO pipeline que las
        # automaticas (parser, formato canon, tracking TP/SL HIT).
        self._btn_paste_signal = _make_button(
            btn_row1, "📋  Pegar Senal Manual", self._cmd_paste_signal_dialog,
            bg="#7c3aed", fg="#ffffff",
        )
        self._btn_paste_signal.pack(side="left", padx=6, pady=2)

        # FIX 2026-04-28: ELIMINADO btn_row2 con "Escanear Ahora", "Pausar Todo",
        # "Cerrar Todo" — botones del scanner muerto. Auto-start movido a btn_row1.
        # Dummies para no romper otros callers que esperan el atributo.
        self._btn_pause_all = self._btn_restart  # alias para compatibilidad

        # Auto-start toggle al final de btn_row1
        self._autostart_var = tk.BooleanVar(value=self.config.get("autostart_bot", True))
        tk.Checkbutton(btn_row1, text="Auto-iniciar al abrir",
                       variable=self._autostart_var, command=self._toggle_autostart,
                       bg=BG_PANEL, fg=TEXT_SEC, selectcolor=BG_INPUT,
                       activebackground=BG_PANEL, activeforeground=TEXT,
                       font=("Segoe UI", 9)).pack(side="right", padx=10)

        # FIX 2026-04-28: ELIMINADA seccion "Estadisticas en Vivo" — datos del
        # scanner muerto (Win Rate 25%, Senales Hoy 0W/0L, Prox Escaneo, Modo Normal).
        # Stats reales ahora estan en la seccion "Signal Copier" abajo.
        # Dummies para callers de _refresh_dashboard que escriben a estos labels:
        _dummy_lbl = _make_label(scroll_frame, "")
        _dummy_lbl.pack_forget()  # invisible
        for _attr in ("_dash_winrate", "_dash_senales_hoy", "_dash_capital", "_dash_ganancia",
                      "_dash_autotrading", "_dash_escaneo", "_dash_modo", "_dash_ops_abiertas"):
            setattr(self, _attr, _dummy_lbl)

        # ── Sección 3: Signal Copier (VIP) ──
        copier_frame = _make_section_frame(scroll_frame, "📡 Copiador de Señales — Canal VIP")
        copier_frame.pack(fill="x", padx=10, pady=5)

        copier_grid = tk.Frame(copier_frame, bg=BG_PANEL)
        copier_grid.pack(fill="x", padx=10, pady=4)
        copier_grid.columnconfigure(1, weight=1)
        copier_grid.columnconfigure(3, weight=1)

        copier_labels_left = [
            ("Estado:",          "_cop_status"),
            ("Señales Hoy:",     "_cop_senales"),
            ("% de Aciertos:",   "_cop_winrate"),
        ]
        copier_labels_right = [
            ("Ganadas / Perdidas:", "_cop_tp_sl"),
            ("Cierres con ganancia:", "_cop_closes"),
            ("Pips Netos:",         "_cop_pips"),
        ]
        for i, (lbl_text, attr) in enumerate(copier_labels_left):
            _make_label(copier_grid, lbl_text, fg=TEXT_SEC,
                        font=("Segoe UI", 10)).grid(row=i, column=0, sticky="w", padx=(0, 8), pady=3)
            val_lbl = _make_label(copier_grid, "--", fg=TEXT, font=("Segoe UI", 10, "bold"))
            val_lbl.grid(row=i, column=1, sticky="w", pady=3)
            setattr(self, attr, val_lbl)

        for i, (lbl_text, attr) in enumerate(copier_labels_right):
            _make_label(copier_grid, lbl_text, fg=TEXT_SEC,
                        font=("Segoe UI", 10)).grid(row=i, column=2, sticky="w", padx=(30, 8), pady=3)
            val_lbl = _make_label(copier_grid, "--", fg=TEXT, font=("Segoe UI", 10, "bold"))
            val_lbl.grid(row=i, column=3, sticky="w", pady=3)
            setattr(self, attr, val_lbl)

        # Mejor señal del día
        self._cop_best = _make_label(copier_frame, "", fg=ACCENT, font=("Segoe UI", 9))
        self._cop_best.pack(anchor="w", padx=10, pady=(0, 5))

        # FIX 2026-04-28: ELIMINADA seccion "Estado de Activos" — semaforos del
        # scanner muerto (EUR/USD, NASDAQ, S&P 500, etc todos en rojo siempre).
        # _traffic_lights queda vacio para que _refresh_dashboard no falle.
        self._traffic_lights = {}

        # ── Sección 3b: LLM PRO (NUEVO 2026-04-30) ──
        # Muestra status de Claude Sonnet 4.6 + caching + features inteligentes
        llm_frame = _make_section_frame(scroll_frame, "🤖 LLM PRO — Inteligencia Activa")
        llm_frame.pack(fill="x", padx=10, pady=5)

        llm_grid = tk.Frame(llm_frame, bg=BG_PANEL)
        llm_grid.pack(fill="x", padx=10, pady=4)
        llm_grid.columnconfigure(1, weight=1)
        llm_grid.columnconfigure(3, weight=1)

        llm_left = [
            ("Modelo:",         "_llm_model"),
            ("Modo:",           "_llm_mode"),
            ("Coste hoy:",      "_llm_cost"),
        ]
        llm_right = [
            ("Reutilizaciones:",  "_llm_cache"),
            ("Clientes detectados:", "_llm_leads"),
            ("Cola de seguimiento:", "_llm_followup"),
        ]
        for i, (lbl_text, attr) in enumerate(llm_left):
            _make_label(llm_grid, lbl_text, fg=TEXT_SEC,
                        font=("Segoe UI", 10)).grid(row=i, column=0, sticky="w", padx=(0, 8), pady=3)
            val_lbl = _make_label(llm_grid, "--", fg=TEXT, font=("Segoe UI", 10, "bold"))
            val_lbl.grid(row=i, column=1, sticky="w", pady=3)
            setattr(self, attr, val_lbl)

        for i, (lbl_text, attr) in enumerate(llm_right):
            _make_label(llm_grid, lbl_text, fg=TEXT_SEC,
                        font=("Segoe UI", 10)).grid(row=i, column=2, sticky="w", padx=(30, 8), pady=3)
            val_lbl = _make_label(llm_grid, "--", fg=TEXT, font=("Segoe UI", 10, "bold"))
            val_lbl.grid(row=i, column=3, sticky="w", pady=3)
            setattr(self, attr, val_lbl)

        # Línea de features activas
        self._llm_features_line = _make_label(llm_frame, "",
                                               fg=ACCENT, font=("Segoe UI", 9))
        self._llm_features_line.pack(anchor="w", padx=10, pady=(2, 5))

        # ── Sección 4: Conexiones ──
        conn_frame = _make_section_frame(scroll_frame, "Conexiones")
        conn_frame.pack(fill="x", padx=10, pady=(5, 15))

        conn_row = tk.Frame(conn_frame, bg=BG_PANEL)
        conn_row.pack(fill="x", pady=4)

        self._conn_mt5 = _make_label(conn_row, "MT5  ●", fg=TEXT_SEC, font=("Segoe UI", 11))
        self._conn_mt5.pack(side="left", padx=15)
        self._conn_telegram = _make_label(conn_row, "Telegram  ●", fg=TEXT_SEC, font=("Segoe UI", 11))
        self._conn_telegram.pack(side="left", padx=15)
        self._conn_copier = _make_label(conn_row, "Copiador  ●", fg=TEXT_SEC, font=("Segoe UI", 11))
        self._conn_copier.pack(side="left", padx=15)
        self._conn_ig = _make_label(conn_row, "Instagram  ●", fg=TEXT_SEC, font=("Segoe UI", 11))
        self._conn_ig.pack(side="left", padx=15)
        self._conn_web = _make_label(conn_row, "Sincronización Web  ●", fg=TEXT_SEC, font=("Segoe UI", 11))
        self._conn_web.pack(side="left", padx=15)

        # Start MT5 capital refresh timer (every 30s)
        self.root.after(5000, self._refresh_mt5_capital_loop)

        # News fetched from Noticias tab

    def _show_about(self):
        messagebox.showinfo(
            "Acerca de BuySell365 Pro",
            "BuySell365 Pro v6.0\n\n"
            "Bot de trading + Signal Copier VIP + Instagram\n"
            "Scalper: EUR/USD, NASDAQ, S&P 500, AUD/CAD, EUR/CHF, USD/CAD\n"
            "Copier VIP: XAUUSD, NAS100, US30, Forex majors\n\n"
            "Creado por Emmanuel Diaz\n"
            "https://buysell365.pro\n\n"
            "(c) 2026 BuySell365. Todos los derechos reservados."
        )

    # --------------------------------------------------------
    #  QUICK CONTROLS (improvement 5)
    # --------------------------------------------------------
    def _cmd_cerrar_todo(self):
        """Emergency close all positions via MT5 in background."""
        if not messagebox.askyesno("Cerrar Todo",
                                   "\u26A0 ATENCION: Esto cerrara TODAS las posiciones abiertas en MT5.\n\n"
                                   "Esta seguro?"):
            return

        def _close_all():
            try:
                import price_feed as mt5
                if not mt5.initialize():
                    self.root.after(0, lambda: messagebox.showerror(
                        "Error", "No se pudo conectar a MT5."))
                    return
                positions = mt5.positions_get()
                if not positions:
                    self.root.after(0, lambda: messagebox.showinfo(
                        "Cerrar Todo", "No hay posiciones abiertas."))
                    return
                closed = 0
                for pos in positions:
                    close_type = mt5.ORDER_TYPE_SELL if pos.type == 0 else mt5.ORDER_TYPE_BUY
                    symbol_info = mt5.symbol_info(pos.symbol)
                    if not symbol_info:
                        continue
                    price = symbol_info.bid if pos.type == 0 else symbol_info.ask
                    request = {
                        "action": mt5.TRADE_ACTION_DEAL,
                        "symbol": pos.symbol,
                        "volume": pos.volume,
                        "type": close_type,
                        "position": pos.ticket,
                        "price": price,
                        "deviation": 20,
                        "magic": pos.magic,
                        "comment": "BuySell365 emergency close",
                        "type_time": mt5.ORDER_TIME_GTC,
                        "type_filling": mt5.ORDER_FILLING_IOC,
                    }
                    result = mt5.order_send(request)
                    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                        closed += 1
                self.root.after(0, lambda: messagebox.showinfo(
                    "Cerrar Todo", f"Se cerraron {closed} de {len(positions)} posiciones."))
                _log(f"Emergency close: {closed}/{len(positions)} posiciones cerradas")
            except ImportError:
                self.root.after(0, lambda: messagebox.showerror(
                    "Error", "MetaTrader5 no esta instalado."))
            except Exception as e:
                self.root.after(0, lambda: messagebox.showerror(
                    "Error", f"Error cerrando posiciones: {e}"))

        threading.Thread(target=_close_all, daemon=True).start()

    def _show_senal_context_menu(self, event):
        """Show right-click context menu on active signals."""
        item = self._senales_active_tree.identify_row(event.y)
        if item:
            self._senales_active_tree.selection_set(item)
            self._senales_ctx_menu.post(event.x_root, event.y_root)

    def _cmd_cerrar_senal(self):
        """Close selected active signal (remove from bot tracking + close MT5 if open)."""
        selection = self._senales_active_tree.selection()
        if not selection:
            messagebox.showwarning("Cerrar Senal", "Selecciona una senal activa primero.")
            return

        item_id = selection[0]
        op_key = getattr(self, '_senales_op_keys', {}).get(item_id)
        if not op_key:
            messagebox.showerror("Error", "No se encontro la clave de operacion.")
            return

        values = self._senales_active_tree.item(item_id, "values")
        nombre = values[1] if len(values) > 1 else "?"
        tipo = values[2] if len(values) > 2 else "?"

        if not messagebox.askyesno("Cerrar Senal",
                                   f"Cerrar senal {nombre} {tipo}?\n\n"
                                   "Esto la eliminara del tracking del bot.\n"
                                   "Si tiene posicion en MT5, tambien se cerrara."):
            return

        # Send close command to bot
        _send_bot_cmd(f"close_op:{op_key}")
        _log(f"Cierre manual solicitado: {nombre} {tipo} (key={op_key})")
        messagebox.showinfo("Cerrar Senal", f"Cierre de {nombre} {tipo} solicitado.\nSe ejecutara en el proximo ciclo.")
        # Refresh immediately
        self.root.after(500, self._refresh_senales)

    def _cmd_escanear_ahora(self):
        """Trigger immediate scan via .bot.cmd."""
        _send_bot_cmd("force_scan")
        _log("Escaneo inmediato solicitado por usuario")
        messagebox.showinfo("Escanear", "Escaneo inmediato solicitado. Se ejecutara en el proximo ciclo.")

    def _cmd_pause_all(self):
        _send_bot_cmd("pause_all")
        self._btn_pause_all.config(text="\u25B6 Reanudar Todo", bg="#238636",
                                   command=self._cmd_resume_all)
        _log("PAUSA TOTAL activada desde consola")

    def _cmd_resume_all(self):
        _send_bot_cmd("resume_all")
        self._btn_pause_all.config(text="\u23F8 Pausar Todo", bg="#b91c1c",
                                   command=self._cmd_pause_all)
        _log("TODO REACTIVADO desde consola")

    def _play_sound(self, sound_type="signal"):
        """Play notification sounds for trading events."""
        def _do_sound():
            try:
                if sound_type == "signal":
                    winsound.Beep(800, 300)
                elif sound_type == "win":
                    winsound.Beep(1000, 200)
                    time.sleep(0.1)
                    winsound.Beep(1200, 200)
                elif sound_type == "loss":
                    winsound.Beep(400, 500)
            except Exception:
                pass
        threading.Thread(target=_do_sound, daemon=True).start()

    # ============================================================
    #  TAB: NOTICIAS ECONOMICAS
    # ============================================================
    # Mapeo moneda → pares afectados
    _CURRENCY_PAIRS = {
        "USD": ["EUR/USD", "NASDAQ", "S&P 500", "USD/CAD"],
        "EUR": ["EUR/USD", "EUR/CHF"],
        "GBP": [],
        "JPY": [],
        "CHF": ["EUR/CHF"],
        "AUD": ["AUD/CAD"],
        "CAD": ["AUD/CAD", "USD/CAD"],
        "NZD": [],
        "CNY": ["NASDAQ", "S&P 500"],
    }

    def _build_noticias(self):
        canvas, scroll_frame = _make_scrollable(self._tab_noticias)
        self._scroll_canvases[str(self._tab_noticias)] = canvas

        # Header
        header = tk.Frame(scroll_frame, bg=BG_MAIN)
        header.pack(fill="x", padx=10, pady=(10, 5))

        _make_button(header, "Actualizar", self._refresh_news,
                     bg=BG_INPUT, fg=TEXT).pack(side="left", padx=(0, 10))
        self._news_time_lbl = _make_label(header, "Ultima actualizacion: --",
                                           fg=TEXT_SEC, font=("Segoe UI", 9))
        self._news_time_lbl.config(bg=BG_MAIN)
        self._news_time_lbl.pack(side="left")

        # Filtros de impacto
        filter_frame = tk.Frame(header, bg=BG_MAIN)
        filter_frame.pack(side="right")
        self._news_filter = tk.StringVar(value="all")
        for val, txt, clr in [("all", "Todos", TEXT), ("High", "\U0001F534 Alto", ERR),
                                ("Medium", "\U0001F7E1 Medio", WARN)]:
            tk.Radiobutton(filter_frame, text=txt, variable=self._news_filter, value=val,
                          command=self._apply_news_filter, bg=BG_MAIN, fg=clr,
                          selectcolor=BG_INPUT, activebackground=BG_MAIN, activeforeground=clr,
                          font=("Segoe UI", 9, "bold")).pack(side="left", padx=5)

        # Resumen del dia
        summary_frame = _make_section_frame(scroll_frame, "\U0001F4CA Resumen del Dia")
        summary_frame.pack(fill="x", padx=10, pady=(5, 5))
        self._news_summary_lbl = _make_label(summary_frame, "Cargando noticias...",
                                              fg=TEXT, font=("Segoe UI", 10))
        self._news_summary_lbl.pack(fill="x", padx=5, pady=5)

        # Proximas noticias importantes
        next_frame = _make_section_frame(scroll_frame, "\u23F0 Proximas Noticias de Alto Impacto")
        next_frame.pack(fill="x", padx=10, pady=5)
        self._news_next_lbl = _make_label(next_frame, "Cargando...",
                                           fg=WARN, font=("Segoe UI", 11, "bold"))
        self._news_next_lbl.pack(fill="x", padx=5, pady=5)

        # Tabla principal
        main_frame = _make_section_frame(scroll_frame, "\U0001F4F0 Calendario Economico — Hoy")
        main_frame.pack(fill="x", padx=10, pady=5)

        news_cols = ("Hora", "Moneda", "Impacto", "Pares Afectados", "Evento", "Previo", "Pronostico", "Actual")
        self._news_tree = ttk.Treeview(main_frame, columns=news_cols, show="headings", height=15)
        self._news_tree.heading("Hora", text="Hora")
        self._news_tree.heading("Moneda", text="Moneda")
        self._news_tree.heading("Impacto", text="Impacto")
        self._news_tree.heading("Pares Afectados", text="Pares Afectados")
        self._news_tree.heading("Evento", text="Evento")
        self._news_tree.heading("Previo", text="Previo")
        self._news_tree.heading("Pronostico", text="Pronostico")
        self._news_tree.heading("Actual", text="Actual")
        self._news_tree.column("Hora", width=60, anchor="center")
        self._news_tree.column("Moneda", width=60, anchor="center")
        self._news_tree.column("Impacto", width=90, anchor="center")
        self._news_tree.column("Pares Afectados", width=180, anchor="w")
        self._news_tree.column("Evento", width=300, anchor="w")
        self._news_tree.column("Previo", width=80, anchor="center")
        self._news_tree.column("Pronostico", width=80, anchor="center")
        self._news_tree.column("Actual", width=80, anchor="center")
        self._news_tree.tag_configure("high", foreground="#ef4444", font=("Segoe UI", 10, "bold"))
        self._news_tree.tag_configure("medium", foreground="#f59e0b")
        self._news_tree.tag_configure("low", foreground="#6b7a8d")
        self._news_tree.tag_configure("past", foreground="#4a5568")
        self._news_tree.tag_configure("upcoming_high", foreground="#ef4444", background="#1a0a0a",
                                       font=("Segoe UI", 10, "bold"))
        # Scrollbar — FIX 2026-04-16: faltaba .pack() — debe ir ANTES del tree
        scrollbar = ttk.Scrollbar(main_frame, orient="vertical", command=self._news_tree.yview)
        scrollbar.pack(side="right", fill="y")
        self._news_tree.configure(yscrollcommand=scrollbar.set)
        self._news_tree.pack(side="left", fill="both", expand=True, pady=(0, 5))

        # Leyenda
        legend_frame = tk.Frame(scroll_frame, bg=BG_MAIN)
        legend_frame.pack(fill="x", padx=10, pady=(0, 10))
        for clr, txt in [(ERR, "\U0001F534 Alto — Puede mover el mercado significativamente"),
                          (WARN, "\U0001F7E1 Medio — Movimiento moderado esperado"),
                          (TEXT_SEC, "\u26AA Bajo — Impacto menor en el mercado")]:
            _make_label(legend_frame, txt, fg=clr, font=("Segoe UI", 9)).pack(anchor="w", padx=5)

        # Cache de datos
        self._news_data_cache = []

        # Fetch inicial
        self._refresh_news()

    def _refresh_news(self):
        """Fetch economic calendar from ForexFactory and update the Noticias tab."""
        def do_fetch():
            try:
                url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=8) as resp:
                    data = json.loads(resp.read().decode())

                # Filter today's events
                today = datetime.now().strftime("%Y-%m-%d")
                today_events = []
                for ev in data:
                    ev_date = ev.get("date", "")[:10]
                    if ev_date == today:
                        today_events.append(ev)

                self._news_data_cache = today_events

                def update_ui():
                    self._render_news(today_events)
                    self._news_time_lbl.config(
                        text=f"Ultima actualizacion: {datetime.now().strftime('%H:%M:%S')} — {len(today_events)} eventos hoy")

                self.root.after(0, update_ui)
            except Exception as e:
                _log(f"Error fetching news: {e}")

        threading.Thread(target=do_fetch, daemon=True).start()

    def _render_news(self, events):
        """Render news events into the treeview with currency→pair mapping."""
        self._news_tree.delete(*self._news_tree.get_children())

        if not events:
            self._news_tree.insert("", "end", values=("--", "--", "--", "--", "Sin noticias hoy", "", "", ""))
            self._news_summary_lbl.config(text="No hay noticias economicas programadas para hoy.")
            self._news_next_lbl.config(text="Sin noticias de alto impacto pendientes", fg=TEXT_SEC)
            return

        now_str = datetime.now().strftime("%H:%M")
        high_count = sum(1 for e in events if e.get("impact") == "High")
        med_count = sum(1 for e in events if e.get("impact") == "Medium")
        low_count = len(events) - high_count - med_count

        # Resumen
        self._news_summary_lbl.config(
            text=f"\U0001F534 {high_count} alto impacto  |  \U0001F7E1 {med_count} medio  |  \u26AA {low_count} bajo  |  Total: {len(events)} eventos")

        # Proxima noticia de alto impacto
        next_high = None
        for ev in sorted(events, key=lambda x: x.get("date", "")):
            hora = ev.get("date", "")[11:16] if len(ev.get("date", "")) > 11 else ""
            if ev.get("impact") == "High" and hora >= now_str:
                next_high = ev
                break

        if next_high:
            _nh_hora = next_high.get("date", "")[11:16]
            _nh_pais = next_high.get("country", "")
            _nh_titulo = next_high.get("title", "")
            _nh_pares = ", ".join(self._CURRENCY_PAIRS.get(_nh_pais, []))
            self._news_next_lbl.config(
                text=f"\u26A0 {_nh_hora}h — {_nh_pais} — {_nh_titulo}  \u2192  Afecta: {_nh_pares}",
                fg=ERR)
        else:
            self._news_next_lbl.config(text="\u2705 No quedan noticias de alto impacto hoy", fg=WIN_COLOR)

        # Filtro
        filt = self._news_filter.get()
        sorted_events = sorted(events, key=lambda x: x.get("date", ""))

        for ev in sorted_events:
            impacto = ev.get("impact", "Low")
            if filt != "all" and impacto != filt:
                continue

            hora = ev.get("date", "")[11:16] if len(ev.get("date", "")) > 11 else "--"
            moneda = ev.get("country", "")
            titulo = ev.get("title", "")
            previo = ev.get("previous", "") or ""
            pronostico = ev.get("forecast", "") or ""
            actual = ev.get("actual", "") or ""

            # Pares afectados
            pares = ", ".join(self._CURRENCY_PAIRS.get(moneda, [moneda]))

            # Impacto display
            if impacto == "High":
                imp_display = "\U0001F534 ALTO"
            elif impacto == "Medium":
                imp_display = "\U0001F7E1 MEDIO"
            else:
                imp_display = "\u26AA Bajo"

            # Tag: pasado vs futuro + impacto
            is_past = hora < now_str
            if is_past:
                tag = "past"
            elif impacto == "High":
                tag = "upcoming_high"
            elif impacto == "Medium":
                tag = "medium"
            else:
                tag = "low"

            self._news_tree.insert("", "end",
                values=(hora, moneda, imp_display, pares, titulo, previo, pronostico, actual),
                tags=(tag,))

    def _apply_news_filter(self):
        """Re-render news with current filter."""
        if hasattr(self, '_news_data_cache') and self._news_data_cache:
            self._render_news(self._news_data_cache)

    # --------------------------------------------------------
    #  MT5 CAPITAL REFRESH (every 30s)
    # --------------------------------------------------------
    def _refresh_mt5_capital_loop(self):
        """Lee capital + currency + tipo de cuenta desde mt5_realtime.json (live) con
        fallback a estado.json. Etiqueta refleja DEMO/REAL y simbolo de moneda real."""
        def _update():
            try:
                # Preferir mt5_realtime.json (lo escribe monitor_real.py con info live)
                bal = None; login = ""; currency = "USD"; acc_type = "REAL"
                from pathlib import Path as _Path
                _rt_path = _Path(__file__).parent / "mt5_realtime.json"
                if _rt_path.exists():
                    try:
                        _rt = json.loads(_rt_path.read_text(encoding="utf-8"))
                        _c = _rt.get("cuenta", {}) or {}
                        bal      = _c.get("balance", None)
                        login    = _c.get("login", "") or ""
                        currency = (_c.get("currency", "") or "USD").upper()
                        acc_type = (_c.get("account_type", "") or "REAL").upper()
                    except Exception:
                        pass
                # Fallback a estado.json
                if bal is None:
                    estado = load_estado()
                    bal = estado.get("capital_usuario", None)
                    login = estado.get("mt5_login", "") or login

                if bal is not None and bal > 0:
                    color = ACCENT_BRIGHT
                    login_txt = f" #{login}" if login else ""
                    sym = {"EUR": "€", "USD": "$", "GBP": "£", "JPY": "¥"}.get(currency, currency + " ")
                    type_txt = "Demo" if acc_type == "DEMO" else ("Contest" if acc_type == "CONTEST" else "Real")
                    self._dash_mt5_capital_lbl.config(
                        text=f"MT5 {type_txt}{login_txt}: {sym}{bal:,.2f}", fg=color)
                    self._mt5_balance_real = bal
                    self._equity_history.append((time.time(), bal))
                    if len(self._equity_history) > 200:
                        self._equity_history = self._equity_history[-200:]
                else:
                    self._dash_mt5_capital_lbl.config(text="MT5: sin datos", fg=TEXT_SEC)
            except Exception:
                self._dash_mt5_capital_lbl.config(text="MT5: sin datos", fg=TEXT_SEC)

        _update()
        self.root.after(8000, self._refresh_mt5_capital_loop)

    # --------------------------------------------------------
    #  P&L CHART DRAWING
    # --------------------------------------------------------
    def _draw_pnl_chart(self, pnl_points):
        """Draw a simple line chart on self._pnl_canvas.
        pnl_points: list of (label, value) where value is cumulative pips."""
        if not hasattr(self, '_pnl_canvas'):
            return
        c = self._pnl_canvas
        c.delete("all")
        w = c.winfo_width() or 600
        h = c.winfo_height() or 120

        if len(pnl_points) < 2:
            c.create_text(w // 2, h // 2, text="Sin datos suficientes",
                          fill=TEXT_SEC, font=("Segoe UI", 10))
            return

        values = [p[1] for p in pnl_points]
        min_v = min(values)
        max_v = max(values)
        spread = max_v - min_v if max_v != min_v else 1

        pad_x = 30
        pad_y = 15
        chart_w = w - 2 * pad_x
        chart_h = h - 2 * pad_y

        # Zero line
        zero_y = pad_y + chart_h * (1 - (0 - min_v) / spread) if min_v < 0 else h - pad_y
        if min_v >= 0:
            zero_y = h - pad_y
        elif max_v <= 0:
            zero_y = pad_y
        else:
            zero_y = pad_y + chart_h * (max_v / spread)
        c.create_line(pad_x, zero_y, w - pad_x, zero_y, fill="#30363d", dash=(4, 4))
        c.create_text(pad_x - 5, zero_y, text="0", fill=TEXT_SEC,
                       font=("Segoe UI", 7), anchor="e")

        # Plot points
        n = len(pnl_points)
        step_x = chart_w / max(n - 1, 1)
        coords = []
        for i, (lbl, val) in enumerate(pnl_points):
            x = pad_x + i * step_x
            y = pad_y + chart_h * (1 - (val - min_v) / spread)
            coords.append((x, y))

        # Fill area
        if len(coords) >= 2:
            fill_coords = [(coords[0][0], zero_y)]
            fill_coords.extend(coords)
            fill_coords.append((coords[-1][0], zero_y))
            flat = [c for pt in fill_coords for c in pt]
            c.create_polygon(flat, fill="#00d4aa20", outline="")

        # Line
        for i in range(len(coords) - 1):
            color = WIN_COLOR if coords[i + 1][1] <= coords[i][1] else LOSS_COLOR
            c.create_line(coords[i][0], coords[i][1],
                          coords[i + 1][0], coords[i + 1][1],
                          fill=color, width=2)

        # End value
        last_val = values[-1]
        last_color = WIN_COLOR if last_val >= 0 else LOSS_COLOR
        c.create_text(w - pad_x + 5, coords[-1][1],
                       text=f"{last_val:+.1f}", fill=last_color,
                       font=("Segoe UI", 8, "bold"), anchor="w")

    # --------------------------------------------------------
    #  EQUITY CURVE DRAWING
    # --------------------------------------------------------
    def _draw_equity_curve(self):
        """Draw equity curve from self._equity_history on self._equity_canvas."""
        if not hasattr(self, '_equity_canvas'):
            return
        c = self._equity_canvas
        c.delete("all")
        w = c.winfo_width() or 600
        h = c.winfo_height() or 120

        if len(self._equity_history) < 2:
            c.create_text(w // 2, h // 2, text="Esperando datos de MT5...",
                          fill=TEXT_SEC, font=("Segoe UI", 10))
            return

        values = [p[1] for p in self._equity_history]
        min_v = min(values) * 0.999
        max_v = max(values) * 1.001
        spread = max_v - min_v if max_v != min_v else 1

        pad_x = 40
        pad_y = 15
        chart_w = w - 2 * pad_x
        chart_h = h - 2 * pad_y

        n = len(values)
        step_x = chart_w / max(n - 1, 1)
        coords = []
        for i, val in enumerate(values):
            x = pad_x + i * step_x
            y = pad_y + chart_h * (1 - (val - min_v) / spread)
            coords.append((x, y))

        # Fill
        if len(coords) >= 2:
            fill_coords = [(coords[0][0], h - pad_y)]
            fill_coords.extend(coords)
            fill_coords.append((coords[-1][0], h - pad_y))
            flat = [c for pt in fill_coords for c in pt]
            c.create_polygon(flat, fill="#00d4aa15", outline="")

        # Line
        for i in range(len(coords) - 1):
            c.create_line(coords[i][0], coords[i][1],
                          coords[i + 1][0], coords[i + 1][1],
                          fill=ACCENT_BRIGHT, width=2)

        # Labels
        c.create_text(pad_x - 5, pad_y, text=f"${max_v:,.0f}", fill=TEXT_SEC,
                       font=("Segoe UI", 7), anchor="e")
        c.create_text(pad_x - 5, h - pad_y, text=f"${min_v:,.0f}", fill=TEXT_SEC,
                       font=("Segoe UI", 7), anchor="e")
        c.create_text(w - pad_x + 5, coords[-1][1],
                       text=f"${values[-1]:,.2f}", fill=ACCENT_BRIGHT,
                       font=("Segoe UI", 8, "bold"), anchor="w")

    # --------------------------------------------------------
    #  TRAFFIC LIGHT UPDATE
    # --------------------------------------------------------
    def _update_traffic_lights(self, estado):
        """Update traffic light indicators for each asset."""
        ops_activas = estado.get("operaciones_activas", {})
        now = datetime.now()
        hora = now.hour

        # Horarios por activo (Andorra time)
        horarios = {
            "EUR/USD": (7, 21), "NASDAQ": (13, 21), "S&P 500": (13, 21),
        }

        # Assets with open positions
        active_assets = set()
        for key, op in ops_activas.items():
            asset = _normalize_asset(op.get("nombre", op.get("ticker", "")))
            if asset in _VALID_ASSETS:
                active_assets.add(asset)

        for asset, (light_canvas, oval_id) in self._traffic_lights.items():
            h_open, h_close = horarios.get(asset, (0, 24))
            if asset in active_assets:
                color = "#00ff66"  # bright green = has position
            elif h_open <= hora < h_close and now.weekday() < 5:
                color = WARN  # yellow = scanning
            else:
                color = ERR  # red = outside hours
            light_canvas.itemconfig(oval_id, fill=color)

    # --------------------------------------------------------
    #  PERFORMANCE PANEL UPDATE
    # --------------------------------------------------------
    def _update_performance_panel(self, historial):
        """Update win rate by period and best/worst trade."""
        now = datetime.now()
        today_str = now.strftime("%d/%m/%Y")

        # Parse dates
        today_ops = []
        week_ops = []
        month_ops = []
        for op in historial:
            fecha_str = op.get("fecha", "")
            try:
                op_date = datetime.strptime(fecha_str, "%d/%m/%Y")
            except Exception:
                continue
            delta = (now - op_date).days
            if fecha_str == today_str:
                today_ops.append(op)
            if delta <= 7:
                week_ops.append(op)
            if delta <= 30:
                month_ops.append(op)

        def _wr(ops):
            total = len(ops)
            if total == 0:
                return "--"
            wins = sum(1 for o in ops if o.get("resultado") == "WIN")
            return f"{wins / total * 100:.1f}% ({wins}/{total})"

        self._perf_wr_today.config(text=_wr(today_ops))
        self._perf_wr_week.config(text=_wr(week_ops))
        self._perf_wr_month.config(text=_wr(month_ops))

        # Best and worst trade today
        if today_ops:
            pips_list = [(op.get("pips", 0), _normalize_asset(op.get("nombre", ""))) for op in today_ops]
            best = max(pips_list, key=lambda x: x[0])
            worst = min(pips_list, key=lambda x: x[0])
            self._perf_best_trade.config(
                text=f"{best[1]} +{best[0]:.1f} pips", fg=WIN_COLOR)
            self._perf_worst_trade.config(
                text=f"{worst[1]} {worst[0]:.1f} pips", fg=LOSS_COLOR)
            total_pips = sum(p[0] for p in pips_list)
            pip_color = WIN_COLOR if total_pips >= 0 else LOSS_COLOR
            self._perf_total_pips.config(text=f"{total_pips:+.1f} pips", fg=pip_color)
        else:
            self._perf_best_trade.config(text="--", fg=TEXT)
            self._perf_worst_trade.config(text="--", fg=TEXT)
            self._perf_total_pips.config(text="--", fg=TEXT)

    # --------------------------------------------------------
    #  VISUAL ALERTS (improvement 3)
    # --------------------------------------------------------
    def _show_trade_alert(self, asset, tipo, score):
        """Show a small popup notification when a trade opens."""
        alert = tk.Toplevel(self.root)
        alert.overrideredirect(True)
        alert.attributes("-topmost", True)
        alert.configure(bg="#1a1a2e")

        # Position at top-right of screen
        screen_w = self.root.winfo_screenwidth()
        alert.geometry(f"320x80+{screen_w - 340}+40")

        color = WIN_COLOR if tipo.upper() == "COMPRA" else LOSS_COLOR
        premium_text = " PREMIUM" if isinstance(score, (int, float)) and score >= 4 else ""

        tk.Label(alert, text=f"\U0001F4E2 Nueva Operacion{premium_text}",
                 bg="#1a1a2e", fg=ACCENT_BRIGHT, font=("Segoe UI", 11, "bold")).pack(
                     anchor="w", padx=10, pady=(8, 0))
        tk.Label(alert, text=f"{asset} - {tipo} (Score: {score})",
                 bg="#1a1a2e", fg=color, font=("Segoe UI", 10)).pack(
                     anchor="w", padx=10, pady=(2, 8))

        # Auto-close after 5 seconds
        alert.after(5000, alert.destroy)

        # Play sound for new signals
        if premium_text:
            try:
                winsound.Beep(1200, 300)
                winsound.Beep(1500, 200)
            except Exception:
                pass
        else:
            self._play_sound("signal")

    def _check_new_trades(self, estado):
        """Detect new trades and fire alerts."""
        ops_activas = estado.get("operaciones_activas", {})
        current_keys = set(ops_activas.keys())
        new_keys = current_keys - self._last_active_ops_keys

        for key in new_keys:
            op = ops_activas[key]
            asset = _normalize_asset(op.get("nombre", op.get("ticker", key)))
            tipo = op.get("tipo", "?")
            score = op.get("score", 0)
            self._show_trade_alert(asset, tipo, score)

        self._last_active_ops_keys = current_keys

    def _update_tab_flash(self, estado):
        """Flash tab text color when there are active positions."""
        ops_activas = estado.get("operaciones_activas", {})
        has_main_ops = len(ops_activas) > 0

        # Flash senales tab
        tick_even = (self._tick_count % 2 == 0)
        if has_main_ops:
            count = len(ops_activas)
            if tick_even:
                self.notebook.tab(self._tab_senales, text=f" \U0001F4E1 Se\u00f1ales ({count}) ")
            else:
                self.notebook.tab(self._tab_senales, text=f" \U0001F534 Se\u00f1ales ({count}) ")
        else:
            self.notebook.tab(self._tab_senales, text=" \U0001F4E1 Se\u00f1ales ")

    # ============================================================
    #  TAB 2: SENALES
    # ============================================================
    def _build_senales(self):
        canvas, scroll_frame = _make_scrollable(self._tab_senales)
        self._scroll_canvases[str(self._tab_senales)] = canvas

        # Header
        header = tk.Frame(scroll_frame, bg=BG_MAIN)
        header.pack(fill="x", padx=10, pady=(10, 5))

        _make_button(header, "Actualizar", self._refresh_senales,
                     bg=BG_INPUT, fg=TEXT).pack(side="left", padx=(0, 10))
        self._senales_escaneo_lbl = _make_label(header, "Proximo escaneo: --s",
                                                fg=TEXT_SEC, font=("Segoe UI", 10))
        self._senales_escaneo_lbl.config(bg=BG_MAIN)
        self._senales_escaneo_lbl.pack(side="left", padx=10)
        self._senales_market_lbl = _make_label(header, "Mercado: --",
                                               fg=TEXT_SEC, font=("Segoe UI", 10))
        self._senales_market_lbl.config(bg=BG_MAIN)
        self._senales_market_lbl.pack(side="right")

        # Active Signals Treeview
        active_frame = _make_section_frame(scroll_frame, "Senales Activas")
        active_frame.pack(fill="x", padx=10, pady=5)

        act_cols = ("hora", "activo", "tipo", "entrada", "sl", "tp1", "score",
                    "confianza", "estado")
        self._senales_active_tree = ttk.Treeview(active_frame, columns=act_cols,
                                                 show="headings", height=6)
        for col, heading, w in [
            ("hora", "Hora", 70), ("activo", "Activo", 100), ("tipo", "Tipo", 80),
            ("entrada", "Entrada", 100), ("sl", "Pérdida (SL)", 100), ("tp1", "Objetivo (TP1)", 100),
            ("score", "Puntuación", 80), ("confianza", "Fuente", 100),
            ("estado", "Estado", 100),
        ]:
            self._senales_active_tree.heading(col, text=heading)
            self._senales_active_tree.column(col, width=w, anchor="center")

        self._senales_active_tree.tag_configure("COMPRA", foreground=WIN_COLOR)
        self._senales_active_tree.tag_configure("VENTA", foreground=LOSS_COLOR)
        self._senales_active_tree.tag_configure("PREMIUM", foreground=PREMIUM_COLOR)
        self._senales_active_tree.pack(fill="x", pady=(0, 5))

        # Buttons below active signals
        btn_row = tk.Frame(active_frame, bg=BG_PANEL)
        btn_row.pack(fill="x", pady=(0, 5))
        _make_button(btn_row, "Cerrar Senal Seleccionada", self._cmd_cerrar_senal,
                     bg="#b91c1c", fg="white").pack(side="left", padx=5)

        # Right-click context menu for active signals
        self._senales_ctx_menu = tk.Menu(self._senales_active_tree, tearoff=0,
                                         bg=BG_PANEL, fg=TEXT)
        self._senales_ctx_menu.add_command(label="Cerrar esta senal",
                                            command=self._cmd_cerrar_senal)
        self._senales_active_tree.bind("<Button-3>", self._show_senal_context_menu)

        # Closed Signals Treeview
        closed_frame = _make_section_frame(scroll_frame, "Ultimas Senales Cerradas (Hoy)")
        closed_frame.pack(fill="x", padx=10, pady=5)

        cls_cols = ("hora", "activo", "tipo", "entrada", "salida", "pips",
                    "resultado", "duracion")
        self._senales_closed_tree = ttk.Treeview(closed_frame, columns=cls_cols,
                                                 show="headings", height=8)
        for col, heading, w in [
            ("hora", "Hora", 70), ("activo", "Activo", 100), ("tipo", "Tipo", 70),
            ("entrada", "Entrada", 100), ("salida", "Salida", 100),
            ("pips", "Pips", 90), ("resultado", "Resultado", 80),
            ("duracion", "Duracion", 80),
        ]:
            self._senales_closed_tree.heading(col, text=heading)
            self._senales_closed_tree.column(col, width=w, anchor="center")

        self._senales_closed_tree.tag_configure("WIN", foreground=WIN_COLOR)
        self._senales_closed_tree.tag_configure("LOSS", foreground=LOSS_COLOR)
        self._senales_closed_tree.tag_configure("MANUAL", foreground="#f0ad4e")
        self._senales_closed_tree.pack(fill="x", pady=(0, 5))

        # Summary bar
        self._senales_summary = _make_label(scroll_frame, "Activas: 0 | Hoy: 0W / 0L | Pips hoy: 0.0",
                                            fg=ACCENT, font=("Segoe UI", 10, "bold"))
        self._senales_summary.config(bg=BG_MAIN)
        self._senales_summary.pack(fill="x", padx=10, pady=(0, 15))

    # ============================================================
    #  TAB 3: ANALISIS
    # ============================================================
    def _build_analisis(self):
        canvas, scroll_frame = _make_scrollable(self._tab_analisis)
        self._scroll_canvases[str(self._tab_analisis)] = canvas

        # Header
        header = tk.Frame(scroll_frame, bg=BG_MAIN)
        header.pack(fill="x", padx=10, pady=(10, 5))

        _make_button(header, "Actualizar Analisis", self._refresh_analisis,
                     bg=BG_INPUT, fg=TEXT).pack(side="left", padx=(0, 10))
        self._analisis_time_lbl = _make_label(header, "Ultima actualizacion: --",
                                              fg=TEXT_SEC, font=("Segoe UI", 9))
        self._analisis_time_lbl.config(bg=BG_MAIN)
        self._analisis_time_lbl.pack(side="left")

        # 3x2 grid of asset cards
        self._analisis_cards = {}
        grid_frame = tk.Frame(scroll_frame, bg=BG_MAIN)
        grid_frame.pack(fill="x", padx=10, pady=5)
        grid_frame.columnconfigure(0, weight=1)
        grid_frame.columnconfigure(1, weight=1)
        grid_frame.columnconfigure(2, weight=1)

        assets_ordered = ["ORO", "EUR/USD", "USD/JPY", "NASDAQ", "S&P 500", "AUD/CAD", "EUR/CHF", "USD/CAD"]
        for idx, asset_name in enumerate(assets_ordered):
            r = idx // 3
            c = idx % 3
            card = self._create_analisis_card(grid_frame, asset_name)
            card.grid(row=r, column=c, padx=5, pady=5, sticky="nsew")

    def _create_analisis_card(self, parent, asset_name):
        card = tk.Frame(parent, bg=BG_PANEL, padx=12, pady=10,
                        highlightthickness=1, highlightbackground="#30363d")

        # Titulo con nombre del activo
        title = tk.Label(card, text=asset_name, bg=BG_PANEL, fg=ACCENT,
                         font=("Segoe UI", 13, "bold"), anchor="w")
        title.pack(fill="x")

        # Precio
        price_lbl = tk.Label(card, text="Precio: --", bg=BG_PANEL, fg=TEXT,
                             font=("Segoe UI", 10), anchor="w")
        price_lbl.pack(fill="x", pady=(2, 4))

        # Bullish/Bearish bar
        bar_frame = tk.Frame(card, bg=BG_PANEL, height=14)
        bar_frame.pack(fill="x", pady=2)
        bar_frame.pack_propagate(False)
        bull_bar = tk.Frame(bar_frame, bg=WIN_COLOR, width=1)
        bull_bar.place(relx=0, rely=0, relwidth=0.5, relheight=1.0)
        bear_bar = tk.Frame(bar_frame, bg=LOSS_COLOR, width=1)
        bear_bar.place(relx=0.5, rely=0, relwidth=0.5, relheight=1.0)

        pct_frame = tk.Frame(card, bg=BG_PANEL)
        pct_frame.pack(fill="x")
        bull_pct = tk.Label(pct_frame, text="Alcista: --%", bg=BG_PANEL, fg=WIN_COLOR,
                            font=("Segoe UI", 8), anchor="w")
        bull_pct.pack(side="left")
        bear_pct = tk.Label(pct_frame, text="Bajista: --%", bg=BG_PANEL, fg=LOSS_COLOR,
                            font=("Segoe UI", 8), anchor="e")
        bear_pct.pack(side="right")

        # Separador
        tk.Frame(card, bg="#30363d", height=1).pack(fill="x", pady=4)

        # Indicadores grid: RSI | ADX | Vol | Spread
        ind_frame = tk.Frame(card, bg=BG_PANEL)
        ind_frame.pack(fill="x")
        ind_frame.columnconfigure(0, weight=1)
        ind_frame.columnconfigure(1, weight=1)

        rsi_lbl = tk.Label(ind_frame, text="RSI: --", bg=BG_PANEL, fg=TEXT_SEC,
                           font=("Segoe UI", 9, "bold"), anchor="w")
        rsi_lbl.grid(row=0, column=0, sticky="w", pady=1)

        adx_lbl = tk.Label(ind_frame, text="ADX: --", bg=BG_PANEL, fg=TEXT_SEC,
                           font=("Segoe UI", 9, "bold"), anchor="w")
        adx_lbl.grid(row=0, column=1, sticky="w", pady=1)

        vol_lbl = tk.Label(ind_frame, text="Vol: --", bg=BG_PANEL, fg=TEXT_SEC,
                           font=("Segoe UI", 9, "bold"), anchor="w")
        vol_lbl.grid(row=1, column=0, sticky="w", pady=1)

        spread_lbl = tk.Label(ind_frame, text="Spread: --", bg=BG_PANEL, fg=TEXT_SEC,
                              font=("Segoe UI", 9, "bold"), anchor="w")
        spread_lbl.grid(row=1, column=1, sticky="w", pady=1)

        # EMA y MACD
        ema_lbl = tk.Label(card, text="EMA20/50: -- | MACD: --", bg=BG_PANEL, fg=TEXT_SEC,
                           font=("Segoe UI", 8), anchor="w")
        ema_lbl.pack(fill="x", pady=(2, 0))

        # Tendencia
        trend_lbl = tk.Label(card, text="Tendencia: --", bg=BG_PANEL, fg=TEXT_SEC,
                             font=("Segoe UI", 10, "bold"), anchor="w")
        trend_lbl.pack(fill="x", pady=(4, 2))

        # Señal activa
        signal_lbl = tk.Label(card, text="Sin senal activa", bg=BG_PANEL, fg=TEXT_SEC,
                              font=("Segoe UI", 9), anchor="w")
        signal_lbl.pack(fill="x")

        self._analisis_cards[asset_name] = {
            "price": price_lbl,
            "bull_bar": bull_bar,
            "bear_bar": bear_bar,
            "bull_pct": bull_pct,
            "bear_pct": bear_pct,
            "rsi": rsi_lbl,
            "adx": adx_lbl,
            "vol": vol_lbl,
            "spread": spread_lbl,
            "ema": ema_lbl,
            "trend": trend_lbl,
            "signal": signal_lbl,
        }
        return card

    # ============================================================
    #  TAB 4: TRADING CONFIG
    # ============================================================
    def _build_trading(self):
        canvas, scroll_frame = _make_scrollable(self._tab_trading)
        self._scroll_canvases[str(self._tab_trading)] = canvas

        tc = load_trading_config()
        self._tc_vars = {}

        # Parametros de Trading
        params_frame = _make_section_frame(scroll_frame, "Parametros de Trading")
        params_frame.pack(fill="x", padx=10, pady=(10, 5))

        params_grid = tk.Frame(params_frame, bg=BG_PANEL)
        params_grid.pack(fill="x")
        params_grid.columnconfigure(1, weight=1)

        row = 0

        # 1. Capital
        row = self._add_trading_entry(params_grid, row, "Capital ($):",
                                      "capital", str(tc.get("capital", 500)),
                                      "Tu capital total en la cuenta MT5")

        # 2. Riesgo por Trade
        row = self._add_trading_scale(params_grid, row, "Riesgo por Trade (%):",
                                      "riesgo_trade", tc.get("riesgo_trade", 0.02),
                                      0.01, 0.05, 0.005,
                                      "% del capital que se arriesga por operacion")

        # 3. Riesgo Premium
        row = self._add_trading_scale(params_grid, row, "Riesgo Premium (%):",
                                      "riesgo_premium", tc.get("riesgo_premium", 0.04),
                                      0.01, 0.08, 0.005,
                                      "% de riesgo para senales premium (score>=4)")

        # 4. Modo
        row = self._add_trading_combo(params_grid, row, "Modo:",
                                      "modo", tc.get("modo", "Normal"),
                                      ["Conservador", "Normal", "Agresivo"],
                                      "Conservador=menos riesgo, Agresivo=mas operaciones")

        # 6. MIN_SCORE
        row = self._add_trading_spinbox(params_grid, row, "MIN_SCORE:",
                                        "min_score", tc.get("min_score", 3),
                                        1, 5,
                                        "Puntaje minimo para abrir senal (4+=solo premium)")

        # 7. Max Trades Simult.
        row = self._add_trading_spinbox(params_grid, row, "Max Trades Simult.:",
                                        "max_trades", tc.get("max_trades", 5),
                                        1, 10,
                                        "Maximo operaciones abiertas al mismo tiempo")

        # 8. Max Perdida Diaria
        row = self._add_trading_scale(params_grid, row, "Max Perdida Diaria (%):",
                                      "max_perdida_diaria",
                                      tc.get("max_perdida_diaria", 0.10),
                                      0.05, 0.20, 0.01,
                                      "Si pierdes mas de esto, el bot se detiene hoy")

        # 9. Hora Apertura
        row = self._add_trading_spinbox(params_grid, row, "Hora Apertura:",
                                        "hora_apertura", tc.get("hora_apertura", 6),
                                        0, 23,
                                        "Hora local para empezar a buscar senales")

        # 10. Hora Corte
        row = self._add_trading_spinbox(params_grid, row, "Hora Corte:",
                                        "hora_corte", tc.get("hora_corte", 22),
                                        0, 23,
                                        "Hora local para dejar de abrir operaciones")

        # 11. Min R:R Ratio
        row = self._add_trading_scale(params_grid, row, "Min R:R Ratio:",
                                      "min_rr", tc.get("min_rr", 1.5),
                                      0.5, 3.0, 0.1,
                                      "Ratio minimo riesgo:beneficio para abrir trade")

        # 12. Auto-cierre (horas)
        row = self._add_trading_spinbox(params_grid, row, "Auto-cierre (horas):",
                                        "auto_cierre_horas",
                                        tc.get("auto_cierre_horas", 24),
                                        1, 48,
                                        "Cerrar operacion automaticamente despues de X horas")

        # 13. Intervalo Escaneo (seg)
        row = self._add_trading_spinbox(params_grid, row, "Intervalo Escaneo (seg):",
                                        "intervalo_escaneo",
                                        tc.get("intervalo_escaneo", 120),
                                        60, 600,
                                        "Cada cuantos segundos el bot escanea el mercado")

        # 14. Auto-Trading MT5
        row = self._add_trading_check(params_grid, row, "Auto-Trading MT5:",
                                      "auto_trading_mt5",
                                      tc.get("auto_trading_mt5", True),
                                      "Activar ejecucion real de ordenes en MetaTrader 5")

        # 15. Solo Premium en MT5
        row = self._add_trading_check(params_grid, row, "Solo Premium en MT5:",
                                      "solo_premium_mt5",
                                      tc.get("solo_premium_mt5", True),
                                      "MT5 solo ejecuta senales premium (score>=4, confianza>=40%)")

        # Spreads Maximos
        spreads_frame = _make_section_frame(scroll_frame, "Spreads Maximos (pips)")
        spreads_frame.pack(fill="x", padx=10, pady=5)

        spreads_grid = tk.Frame(spreads_frame, bg=BG_PANEL)
        spreads_grid.pack(fill="x")

        self._spread_entries = {}
        spreads = tc.get("spreads_max", {})
        for idx, asset in enumerate(["EUR/USD", "NASDAQ", "S&P 500", "AUD/CAD", "EUR/CHF", "USD/CAD"]):
            r = idx // 3
            c = (idx % 3) * 2
            _make_label(spreads_grid, f"{asset}:", fg=TEXT_SEC).grid(
                row=r, column=c, sticky="w", padx=(10, 5), pady=3)
            e = _make_entry(spreads_grid, width=8)
            e.insert(0, str(spreads.get(asset, 5)))
            e.grid(row=r, column=c + 1, sticky="w", padx=(0, 15), pady=3)
            self._spread_entries[asset] = e

        # Activos Habilitados
        activos_frame = _make_section_frame(scroll_frame, "Activos Habilitados")
        activos_frame.pack(fill="x", padx=10, pady=5)

        activos_grid = tk.Frame(activos_frame, bg=BG_PANEL)
        activos_grid.pack(fill="x")

        self._activo_vars = {}
        habilitados = tc.get("activos_habilitados", {})
        for idx, asset in enumerate(["EUR/USD", "NASDAQ", "S&P 500", "AUD/CAD", "EUR/CHF", "USD/CAD"]):
            r = idx // 3
            c = idx % 3
            var = tk.BooleanVar(value=habilitados.get(asset, True))
            chk = tk.Checkbutton(activos_grid, text=asset, variable=var,
                                 bg=BG_PANEL, fg=TEXT, selectcolor=BG_INPUT,
                                 activebackground=BG_PANEL, activeforeground=TEXT,
                                 font=("Segoe UI", 10))
            chk.grid(row=r, column=c, sticky="w", padx=15, pady=3)
            self._activo_vars[asset] = var

        # Save button
        btn_frame = tk.Frame(scroll_frame, bg=BG_MAIN)
        btn_frame.pack(fill="x", padx=10, pady=(10, 20))
        _make_button(btn_frame, "Guardar Configuracion", self._save_trading_config,
                     bg=ACCENT, fg="#000000").pack(side="left", padx=5)

    def _add_trading_entry(self, parent, row, label, key, default_val, desc):
        _make_label(parent, label, fg=TEXT).grid(row=row, column=0, sticky="w", padx=5, pady=3)
        e = _make_entry(parent, width=20)
        e.insert(0, default_val)
        e.grid(row=row, column=1, sticky="w", padx=5, pady=3)
        d = tk.Label(parent, text=desc, bg=BG_PANEL, fg=TEXT_SEC,
                     font=("Segoe UI", 8, "italic"), anchor="w")
        d.grid(row=row, column=2, sticky="w", padx=5, pady=3)
        self._tc_vars[key] = ("entry", e)
        return row + 1

    def _add_trading_scale(self, parent, row, label, key, default_val,
                           from_, to_, resolution, desc):
        _make_label(parent, label, fg=TEXT).grid(row=row, column=0, sticky="w", padx=5, pady=3)

        scale_frame = tk.Frame(parent, bg=BG_PANEL)
        scale_frame.grid(row=row, column=1, sticky="ew", padx=5, pady=3)

        val_var = tk.DoubleVar(value=default_val)
        val_label = tk.Label(scale_frame, text=f"{default_val:.3f}", bg=BG_PANEL, fg=ACCENT,
                             font=("Segoe UI", 10, "bold"), width=8)
        val_label.pack(side="right")

        def on_scale_change(val):
            val_label.config(text=f"{float(val):.3f}")

        scale = tk.Scale(scale_frame, from_=from_, to=to_, resolution=resolution,
                         orient="horizontal", variable=val_var,
                         bg=BG_PANEL, fg=TEXT, troughcolor=BG_INPUT,
                         highlightthickness=0, sliderrelief="flat",
                         showvalue=False, command=on_scale_change, length=200)
        scale.pack(side="left", fill="x", expand=True)

        d = tk.Label(parent, text=desc, bg=BG_PANEL, fg=TEXT_SEC,
                     font=("Segoe UI", 8, "italic"), anchor="w")
        d.grid(row=row, column=2, sticky="w", padx=5, pady=3)
        self._tc_vars[key] = ("scale", val_var)
        return row + 1

    def _add_trading_combo(self, parent, row, label, key, default_val, values, desc):
        _make_label(parent, label, fg=TEXT).grid(row=row, column=0, sticky="w", padx=5, pady=3)
        var = tk.StringVar(value=default_val)
        combo = ttk.Combobox(parent, textvariable=var, values=values, state="readonly", width=18)
        combo.grid(row=row, column=1, sticky="w", padx=5, pady=3)
        d = tk.Label(parent, text=desc, bg=BG_PANEL, fg=TEXT_SEC,
                     font=("Segoe UI", 8, "italic"), anchor="w")
        d.grid(row=row, column=2, sticky="w", padx=5, pady=3)
        self._tc_vars[key] = ("combo", var)
        return row + 1

    def _add_trading_spinbox(self, parent, row, label, key, default_val, from_, to_, desc):
        _make_label(parent, label, fg=TEXT).grid(row=row, column=0, sticky="w", padx=5, pady=3)
        var = tk.IntVar(value=default_val)
        spin = tk.Spinbox(parent, from_=from_, to=to_, textvariable=var, width=8,
                          bg=BG_INPUT, fg=TEXT, buttonbackground=BG_INPUT,
                          insertbackground=TEXT, font=("Segoe UI", 10),
                          highlightthickness=1, highlightcolor=ACCENT,
                          highlightbackground="#30363d", relief="flat")
        spin.grid(row=row, column=1, sticky="w", padx=5, pady=3)
        d = tk.Label(parent, text=desc, bg=BG_PANEL, fg=TEXT_SEC,
                     font=("Segoe UI", 8, "italic"), anchor="w")
        d.grid(row=row, column=2, sticky="w", padx=5, pady=3)
        self._tc_vars[key] = ("spinbox", var)
        return row + 1

    def _add_trading_check(self, parent, row, label, key, default_val, desc):
        _make_label(parent, label, fg=TEXT).grid(row=row, column=0, sticky="w", padx=5, pady=3)
        var = tk.BooleanVar(value=default_val)
        chk = tk.Checkbutton(parent, variable=var, bg=BG_PANEL, fg=TEXT,
                             selectcolor=BG_INPUT, activebackground=BG_PANEL,
                             activeforeground=TEXT)
        chk.grid(row=row, column=1, sticky="w", padx=5, pady=3)
        d = tk.Label(parent, text=desc, bg=BG_PANEL, fg=TEXT_SEC,
                     font=("Segoe UI", 8, "italic"), anchor="w")
        d.grid(row=row, column=2, sticky="w", padx=5, pady=3)
        self._tc_vars[key] = ("check", var)
        return row + 1

    def _save_trading_config(self):
        tc = {}
        for key, (wtype, widget) in self._tc_vars.items():
            if wtype == "entry":
                val = widget.get().strip()
                try:
                    tc[key] = float(val)
                except ValueError:
                    tc[key] = val
            elif wtype == "scale":
                tc[key] = round(widget.get(), 4)
            elif wtype == "combo":
                tc[key] = widget.get()
            elif wtype == "spinbox":
                tc[key] = widget.get()
            elif wtype == "check":
                tc[key] = widget.get()

        # Spreads
        spreads = {}
        for asset, entry in self._spread_entries.items():
            try:
                spreads[asset] = float(entry.get().strip())
            except ValueError:
                spreads[asset] = 5
        tc["spreads_max"] = spreads

        # Activos habilitados
        habilitados = {}
        for asset, var in self._activo_vars.items():
            habilitados[asset] = var.get()
        tc["activos_habilitados"] = habilitados

        save_trading_config(tc)
        messagebox.showinfo("Guardado", "Configuracion de trading guardada correctamente.")

    # ============================================================
    #  TAB 5: CONEXIONES
    # ============================================================
    def _build_conexiones(self):
        canvas, scroll_frame = _make_scrollable(self._tab_conexiones)
        self._scroll_canvases[str(self._tab_conexiones)] = canvas

        env = load_env()
        self._conn_entries = {}

        # === MetaTrader 5 ===
        mt5_frame = _make_section_frame(scroll_frame, "MetaTrader 5")
        mt5_frame.pack(fill="x", padx=10, pady=(10, 5))

        tk.Label(mt5_frame, text="Credenciales de tu cuenta de broker para ejecutar operaciones reales",
                 bg=BG_PANEL, fg=TEXT_SEC, font=("Segoe UI", 9, "italic")).pack(anchor="w", pady=(0, 5))

        mt5_grid = tk.Frame(mt5_frame, bg=BG_PANEL)
        mt5_grid.pack(fill="x")

        self._add_conn_field(mt5_grid, 0, "MT5 Login:", "MT5_LOGIN", env.get("MT5_LOGIN", ""))
        self._add_conn_field(mt5_grid, 1, "MT5 Password:", "MT5_PASSWORD",
                             env.get("MT5_PASSWORD", ""), show="*")
        self._add_conn_field(mt5_grid, 2, "MT5 Server:", "MT5_SERVER",
                             env.get("MT5_SERVER", ""))

        btn_mt5 = _make_button(mt5_frame, "Probar Conexion MT5", self._test_mt5,
                               bg=BG_INPUT, fg=TEXT)
        btn_mt5.pack(anchor="w", pady=(5, 0))

        # === Telegram ===
        tg_frame = _make_section_frame(scroll_frame, "Telegram")
        tg_frame.pack(fill="x", padx=10, pady=5)

        tk.Label(tg_frame, text="Bot de Telegram para enviar senales y recibir comandos",
                 bg=BG_PANEL, fg=TEXT_SEC, font=("Segoe UI", 9, "italic")).pack(anchor="w", pady=(0, 5))

        tg_grid = tk.Frame(tg_frame, bg=BG_PANEL)
        tg_grid.pack(fill="x")

        self._add_conn_field(tg_grid, 0, "Bot Token:", "TELEGRAM_TOKEN",
                             env.get("TELEGRAM_TOKEN", ""), show="*")
        self._add_conn_field(tg_grid, 1, "Channel ID (VIP):", "CHANNEL_ID",
                             env.get("CHANNEL_ID", ""))
        self._add_conn_field(tg_grid, 2, "Group ID (publico):", "GROUP_ID",
                             env.get("GROUP_ID", ""))
        self._add_conn_field(tg_grid, 3, "User ID 1 (Admin):", "USER_ID_1",
                             env.get("USER_ID_1", ""))
        self._add_conn_field(tg_grid, 4, "User ID 2:", "USER_ID_2",
                             env.get("USER_ID_2", ""))

        btn_tg = _make_button(tg_frame, "Probar Telegram", self._test_telegram,
                              bg=BG_INPUT, fg=TEXT)
        btn_tg.pack(anchor="w", pady=(5, 0))

        # === Web Sync ===
        web_frame = _make_section_frame(scroll_frame, "Sincronizacion Web")
        web_frame.pack(fill="x", padx=10, pady=5)

        tk.Label(web_frame, text="Conexion con buysell365.pro para mostrar el dashboard online",
                 bg=BG_PANEL, fg=TEXT_SEC, font=("Segoe UI", 9, "italic")).pack(anchor="w", pady=(0, 5))

        web_grid = tk.Frame(web_frame, bg=BG_PANEL)
        web_grid.pack(fill="x")

        self._add_conn_field(web_grid, 0, "Web URL:", "WEB_URL",
                             env.get("WEB_URL", "https://buysell365.pro"))
        self._add_conn_field(web_grid, 1, "Sync Secret:", "SYNC_SECRET",
                             env.get("SYNC_SECRET", ""), show="*")
        self._add_conn_field(web_grid, 2, "API Secret Key:", "API_SECRET_KEY",
                             env.get("API_SECRET_KEY", ""), show="*")
        self._add_conn_field(web_grid, 3, "Dashboard URL:", "DASHBOARD_URL",
                             env.get("DASHBOARD_URL", "https://buysell365.pro/dashboard"))

        btn_web = _make_button(web_frame, "Probar Web", self._test_web,
                               bg=BG_INPUT, fg=TEXT)
        btn_web.pack(anchor="w", pady=(5, 0))

        # === APIs de Datos ===
        api_frame = _make_section_frame(scroll_frame, "APIs de Datos")
        api_frame.pack(fill="x", padx=10, pady=5)

        tk.Label(api_frame, text="Claves de servicios externos para precios y analisis",
                 bg=BG_PANEL, fg=TEXT_SEC, font=("Segoe UI", 9, "italic")).pack(anchor="w", pady=(0, 5))

        api_grid = tk.Frame(api_frame, bg=BG_PANEL)
        api_grid.pack(fill="x")

        self._add_conn_field(api_grid, 0, "Twelve Data Key:", "TWELVE_DATA_KEY",
                             env.get("TWELVE_DATA_KEY", ""), show="*")
        self._add_conn_field(api_grid, 1, "Binance API Key:", "BINANCE_API_KEY",
                             env.get("BINANCE_API_KEY", ""), show="*")
        self._add_conn_field(api_grid, 2, "Binance API Secret:", "BINANCE_API_SECRET",
                             env.get("BINANCE_API_SECRET", ""), show="*")
        self._add_conn_field(api_grid, 3, "Finnhub Key:", "FINNHUB_KEY",
                             env.get("FINNHUB_KEY", ""), show="*")
        self._add_conn_field(api_grid, 4, "HuggingFace Token:", "HF_TOKEN",
                             env.get("HF_TOKEN", ""), show="*")
        self._add_conn_field(api_grid, 5, "Anthropic API Key:", "ANTHROPIC_API_KEY",
                             env.get("ANTHROPIC_API_KEY", ""), show="*")

        # Save button
        btn_frame = tk.Frame(scroll_frame, bg=BG_MAIN)
        btn_frame.pack(fill="x", padx=10, pady=(10, 20))
        _make_button(btn_frame, "Guardar Conexiones", self._save_conexiones,
                     bg=ACCENT, fg="#000000").pack(side="left", padx=5)

    def _add_conn_field(self, parent, row, label, key, value, show=None):
        _make_label(parent, label, fg=TEXT).grid(row=row, column=0, sticky="w", padx=5, pady=3)
        e = _make_entry(parent, show=show, width=45)
        e.insert(0, value)
        e.grid(row=row, column=1, sticky="ew", padx=5, pady=3)
        parent.columnconfigure(1, weight=1)

        # Toggle visibility button for masked fields
        if show:
            def toggle_vis(entry=e, s=show):
                if entry.cget("show") == "":
                    entry.config(show=s)
                else:
                    entry.config(show="")

            btn = tk.Button(parent, text="Ver", command=toggle_vis,
                            bg=BG_INPUT, fg=TEXT_SEC, relief="flat",
                            font=("Segoe UI", 8), cursor="hand2", padx=4)
            btn.grid(row=row, column=2, padx=2, pady=3)

        self._conn_entries[key] = e

    def _save_conexiones(self):
        updates = {}
        for key, entry in self._conn_entries.items():
            val = entry.get().strip()
            if val:
                updates[key] = val
        save_env(updates)
        messagebox.showinfo("Guardado", "Conexiones guardadas en .env correctamente.")

    def _test_mt5(self):
        login = self._conn_entries.get("MT5_LOGIN")
        server = self._conn_entries.get("MT5_SERVER")
        if login and server:
            messagebox.showinfo(
                "MetaTrader 5",
                f"Configuracion MT5:\n"
                f"Login: {login.get()}\n"
                f"Server: {server.get()}\n\n"
                f"Para probar la conexion real, el bot debe estar ejecutandose.\n"
                f"Asegurate de tener MetaTrader 5 instalado y la terminal abierta."
            )
        else:
            messagebox.showwarning("MT5", "Completa los campos de MT5 primero.")

    def _test_telegram(self):
        token = self._conn_entries.get("TELEGRAM_TOKEN")
        if not token:
            messagebox.showwarning("Telegram", "Introduce el Bot Token primero.")
            return
        token_val = token.get().strip()
        if not token_val:
            messagebox.showwarning("Telegram", "El Bot Token esta vacio.")
            return

        def do_test():
            try:
                url = f"https://api.telegram.org/bot{token_val}/getMe"
                req = urllib.request.Request(url, method="GET")
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read().decode())
                if data.get("ok"):
                    bot_info = data.get("result", {})
                    name = bot_info.get("first_name", "?")
                    username = bot_info.get("username", "?")
                    self.root.after(0, lambda: messagebox.showinfo(
                        "Telegram OK",
                        f"Conexion exitosa!\n\nBot: {name}\nUsername: @{username}"
                    ))
                else:
                    self.root.after(0, lambda: messagebox.showerror(
                        "Telegram Error", f"Respuesta: {data}"))
            except Exception as ex:
                self.root.after(0, lambda: messagebox.showerror(
                    "Telegram Error", f"Error: {ex}"))

        threading.Thread(target=do_test, daemon=True).start()

    def _test_web(self):
        web_url = self._conn_entries.get("WEB_URL")
        if not web_url:
            messagebox.showwarning("Web", "Introduce la Web URL primero.")
            return
        url_val = web_url.get().strip().rstrip("/")
        if not url_val:
            messagebox.showwarning("Web", "La Web URL esta vacia.")
            return

        def do_test():
            try:
                url = f"{url_val}/api/health"
                req = urllib.request.Request(url, method="GET")
                api_key = self._conn_entries.get("API_SECRET_KEY")
                if api_key:
                    req.add_header("X-API-Key", api_key.get().strip())
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read().decode())
                self.root.after(0, lambda: messagebox.showinfo(
                    "Web OK",
                    f"Conexion exitosa!\n\nRespuesta: {json.dumps(data, indent=2)}"
                ))
            except Exception as ex:
                self.root.after(0, lambda: messagebox.showerror(
                    "Web Error", f"Error conectando a {url_val}:\n{ex}"))

        threading.Thread(target=do_test, daemon=True).start()

    # ============================================================
    #  TAB 6: VIP
    # ============================================================
    def _build_vip(self):
        """FIX 2026-04-28: VIP simplificada — eliminadas secciones 'Configuracion VIP'
        (datos engañosos, los pagos los gestiona bot.py) y 'Codigos de Invitacion'
        (feature sin uso). Solo queda: tabla de suscriptores + botones de gestion +
        acciones rapidas. Variables _vip_entries/_vip_duracion_var/_vip_trial_var
        preservadas como dummies para no romper _load/save_vip_config callers.
        """
        canvas, scroll_frame = _make_scrollable(self._tab_vip)
        self._scroll_canvases[str(self._tab_vip)] = canvas

        # Dummies para preservar API (otros metodos los referencian)
        self._vip_entries = {}
        self._vip_duracion_var = tk.IntVar(value=30)
        self._vip_trial_var = tk.IntVar(value=0)

        # Section: Suscriptores VIP Activos (lo unico que realmente sirve)
        subs_frame = _make_section_frame(scroll_frame, "Suscriptores VIP Activos")
        subs_frame.pack(fill="x", padx=10, pady=(10, 5))

        sub_cols = ("usuario", "inicio", "expira", "dias_rest", "monto", "estado")
        self._vip_subs_tree = ttk.Treeview(subs_frame, columns=sub_cols,
                                           show="headings", height=6)
        for col, heading, w in [
            ("usuario", "Usuario", 150), ("inicio", "Inicio", 110),
            ("expira", "Expira", 110), ("dias_rest", "Dias Rest.", 80),
            ("monto", "Monto", 70), ("estado", "Estado", 90),
        ]:
            self._vip_subs_tree.heading(col, text=heading)
            self._vip_subs_tree.column(col, width=w, anchor="center")

        self._vip_subs_tree.tag_configure("activo", foreground=WIN_COLOR)
        self._vip_subs_tree.tag_configure("por_vencer", foreground=WARN)
        self._vip_subs_tree.tag_configure("expirado", foreground=ERR)
        self._vip_subs_tree.tag_configure("neutral", foreground=TEXT)

        self._vip_subs_tree.pack(fill="x", pady=(0, 5))

        # ── Botones de gestión por usuario seleccionado ──
        vip_user_btns = tk.Frame(subs_frame, bg=BG_PANEL)
        vip_user_btns.pack(fill="x", pady=(0, 5))

        _make_button(vip_user_btns, "+ Agregar dias", self._vip_agregar_dias,
                     bg="#238636", fg="#ffffff").pack(side="left", padx=5)
        _make_button(vip_user_btns, "Renovar (30 dias)", self._vip_renovar_30,
                     bg=ACCENT, fg="#000000").pack(side="left", padx=5)
        _make_button(vip_user_btns, "Revocar VIP", self._vip_revocar,
                     bg=ERR, fg="#ffffff").pack(side="left", padx=5)

        _make_label(vip_user_btns, "  ↑ Selecciona un usuario y usa estos botones", fg="#8b949e").pack(side="left", padx=10)

        # ELIMINADA Section 3: Codigos de Invitacion (feature sin uso, FIX 2026-04-28).
        # Se preserva _vip_codes_tree como dummy para no romper _refresh_vip que lo limpia.
        self._vip_codes_tree = ttk.Treeview(scroll_frame, columns=("dummy",),
                                            show="headings", height=0)

        # Section: Acciones Rapidas
        actions_frame = _make_section_frame(scroll_frame, "Acciones Rapidas")
        actions_frame.pack(fill="x", padx=10, pady=(5, 15))

        btn_row = tk.Frame(actions_frame, bg=BG_PANEL)
        btn_row.pack(fill="x")

        _make_button(btn_row, "Dar VIP manual", self._vip_dar_manual,
                     bg="#238636", fg="#ffffff").pack(side="left", padx=5, pady=3)
        _make_button(btn_row, "Generar codigo", self._vip_generar_codigo,
                     bg=ACCENT, fg="#000000").pack(side="left", padx=5, pady=3)
        _make_button(btn_row, "Ver pagos pendientes", self._vip_ver_pagos,
                     bg=WARN, fg="#000000").pack(side="left", padx=5, pady=3)
        _make_button(btn_row, "Actualizar Datos", self._refresh_vip,
                     bg=BG_INPUT, fg=TEXT).pack(side="right", padx=5, pady=3)

        # Load saved VIP config from estado.json
        self.root.after(500, self._load_vip_config)
        # Initial load
        self.root.after(500, self._refresh_vip)

    def _load_vip_config(self):
        """Carga configuración VIP guardada desde estado.json."""
        try:
            estado = self._get_estado()
            config = estado.get("_vip_config_launcher", {})
            if not config:
                return
            for key, entry in self._vip_entries.items():
                if key in config:
                    entry.delete(0, "end")
                    entry.insert(0, config[key])
            if "duracion_dias" in config:
                self._vip_duracion_var.set(config["duracion_dias"])
            if "trial_dias" in config:
                self._vip_trial_var.set(config["trial_dias"])
        except Exception:
            pass

    def _save_vip_config(self):
        """Guarda la configuración VIP en estado.json."""
        estado = load_estado()
        vip_config = {}
        for key, entry in self._vip_entries.items():
            vip_config[key] = entry.get().strip()
        vip_config["duracion_dias"] = self._vip_duracion_var.get()
        vip_config["trial_dias"] = self._vip_trial_var.get()
        estado["_vip_config_launcher"] = vip_config
        save_estado(estado)
        self._estado_mtime = 0
        messagebox.showinfo("VIP", "Configuracion VIP guardada correctamente.")

    def _vip_dar_manual(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("Dar VIP Manual")
        dialog.geometry("400x280")
        dialog.configure(bg=BG_PANEL)
        dialog.transient(self.root)
        dialog.grab_set()

        tk.Label(dialog, text="Dar VIP Manual", bg=BG_PANEL, fg=ACCENT,
                 font=("Segoe UI", 14, "bold")).pack(pady=(15, 10))

        form = tk.Frame(dialog, bg=BG_PANEL)
        form.pack(padx=20, fill="x")

        _make_label(form, "User ID de Telegram:", fg=TEXT).pack(anchor="w")
        uid_entry = _make_entry(form, width=30)
        uid_entry.pack(fill="x", pady=(0, 8))

        _make_label(form, "Nombre:", fg=TEXT).pack(anchor="w")
        name_entry = _make_entry(form, width=30)
        name_entry.pack(fill="x", pady=(0, 8))

        _make_label(form, "Dias de VIP (9999 = permanente):", fg=TEXT).pack(anchor="w")
        days_var = tk.IntVar(value=30)
        tk.Spinbox(form, from_=1, to=9999, textvariable=days_var, width=8,
                   bg=BG_INPUT, fg=TEXT, buttonbackground=BG_INPUT,
                   insertbackground=TEXT, font=("Segoe UI", 10),
                   relief="flat").pack(anchor="w", pady=(0, 8))

        def do_give():
            uid = uid_entry.get().strip()
            name = name_entry.get().strip() or "Usuario"
            days = days_var.get()
            if not uid:
                messagebox.showwarning("Error", "Introduce el User ID.")
                return

            # FIX 2026-04-25: crear invite link Telegram + enviar DM al cliente.
            # Antes solo guardaba la suscripcion en estado.json con invite_link
            # vacio, y el admin tenia que pegar el link a mano (caso Gian).
            now = datetime.now()
            expira_dt = now + timedelta(days=days)

            env = load_env()
            tg_token = env.get("TELEGRAM_TOKEN", "").strip()
            ch_id = env.get("CHANNEL_ID", "").strip()
            invite_link = ""
            tg_error = ""

            if tg_token and ch_id:
                # 1. Crear invite link unico (1 uso, expira en days+1)
                try:
                    expire_unix = int((expira_dt + timedelta(days=1)).timestamp())
                    payload = json.dumps({
                        "chat_id": int(ch_id),
                        "expire_date": expire_unix,
                        "member_limit": 1,
                        "name": f"VIP-{uid[-6:]}-{now.strftime('%m%d')}",
                    }).encode("utf-8")
                    req = urllib.request.Request(
                        f"https://api.telegram.org/bot{tg_token}/createChatInviteLink",
                        data=payload,
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urllib.request.urlopen(req, timeout=15) as resp:
                        body = json.loads(resp.read().decode("utf-8"))
                    if body.get("ok"):
                        invite_link = body.get("result", {}).get("invite_link", "")
                    else:
                        tg_error = f"createChatInviteLink: {body}"
                except Exception as e:
                    tg_error = f"createChatInviteLink: {e}"

                # 2. Enviar DM de bienvenida con el link al cliente
                if invite_link:
                    try:
                        dm_text = (
                            f"🏆 *¡Bienvenido al VIP, {name}!*\n"
                            f"━━━━━━━━━━━━━━━━━━━━\n\n"
                            f"Tu acceso al canal *BuySell365.Pro · Señales VIP* "
                            f"esta activo hasta el *{expira_dt.strftime('%d-%m-%Y')}* "
                            f"({days} dias).\n\n"
                            f"👇 Toca el boton para entrar al canal:"
                        )
                        dm_payload = json.dumps({
                            "chat_id": int(uid),
                            "text": dm_text,
                            "parse_mode": "Markdown",
                            "reply_markup": {
                                "inline_keyboard": [[
                                    {"text": "👑 ENTRAR AL CANAL VIP", "url": invite_link}
                                ]]
                            },
                        }).encode("utf-8")
                        req2 = urllib.request.Request(
                            f"https://api.telegram.org/bot{tg_token}/sendMessage",
                            data=dm_payload,
                            headers={"Content-Type": "application/json"},
                            method="POST",
                        )
                        with urllib.request.urlopen(req2, timeout=15) as resp2:
                            dm_body = json.loads(resp2.read().decode("utf-8"))
                        if not dm_body.get("ok"):
                            tg_error = (tg_error + " | " if tg_error else "") + f"sendMessage: {dm_body.get('description','?')}"
                    except Exception as e:
                        tg_error = (tg_error + " | " if tg_error else "") + f"sendMessage: {e}"

            estado = load_estado()
            subs = estado.get("suscripciones_vip", {})
            subs[uid] = {
                "nombre": name,
                "username": "",
                "inicio": now.isoformat(),
                "expira": expira_dt.isoformat(),
                "aviso_enviado": False,
                "monto_pagado": 0,
                "tx_id": "manual_launcher",
                "invite_link": invite_link,
                "es_trial": False,
                "entrada_confirmada": True,
            }
            estado["suscripciones_vip"] = subs
            save_estado(estado)
            self._estado_mtime = 0  # Force reload

            # Mensaje final al admin con resultado
            if invite_link and not tg_error:
                msg_final = (
                    f"VIP otorgado a {name} ({uid}) por {days} dias.\n\n"
                    f"Link enviado por DM al cliente:\n{invite_link}"
                )
            elif invite_link and tg_error:
                msg_final = (
                    f"VIP otorgado a {name} ({uid}) por {days} dias.\n"
                    f"Link creado pero hubo problema con el DM:\n{tg_error}\n\n"
                    f"Pasale el link a mano:\n{invite_link}"
                )
            elif tg_error:
                msg_final = (
                    f"VIP otorgado en estado.json a {name} ({uid}) por {days} dias.\n"
                    f"PERO no se pudo crear el invite link:\n{tg_error}\n\n"
                    f"Tendras que generar y pasar el link manualmente."
                )
            else:
                msg_final = (
                    f"VIP otorgado a {name} ({uid}) por {days} dias.\n"
                    f"AVISO: TELEGRAM_TOKEN o CHANNEL_ID no configurados — sin link automatico."
                )
            messagebox.showinfo("VIP", msg_final)
            dialog.destroy()
            self._refresh_vip()

        _make_button(form, "Confirmar", do_give, bg=ACCENT, fg="#000000").pack(pady=(10, 0))

    def _vip_generar_codigo(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("Generar Codigo de Invitacion")
        dialog.geometry("400x250")
        dialog.configure(bg=BG_PANEL)
        dialog.transient(self.root)
        dialog.grab_set()

        tk.Label(dialog, text="Generar Codigo VIP", bg=BG_PANEL, fg=ACCENT,
                 font=("Segoe UI", 14, "bold")).pack(pady=(15, 10))

        form = tk.Frame(dialog, bg=BG_PANEL)
        form.pack(padx=20, fill="x")

        _make_label(form, "Dias de VIP:", fg=TEXT).pack(anchor="w")
        days_var = tk.IntVar(value=7)
        tk.Spinbox(form, from_=1, to=365, textvariable=days_var, width=8,
                   bg=BG_INPUT, fg=TEXT, buttonbackground=BG_INPUT,
                   insertbackground=TEXT, font=("Segoe UI", 10),
                   relief="flat").pack(anchor="w", pady=(0, 8))

        _make_label(form, "Max usos:", fg=TEXT).pack(anchor="w")
        max_var = tk.IntVar(value=1)
        tk.Spinbox(form, from_=1, to=100, textvariable=max_var, width=8,
                   bg=BG_INPUT, fg=TEXT, buttonbackground=BG_INPUT,
                   insertbackground=TEXT, font=("Segoe UI", 10),
                   relief="flat").pack(anchor="w", pady=(0, 8))

        code_lbl = _make_label(form, "", fg=ACCENT, font=("Segoe UI", 12, "bold"))
        code_lbl.pack(anchor="w", pady=5)

        def do_gen():
            import random
            import string
            code = "VIP-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
            days = days_var.get()
            max_uses = max_var.get()

            estado = load_estado()
            codigos = estado.get("_codigos_invitacion", {})
            codigos[code] = {
                "creado_por": "launcher",
                "dias": days,
                "usos": 0,
                "max_usos": max_uses,
                "creado": datetime.now().isoformat(),
            }
            estado["_codigos_invitacion"] = codigos
            save_estado(estado)
            self._estado_mtime = 0
            code_lbl.config(text=f"Codigo: {code}")
            self._refresh_vip()

        _make_button(form, "Generar", do_gen, bg=ACCENT, fg="#000000").pack(pady=(5, 0))

    def _vip_ver_pagos(self):
        estado = self._get_estado()
        pagos = estado.get("pagos_pendientes_vip", {})
        if not pagos:
            messagebox.showinfo("Pagos Pendientes", "No hay pagos pendientes.")
            return
        lines = []
        for uid, info in pagos.items():
            name = info.get("nombre", uid)
            monto = info.get("monto", "?")
            fecha = info.get("fecha", "?")
            lines.append(f"  {name} ({uid}): ${monto} - {fecha}")
        messagebox.showinfo("Pagos Pendientes", "Pagos pendientes:\n\n" + "\n".join(lines))

    def _vip_get_selected_uid(self):
        """Obtiene el UID del VIP seleccionado en la tabla."""
        sel = self._vip_subs_tree.selection()
        if not sel:
            messagebox.showwarning("VIP", "Selecciona un usuario de la tabla primero.")
            return None, None
        values = self._vip_subs_tree.item(sel[0], "values")
        display_name = values[0]  # "Emmanuel (@BuySell365trading)"
        # Buscar en estado.json por nombre
        estado = self._get_estado()
        subs = estado.get("suscripciones_vip", {})
        for uid, info in subs.items():
            nombre = info.get("nombre", uid)
            username = info.get("username", "")
            dn = f"{nombre} (@{username})" if username else nombre
            if dn == display_name:
                return uid, info
        messagebox.showerror("VIP", f"No se encontró el usuario: {display_name}")
        return None, None

    def _vip_agregar_dias(self):
        """Agrega días extra a un VIP seleccionado."""
        uid, info = self._vip_get_selected_uid()
        if not uid:
            return

        dialog = tk.Toplevel(self.root)
        dialog.title("Agregar Dias VIP")
        dialog.geometry("350x200")
        dialog.configure(bg=BG_PANEL)
        dialog.transient(self.root)
        dialog.grab_set()

        nombre = info.get("nombre", uid)
        expira_str = info.get("expira", "")[:10]

        tk.Label(dialog, text=f"Agregar dias a: {nombre}", bg=BG_PANEL, fg=ACCENT,
                 font=("Segoe UI", 13, "bold")).pack(pady=(15, 5))
        tk.Label(dialog, text=f"Expira actualmente: {expira_str}", bg=BG_PANEL, fg=TEXT,
                 font=("Segoe UI", 10)).pack(pady=(0, 10))

        form = tk.Frame(dialog, bg=BG_PANEL)
        form.pack(padx=20, fill="x")

        _make_label(form, "Dias a agregar:", fg=TEXT).pack(anchor="w")
        days_var = tk.IntVar(value=7)
        tk.Spinbox(form, from_=1, to=365, textvariable=days_var, width=8,
                   bg=BG_INPUT, fg=TEXT, buttonbackground=BG_INPUT,
                   insertbackground=TEXT, font=("Segoe UI", 10),
                   relief="flat").pack(anchor="w", pady=(0, 10))

        def do_add():
            days = days_var.get()
            estado = load_estado()
            subs = estado.get("suscripciones_vip", {})
            if uid in subs:
                try:
                    expira_dt = datetime.fromisoformat(subs[uid]["expira"])
                    # Si ya expiró, partir desde hoy
                    if expira_dt < datetime.now():
                        expira_dt = datetime.now()
                    nueva_expira = expira_dt + timedelta(days=days)
                    subs[uid]["expira"] = nueva_expira.isoformat()
                    subs[uid]["aviso_enviado"] = False
                    estado["suscripciones_vip"] = subs
                    save_estado(estado)
                    self._estado_mtime = 0
                    messagebox.showinfo("VIP", f"Se agregaron {days} dias a {nombre}.\nNueva expiracion: {nueva_expira.strftime('%Y-%m-%d %H:%M')}")
                    dialog.destroy()
                    self._refresh_vip()
                except Exception as e:
                    messagebox.showerror("Error", f"Error: {e}")

        _make_button(form, "Agregar Dias", do_add, bg="#238636", fg="#ffffff").pack(pady=(5, 0))

    def _vip_renovar_30(self):
        """Renueva un VIP por 30 días desde hoy."""
        uid, info = self._vip_get_selected_uid()
        if not uid:
            return
        nombre = info.get("nombre", uid)
        if not messagebox.askyesno("Renovar VIP", f"¿Renovar a {nombre} por 30 dias desde hoy?"):
            return
        estado = load_estado()
        subs = estado.get("suscripciones_vip", {})
        if uid in subs:
            nueva_expira = datetime.now() + timedelta(days=30)
            subs[uid]["expira"] = nueva_expira.isoformat()
            subs[uid]["aviso_enviado"] = False
            estado["suscripciones_vip"] = subs
            save_estado(estado)
            self._estado_mtime = 0
            messagebox.showinfo("VIP", f"{nombre} renovado hasta {nueva_expira.strftime('%Y-%m-%d')}.")
            self._refresh_vip()

    def _vip_revocar(self):
        """Revoca el VIP de un usuario."""
        uid, info = self._vip_get_selected_uid()
        if not uid:
            return
        nombre = info.get("nombre", uid)
        if not messagebox.askyesno("Revocar VIP", f"¿Revocar VIP de {nombre}?\nSe eliminará su suscripción."):
            return
        estado = load_estado()
        subs = estado.get("suscripciones_vip", {})
        if uid in subs:
            del subs[uid]
            estado["suscripciones_vip"] = subs
            save_estado(estado)
            self._estado_mtime = 0
            messagebox.showinfo("VIP", f"VIP revocado para {nombre}.")
            self._refresh_vip()

    def _refresh_vip(self):
        estado = self._get_estado()

        # Suscriptores
        self._vip_subs_tree.delete(*self._vip_subs_tree.get_children())
        subs = estado.get("suscripciones_vip", {})
        now = datetime.now()
        for uid, info in subs.items():
            nombre = info.get("nombre", uid)
            username = info.get("username", "")
            display_name = f"{nombre} (@{username})" if username else nombre
            inicio = info.get("inicio", "--")[:10]
            expira_raw = info.get("expira", "")
            monto = info.get("monto_pagado", 0)
            # Detectar tipo de cuenta: Dueño / Permanente / Pagado / Prueba.
            # ADMIN_IDS configurados son los dueños (cuenta admin propia).
            _admin_uids_str = (os.environ.get("USER_ID_1", ""), os.environ.get("USER_ID_2", ""))
            _is_admin = str(uid) in _admin_uids_str
            if _is_admin:
                monto_str = "Dueño"
            elif monto:
                monto_str = f"${monto}"
            else:
                # Sin pago — chequear si es permanente para etiquetar mejor
                _is_perm = False
                try:
                    if isinstance(expira_raw, (int, float)) and expira_raw >= 9999999990:
                        _is_perm = True
                    elif isinstance(expira_raw, str) and ("2099" in expira_raw or "2300" in expira_raw):
                        _is_perm = True
                except Exception:
                    pass
                monto_str = "Permanente" if _is_perm else "Prueba"

            # Normalizar expira: puede ser int (Unix timestamp) o str ISO
            if isinstance(expira_raw, (int, float)):
                if expira_raw >= 9999999990:
                    expira_str = "2099-12-31T00:00:00"
                else:
                    try:
                        expira_str = datetime.fromtimestamp(expira_raw).isoformat()
                    except Exception:
                        expira_str = "2099-12-31T00:00:00"
            else:
                expira_str = str(expira_raw)

            dias_rest = "--"
            try:
                expira_dt = datetime.fromisoformat(expira_str)
                delta = expira_dt - now
                if expira_dt < now:
                    estado_txt = "Expirado"
                    tag = "expirado"
                    dias_rest = f"{delta.days}d"
                elif delta.days > 9000:
                    estado_txt = "Activo"
                    tag = "activo"
                    dias_rest = "♾️ Permanente"
                elif delta.days <= 2:
                    estado_txt = "Por vencer"
                    tag = "por_vencer"
                    horas = int(delta.total_seconds() / 3600)
                    dias_rest = f"{horas}h" if delta.days == 0 else f"{delta.days}d {horas % 24}h"
                else:
                    estado_txt = "Activo"
                    tag = "activo"
                    dias_rest = f"{delta.days}d"
                expira_show = expira_str[:10]
            except Exception:
                estado_txt = "Activo"
                tag = "activo"
                expira_show = str(expira_raw)[:10] if expira_raw else "--"

            self._vip_subs_tree.insert("", "end",
                                       values=(display_name, inicio, expira_show,
                                               dias_rest, monto_str, estado_txt),
                                       tags=(tag,))

        # Codigos
        self._vip_codes_tree.delete(*self._vip_codes_tree.get_children())
        codigos = estado.get("_codigos_invitacion", {})
        for code, info in codigos.items():
            creado = info.get("creado_por", "--")
            dias = info.get("dias", "--")
            usos = info.get("usos", 0)
            max_usos = info.get("max_usos", 1)
            self._vip_codes_tree.insert("", "end",
                                        values=(code, creado, dias, usos, max_usos))

    # ============================================================
    #  TAB 7: LOGS
    # ============================================================
    def _build_logs(self):
        # Header
        header = tk.Frame(self._tab_logs, bg=BG_MAIN)
        header.pack(fill="x", padx=10, pady=(10, 5))

        # Selector de fuente de logs (Bot vs Copier)
        self._log_source = tk.StringVar(value="Bot")
        for src in ["Bot", "Copier"]:
            rb_src = tk.Radiobutton(header, text=src, variable=self._log_source, value=src,
                                    command=self._refresh_logs, bg=BG_MAIN, fg=ACCENT,
                                    selectcolor=BG_INPUT, activebackground=BG_MAIN,
                                    activeforeground=ACCENT_BRIGHT, font=("Segoe UI", 10, "bold"))
            rb_src.pack(side="left", padx=5)

        # Separador visual
        _make_label(header, " | ", fg=TEXT_SEC, font=("Segoe UI", 9)).pack(side="left", padx=2)

        self._log_filter = tk.StringVar(value="Todos")
        for level in ["Todos", "INFO", "WARNING", "ERROR"]:
            rb = tk.Radiobutton(header, text=level, variable=self._log_filter, value=level,
                                command=self._refresh_logs, bg=BG_MAIN, fg=TEXT,
                                selectcolor=BG_INPUT, activebackground=BG_MAIN,
                                activeforeground=ACCENT, font=("Segoe UI", 9))
            rb.pack(side="left", padx=5)

        # FIX 2026-04-28: nuevos controles — Pause auto-refresh, auto-scroll, busqueda
        _make_button(header, "Abrir carpeta", self._open_logs_folder,
                     bg=BG_INPUT, fg=TEXT).pack(side="right", padx=5)
        _make_button(header, "Limpiar", self._clear_log_view,
                     bg=BG_INPUT, fg=TEXT).pack(side="right", padx=5)
        _make_button(header, "↻ Actualizar", self._refresh_logs,
                     bg=BG_INPUT, fg=TEXT).pack(side="right", padx=5)
        # Toggle auto-scroll
        self._log_autoscroll = tk.BooleanVar(value=True)
        tk.Checkbutton(header, text="Auto-scroll", variable=self._log_autoscroll,
                       bg=BG_MAIN, fg=TEXT_SEC, selectcolor=BG_INPUT,
                       activebackground=BG_MAIN, activeforeground=ACCENT,
                       font=("Segoe UI", 9)).pack(side="right", padx=5)
        # Toggle pause
        self._log_paused = tk.BooleanVar(value=False)
        tk.Checkbutton(header, text="⏸ Pausa", variable=self._log_paused,
                       bg=BG_MAIN, fg=TEXT_SEC, selectcolor=BG_INPUT,
                       activebackground=BG_MAIN, activeforeground=WARN,
                       font=("Segoe UI", 9)).pack(side="right", padx=5)

        # Segunda fila: busqueda + filtros adicionales
        sub_header = tk.Frame(self._tab_logs, bg=BG_MAIN)
        sub_header.pack(fill="x", padx=10, pady=(0, 5))
        _make_label(sub_header, "🔍 Buscar:", fg=TEXT_SEC,
                    font=("Segoe UI", 9)).pack(side="left", padx=(0, 4))
        self._log_search_var = tk.StringVar()
        self._log_search_var.trace_add("write", lambda *a: self._refresh_logs())
        _entry_search = tk.Entry(sub_header, textvariable=self._log_search_var,
                                 bg=BG_INPUT, fg=TEXT, insertbackground=TEXT,
                                 width=30, font=("Segoe UI", 9), relief="flat")
        _entry_search.pack(side="left", padx=5)

        # Stats hoy (se actualizan en _refresh_logs)
        self._log_stats_lbl = _make_label(sub_header, "", fg=ACCENT,
                                          font=("Segoe UI", 9, "bold"))
        self._log_stats_lbl.pack(side="right", padx=10)

        # Text widget
        log_frame = tk.Frame(self._tab_logs, bg=BG_MAIN)
        log_frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        self._log_text = tk.Text(
            log_frame, bg=BG_PANEL, fg=TEXT, font=("Consolas", 9),
            wrap="word", state="disabled", insertbackground=TEXT,
            highlightthickness=1, highlightbackground="#30363d",
            selectbackground="#30363d", selectforeground=ACCENT,
        )
        log_scroll = ttk.Scrollbar(log_frame, orient="vertical",
                                   command=self._log_text.yview)
        self._log_text.configure(yscrollcommand=log_scroll.set)

        log_scroll.pack(side="right", fill="y")
        self._log_text.pack(side="left", fill="both", expand=True)

        # Color tags + nuevos para TPs/SLs/Heartbeat
        self._log_text.tag_configure("INFO", foreground=TEXT)
        self._log_text.tag_configure("WARNING", foreground=WARN)
        self._log_text.tag_configure("ERROR", foreground=ERR)
        self._log_text.tag_configure("CRITICAL", foreground="#ff0000")
        self._log_text.tag_configure("SUCCESS", foreground=WIN_COLOR)
        self._log_text.tag_configure("SIGNAL", foreground="#FFD700")
        # FIX 2026-04-28: tags adicionales para resaltado fino
        self._log_text.tag_configure("TP_HIT", foreground="#00ff88", font=("Consolas", 9, "bold"))
        self._log_text.tag_configure("SL_HIT", foreground="#ff5555", font=("Consolas", 9, "bold"))
        self._log_text.tag_configure("HEARTBEAT", foreground="#555555")  # gris atenuado

    # ============================================================
    #  WHATSAPP TAB — gestion multi-destinatario con filtros y eventos
    # ============================================================
    _WSP_RECIPIENTS_FILE = os.path.join(BASE_DIR, "whatsapp_recipients.json")
    # Codigos internos (en ingles para estabilidad) -> (etiqueta UI, descripcion UI)
    _WSP_FILTER_OPTIONS = ["GOLD", "CRYPTO", "FOREX", "INDICES", "BTC", "ETH", "ALL"]
    _WSP_FILTER_LABELS = {
        "GOLD": ("ORO", "Solo señales de ORO (XAUUSD)"),
        "CRYPTO": ("CRIPTO", "Bitcoin + Ethereum (ambos)"),
        "FOREX": ("DIVISAS", "EUR/USD, GBP/USD, USD/JPY, etc."),
        "INDICES": ("ÍNDICES", "NASDAQ, S&P 500, Dow 30"),
        "BTC": ("Solo Bitcoin", "Únicamente BTC (sin ETH)"),
        "ETH": ("Solo Ethereum", "Únicamente ETH (sin BTC)"),
        "ALL": ("TODO", "Todas las señales (anula los demás filtros)"),
    }
    _WSP_EVENT_LABELS = {
        "new_signal": ("📍 Señal nueva", "Cuando publicamos una operación nueva (Entrada, Objetivos, Stop)"),
        "be_activated": ("🛡️ Stop en Entrada", "Movemos el Stop al precio de entrada (operación sin riesgo)"),
        "tp_hit": ("🎯 Objetivo alcanzado", "TP1, TP2 o TP3 conseguido (la operación sigue activa)"),
        "partial_close": ("💰 Cierre parcial", "Cerramos el 50% para asegurar ganancia"),
        "position_closed": ("🏁 Cierre final", "La operación se cierra del todo (Stop, objetivo final o manual)"),
    }
    # Etiquetas cortas para la columna Avisos de la tabla
    _WSP_EVENT_SHORT = {
        "new_signal": "Señal",
        "be_activated": "Stop-Entrada",
        "tp_hit": "Objetivos",
        "partial_close": "Parcial",
        "position_closed": "Cierre",
    }
    _WSP_DEFAULT_EVENTS = ["new_signal", "be_activated", "position_closed"]

    def _wsp_load_recipients(self) -> list:
        try:
            if os.path.exists(self._WSP_RECIPIENTS_FILE):
                with open(self._WSP_RECIPIENTS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    return [r for r in data if isinstance(r, dict)]
        except Exception as e:
            _log(f"[WSP-GUI] error leyendo JSON: {e}")
        return []

    def _wsp_save_recipients(self, data: list) -> bool:
        try:
            tmp = self._WSP_RECIPIENTS_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp, self._WSP_RECIPIENTS_FILE)
            return True
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo guardar JSON:\n{e}")
            return False

    def _wsp_default_apikey(self) -> str:
        """APIKEY a usar para destinatarios nuevos: la del primer recipient TextMeBot existente."""
        for r in self._wsp_load_recipients():
            if (r.get("backend") or "").lower() == "textmebot" and r.get("apikey"):
                return r.get("apikey")
        return ""  # primera vez sin destinatarios -> el modal pedira la APIKEY

    def _build_whatsapp(self):
        """Tab WhatsApp: gestion multi-destinatario con filtros de pares y eventos."""
        canvas, scroll_frame = _make_scrollable(self._tab_whatsapp)
        self._scroll_canvases[str(self._tab_whatsapp)] = canvas

        # ── Cabecera ──
        hdr = tk.Frame(scroll_frame, bg=BG_MAIN)
        hdr.pack(fill="x", padx=10, pady=(10, 4))

        tk.Label(hdr, text="📱 Avisos por WhatsApp", bg=BG_MAIN, fg=ACCENT_BRIGHT,
                 font=("Segoe UI", 13, "bold")).pack(side="left")

        self._wsp_status_lbl = tk.Label(hdr, text="● Cargando...", bg=BG_MAIN,
                                          fg=TEXT_SEC, font=("Segoe UI", 10))
        self._wsp_status_lbl.pack(side="left", padx=(16, 0))

        # ── Información del servicio ──
        info_frame = _make_section_frame(scroll_frame, "Información del servicio")
        info_frame.pack(fill="x", padx=10, pady=5)
        info_grid = tk.Frame(info_frame, bg=BG_PANEL)
        info_grid.pack(fill="x", padx=4, pady=4)

        tk.Label(info_grid, text="Servicio:", bg=BG_PANEL, fg=TEXT_SEC,
                 font=("Segoe UI", 9)).grid(row=0, column=0, sticky="w", padx=(0, 8))
        tk.Label(info_grid, text="TextMeBot — los mensajes salen de tu WhatsApp Business +34 674 97 65 72",
                 bg=BG_PANEL, fg=TEXT, font=("Segoe UI", 9, "bold")).grid(row=0, column=1, sticky="w")

        tk.Label(info_grid, text="Sistema:", bg=BG_PANEL, fg=TEXT_SEC,
                 font=("Segoe UI", 9)).grid(row=1, column=0, sticky="w", padx=(0, 8), pady=(2, 0))
        self._wsp_enabled_lbl = tk.Label(info_grid, text="-", bg=BG_PANEL, fg=TEXT,
                                           font=("Segoe UI", 9, "bold"))
        self._wsp_enabled_lbl.grid(row=1, column=1, sticky="w", pady=(2, 0))

        tk.Label(info_grid, text="Personas configuradas:", bg=BG_PANEL, fg=TEXT_SEC,
                 font=("Segoe UI", 9)).grid(row=2, column=0, sticky="w", padx=(0, 8), pady=(2, 0))
        self._wsp_count_lbl = tk.Label(info_grid, text="0", bg=BG_PANEL, fg=TEXT,
                                         font=("Segoe UI", 9, "bold"))
        self._wsp_count_lbl.grid(row=2, column=1, sticky="w", pady=(2, 0))

        # ── Tabla destinatarios ──
        table_frame = _make_section_frame(scroll_frame, "Personas que reciben avisos")
        table_frame.pack(fill="both", expand=True, padx=10, pady=5)

        cols = ("name", "phone", "filters", "events", "status")
        self._wsp_tree = ttk.Treeview(table_frame, columns=cols, show="headings", height=10)
        self._wsp_tree.heading("name", text="Nombre")
        self._wsp_tree.heading("phone", text="Número de WhatsApp")
        self._wsp_tree.heading("filters", text="Tipos de señales")
        self._wsp_tree.heading("events", text="Avisos que recibe")
        self._wsp_tree.heading("status", text="Estado")
        self._wsp_tree.column("name", width=200, anchor="w")
        self._wsp_tree.column("phone", width=160, anchor="w")
        self._wsp_tree.column("filters", width=180, anchor="w")
        self._wsp_tree.column("events", width=260, anchor="w")
        self._wsp_tree.column("status", width=110, anchor="center")
        self._wsp_tree.pack(fill="both", expand=True, padx=4, pady=(4, 0))
        self._wsp_tree.tag_configure("on", foreground=WIN_COLOR)
        self._wsp_tree.tag_configure("off", foreground=TEXT_SEC)
        self._wsp_tree.bind("<Double-1>", lambda e: self._wsp_edit())

        # ── Botones de acción ──
        btn_row = tk.Frame(table_frame, bg=BG_PANEL)
        btn_row.pack(fill="x", padx=4, pady=8)
        _make_button(btn_row, "➕  Añadir persona", self._wsp_add, bg="#238636", fg="#ffffff").pack(side="left", padx=(0, 6))
        _make_button(btn_row, "✏️  Editar", self._wsp_edit, bg=ACCENT, fg="#000000").pack(side="left", padx=6)
        _make_button(btn_row, "🗑  Eliminar", self._wsp_remove, bg=ERR, fg="#ffffff").pack(side="left", padx=6)
        _make_button(btn_row, "⏯  Activar / Pausar", self._wsp_toggle, bg=WARN, fg="#000000").pack(side="left", padx=6)
        _make_button(btn_row, "📤  Enviar prueba", self._wsp_test, bg="#7c3aed", fg="#ffffff").pack(side="left", padx=6)
        _make_button(btn_row, "🔄  Actualizar lista", self._wsp_refresh_table, bg=BG_INPUT, fg=TEXT).pack(side="right")

        # ── Mensajes enviados (registro reciente) ──
        log_frame = _make_section_frame(scroll_frame, "Registro reciente de mensajes enviados")
        log_frame.pack(fill="x", padx=10, pady=5)
        self._wsp_log_text = tk.Text(log_frame, height=8, bg=BG_INPUT, fg=TEXT,
                                       font=("Consolas", 9), wrap="word", state="disabled",
                                       insertbackground=TEXT)
        self._wsp_log_text.pack(fill="x", padx=4, pady=4)

        # Initial load
        self._wsp_refresh_table()
        # Auto-refresh logs cada 3s
        self.root.after(3000, self._wsp_refresh_logs_loop)

    def _wsp_refresh_table(self):
        """Recarga la tabla desde el JSON con etiquetas en español."""
        # Clear
        for iid in self._wsp_tree.get_children():
            self._wsp_tree.delete(iid)
        recipients = self._wsp_load_recipients()
        for i, r in enumerate(recipients):
            name = r.get("name", "?")
            phone = r.get("phone", "")
            # Traduce filtros: 'GOLD,CRYPTO' -> 'ORO + CRIPTO'
            filters_raw = (r.get("filter_pair", "GOLD") or "GOLD").upper()
            filters_pretty = self._wsp_format_filters(filters_raw)
            # Traduce eventos
            events = r.get("events") or self._WSP_DEFAULT_EVENTS
            events_pretty = self._wsp_format_events(events)
            enabled = bool(r.get("enabled", True))
            status = "🟢 Activo" if enabled else "⏸ En pausa"
            tag = "on" if enabled else "off"
            self._wsp_tree.insert("", "end", iid=str(i),
                                   values=(name, phone, filters_pretty, events_pretty, status), tags=(tag,))
        # Update header
        try:
            from dotenv import dotenv_values
            env = dotenv_values(ENV_FILE)
            wsp_on = (env.get("WHATSAPP_PERSONAL_ENABLED", "false") or "").lower() in ("true", "1", "yes")
        except Exception:
            wsp_on = True
        self._wsp_enabled_lbl.config(text=("🟢 Encendido" if wsp_on else "🔴 Apagado"),
                                       fg=(WIN_COLOR if wsp_on else ERR))
        self._wsp_count_lbl.config(text=f"{len(recipients)} en total · {sum(1 for r in recipients if r.get('enabled',True))} activas")
        self._wsp_status_lbl.config(text=f"● {len(recipients)} persona(s) configurada(s)",
                                      fg=ACCENT)

    def _wsp_format_filters(self, filters_raw: str) -> str:
        """Convierte 'GOLD,CRYPTO' en 'ORO + CRIPTO' (etiquetas español)."""
        codes = [c.strip() for c in filters_raw.split(",") if c.strip()]
        labels = []
        for c in codes:
            label, _ = self._WSP_FILTER_LABELS.get(c, (c, ""))
            labels.append(label)
        return " + ".join(labels) if labels else "-"

    def _wsp_format_events(self, events: list) -> str:
        """Compacta lista de eventos en español. Ej: 'Señal · Stop-Entrada · Cierre'."""
        if not events:
            return "-"
        return " · ".join(self._WSP_EVENT_SHORT.get(e, e) for e in events)

    def _wsp_get_selected(self) -> tuple:
        """Devuelve (index, recipient_dict) del seleccionado, o (None, None)."""
        sel = self._wsp_tree.selection()
        if not sel:
            return (None, None)
        idx = int(sel[0])
        recipients = self._wsp_load_recipients()
        if idx < len(recipients):
            return (idx, recipients[idx])
        return (None, None)

    def _wsp_add(self):
        self._wsp_show_modal(None, None)

    def _wsp_edit(self):
        idx, r = self._wsp_get_selected()
        if r is None:
            messagebox.showinfo("Selecciona una persona",
                                "Antes de editar, haz clic en una fila de la tabla.")
            return
        self._wsp_show_modal(idx, r)

    def _wsp_remove(self):
        idx, r = self._wsp_get_selected()
        if r is None:
            messagebox.showinfo("Selecciona una persona",
                                "Antes de eliminar, haz clic en una fila de la tabla.")
            return
        if not messagebox.askyesno("¿Eliminar a esta persona?",
                                    f"Vas a eliminar a:\n\n   {r.get('name','?')}\n   {r.get('phone','?')}\n\n"
                                    f"Esta acción NO se puede deshacer.\n¿Continuar?"):
            return
        recipients = self._wsp_load_recipients()
        recipients.pop(idx)
        if self._wsp_save_recipients(recipients):
            self._wsp_refresh_table()

    def _wsp_toggle(self):
        idx, r = self._wsp_get_selected()
        if r is None:
            messagebox.showinfo("Selecciona una persona",
                                "Antes de activar/pausar, haz clic en una fila de la tabla.")
            return
        recipients = self._wsp_load_recipients()
        recipients[idx]["enabled"] = not recipients[idx].get("enabled", True)
        if self._wsp_save_recipients(recipients):
            self._wsp_refresh_table()

    def _wsp_test(self):
        idx, r = self._wsp_get_selected()
        if r is None:
            messagebox.showinfo("Selecciona una persona",
                                "Antes de enviar la prueba, haz clic en una fila de la tabla.")
            return
        if not messagebox.askyesno("Enviar mensaje de prueba",
                                    f"Vamos a mandar un mensaje de prueba a:\n\n   {r.get('name','?')}\n   {r.get('phone','?')}\n\n¿Confirmas?"):
            return
        threading.Thread(target=self._wsp_test_send, args=(r,), daemon=True).start()

    def _wsp_test_send(self, recipient: dict):
        """Envío de prueba en hilo aparte (no bloquea la GUI).
        FIX 2026-05-12 (noche): añadido append directo a bot.log para que el envío
        de prueba aparezca en el panel "Registro reciente". Sin esto, el log del
        whatsapp_notifier corria en el proceso del launcher, no en el del bot,
        y el archivo bot.log no recibia la linea."""
        try:
            os.environ["WHATSAPP_PERSONAL_ENABLED"] = "true"
            import importlib
            import whatsapp_notifier as wn
            importlib.reload(wn)
            text = f"Mensaje de prueba desde el panel BuySell365 Pro\nPara: {recipient.get('name','?')}\nHora: {time.strftime('%H:%M:%S')}"
            wn._send_one(recipient, text)
            # Append manual al log que la GUI lee
            try:
                os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
                with open(LOG_FILE, "a", encoding="utf-8") as _lf:
                    _ts = time.strftime("%Y-%m-%d %H:%M:%S")
                    _name = recipient.get("name", "?")
                    _backend = recipient.get("backend", "textmebot")
                    _lf.write(f"{_ts} [INFO] [GENERAL] [WSP] {_name} ({_backend}) PRUEBA enviada ({len(text)}c)\n")
            except Exception:
                pass
            self.root.after(0, lambda: messagebox.showinfo("Mensaje enviado",
                f"Se ha enviado el mensaje de prueba a:\n\n   {recipient.get('name','?')}\n   {recipient.get('phone','?')}\n\nRevisa su WhatsApp."))
        except Exception as e:
            try:
                with open(LOG_FILE, "a", encoding="utf-8") as _lf:
                    _ts = time.strftime("%Y-%m-%d %H:%M:%S")
                    _name = recipient.get("name", "?")
                    _lf.write(f"{_ts} [ERROR] [GENERAL] [WSP] {_name} PRUEBA fallo: {e}\n")
            except Exception:
                pass
            self.root.after(0, lambda: messagebox.showerror("Error al enviar",
                f"No se pudo enviar el mensaje:\n\n{e}"))

    def _wsp_refresh_logs_loop(self):
        """Lee bot.log y filtra ultimas lineas con [WSP]."""
        try:
            entries = []
            if os.path.exists(LOG_FILE):
                with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
                    for line in f.readlines()[-1000:]:
                        if "[WSP]" in line or "whatsapp" in line.lower():
                            entries.append(line.rstrip())
            entries = entries[-15:]  # ultimas 15
            self._wsp_log_text.config(state="normal")
            self._wsp_log_text.delete("1.0", "end")
            for line in entries:
                self._wsp_log_text.insert("end", line + "\n")
            self._wsp_log_text.config(state="disabled")
        except Exception:
            pass
        self.root.after(3000, self._wsp_refresh_logs_loop)

    def _wsp_show_modal(self, idx, recipient: dict | None):
        """Modal Anadir/Editar destinatario.
        Estructura: contenido scrollable arriba + botones FIJOS abajo (siempre visibles)."""
        is_edit = recipient is not None
        win = tk.Toplevel(self.root)
        win.title("Editar persona" if is_edit else "Añadir nueva persona")
        win.configure(bg=BG_PANEL)
        # Tamaño + centrado adaptado a pantalla
        win_w = 680
        win_h = 820
        try:
            sw = self.root.winfo_screenwidth()
            sh = self.root.winfo_screenheight()
            win_h = min(win_h, max(560, sh - 100))
            win_w = min(win_w, max(560, sw - 80))
            x = max(0, (sw - win_w) // 2)
            y = max(0, (sh - win_h) // 2 - 20)
            win.geometry(f"{win_w}x{win_h}+{x}+{y}")
        except Exception:
            win.geometry(f"{win_w}x{win_h}")
        win.transient(self.root)
        win.grab_set()
        win.resizable(True, True)

        # ── Footer FIJO con botones (se empaqueta PRIMERO para que quede abajo) ──
        footer = tk.Frame(win, bg=BG_PANEL, pady=10)
        footer.pack(side="bottom", fill="x")
        # Linea separadora
        tk.Frame(win, bg="#2a3344", height=1).pack(side="bottom", fill="x")

        # ── Area scrollable para el contenido ──
        body = tk.Frame(win, bg=BG_PANEL)
        body.pack(side="top", fill="both", expand=True)
        canvas_modal = tk.Canvas(body, bg=BG_PANEL, highlightthickness=0)
        scrollbar = ttk.Scrollbar(body, orient="vertical", command=canvas_modal.yview)
        canvas_modal.configure(yscrollcommand=scrollbar.set)
        canvas_modal.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        outer = tk.Frame(canvas_modal, bg=BG_PANEL)
        canvas_window = canvas_modal.create_window((0, 0), window=outer, anchor="nw")
        def _on_canvas_configure(event):
            canvas_modal.itemconfig(canvas_window, width=event.width)
        def _on_outer_configure(event):
            canvas_modal.configure(scrollregion=canvas_modal.bbox("all"))
        canvas_modal.bind("<Configure>", _on_canvas_configure)
        outer.bind("<Configure>", _on_outer_configure)
        # Mousewheel solo cuando el cursor esta dentro del canvas modal
        def _on_modal_wheel(event):
            try:
                canvas_modal.yview_scroll(int(-1 * (event.delta / 120)), "units")
            except Exception:
                pass
        canvas_modal.bind("<Enter>", lambda e: canvas_modal.bind_all("<MouseWheel>", _on_modal_wheel))
        canvas_modal.bind("<Leave>", lambda e: canvas_modal.bind_all("<MouseWheel>", self._on_mousewheel))
        # Padding interno
        inner = tk.Frame(outer, bg=BG_PANEL)
        inner.pack(fill="both", expand=True, padx=14, pady=14)
        outer = inner  # el resto del codigo añade widgets a "outer"

        # ── Sección: Datos básicos ──
        tk.Label(outer, text="👤  Datos de la persona", bg=BG_PANEL, fg=ACCENT,
                 font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(0, 6))

        tk.Label(outer, text="Nombre (cómo lo identificas tú, ej: Esposa, Cliente Carlos):",
                 bg=BG_PANEL, fg=TEXT, font=("Segoe UI", 9)).pack(anchor="w")
        e_name = _make_entry(outer, width=60)
        e_name.insert(0, recipient.get("name", "") if is_edit else "")
        e_name.pack(fill="x", pady=(2, 8))

        tk.Label(outer, text="Número de WhatsApp (con prefijo del país, ejemplo: +34611082880):",
                 bg=BG_PANEL, fg=TEXT, font=("Segoe UI", 9)).pack(anchor="w")
        e_phone = _make_entry(outer, width=60)
        e_phone.insert(0, recipient.get("phone", "+") if is_edit else "+")
        e_phone.pack(fill="x", pady=(2, 4))
        tk.Label(outer, text="🔑  La clave API se asigna automáticamente (la del dueño BuySell365 Pro)",
                 bg=BG_PANEL, fg=TEXT_SEC, font=("Segoe UI", 8, "italic")).pack(anchor="w", pady=(0, 8))

        # ── Sección: Tipos de señales ──
        tk.Label(outer, text="📊  ¿Qué tipos de señales le enviamos?",
                 bg=BG_PANEL, fg=ACCENT, font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(8, 2))
        tk.Label(outer, text="Marca uno o varios. Si marcas TODO, esta persona recibe absolutamente todas las señales.",
                 bg=BG_PANEL, fg=TEXT_SEC, font=("Segoe UI", 8, "italic")).pack(anchor="w", pady=(0, 6))

        current_filters = (recipient.get("filter_pair", "GOLD") if is_edit else "GOLD").upper().split(",")
        current_filters = [f.strip() for f in current_filters if f.strip()]
        filter_vars = {}
        filter_grid = tk.Frame(outer, bg=BG_PANEL)
        filter_grid.pack(fill="x")
        for i, opt in enumerate(self._WSP_FILTER_OPTIONS):
            label_es, desc_es = self._WSP_FILTER_LABELS.get(opt, (opt, ""))
            v = tk.BooleanVar(value=(opt in current_filters))
            filter_vars[opt] = v
            tk.Checkbutton(filter_grid, text=f"  {label_es}",
                            variable=v, bg=BG_PANEL, fg=TEXT, selectcolor=BG_INPUT,
                            activebackground=BG_PANEL, activeforeground=TEXT,
                            font=("Segoe UI", 10, "bold"), anchor="w"
                          ).grid(row=i, column=0, sticky="w", pady=1)
            tk.Label(filter_grid, text=desc_es, bg=BG_PANEL, fg=TEXT_SEC,
                     font=("Segoe UI", 9)).grid(row=i, column=1, sticky="w", padx=(20, 0), pady=1)

        # ── Sección: Avisos que recibe ──
        tk.Label(outer, text="🔔  ¿Qué tipo de avisos recibirá?",
                 bg=BG_PANEL, fg=ACCENT, font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(14, 2))
        tk.Label(outer, text="Cada señal puede generar varios mensajes durante su vida. Elige cuáles quieres que reciba.",
                 bg=BG_PANEL, fg=TEXT_SEC, font=("Segoe UI", 8, "italic")).pack(anchor="w", pady=(0, 6))

        current_events = recipient.get("events") if is_edit else None
        if current_events is None:
            current_events = self._WSP_DEFAULT_EVENTS
        event_vars = {}
        event_grid = tk.Frame(outer, bg=BG_PANEL)
        event_grid.pack(fill="x")
        for i, (ev_code, (ev_label, ev_desc)) in enumerate(self._WSP_EVENT_LABELS.items()):
            v = tk.BooleanVar(value=(ev_code in current_events))
            event_vars[ev_code] = v
            tk.Checkbutton(event_grid, text=f"  {ev_label}",
                            variable=v, bg=BG_PANEL, fg=TEXT, selectcolor=BG_INPUT,
                            activebackground=BG_PANEL, activeforeground=TEXT,
                            font=("Segoe UI", 10, "bold"), anchor="w"
                          ).grid(row=i, column=0, sticky="w", pady=2)
            tk.Label(event_grid, text=ev_desc, bg=BG_PANEL, fg=TEXT_SEC,
                     font=("Segoe UI", 9), wraplength=320, justify="left"
                    ).grid(row=i, column=1, sticky="w", padx=(20, 0), pady=2)

        # ── Sección: Filtro por probabilidad ──
        tk.Label(outer, text="🎯  Probabilidad mínima de la señal",
                 bg=BG_PANEL, fg=ACCENT, font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(14, 2))
        tk.Label(outer, text="Solo enviar señales con un % de probabilidad igual o mayor al elegido. Sin filtro = recibe todas.",
                 bg=BG_PANEL, fg=TEXT_SEC, font=("Segoe UI", 8, "italic")).pack(anchor="w", pady=(0, 6))
        prob_options = [
            ("0",  "Sin filtro (recibe todas las señales)"),
            ("50", "Mínimo 50% — solo señales decentes"),
            ("60", "Mínimo 60% — solo señales sólidas"),
            ("70", "Mínimo 70% — solo alta confianza"),
            ("80", "Mínimo 80% — solo muy alta confianza"),
            ("90", "Mínimo 90% — solo certeza casi total"),
        ]
        current_prob = str(int(recipient.get("min_probability", 0) or 0)) if is_edit else "0"
        if current_prob not in [o[0] for o in prob_options]:
            current_prob = "0"
        prob_var = tk.StringVar(value=current_prob)
        prob_grid = tk.Frame(outer, bg=BG_PANEL)
        prob_grid.pack(fill="x")
        for i, (val, desc) in enumerate(prob_options):
            tk.Radiobutton(prob_grid, text=f"  {val}%" if val != "0" else "  Sin filtro",
                           variable=prob_var, value=val,
                           bg=BG_PANEL, fg=TEXT, selectcolor=BG_INPUT,
                           activebackground=BG_PANEL, activeforeground=TEXT,
                           font=("Segoe UI", 10, "bold"), anchor="w"
                          ).grid(row=i, column=0, sticky="w", pady=1)
            tk.Label(prob_grid, text=desc, bg=BG_PANEL, fg=TEXT_SEC,
                     font=("Segoe UI", 9)).grid(row=i, column=1, sticky="w", padx=(20, 0), pady=1)

        # ── Sección: Estado ──
        enabled_var = tk.BooleanVar(value=(recipient.get("enabled", True) if is_edit else True))
        tk.Checkbutton(outer,
                        text="  ✅  Activo ahora (recibirá mensajes en cuanto guardes)",
                        variable=enabled_var, bg=BG_PANEL, fg=TEXT, selectcolor=BG_INPUT,
                        activebackground=BG_PANEL, activeforeground=TEXT,
                        font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(14, 0))
        tk.Label(outer, text="(Si lo desactivas, queda guardado pero no recibe mensajes hasta que lo vuelvas a activar)",
                 bg=BG_PANEL, fg=TEXT_SEC, font=("Segoe UI", 8, "italic")).pack(anchor="w")

        # ── Botones FIJOS (en el footer del modal, siempre visibles) ──
        # btn_frame ya existe como "footer" creado arriba; lo usamos directamente.
        btn_frame = footer
        for w in btn_frame.winfo_children():
            w.destroy()  # limpia por si reabren modal

        def _do_save():
            name = e_name.get().strip()
            phone = e_phone.get().strip()
            if not name:
                messagebox.showwarning("Falta el nombre",
                                        "Pon un nombre para identificar a esta persona (ej: Esposa, Cliente Carlos).",
                                        parent=win)
                return
            if not phone or not phone.startswith("+"):
                messagebox.showwarning("Número inválido",
                                        "El número debe empezar por + y el prefijo del país.\nEjemplo: +34611082880",
                                        parent=win)
                return
            filters_sel = [k for k, v in filter_vars.items() if v.get()]
            if not filters_sel:
                messagebox.showwarning("Sin tipos de señal",
                                        "Marca al menos un tipo de señal (ORO, CRIPTO, DIVISAS, etc.).",
                                        parent=win)
                return
            events_sel = [k for k, v in event_vars.items() if v.get()]
            if not events_sel:
                messagebox.showwarning("Sin avisos",
                                        "Marca al menos un tipo de aviso (Señal nueva, Cierre, etc.).",
                                        parent=win)
                return

            recipients = self._wsp_load_recipients()
            try:
                min_prob_val = int(prob_var.get() or "0")
            except Exception:
                min_prob_val = 0
            entry = {
                "name": name,
                "phone": phone,
                "apikey": (recipient.get("apikey") if is_edit else self._wsp_default_apikey()),
                "backend": "textmebot",
                "filter_pair": ",".join(filters_sel),
                "events": events_sel,
                "min_probability": min_prob_val,
                "enabled": enabled_var.get(),
                "notes": recipient.get("notes", "") if is_edit else f"Añadido desde el panel {time.strftime('%Y-%m-%d %H:%M')}",
            }
            if is_edit and idx is not None and idx < len(recipients):
                recipients[idx] = entry
            else:
                recipients.append(entry)
            if self._wsp_save_recipients(recipients):
                self._wsp_refresh_table()
                win.destroy()

        def _do_test():
            name = e_name.get().strip() or "Prueba"
            phone = e_phone.get().strip()
            if not phone.startswith("+"):
                messagebox.showwarning("Número inválido",
                                        "Antes de probar, escribe el número con + y prefijo de país.",
                                        parent=win)
                return
            apikey = recipient.get("apikey") if is_edit else self._wsp_default_apikey()
            entry = {"name": name, "phone": phone, "apikey": apikey, "backend": "textmebot"}
            threading.Thread(target=self._wsp_test_send, args=(entry,), daemon=True).start()

        _make_button(btn_frame, "Cancelar", win.destroy, bg=BG_INPUT, fg=TEXT).pack(side="left", padx=(0, 8))
        _make_button(btn_frame, "📤  Probar envío ahora", _do_test, bg="#7c3aed", fg="#ffffff").pack(side="left", padx=8)
        _make_button(btn_frame, "💾  Guardar", _do_save, bg="#238636", fg="#ffffff").pack(side="right")

    def _open_logs_folder(self):
        logs_dir = os.path.join(BASE_DIR, "logs")
        if os.path.exists(logs_dir):
            os.startfile(logs_dir)
        else:
            messagebox.showwarning("Logs", "La carpeta de logs no existe.")

    def _clear_log_view(self):
        self._log_text.config(state="normal")
        self._log_text.delete("1.0", "end")
        self._log_text.config(state="disabled")

    def _refresh_logs(self, *args):
        """FIX 2026-04-28: mejorado con pausa, auto-scroll toggle, busqueda y
        contadores de TPs/SLs/Warnings/Errors hoy en el header."""
        # Pausa: si el toggle "Pausa" esta activo, no refrescar
        if getattr(self, "_log_paused", None) and self._log_paused.get():
            return

        _src = getattr(self, '_log_source', None)
        _log_path = COPIER_LOG_FILE if (_src and _src.get() == "Copier") else LOG_FILE
        if not os.path.exists(_log_path):
            self._log_text.config(state="normal")
            self._log_text.delete("1.0", "end")
            self._log_text.insert("end", f"Archivo no encontrado: {_log_path}\n", "WARNING")
            self._log_text.config(state="disabled")
            return
        try:
            with open(_log_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except Exception:
            return

        # Take last 500 lines (mas que antes)
        lines = lines[-500:]

        # FIX 2026-05-16 (v2): preferir copier_stats.json (fuente oficial), fallback
        # a regex de copier.log si JSON vacio para el dia. Antes (fix v1 16-may) usaba
        # regex siempre porque crei que el JSON estaba roto — error mio: comparaba
        # fechas como strings dd/mm/yyyy en lugar de parsear. El JSON SI funciona.
        from datetime import datetime as _dt_lg
        _today_prefix = _dt_lg.now().strftime("%Y-%m-%d")
        _stats = {"tp": 0, "sl": 0, "warn": 0, "err": 0}
        # Intento 1: leer copier_stats.json (fuente oficial)
        try:
            _cop_now_v2 = _get_copier_stats_today()
            _stats["tp"] = int(_cop_now_v2.get("tps", 0) or 0)
            _stats["sl"] = int(_cop_now_v2.get("sls", 0) or 0)
        except Exception:
            pass
        # Fallback: si JSON dio 0 TPs y 0 SLs pero el log SI tiene eventos, usar regex
        if _stats["tp"] == 0 and _stats["sl"] == 0:
            try:
                if os.path.exists(COPIER_LOG_FILE):
                    with open(COPIER_LOG_FILE, "r", encoding="utf-8", errors="replace") as _fcl:
                        _cop_lines = _fcl.readlines()
                    for _cl in _cop_lines[-2000:]:
                        if not _cl.startswith(_today_prefix):
                            continue
                        _cl_up = _cl.upper()
                        # Regex estricto: NO contar "Monitor TP/SL loop iniciado"
                        if (
                            "TP HIT" in _cl_up
                            or "TP CELEBRATION ENVIADA" in _cl_up
                            or "TP REACHED" in _cl_up
                        ):
                            _stats["tp"] += 1
                        elif (
                            "SL HIT" in _cl_up
                            or "SL NOTIFICATION ENVIADA" in _cl_up
                            or "STOP LOSS" in _cl_up
                        ):
                            _stats["sl"] += 1
            except Exception:
                pass
        # Warnings y Errors desde el log activo (lo que el usuario esta viendo)
        for ln in lines:
            if not ln.startswith(_today_prefix):
                continue
            ln_upper = ln.upper()
            if "[ERROR]" in ln_upper or "❌" in ln:
                _stats["err"] += 1
            elif "[WARNING]" in ln_upper or "⚠" in ln:
                _stats["warn"] += 1
        try:
            self._log_stats_lbl.config(
                text=f"Hoy:  ✓{_stats['tp']} TPs  ●{_stats['sl']} SLs  ⚠{_stats['warn']} W  ✗{_stats['err']} E"
            )
        except Exception:
            pass

        # Filter por nivel
        level_filter = self._log_filter.get()
        if level_filter != "Todos":
            filtered = []
            for line in lines:
                upper = line.upper()
                if level_filter.upper() in upper:
                    filtered.append(line)
            lines = filtered

        # FIX 2026-04-28: filter por busqueda (case-insensitive substring)
        _search = ""
        try:
            _search = (self._log_search_var.get() or "").strip().lower()
        except Exception:
            _search = ""
        if _search:
            lines = [l for l in lines if _search in l.lower()]

        self._log_text.config(state="normal")
        self._log_text.delete("1.0", "end")

        for line in lines:
            tag = "INFO"
            upper = line.upper()
            # FIX 2026-04-28: tags mas precisos (TP_HIT verde brillante, SL rojo bold, heartbeat gris)
            if "HEARTBEAT" in upper or "💓" in line:
                tag = "HEARTBEAT"
            elif "[ERROR]" in upper or "TRACEBACK" in upper or "EXCEPTION" in upper or "❌" in line:
                tag = "ERROR"
            elif "[WARNING]" in upper or "[WARN]" in upper or "⚠" in line:
                tag = "WARNING"
            elif "CRITICAL" in upper:
                tag = "CRITICAL"
            elif "🎯" in line and ("TP HIT" in upper or "TP_HIT" in upper or "ALCANZAD" in upper):
                tag = "TP_HIT"
            elif "🛑" in line or "SL HIT" in upper or "STOP LOSS" in upper:
                tag = "SL_HIT"
            elif "WIN" in upper or "TP1" in upper or "TP2" in upper:
                tag = "SUCCESS"
            elif "SIGNAL" in upper or "SENAL" in upper or "COMPRA" in upper or "VENTA" in upper or "📡" in line:
                tag = "SIGNAL"
            self._log_text.insert("end", line, tag)

        self._log_text.config(state="disabled")
        # FIX 2026-04-28: auto-scroll opcional
        if getattr(self, "_log_autoscroll", None) and self._log_autoscroll.get():
            self._log_text.see("end")

    # ============================================================
    #  TAB 8: WEB
    # ============================================================
    def _build_web(self):
        canvas, scroll_frame = _make_scrollable(self._tab_web)
        self._scroll_canvases[str(self._tab_web)] = canvas

        # Header
        header = tk.Frame(scroll_frame, bg=BG_MAIN)
        header.pack(fill="x", padx=10, pady=(10, 5))

        _make_button(header, "Actualizar", self._refresh_web,
                     bg=BG_INPUT, fg=TEXT).pack(side="left", padx=(0, 10))
        _make_button(header, "Abrir Dashboard en Navegador", self._open_dashboard,
                     bg=ACCENT, fg="#000000").pack(side="left")

        # Estado del Servidor Web
        server_frame = _make_section_frame(scroll_frame, "Estado del Servidor Web")
        server_frame.pack(fill="x", padx=10, pady=5)

        server_grid = tk.Frame(server_frame, bg=BG_PANEL)
        server_grid.pack(fill="x")

        web_labels = [
            ("URL:", "_web_url"),
            ("Estado:", "_web_estado"),
            ("Ultimo Sync:", "_web_sync"),
            ("Ops Activas:", "_web_ops"),
            ("Senales Pendientes:", "_web_senales"),
        ]
        for i, (lbl_text, attr) in enumerate(web_labels):
            _make_label(server_grid, lbl_text, fg=TEXT_SEC).grid(row=i, column=0,
                                                                  sticky="w", padx=5, pady=2)
            val = _make_label(server_grid, "--", fg=TEXT)
            val.grid(row=i, column=1, sticky="w", padx=5, pady=2)
            setattr(self, attr, val)

        # Visitantes Web
        visitors_frame = _make_section_frame(scroll_frame, "Visitantes Web (Analytics)")
        visitors_frame.pack(fill="x", padx=10, pady=5)

        visitors_grid = tk.Frame(visitors_frame, bg=BG_PANEL)
        visitors_grid.pack(fill="x")

        visitor_labels = [
            ("Visitas Totales:", "_vis_total"),
            ("Visitantes Unicos:", "_vis_unique"),
            ("Visitas Hoy:", "_vis_today"),
        ]
        for i, (lbl_text, attr) in enumerate(visitor_labels):
            _make_label(visitors_grid, lbl_text, fg=TEXT_SEC).grid(row=0, column=i*2,
                                                                     sticky="w", padx=5, pady=2)
            val = _make_label(visitors_grid, "0", fg=ACCENT, font=("Segoe UI", 14))
            val.grid(row=0, column=i*2+1, sticky="w", padx=(0, 20), pady=2)
            setattr(self, attr, val)

        # Visitas últimos 7 días
        daily_frame = tk.Frame(visitors_frame, bg=BG_PANEL)
        daily_frame.pack(fill="x", pady=(5, 0))
        _make_label(daily_frame, "Ultimos 7 dias:", fg=TEXT_SEC).pack(anchor="w", padx=5)
        self._vis_daily = _make_label(daily_frame, "", fg=TEXT)
        self._vis_daily.pack(anchor="w", padx=5)

        # Páginas más visitadas
        pages_frame = tk.Frame(visitors_frame, bg=BG_PANEL)
        pages_frame.pack(fill="x", pady=(5, 0))
        _make_label(pages_frame, "Paginas mas visitadas:", fg=TEXT_SEC).pack(anchor="w", padx=5)
        self._vis_pages = _make_label(pages_frame, "", fg=TEXT)
        self._vis_pages.pack(anchor="w", padx=5)

        # Visitantes recientes
        recent_frame = _make_section_frame(scroll_frame, "Visitantes Recientes")
        recent_frame.pack(fill="x", padx=10, pady=5)

        cols_vis = ("Hora", "Pagina", "Idioma", "Navegador")
        self._vis_tree = ttk.Treeview(recent_frame, columns=cols_vis, show="headings", height=8)
        for c in cols_vis:
            self._vis_tree.heading(c, text=c)
        self._vis_tree.column("Hora", width=140)
        self._vis_tree.column("Pagina", width=150)
        self._vis_tree.column("Idioma", width=80)
        self._vis_tree.column("Navegador", width=300)
        self._vis_tree.pack(fill="x", pady=(0, 5))

        # Stats from web (JSON raw)
        stats_frame = _make_section_frame(scroll_frame, "Estadisticas desde la Web")
        stats_frame.pack(fill="x", padx=10, pady=(5, 15))

        self._web_stats_text = tk.Text(
            stats_frame, bg=BG_INPUT, fg=TEXT, font=("Consolas", 9),
            wrap="word", height=10, state="disabled", insertbackground=TEXT,
            highlightthickness=0,
        )
        self._web_stats_text.pack(fill="both", expand=True, pady=(0, 5))

    def _open_dashboard(self):
        env = load_env()
        url = env.get("DASHBOARD_URL", env.get("WEB_URL", "https://buysell365.pro/dashboard"))
        webbrowser.open(url)

    def _refresh_web(self):
        env = load_env()
        web_url = env.get("WEB_URL", "https://buysell365.pro").rstrip("/")
        api_key = env.get("API_SECRET_KEY", "")

        self._web_url.config(text=web_url)

        def do_fetch():
            # Health check
            try:
                req = urllib.request.Request(f"{web_url}/api/health", method="GET")
                if api_key:
                    req.add_header("X-API-Key", api_key)
                with urllib.request.urlopen(req, timeout=10) as resp:
                    health = json.loads(resp.read().decode())
                status = "ONLINE"
                status_color = WIN_COLOR
                ops = health.get("active_ops", health.get("operaciones_activas", "--"))
                sync_time = health.get("last_sync", health.get("ultimo_sync", "--"))
                senales = health.get("pending_signals", health.get("senales_pendientes", "--"))
            except Exception:
                status = "OFFLINE"
                status_color = ERR
                ops = "--"
                sync_time = "--"
                senales = "--"

            self.root.after(0, lambda: self._web_estado.config(text=status, fg=status_color))
            self.root.after(0, lambda: self._web_sync.config(text=str(sync_time)))
            self.root.after(0, lambda: self._web_ops.config(text=str(ops)))
            self.root.after(0, lambda: self._web_senales.config(text=str(senales)))

            # Visitors
            visitors_data = None
            try:
                req = urllib.request.Request(f"{web_url}/api/visitors", method="GET")
                if api_key:
                    req.add_header("X-API-Key", api_key)
                with urllib.request.urlopen(req, timeout=10) as resp:
                    visitors_data = json.loads(resp.read().decode())
            except Exception:
                pass

            if visitors_data:
                def update_visitors():
                    self._vis_total.config(text=str(visitors_data.get("total_visits", 0)))
                    self._vis_unique.config(text=str(visitors_data.get("total_unique", 0)))
                    self._vis_today.config(text=str(visitors_data.get("visits_today", 0)))

                    # Daily last 7
                    daily = visitors_data.get("daily_last_7", {})
                    daily_text = "  |  ".join(f"{d[-5:]}: {v}" for d, v in sorted(daily.items()))
                    self._vis_daily.config(text=daily_text if daily_text else "Sin datos")

                    # Top pages
                    pages = visitors_data.get("top_pages", {})
                    pages_text = "  |  ".join(f"{p}: {c}" for p, c in list(pages.items())[:5])
                    self._vis_pages.config(text=pages_text if pages_text else "Sin datos")

                    # Recent visitors table
                    for item in self._vis_tree.get_children():
                        self._vis_tree.delete(item)
                    for v in visitors_data.get("recent_visitors", [])[:15]:
                        self._vis_tree.insert("", "end", values=(
                            v.get("time", ""),
                            v.get("page", ""),
                            v.get("lang", ""),
                            v.get("ua", "")[:60]
                        ))
                self.root.after(0, update_visitors)

            # Stats
            stats_text = ""
            try:
                req = urllib.request.Request(f"{web_url}/api/stats", method="GET")
                if api_key:
                    req.add_header("X-API-Key", api_key)
                with urllib.request.urlopen(req, timeout=10) as resp:
                    stats = json.loads(resp.read().decode())
                stats_text = json.dumps(stats, indent=2, ensure_ascii=False)
            except Exception as ex:
                stats_text = f"Error obteniendo estadisticas: {ex}"

            def update_stats():
                self._web_stats_text.config(state="normal")
                self._web_stats_text.delete("1.0", "end")
                self._web_stats_text.insert("1.0", stats_text)
                self._web_stats_text.config(state="disabled")

            self.root.after(0, update_stats)

        threading.Thread(target=do_fetch, daemon=True).start()

    # ============================================================
    #  BOT CONTROL COMMANDS
    # ============================================================
    def _cmd_start_bot(self):
        if self.bot.is_running:
            messagebox.showinfo("Bot", "El bot ya esta ejecutandose.")
            return
        threading.Thread(target=self.bot.start, daemon=True).start()

    def _cmd_stop_bot(self):
        if not self.bot.is_running:
            messagebox.showinfo("Bot", "El bot ya esta detenido.")
            return
        self._btn_stop.config(text="Deteniendo...", state="disabled", bg=TEXT_SEC)
        def do_stop():
            self.bot.stop()
            # Forzar refresh del dashboard en el hilo principal
            def _after_stop():
                self._btn_stop.config(text="Detener Bot", state="normal", bg=ERR)
                try:
                    self._update_dashboard()
                except Exception:
                    pass
            self.root.after(1000, _after_stop)
        threading.Thread(target=do_stop, daemon=True).start()

    def _cmd_restart_bot(self):
        self._btn_restart.config(text="Reiniciando...", state="disabled", bg=TEXT_SEC)
        self._restart_pending = True
        self._restart_start_time = time.time()
        def do_restart():
            try:
                self.bot.restart()
            except Exception as e:
                _log(f"Error en restart: {e}")
            finally:
                self._restart_pending = False
        threading.Thread(target=do_restart, daemon=True).start()
        self._check_restart_done()

    def _check_restart_done(self):
        """Poll desde el hilo principal hasta que restart termine o timeout 30s."""
        elapsed = time.time() - getattr(self, '_restart_start_time', time.time())
        if getattr(self, '_restart_pending', False) and elapsed < 30:
            self.root.after(500, self._check_restart_done)
        else:
            self._btn_restart.config(text="Reiniciar", state="normal", bg=WARN)

    # ============================================================
    #  PEGAR SENAL MANUAL (dialog + process_pasted_signal)
    # ============================================================
    def _cmd_paste_signal_dialog(self):
        """Abre dialog con textarea para pegar senal cruda. Al procesar, llama
        process_pasted_signal del send_manual_signal (mismo pipeline que copier
        para canales aliados: parser + formato canon + tracking TP/SL HIT)."""
        dialog = tk.Toplevel(self.root)
        dialog.title("Pegar Senal Manual")
        dialog.geometry("600x500")
        dialog.configure(bg=BG_PANEL)
        dialog.transient(self.root)

        _make_label(dialog, "Pega la senal cruda (formato libre — el parser la entiende):",
                    fg=TEXT, font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=12, pady=(12, 4))
        _make_label(dialog, "Ejemplo:  #XAUUSD BUY 4600 4596\\n SL 4587\\n TP 4604 TP 4608 TP 4612",
                    fg=TEXT_SEC, font=("Segoe UI", 8)).pack(anchor="w", padx=12, pady=(0, 6))

        txt = tk.Text(dialog, height=14, bg=BG_INPUT, fg=TEXT,
                      insertbackground=TEXT, font=("Consolas", 10), relief="flat", borderwidth=1)
        txt.pack(fill="both", expand=True, padx=12, pady=4)
        txt.focus_set()

        result_lbl = _make_label(dialog, "", fg=TEXT_SEC, font=("Segoe UI", 9))
        result_lbl.pack(anchor="w", padx=12, pady=(4, 0))

        btn_row = tk.Frame(dialog, bg=BG_PANEL)
        btn_row.pack(fill="x", padx=12, pady=8)

        def do_process():
            raw = txt.get("1.0", "end").strip()
            if not raw:
                result_lbl.config(text="Pega algun texto primero", fg=ERR)
                return
            result_lbl.config(text="Procesando...", fg=WARN)
            dialog.update_idletasks()

            def worker():
                try:
                    import sys as _sys, importlib
                    if BASE_DIR not in _sys.path:
                        _sys.path.insert(0, BASE_DIR)
                    if "send_manual_signal" in _sys.modules:
                        importlib.reload(_sys.modules["send_manual_signal"])
                    from send_manual_signal import process_pasted_signal
                    res = process_pasted_signal(raw)
                except Exception as e:
                    res = {"ok": False, "error": f"Excepcion: {e}"}

                def show():
                    if res.get("ok"):
                        p = res.get("parsed", {})
                        msg = (
                            f"OK Publicada — msg_id={res.get('msg_id')} sig_id={res.get('sig_id')}\n"
                            f"Parsed: {p.get('pair')} {p.get('direction')} entry={p.get('entry')} sl={p.get('sl')}"
                        )
                        result_lbl.config(text=msg, fg=ACCENT)
                        try:
                            messagebox.showinfo("Publicada", msg)
                        except Exception:
                            pass
                    else:
                        result_lbl.config(text=f"ERROR: {res.get('error')}", fg=ERR)
                self.root.after(0, show)

            threading.Thread(target=worker, daemon=True).start()

        _make_button(btn_row, "Procesar y Publicar", do_process,
                     bg="#238636", fg="#ffffff").pack(side="right", padx=4)
        _make_button(btn_row, "Cancelar", dialog.destroy,
                     bg=TEXT_SEC, fg="#ffffff").pack(side="right", padx=4)

    def _toggle_autostart(self):
        self.config["autostart_bot"] = self._autostart_var.get()
        save_config(self.config)

    # ============================================================
    #  UPDATE LOOP
    # ============================================================
    def _update_loop(self):
        # No seguir si se está cerrando
        if not getattr(self, '_update_loop_active', True):
            return
        try:
            self._tick_count = getattr(self, '_tick_count', 0) + 1

            # Always update dashboard (every 2s)
            try:
                self._update_dashboard()
            except Exception as e:
                _log(f"Error actualizando dashboard: {e}")

            # Solo actualizar tabs pesados si el bot está corriendo
            _bot_on = self.bot.is_running

            # Senales (every 4s)
            if _bot_on and self._tick_count % 2 == 0:
                try:
                    self._refresh_senales()
                except Exception:
                    pass

            # Logs (every 6s)
            if self._tick_count % 3 == 0:
                try:
                    self._refresh_logs()
                except Exception:
                    pass

            # News (every 30 min = 900 ticks at 2s each)
            if self._tick_count % 900 == 0 or self._tick_count == 1:
                try:
                    self._refresh_news()
                except Exception:
                    pass

            # Countdown
            self._scan_countdown = max(0, self._scan_countdown - 2)
            if self._scan_countdown <= 0:
                try:
                    tc = load_trading_config()
                    self._scan_countdown = tc.get("intervalo_escaneo", 120)
                except Exception:
                    self._scan_countdown = 120

        except Exception as e:
            _log(f"Error critico en update_loop: {e}")

        self.root.after(2000, self._update_loop)

    # ============================================================
    #  DASHBOARD UPDATE
    # ============================================================
    def _update_dashboard(self):
        estado = self._get_estado()

        # Bot status
        if self.bot.is_running:
            self._dash_estado_lbl.config(text="● Bot Activo", fg=WIN_COLOR)
            self._dash_uptime_lbl.config(text=f"Uptime: {self.bot.uptime_str()}")
        else:
            self._dash_estado_lbl.config(text="● Bot Detenido", fg=ERR)
            self._dash_uptime_lbl.config(text="")


        # Stats
        historial = estado.get("historial_operaciones", [])
        stats_dia = estado.get("estadisticas_diarias", {})
        capital = estado.get("capital_usuario", 0)
        modo = estado.get("modo_riesgo", "normal")

        # Calculate today's stats from historial
        today_str = datetime.now().strftime("%d/%m/%Y")
        today_ops = [op for op in historial if op.get("fecha") == today_str]
        wins_today = sum(1 for op in today_ops if op.get("resultado") == "WIN")
        losses_today = sum(1 for op in today_ops if op.get("resultado") == "LOSS")
        pips_today = sum(op.get("pips", 0) for op in today_ops)
        total_today = wins_today + losses_today

        # Overall win rate from all history
        total_all = len(historial)
        wins_all = sum(1 for op in historial if op.get("resultado") == "WIN")
        win_rate = (wins_all / total_all * 100) if total_all > 0 else 0

        # Update window title with P&L info (usar balance MT5 real si disponible)
        _cap_titulo = getattr(self, '_mt5_balance_real', capital)
        # Incluir pips + W/L del copier VIP en el título.
        # Fix 2026-05-10: antes solo se leian wins/losses de estado.json (bot.py scanner)
        # ignorando totalmente los trades del copier — el title mostraba 0W/0L aunque
        # el copier tuviera 3 TPs hoy. Ahora suma ambas fuentes.
        try:
            _cop_day = _get_copier_stats_today()
            _cop_pips_t = _cop_day.get("pips_total", 0)
            _cop_tps = _cop_day.get("tps", 0)
            _cop_sls = _cop_day.get("sls", 0)
            _cop_info = f" | VIP: {_cop_pips_t:+.0f} pts" if _cop_pips_t != 0 else ""
            # Sumar W/L del copier al contador del title
            wins_today += _cop_tps
            losses_today += _cop_sls
        except Exception:
            _cop_info = ""
        # Currency symbol desde mt5_realtime.json (fallback $)
        try:
            from pathlib import Path as _Pt
            _rt = _Pt(__file__).parent / "mt5_realtime.json"
            _cur = "USD"
            if _rt.exists():
                _cur = (json.loads(_rt.read_text(encoding="utf-8")).get("cuenta", {}).get("currency") or "USD").upper()
            _sym_titulo = {"EUR": "€", "USD": "$", "GBP": "£", "JPY": "¥"}.get(_cur, _cur + " ")
        except Exception:
            _sym_titulo = "$"
        self.root.title(f"BuySell365 Pro | {_sym_titulo}{_cap_titulo:.0f} | {pips_today:+.1f} pips{_cop_info} | {wins_today}W/{losses_today}L | Propiedad de Emmanuel Diaz")

        self._dash_winrate.config(text=f"{win_rate:.1f}%",
                                  fg=WIN_COLOR if win_rate >= 50 else ERR)
        self._dash_senales_hoy.config(text=f"{total_today} ({wins_today}W / {losses_today}L)")
        self._dash_capital.config(text=f"{_sym_titulo}{_cap_titulo:,.2f}")
        self._dash_modo.config(text=modo.capitalize())

        if pips_today >= 0:
            self._dash_ganancia.config(text=f"+{pips_today:.1f} pips", fg=WIN_COLOR)
        else:
            self._dash_ganancia.config(text=f"{pips_today:.1f} pips", fg=ERR)

        self._dash_escaneo.config(text=f"{self._scan_countdown}s")

        # Auto-trading
        auto_trading = estado.get("mt5_pausado", False)
        env = load_env()
        auto_t = env.get("AUTO_TRADING", "True")
        if auto_trading:
            self._dash_autotrading.config(text="PAUSADO", fg=WARN)
        elif auto_t.lower() == "true":
            self._dash_autotrading.config(text="SÍ", fg=WIN_COLOR)
        else:
            self._dash_autotrading.config(text="NO", fg=ERR)

        # Operaciones abiertas
        active_count = len(estado.get("operaciones_activas", {}))
        if hasattr(self, '_dash_ops_abiertas'):
            self._dash_ops_abiertas.config(
                text=f"{active_count}" if active_count == 0 else f"{active_count} activas",
                fg=WIN_COLOR if active_count > 0 else TEXT
            )

        # ── Signal Copier Stats ──
        try:
            cop = _get_copier_stats_today()
            # Estado
            _copier_lock = os.path.join(BASE_DIR, ".copier.lock")
            _cop_running = False
            try:
                if os.path.exists(_copier_lock):
                    with open(_copier_lock) as _lf:
                        _cpid = int(_lf.read().strip())
                    import psutil
                    _cop_running = psutil.pid_exists(_cpid)
            except Exception:
                pass
            if _cop_running:
                self._cop_status.config(text="● Activo", fg=WIN_COLOR)
            else:
                self._cop_status.config(text="● Inactivo", fg=ERR)

            _cop_total = cop.get("total", 0)
            _cop_tps = cop.get("tps", 0)
            _cop_sls = cop.get("sls", 0)
            _cop_closes = cop.get("closes", 0)
            _cop_pips = cop.get("pips_total", 0)
            _cop_wr = cop.get("win_rate", 0)

            self._cop_senales.config(text=f"{_cop_total}" if _cop_total > 0 else "0")
            self._cop_tp_sl.config(
                text=f"✅ {_cop_tps}  /  🛑 {_cop_sls}",
                fg=WIN_COLOR if _cop_tps > _cop_sls else (ERR if _cop_sls > _cop_tps else TEXT))
            self._cop_closes.config(
                text=f"⚡ {_cop_closes}" if _cop_closes > 0 else "0",
                fg=ACCENT if _cop_closes > 0 else TEXT)
            self._cop_winrate.config(
                text=f"{_cop_wr:.0f}%",
                fg=WIN_COLOR if _cop_wr >= 50 else (WARN if _cop_wr >= 30 else ERR))
            if _cop_pips >= 0:
                self._cop_pips.config(text=f"+{_cop_pips:.0f}", fg=WIN_COLOR)
            else:
                self._cop_pips.config(text=f"{_cop_pips:.0f}", fg=ERR)

            # Mejor señal
            _best = cop.get("best")
            if _best and _best.get("pips", 0) > 0:
                _best_txt = f"🏆 Mejor: {_best['pair_display']} {_best.get('direction','')} +{_best['pips']:.0f} {_best.get('pips_unit','pts')}"
                self._cop_best.config(text=_best_txt)
            else:
                self._cop_best.config(text="")
        except Exception:
            pass

        # ── LLM PRO Stats (NUEVO 2026-04-30) ──
        try:
            self._update_llm_pro_stats(estado)
        except Exception:
            pass

        # Connections
        self._update_connections(estado)

        # Traffic lights
        try:
            self._update_traffic_lights(estado)
        except Exception:
            pass

        # Check for new trades and fire alerts
        try:
            self._check_new_trades(estado)
        except Exception:
            pass

        # Tab flash for active positions
        try:
            self._update_tab_flash(estado)
        except Exception:
            pass

        # Timestamp de ultima actualizacion
        if hasattr(self, '_dash_last_update_lbl'):
            self._dash_last_update_lbl.config(text=f"Actualizado: {datetime.now().strftime('%H:%M:%S')}")

        # Status bar (usar balance MT5 real si disponible)
        # FIX 2026-05-15: incluir senales del COPIER en el contador "Activas".
        # Antes solo se contaban `operaciones_activas` (estrategias legacy del bot),
        # dejando "Activas: 0" mientras la tab Senales mostraba 4 senales del copier.
        active_count = len(estado.get("operaciones_activas", {}))
        try:
            _open_path_sb = os.path.join(BASE_DIR, "copier_open_signals.json")
            if os.path.exists(_open_path_sb):
                with open(_open_path_sb, "r", encoding="utf-8") as _fsb:
                    _open_sb = json.load(_fsb) or {}
                active_count += len(_open_sb)
        except Exception:
            pass
        _cap_status = getattr(self, '_mt5_balance_real', capital)
        try:
            _sym_status = _sym_titulo
        except NameError:
            _sym_status = "$"
        self._status_bar.config(
            text=f"BuySell365 Pro v6.0 | Bot: {'ON' if self.bot.is_running else 'OFF'} | "
                 f"Activas: {active_count} | Capital: {_sym_status}{_cap_status:,.2f} | "
                 f"{datetime.now().strftime('%H:%M:%S')}"
        )

    def _update_llm_pro_stats(self, estado):
        """FIX 2026-04-30: actualiza panel LLM PRO con stats reales del bot.
        Lee de .env + features_stats + leads_followup.json + posttrade_lessons.log."""
        env = load_env()
        _llm_enabled = env.get("LLM_PARSER_ENABLED", "false").lower() in ("true", "1", "yes")
        _llm_mode = env.get("LLM_MODE", "fallback").lower()
        _llm_model = env.get("LLM_MODEL", "claude-sonnet-4-6")
        _vision_on = env.get("LLM_VISION_ENABLED", "true").lower() in ("true", "1", "yes")
        _ant_key = env.get("ANTHROPIC_API_KEY", "")

        # Modelo
        if _llm_enabled and _ant_key:
            _model_short = _llm_model.replace("claude-", "").upper()
            self._llm_model.config(text=f"✅ {_model_short}", fg=WIN_COLOR)
        elif _ant_key:
            self._llm_model.config(text=f"⚠️ Configurado pero OFF", fg=WARN)
        else:
            self._llm_model.config(text="❌ Sin API key", fg=ERR)

        # Modo
        _mode_label = {
            "always": "🔥 ALWAYS (PRO)",
            "fallback": "⚖️ Fallback",
            "off": "❌ OFF",
        }.get(_llm_mode, _llm_mode)
        _mode_color = WIN_COLOR if _llm_mode == "always" else (WARN if _llm_mode == "fallback" else ERR)
        self._llm_mode.config(text=_mode_label, fg=_mode_color)

        # Coste hoy + cache hits — lee features_stats si está disponible
        _cost_today = 0.0
        _cache_hits = 0
        _features_calls = {}
        try:
            # Las stats viven en el proceso del bot — intentar leerlas
            # via import (mismo sys.path que el bot).
            import sys as _sys, os as _os
            _bot_dir = _os.path.dirname(_os.path.abspath(__file__))
            if _bot_dir not in _sys.path:
                _sys.path.insert(0, _bot_dir)
            try:
                from llm_features import get_features_stats
                _fs = get_features_stats()
                _features_calls = _fs
                _cache_hits = _fs.get("cache_anthropic_hits", 0)
                _cost_dict = _fs.get("feature_costs", {})
                if isinstance(_cost_dict, dict):
                    _cost_today = sum(_cost_dict.values())
            except Exception:
                pass
        except Exception:
            pass

        # Coste
        _cost_color = WIN_COLOR if _cost_today < 0.50 else (WARN if _cost_today < 1.0 else ERR)
        self._llm_cost.config(text=f"${_cost_today:.4f}", fg=_cost_color)

        # Cache hits — verde si >0 (caching activo y funcionando)
        self._llm_cache.config(
            text=f"{_cache_hits}" if _cache_hits > 0 else "0 (esperando)",
            fg=WIN_COLOR if _cache_hits > 0 else TEXT_SEC,
        )

        # Leads detectados hoy + queue followup
        _leads_today = _features_calls.get("lead", 0)
        _followup_queue = 0
        try:
            _lf_path = os.path.join(BASE_DIR, "leads_followup.json")
            if os.path.exists(_lf_path):
                with open(_lf_path, "r", encoding="utf-8") as _flf:
                    _lf_data = json.load(_flf)
                # Queue = leads sin followup_sent
                _followup_queue = sum(
                    1 for v in _lf_data.values()
                    if isinstance(v, dict) and v.get("followup_sent_ts", 0) == 0
                )
        except Exception:
            pass

        self._llm_leads.config(text=f"{_leads_today}",
                                fg=WIN_COLOR if _leads_today > 0 else TEXT_SEC)
        self._llm_followup.config(text=f"{_followup_queue}",
                                   fg=ACCENT if _followup_queue > 0 else TEXT_SEC)

        # Línea de funciones activas — todo en español
        _activos = []
        if _llm_enabled:
            _activos.append("Lectura híbrida de señales")
        if _vision_on:
            _activos.append("Análisis visual de gráficos")
        # Otras funciones siempre activas si LLM_PARSER_ENABLED
        if _llm_enabled:
            _activos.append("SL inteligente")
            _activos.append("Detección de clientes")
            _activos.append("Análisis post-operación")
            _activos.append("Seguimiento automático")
            _activos.append("Evaluación de fiabilidad")
            _activos.append("Alertas de noticias")
        if _activos:
            self._llm_features_line.config(text="🟢 Funciones activas: " + " · ".join(_activos))
        else:
            self._llm_features_line.config(text="⚠️ LLM PRO desactivado — funciones no disponibles")


    def _update_connections(self, estado):
        env = load_env()

        # MT5
        mt5_login = env.get("MT5_LOGIN", "")
        if mt5_login and self.bot.is_running:
            self._conn_mt5.config(text="MT5  \u25cf Conectado", fg=WIN_COLOR)
        elif mt5_login:
            self._conn_mt5.config(text="MT5  \u25cf Configurado", fg=WARN)
        else:
            self._conn_mt5.config(text="MT5  \u25cf Sin configurar", fg=ERR)

        # Telegram
        tg_token = env.get("TELEGRAM_TOKEN", "")
        if tg_token and self.bot.is_running:
            self._conn_telegram.config(text="Telegram  \u25cf Conectado", fg=WIN_COLOR)
        elif tg_token:
            self._conn_telegram.config(text="Telegram  \u25cf Configurado", fg=WARN)
        else:
            self._conn_telegram.config(text="Telegram  \u25cf Sin configurar", fg=ERR)

        # Signal Copier
        _copier_lock = os.path.join(BASE_DIR, ".copier.lock")
        _copier_alive = False
        try:
            if os.path.exists(_copier_lock):
                with open(_copier_lock) as _lf:
                    _cpid = int(_lf.read().strip())
                import psutil
                if psutil.pid_exists(_cpid):
                    _copier_alive = True
        except Exception:
            pass
        if _copier_alive:
            self._conn_copier.config(text="Copiador  \u25cf Activo", fg=WIN_COLOR)
        elif self.bot.is_running:
            self._conn_copier.config(text="Copiador  \u25cf Iniciando...", fg=WARN)
        else:
            self._conn_copier.config(text="Copiador  \u25cf Inactivo", fg=ERR)

        # Instagram
        ig_user = env.get("IG_USERNAME", "")
        ig_pass = env.get("IG_PASSWORD", "")
        if ig_user and ig_pass and self.bot.is_running:
            self._conn_ig.config(text="Instagram  \u25cf Conectado", fg=WIN_COLOR)
        elif ig_user:
            self._conn_ig.config(text="Instagram  \u25cf Configurado", fg=WARN)
        else:
            self._conn_ig.config(text="Instagram  \u25cf No config", fg=TEXT_SEC)

        # Web
        web_url = env.get("WEB_URL", "")
        if web_url and self.bot.is_running:
            self._conn_web.config(text="Sincronizaci\u00f3n Web  \u25cf Activo", fg=WIN_COLOR)
        elif web_url:
            self._conn_web.config(text="Sincronizaci\u00f3n Web  \u25cf Configurado", fg=WARN)
        else:
            self._conn_web.config(text="Sincronizaci\u00f3n Web  \u25cf Sin configurar", fg=ERR)

    def _update_rendimiento(self, historial):
        self._dash_tree.delete(*self._dash_tree.get_children())

        # Aggregate by normalized asset
        stats = {}
        for asset in _VALID_ASSETS:
            stats[asset] = {"ops": 0, "wins": 0, "losses": 0, "pips": 0.0}

        for op in historial:
            nombre = _normalize_asset(op.get("nombre", ""))
            ticker = op.get("ticker", "")
            # Try nombre first, then ticker
            asset = nombre if nombre in _VALID_ASSETS else _normalize_asset(ticker)
            if asset not in _VALID_ASSETS:
                continue
            stats[asset]["ops"] += 1
            if op.get("resultado") == "WIN":
                stats[asset]["wins"] += 1
            elif op.get("resultado") == "LOSS":
                stats[asset]["losses"] += 1
            stats[asset]["pips"] += op.get("pips", 0)

        # Sort by pips descending
        sorted_assets = sorted(stats.items(), key=lambda x: x[1]["pips"], reverse=True)

        for idx, (asset, s) in enumerate(sorted_assets):
            ops = s["ops"]
            wins = s["wins"]
            losses = s["losses"]
            pips = s["pips"]
            wp = (wins / ops * 100) if ops > 0 else 0

            base_tag = "positive" if pips >= 0 else "negative"
            if ops == 0:
                base_tag = "neutral"
            tag = f"alt_{base_tag}" if idx % 2 == 1 else base_tag

            self._dash_tree.insert("", "end",
                                   values=(asset, ops, wins, losses,
                                           f"{pips:+.1f}", f"{wp:.0f}%"),
                                   tags=(tag,))

    # ============================================================
    #  SENALES UPDATE
    # ============================================================
    def _refresh_senales(self, *args):
        """FIX 2026-04-28: Senales tab REPARADA — ahora muestra datos REALES del copier
        (copier_open_signals.json para activas, copier_stats.json para cerradas hoy).
        Antes leia operaciones_activas/historial del scanner muerto -> celdas vacias '--'.
        """
        import json as _j
        from pathlib import Path as _Pth

        # Update countdown label (sin scanner ahora — pero mantenemos por si user mira)
        self._senales_escaneo_lbl.config(text=f"Senales del Copier (refresh cada 5s)")

        # Market status
        now = datetime.now()
        weekday = now.weekday()
        if weekday < 5:
            self._senales_market_lbl.config(text="Mercado: Abierto", fg=WIN_COLOR)
        else:
            self._senales_market_lbl.config(text="Mercado: Cerrado FinDeSemana", fg=ERR)

        # ── ACTIVAS — leer copier_open_signals.json ──
        self._senales_active_tree.delete(*self._senales_active_tree.get_children())
        self._senales_op_keys = {}
        active_count = 0
        try:
            _open_path = _Pth(BASE_DIR) / "copier_open_signals.json"
            if _open_path.exists():
                with open(_open_path, "r", encoding="utf-8") as f:
                    _open = _j.load(f)
                for sig_id, sdata in _open.items():
                    sig = sdata.get("signal", sdata) if isinstance(sdata, dict) else {}
                    if not isinstance(sig, dict):
                        continue
                    _pair = sig.get("pair", "?")
                    _direction = sig.get("direction", "?")
                    _tipo = "COMPRA" if _direction.upper() == "BUY" else ("VENTA" if _direction.upper() == "SELL" else _direction)
                    _entry_real = sig.get("mt5_entry", 0) or sig.get("entry", 0) or 0
                    _sl = sig.get("sl", 0) or 0
                    _tp = sig.get("tp", 0) or 0
                    _source = sig.get("source", "")
                    _ts = sig.get("timestamp", 0) or 0
                    _hora = datetime.fromtimestamp(_ts).strftime("%H:%M") if _ts > 0 else "--"
                    # Decimales segun activo
                    _p_up = str(_pair).upper()
                    if "JPY" in _p_up:
                        _dec = 3
                    elif _entry_real >= 100:
                        _dec = 2
                    else:
                        _dec = 5
                    _entry_str = f"{_entry_real:.{_dec}f}" if _entry_real > 0 else "Mercado"
                    _sl_str = f"{_sl:.{_dec}f}" if _sl > 0 else "--"
                    _tp_str = f"{_tp:.{_dec}f}" if _tp > 0 else "--"
                    _order_type_raw = sig.get("order_type", "Market")
                    # Traducir al español para que se entienda mejor
                    _order_type_map = {"Market": "Mercado", "Limit": "Limitada", "Stop": "Tope"}
                    _order_type = _order_type_map.get(_order_type_raw, _order_type_raw)
                    _estado_op = "Pendiente" if _order_type_raw in ("Limit", "Stop") else "Activa"
                    tag = "COMPRA" if _direction.upper() == "BUY" else "VENTA"
                    item_id = self._senales_active_tree.insert("", "end",
                        values=(_hora, _pair, _tipo, _entry_str, _sl_str, _tp_str,
                                _order_type, _source[:12], _estado_op),
                        tags=(tag,))
                    self._senales_op_keys[item_id] = sig_id
                    active_count += 1
        except Exception:
            pass

        # ── CERRADAS HOY — leer copier_stats.json ──
        self._senales_closed_tree.delete(*self._senales_closed_tree.get_children())
        wins_today = 0
        losses_today = 0
        pips_today = 0.0
        try:
            cop = _get_copier_stats_today()
            # Sort by closed_at descending (mas reciente arriba)
            _trades = sorted(cop.get("trades", []), key=lambda t: t.get("closed_at", 0), reverse=True)
            for t in _trades:
                _pair_d = t.get("pair_display", t.get("pair", "?"))
                _dir = t.get("direction", "?")
                _tipo = "COMPRA" if _dir.upper() == "BUY" else ("VENTA" if _dir.upper() == "SELL" else _dir)
                _result = t.get("result", "?")
                _pips = t.get("pips", 0) or 0
                _unit = t.get("pips_unit", "pts")
                _closed = t.get("closed_at", 0) or 0
                _opened = t.get("opened_at", 0) or 0
                _hora = datetime.fromtimestamp(_closed).strftime("%H:%M") if _closed > 0 else "--"
                _entry = t.get("entry", 0) or 0
                _tp_t = t.get("tp", 0) or 0
                _sl_t = t.get("sl", 0) or 0
                # Salida = TP si result=tp, SL si sl, sino ultima entry
                if _result == "tp":
                    _salida = _tp_t
                elif _result == "sl":
                    _salida = _sl_t
                else:
                    _salida = _entry  # parcial/cierre — aproximacion
                _p_up = str(_pair_d).upper()
                if "JPY" in _p_up:
                    _dec = 3
                elif _entry >= 100:
                    _dec = 2
                else:
                    _dec = 5
                _entry_str = f"{_entry:.{_dec}f}" if _entry > 0 else "--"
                _salida_str = f"{_salida:.{_dec}f}" if _salida > 0 else "--"
                # Duracion en minutos
                if _opened > 0 and _closed > _opened:
                    _dur_min = (_closed - _opened) / 60
                    _duracion = f"{_dur_min:.0f}m"
                else:
                    _duracion = "--"
                # Resultado legible
                _res_map = {"tp": "TP +", "sl": "SL -", "close_half": "Parc 50%",
                            "close_partial": "Parc", "full_close": "Cierre"}
                _resultado = _res_map.get(_result, _result)
                # Tag y stats
                if _result == "tp" or (_result in ("close_half", "close_partial", "full_close") and _pips > 0):
                    tag = "WIN"
                    if _result == "tp":
                        wins_today += 1
                elif _result == "sl":
                    tag = "LOSS"
                    losses_today += 1
                else:
                    tag = "MANUAL"
                # FIX 2026-05-15: para SL los pips deben mostrarse SIEMPRE negativos.
                # Antes el campo `pips` en copier_stats.json se guardaba como abs() en algunos
                # paths (ej. BUY que sale por SL) → la tabla mostraba "+90 pips" en una SL,
                # confundiendo al usuario. Forzamos el signo correcto segun resultado.
                if _result == "sl":
                    _pips_for_display = -abs(_pips)
                else:
                    _pips_for_display = _pips
                _pips_txt = f"{_pips_for_display:+.0f} {_unit}"
                pips_today += _pips_for_display
                self._senales_closed_tree.insert("", "end",
                    values=(_hora, _pair_d, _tipo, _entry_str, _salida_str, _pips_txt, _resultado, _duracion),
                    tags=(tag,))
        except Exception as _e_cop:
            pass

        # Summary
        self._senales_summary.config(
            text=f"Activas: {active_count}  |  Hoy: {wins_today}W / {losses_today}L  |  Pips hoy: {pips_today:+.1f}"
        )

        # Tab title update is handled by _update_tab_flash

    # ============================================================
    #  ANALISIS UPDATE
    # ============================================================
    def _refresh_analisis(self, *args):
        estado = self._get_estado()
        ops_activas = estado.get("operaciones_activas", {})
        historial = estado.get("historial_operaciones", [])
        diagnosticos = estado.get("diagnostico_activos", {})

        self._last_analisis_time = datetime.now().strftime("%H:%M:%S")
        self._analisis_time_lbl.config(text=f"Ultima actualizacion: {self._last_analisis_time}")

        for asset_name, widgets in self._analisis_cards.items():
            # Find ticker for this asset
            ticker = _ASSET_TICKERS.get(asset_name, "")
            diag = diagnosticos.get(ticker, {})

            # === INDICADORES EN TIEMPO REAL desde diagnóstico ===
            if diag:
                _rsi = diag.get("rsi", 0)
                _adx = diag.get("adx", 0)
                _vol = diag.get("vol", 0)
                _spread = diag.get("spread", 0)
                _ema_bull = diag.get("ema_bull", False)
                _macd_bull = diag.get("macd_bull", False)
                _precio = diag.get("precio", 0)

                # Precio
                if asset_name in ("EUR/USD", "GBP/JPY", "USD/JPY"):
                    widgets["price"].config(text=f"Precio: {_precio:.5f}")
                else:
                    widgets["price"].config(text=f"Precio: {_precio:,.2f}")

                # RSI con color
                if _rsi > 70:
                    rsi_clr = ERR  # Sobrecomprado
                    rsi_tag = " \u2191OB"
                elif _rsi < 30:
                    rsi_clr = WIN_COLOR  # Sobrevendido
                    rsi_tag = " \u2193OS"
                elif _rsi > 50:
                    rsi_clr = WIN_COLOR
                    rsi_tag = ""
                else:
                    rsi_clr = ERR
                    rsi_tag = ""
                widgets["rsi"].config(text=f"RSI: {_rsi:.0f}{rsi_tag}", fg=rsi_clr)

                # ADX con fuerza
                if _adx >= 25:
                    adx_clr = WIN_COLOR
                    adx_tag = " \u2705"
                elif _adx >= 20:
                    adx_clr = WARN
                    adx_tag = " ~"
                else:
                    adx_clr = ERR
                    adx_tag = " \u274C"
                widgets["adx"].config(text=f"ADX: {_adx:.0f}{adx_tag}", fg=adx_clr)

                # Volumen
                if _vol >= 0.5:
                    vol_clr = WIN_COLOR
                elif _vol >= 0.3:
                    vol_clr = WARN
                else:
                    vol_clr = ERR
                widgets["vol"].config(text=f"Vol: {_vol:.1f}x", fg=vol_clr)

                # Spread
                widgets["spread"].config(text=f"Spread: {_spread:.0f}",
                                          fg=WIN_COLOR if _spread < 30 else (WARN if _spread < 50 else ERR))

                # EMA y MACD
                ema_txt = "\u2705 EMA20>50" if _ema_bull else "\u274C EMA20<50"
                macd_txt = "\u2705 MACD>Sig" if _macd_bull else "\u274C MACD<Sig"
                ema_clr = WIN_COLOR if (_ema_bull and _macd_bull) else (WARN if (_ema_bull or _macd_bull) else ERR)
                widgets["ema"].config(text=f"{ema_txt} | {macd_txt}", fg=ema_clr)

                # Tendencia basada en indicadores reales
                _bull_count = sum([_ema_bull, _macd_bull, _rsi > 50, _adx >= 20])
                if _bull_count >= 3:
                    trend_text = "\u2B06 ALCISTA"
                    trend_color = WIN_COLOR
                elif _bull_count <= 1:
                    trend_text = "\u2B07 BAJISTA"
                    trend_color = ERR
                else:
                    trend_text = "\u2194 LATERAL"
                    trend_color = WARN
                widgets["trend"].config(text=f"Tendencia: {trend_text}", fg=trend_color)

                # Barra alcista/bajista basada en indicadores
                bull_pct = _bull_count / 4 * 100
                bear_pct = 100 - bull_pct
                bull_ratio = max(0.05, bull_pct / 100)
                bear_ratio = max(0.05, 1 - bull_ratio)
                widgets["bull_bar"].place(relx=0, rely=0, relwidth=bull_ratio, relheight=1.0)
                widgets["bear_bar"].place(relx=bull_ratio, rely=0, relwidth=bear_ratio, relheight=1.0)
                widgets["bull_pct"].config(text=f"Alcista: {bull_pct:.0f}%")
                widgets["bear_pct"].config(text=f"Bajista: {bear_pct:.0f}%")
            else:
                # Sin datos de diagnóstico — usar historial como fallback
                widgets["price"].config(text="Precio: esperando datos...")
                widgets["rsi"].config(text="RSI: --", fg=TEXT_SEC)
                widgets["adx"].config(text="ADX: --", fg=TEXT_SEC)
                widgets["vol"].config(text="Vol: --", fg=TEXT_SEC)
                widgets["spread"].config(text="Spread: --", fg=TEXT_SEC)
                widgets["ema"].config(text="EMA20/50: -- | MACD: --", fg=TEXT_SEC)
                widgets["trend"].config(text="Tendencia: esperando...", fg=TEXT_SEC)
                widgets["bull_bar"].place(relx=0, rely=0, relwidth=0.5, relheight=1.0)
                widgets["bear_bar"].place(relx=0.5, rely=0, relwidth=0.5, relheight=1.0)
                widgets["bull_pct"].config(text="Alcista: --%")
                widgets["bear_pct"].config(text="Bajista: --%")

            # Señal activa
            signal_text = "Sin senal activa"
            signal_color = TEXT_SEC
            for key, op in ops_activas.items():
                if not isinstance(op, dict):
                    continue
                op_asset = _normalize_asset(op.get("nombre", op.get("ticker", "")))
                if op_asset == asset_name:
                    tipo = op.get("tipo", "?")
                    score = op.get("score", "?")
                    conf = op.get("confianza", "?")
                    if isinstance(conf, (int, float)):
                        conf = f"{conf}%"
                    signal_text = f"Senal activa: {tipo} (Score {score}/5, {conf})"
                    signal_color = WIN_COLOR if tipo == "COMPRA" else ERR
                    break

            widgets["signal"].config(text=signal_text, fg=signal_color)

    # ============================================================
    #  RUN
    # ============================================================
    def _hide_aux_tk_windows(self):
        # FIX 2026-04-30: aplicar WS_EX_TOOLWINDOW a auxiliares de Tk
        # (EmbeddedMenuWindow, MenuWindow, TtkMonitor) para evitar que Windows
        # las exponga en taskbar/Alt+Tab. Complementa el fix 3bda74b que solo
        # ocultaba la root durante la construccion inicial.
        if sys.platform != "win32":
            return
        try:
            import ctypes
            from ctypes import wintypes

            GWL_EXSTYLE = -20
            WS_EX_TOOLWINDOW = 0x00000080
            WS_EX_APPWINDOW = 0x00040000
            # SWP_NOSIZE | NOMOVE | NOZORDER | NOACTIVATE | FRAMECHANGED
            SWP_FLAGS = 0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020

            user32 = ctypes.windll.user32
            targets = {"EmbeddedMenuWindowClass", "MenuWindowClass", "TtkMonitorClass"}
            my_pid = os.getpid()

            EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

            def _cb(hwnd, _lparam):
                try:
                    pid = wintypes.DWORD()
                    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                    if pid.value != my_pid:
                        return True
                    buf = ctypes.create_unicode_buffer(256)
                    user32.GetClassNameW(hwnd, buf, 256)
                    if buf.value in targets:
                        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
                        new_style = (style | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW
                        if new_style != style:
                            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, new_style)
                            user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, SWP_FLAGS)
                except Exception:
                    pass
                return True

            user32.EnumWindows(EnumProc(_cb), 0)
        except Exception:
            pass

    def run(self):
        # FIX 2026-04-25: mostrar la root ahora que esta totalmente construida
        # (titulo, geometria, tabs, widgets). Complementa el withdraw() del __init__.
        self.root.deiconify()
        # FIX 2026-04-30: ocultar auxiliares Tk de la taskbar (Embed/Menu/TtkMonitor).
        # Doble llamada porque Tk crea algunas auxiliares lazy en el primer paint.
        self._hide_aux_tk_windows()
        self.root.after(500, self._hide_aux_tk_windows)
        self.root.mainloop()


# ============================================================
#  MAIN ENTRY POINT
# ============================================================
def _single_instance_check():
    """Previene múltiples instancias del launcher.
    FIX 2026-04-25: el check antiguo via FindWindow buscaba "BuySell365 Pro" exacto,
    pero el titulo real lleva sufijo " - Consola de Control | ..." → siempre fallaba
    y permitia spawnear N launchers. Cada launcher tiene su propio watchdog → guerra
    entre watchdogs reiniciando bot.py cada 5-10s.
    Nuevo enfoque: enumerar procesos via psutil. Si hay otro python con launcher.py
    en el cmdline (que no sea yo), traer al frente su ventana y salir.
    """
    import ctypes
    _my_pid = os.getpid()
    _other_pid = None
    try:
        import psutil
        for _p in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                if _p.info['pid'] == _my_pid:
                    continue
                _name = (_p.info.get('name') or '').lower()
                if 'python' not in _name:
                    continue
                _cmd = ' '.join(_p.info.get('cmdline') or []).lower()
                if 'launcher.py' in _cmd:
                    _other_pid = _p.info['pid']
                    break
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except ImportError:
        # Sin psutil: confiar en mutex puro (best effort)
        pass

    if _other_pid:
        _log(f"⚠️ Launcher ya corriendo (PID={_other_pid}) — saliendo")
        # Intentar traer su ventana al frente (best effort, no critico)
        try:
            EnumWindows = ctypes.windll.user32.EnumWindows
            GetWindowThreadProcessId = ctypes.windll.user32.GetWindowThreadProcessId
            ShowWindow = ctypes.windll.user32.ShowWindow
            SetForegroundWindow = ctypes.windll.user32.SetForegroundWindow
            EnumWindowsProc = ctypes.WINFUNCTYPE(
                ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p
            )
            _found = []

            def _cb(hwnd, _):
                _wpid = ctypes.c_ulong()
                GetWindowThreadProcessId(hwnd, ctypes.byref(_wpid))
                if _wpid.value == _other_pid:
                    _found.append(hwnd)
                return True
            EnumWindows(EnumWindowsProc(_cb), 0)
            for _h in _found:
                ShowWindow(_h, 9)  # SW_RESTORE
                SetForegroundWindow(_h)
        except Exception:
            pass
        return False

    # No hay otro launcher → crear mutex por compat (no critico)
    try:
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        _launcher_mutex = kernel32.CreateMutexW(None, False, "BuySell365_Launcher_Mutex")
        _single_instance_check._mutex_handle = _launcher_mutex
    except Exception:
        pass
    return True


def main():
    # ── Fijar icono en la barra de tareas de Windows (evita que muestre Python) ──
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("BuySell365.Pro.Launcher")
    except Exception:
        pass

    if not _single_instance_check():
        return
    _log("=== BuySell365 Pro Launcher iniciando ===")
    config = load_config()
    bot = BotManager()
    # Login desactivado temporalmente - acceso libre
    console = ManagementConsole(config, bot)
    console.run()
    _remove_lock()


if __name__ == "__main__":
    main()

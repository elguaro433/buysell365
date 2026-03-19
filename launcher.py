"""
BuySell365 Pro - Consola de Control
Herramienta de gestion profesional para el bot de trading BuySell365 Pro.
Interfaz completa con 8 pestanas: Dashboard, Senales, Analisis, Trading Config,
Conexiones, VIP, Logs y Web.
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
LAUNCHER_LOG = os.path.join(BASE_DIR, "logs", "launcher.log")
ESTADO_FILE = os.path.join(BASE_DIR, "estado.json")
ENV_FILE = os.path.join(BASE_DIR, ".env")
ICON_PATH = os.path.join(BASE_DIR, "static", "bull-logo.png")
ICO_PATH = os.path.join(BASE_DIR, "static", "bull-logo.ico")

# ============================================================
#  ASSET NAME NORMALIZATION
# ============================================================
_ASSET_MAP = {
    "ORO": "ORO", "GC=F": "ORO", "XAUUSD": "ORO", "Gold": "ORO", "GOLD": "ORO",
    "EUR/USD": "EUR/USD", "EURUSD=X": "EUR/USD", "EURUSD": "EUR/USD",
    "USD/JPY": "USD/JPY", "JPY=X": "USD/JPY", "USDJPY": "USD/JPY", "USDJPY=X": "USD/JPY",
    "GBP/JPY": "GBP/JPY", "GBPJPY=X": "GBP/JPY", "GBPJPY": "GBP/JPY",
    "NASDAQ": "NASDAQ", "NQ=F": "NASDAQ", "US100": "NASDAQ", "US100Cash": "NASDAQ",
    "S&P 500": "S&P 500", "ES=F": "S&P 500", "US500": "S&P 500", "US500Cash": "S&P 500",
}
_VALID_ASSETS = {"ORO", "EUR/USD", "USD/JPY", "GBP/JPY", "NASDAQ", "S&P 500"}

# Reverse map: friendly name -> tickers used in estado.json
_ASSET_TICKERS = {
    "ORO": "GC=F",
    "EUR/USD": "EURUSD=X",
    "USD/JPY": "USDJPY=X",
    "GBP/JPY": "GBPJPY=X",
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
#  CONFIG MANAGEMENT
# ============================================================
def load_config() -> dict:
    defaults = {
        "autostart_bot": False,
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
        "capital": 500.0,
        "riesgo_trade": 0.02,
        "riesgo_premium": 0.04,
        "riesgo_oro": 0.02,
        "modo": "Normal",
        "min_score": 3,
        "max_trades": 5,
        "max_perdida_diaria": 0.10,
        "hora_apertura": 6,
        "hora_corte": 22,
        "min_rr": 1.5,
        "auto_cierre_horas": 24,
        "intervalo_escaneo": 120,
        "auto_trading_mt5": True,
        "solo_premium_mt5": True,
        "spreads_max": {
            "ORO": 50,
            "EUR/USD": 3,
            "USD/JPY": 3,
            "GBP/JPY": 5,
            "NASDAQ": 30,
            "S&P 500": 20,
        },
        "activos_habilitados": {
            "ORO": True,
            "EUR/USD": True,
            "USD/JPY": True,
            "GBP/JPY": True,
            "NASDAQ": True,
            "S&P 500": True,
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
    try:
        with open(ESTADO_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
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
    r'C:\Program Files\XM Global MT5\terminal64.exe',
    r'C:\Program Files\XM MT5\terminal64.exe',
    r'C:\Program Files\MetaTrader 5\terminal64.exe',
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
        self.pid = None
        self.start_time = None
        self.restart_count = 0
        self._watchdog_thread = None

    @property
    def is_running(self):
        # 1. Si el launcher arrancó el bot, verificar su proceso
        if self._proc and self._proc.poll() is None:
            return True
        # 2. Si no, verificar si hay un bot corriendo via .bot.pid
        pid_file = os.path.join(BASE_DIR, ".bot.pid")
        if os.path.exists(pid_file):
            try:
                with open(pid_file, "r") as f:
                    ext_pid = int(f.read().strip())
                # Verificar si ese PID existe en Windows
                import ctypes
                kernel32 = ctypes.windll.kernel32
                PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
                handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, ext_pid)
                if handle:
                    kernel32.CloseHandle(handle)
                    # Bot corriendo externamente — adoptar PID
                    if self.pid != ext_pid:
                        self.pid = ext_pid
                        if not self.start_time:
                            self.start_time = time.time()
                        self._running = True
                    return True
            except Exception:
                pass
        self._running = False
        return False

    def start(self):
        if self.is_running:
            return
        # Auto-abrir MT5 antes de iniciar el bot
        _ensure_mt5_running()
        # Clean up stale PID before starting
        self._cleanup_pid_file()
        python = sys.executable
        try:
            # CREATE_NEW_PROCESS_GROUP allows sending CTRL_BREAK for graceful shutdown
            _flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0) | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)
            self._proc = subprocess.Popen(
                [python, BOT_SCRIPT],
                cwd=BASE_DIR,
                creationflags=_flags,
            )
            self.pid = self._proc.pid
            self.start_time = time.time()
            self._running = True
            _log(f"Bot iniciado PID={self.pid}")
            if self._watchdog_thread is None or not self._watchdog_thread.is_alive():
                self._watchdog_thread = threading.Thread(target=self.watchdog_loop, daemon=True)
                self._watchdog_thread.start()
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

    def stop(self):
        if not self.is_running:
            return
        _target_pid = None
        try:
            # Determinar PID a matar (proceso propio o adoptado)
            if self._proc and self._proc.poll() is None:
                _target_pid = self._proc.pid
            elif self.pid:
                _target_pid = self.pid

            if not _target_pid:
                _log("Stop: no hay PID de bot conocido")
                self._running = False
                return

            # Graceful shutdown: taskkill en Windows (CTRL_BREAK mata el grupo entero)
            import subprocess as _sp
            try:
                if os.name == 'nt':
                    # Primero intentar taskkill sin /F (graceful)
                    _sp.run(["taskkill", "/PID", str(_target_pid)],
                            capture_output=True, timeout=10)
                else:
                    import signal as _sig
                    os.kill(_target_pid, _sig.SIGTERM)
                # Esperar a que termine
                for _ in range(30):
                    time.sleep(0.5)
                    if self._proc and self._proc.poll() is not None:
                        break
                    if not self._pid_alive(_target_pid):
                        break
                if not self._pid_alive(_target_pid):
                    _log("Bot detenido gracefully (estado guardado)")
                else:
                    raise TimeoutError("Bot no respondio")
            except (TimeoutError, _sp.TimeoutExpired, OSError):
                _log("Bot no respondio al cierre graceful, forzando kill...")
                try:
                    _sp.run(["taskkill", "/F", "/PID", str(_target_pid)],
                            capture_output=True, timeout=10)
                    time.sleep(1)
                except Exception as e2:
                    _log(f"Error en kill forzado: {e2}")
        except Exception as e:
            _log(f"Error deteniendo bot: {e}")
        self._proc = None
        self.pid = None
        self._running = False
        self._cleanup_pid_file()
        _log("Bot detenido")

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
        # Esperar a que el proceso realmente muera
        for _ in range(15):
            time.sleep(1)
            if not self.is_running and (not self.pid or not self._pid_alive(self.pid)):
                break
        # Forzar estado limpio
        self._proc = None
        self.pid = None
        self._running = False
        time.sleep(2)
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
        """Watchdog: monitors bot process, restarts on crash with backoff."""
        _consecutive_restarts = 0
        _last_restart_time = 0
        while self._running:
            time.sleep(15)
            if self._running and not self.is_running:
                _consecutive_restarts += 1
                _now = time.time()
                # Backoff: wait longer if bot keeps crashing
                if _consecutive_restarts > 3:
                    _wait = min(60 * _consecutive_restarts, 300)  # Max 5 min
                    _log(f"Watchdog: bot caido (crash #{_consecutive_restarts}), esperando {_wait}s antes de reiniciar...")
                    time.sleep(_wait)
                else:
                    _log(f"Watchdog: bot caido, reiniciando... (intento #{_consecutive_restarts})")
                self._cleanup_pid_file()
                self.start()
                self.restart_count += 1
                _last_restart_time = time.time()
            elif self.is_running:
                # Bot is healthy, reset consecutive restart counter after 5 min of stability
                if _consecutive_restarts > 0 and (time.time() - _last_restart_time) > 300:
                    _consecutive_restarts = 0


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
        self._scalper_paused = False
        self._tab_flash_state = {}  # tab_name -> bool for flashing

        # Build root window
        self.root = tk.Tk()
        self.root.title("BuySell365 Pro - Consola de Control | Creado por Emmanuel Diaz")
        self.root.geometry("1400x850")
        self.root.minsize(1100, 700)
        self.root.configure(bg=BG_MAIN)

        # Try to set icon (.ico for taskbar, .png for window)
        try:
            if os.path.exists(ICO_PATH):
                self.root.iconbitmap(ICO_PATH)
            elif os.path.exists(ICON_PATH):
                icon_img = tk.PhotoImage(file=ICON_PATH)
                self.root.iconphoto(True, icon_img)
                self._icon_img = icon_img  # prevent GC
        except Exception:
            pass

        # Style
        self._setup_style()

        # Notebook (tabs)
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=4, pady=4)

        # Create all tabs
        self._tab_dashboard = tk.Frame(self.notebook, bg=BG_MAIN)
        self._tab_senales = tk.Frame(self.notebook, bg=BG_MAIN)
        self._tab_analisis = tk.Frame(self.notebook, bg=BG_MAIN)
        self._tab_trading = tk.Frame(self.notebook, bg=BG_MAIN)
        self._tab_conexiones = tk.Frame(self.notebook, bg=BG_MAIN)
        self._tab_vip = tk.Frame(self.notebook, bg=BG_MAIN)
        self._tab_logs = tk.Frame(self.notebook, bg=BG_MAIN)
        self._tab_web = tk.Frame(self.notebook, bg=BG_MAIN)
        self._tab_scalper = tk.Frame(self.notebook, bg=BG_MAIN)
        self._tab_noticias = tk.Frame(self.notebook, bg=BG_MAIN)

        self.notebook.add(self._tab_dashboard, text=" \U0001F4CA Dashboard ")
        self.notebook.add(self._tab_senales, text=" \U0001F4E1 Se\u00f1ales ")
        self.notebook.add(self._tab_scalper, text=" \U0001FA92 Scalper ")
        self.notebook.add(self._tab_noticias, text=" \U0001F4F0 Noticias ")
        self.notebook.add(self._tab_analisis, text=" \U0001F50D Analisis ")
        self.notebook.add(self._tab_trading, text=" \u2699 Trading Config ")
        self.notebook.add(self._tab_vip, text=" \u2B50 VIP ")
        self.notebook.add(self._tab_logs, text=" \U0001F4DD Logs ")
        self.notebook.add(self._tab_web, text=" \U0001F310 Web ")

        # Scrollable canvases dict for mousewheel binding
        self._scroll_canvases = {}

        # Build each tab
        self._build_dashboard()
        self._build_senales()
        self._build_analisis()
        self._build_trading()
        self._build_conexiones()
        self._build_vip()
        self._build_logs()
        self._build_web()
        self._build_scalper()
        self._build_noticias()

        # Mousewheel binding
        self.root.bind_all("<MouseWheel>", self._on_mousewheel)
        self.root.bind_all("<Button-4>", self._on_mousewheel_linux)
        self.root.bind_all("<Button-5>", self._on_mousewheel_linux)

        # Auto-start bot if configured
        if self.config.get("autostart_bot", False):
            self.root.after(1000, self._auto_start_bot)

        # Status bar
        _status_frame = tk.Frame(self.root, bg="#0a0e14")
        _status_frame.pack(side="bottom", fill="x")
        self._status_bar = tk.Label(
            _status_frame, text="BuySell365 Pro v5.0 | Bot: OFF",
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
        if self.bot.is_running:
            if messagebox.askyesno("Salir",
                                   "El bot esta ejecutandose. Deseas detenerlo antes de salir?"):
                self.bot.stop()
        if self._tray_icon:
            try:
                self._tray_icon.stop()
            except Exception:
                pass
        self.root.destroy()

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

        # === Barra superior con boton Actualizar ===
        dash_header = tk.Frame(scroll_frame, bg=BG_MAIN)
        dash_header.pack(fill="x", padx=10, pady=(10, 0))
        _make_button(dash_header, "Actualizar", lambda: self._update_dashboard(),
                     bg=BG_INPUT, fg=TEXT).pack(side="left", padx=0, pady=3)
        self._dash_last_update_lbl = _make_label(dash_header, "", fg=TEXT_SEC,
                                                  font=("Segoe UI", 9))
        self._dash_last_update_lbl.pack(side="left", padx=10)

        # Real-time capital from MT5
        self._dash_mt5_capital_lbl = _make_label(dash_header, "MT5 Capital: --", fg=ACCENT_BRIGHT,
                                                  font=("Segoe UI", 10, "bold"))
        self._dash_mt5_capital_lbl.pack(side="right", padx=10)

        # === Section 1: Estado + Controles ===
        top_frame = tk.Frame(scroll_frame, bg=BG_MAIN)
        top_frame.pack(fill="x", padx=10, pady=(5, 5))

        # Estado del Bot
        estado_frame = _make_section_frame(top_frame, "Estado del Bot")
        estado_frame.pack(side="left", fill="both", expand=True, padx=(0, 5))

        info_grid = tk.Frame(estado_frame, bg=BG_PANEL)
        info_grid.pack(fill="x")

        self._dash_estado_lbl = _make_label(info_grid, "Estado: --", fg=TEXT_SEC)
        self._dash_estado_lbl.grid(row=0, column=0, sticky="w", pady=2)
        self._dash_pid_lbl = _make_label(info_grid, "PID: --", fg=TEXT_SEC)
        self._dash_pid_lbl.grid(row=1, column=0, sticky="w", pady=2)
        self._dash_uptime_lbl = _make_label(info_grid, "Uptime: --", fg=TEXT_SEC)
        self._dash_uptime_lbl.grid(row=2, column=0, sticky="w", pady=2)
        self._dash_restart_lbl = _make_label(info_grid, "Reinicios: 0", fg=TEXT_SEC)
        self._dash_restart_lbl.grid(row=3, column=0, sticky="w", pady=2)

        # Controles
        ctrl_frame = _make_section_frame(top_frame, "Controles")
        ctrl_frame.pack(side="right", fill="both", expand=True, padx=(5, 0))

        btn_row = tk.Frame(ctrl_frame, bg=BG_PANEL)
        btn_row.pack(fill="x", pady=5)

        self._btn_start = _make_button(btn_row, "Iniciar Bot", self._cmd_start_bot,
                                       bg="#238636", fg="#ffffff")
        self._btn_start.pack(side="left", padx=5, pady=3)

        self._btn_stop = _make_button(btn_row, "Detener Bot", self._cmd_stop_bot,
                                      bg=ERR, fg="#ffffff")
        self._btn_stop.pack(side="left", padx=5, pady=3)

        self._btn_restart = _make_button(btn_row, "Reiniciar", self._cmd_restart_bot,
                                         bg=WARN, fg="#000000")
        self._btn_restart.pack(side="left", padx=5, pady=3)

        # Auto-start checkbox
        self._autostart_var = tk.BooleanVar(value=self.config.get("autostart_bot", True))
        chk = tk.Checkbutton(ctrl_frame, text="Auto-iniciar bot al abrir",
                             variable=self._autostart_var, command=self._toggle_autostart,
                             bg=BG_PANEL, fg=TEXT, selectcolor=BG_INPUT,
                             activebackground=BG_PANEL, activeforeground=TEXT,
                             font=("Segoe UI", 9))
        chk.pack(anchor="w", pady=(5, 0))

        # === Quick Controls (improvement 5) ===
        qctrl_frame = _make_section_frame(scroll_frame, "Controles Rapidos")
        qctrl_frame.pack(fill="x", padx=10, pady=5)

        qctrl_row = tk.Frame(qctrl_frame, bg=BG_PANEL)
        qctrl_row.pack(fill="x", pady=3)

        self._btn_scalper_toggle = _make_button(
            qctrl_row, "\u23F8 Pausar Scalper", self._cmd_toggle_scalper,
            bg="#6e40c9", fg="#ffffff")
        self._btn_scalper_toggle.pack(side="left", padx=5, pady=3)

        _make_button(qctrl_row, "\u26A0 Cerrar Todo", self._cmd_cerrar_todo,
                     bg="#b91c1c", fg="#ffffff").pack(side="left", padx=5, pady=3)

        _make_button(qctrl_row, "\U0001F50D Escanear Ahora", self._cmd_escanear_ahora,
                     bg="#1d4ed8", fg="#ffffff").pack(side="left", padx=5, pady=3)

        self._btn_pause_all = _make_button(qctrl_row, "\u23F8 Pausar Todo", self._cmd_pause_all,
                                           bg="#b91c1c", fg="#ffffff")
        self._btn_pause_all.pack(side="left", padx=5, pady=3)

        # === Section 2: Estadisticas en Vivo ===
        stats_frame = _make_section_frame(scroll_frame, "Estadisticas en Vivo")
        stats_frame.pack(fill="x", padx=10, pady=5)

        stats_grid = tk.Frame(stats_frame, bg=BG_PANEL)
        stats_grid.pack(fill="x")
        stats_grid.columnconfigure(1, weight=1)
        stats_grid.columnconfigure(3, weight=1)

        labels_left = [
            ("Win Rate:", "_dash_winrate"),
            ("Senales Hoy:", "_dash_senales_hoy"),
            ("Capital:", "_dash_capital"),
            ("Modo:", "_dash_modo"),
        ]
        labels_right = [
            ("Ganancia Hoy:", "_dash_ganancia"),
            ("Drawdown:", "_dash_drawdown"),
            ("Proximo Escaneo:", "_dash_escaneo"),
            ("Auto-Trading:", "_dash_autotrading"),
        ]

        for i, (lbl_text, attr) in enumerate(labels_left):
            _make_label(stats_grid, lbl_text, fg=TEXT_SEC, font=("Segoe UI", 10)).grid(
                row=i, column=0, sticky="w", padx=(0, 8), pady=2)
            val_lbl = _make_label(stats_grid, "--", fg=TEXT, font=("Segoe UI", 10, "bold"))
            val_lbl.grid(row=i, column=1, sticky="w", pady=2)
            setattr(self, attr, val_lbl)

        for i, (lbl_text, attr) in enumerate(labels_right):
            _make_label(stats_grid, lbl_text, fg=TEXT_SEC, font=("Segoe UI", 10)).grid(
                row=i, column=2, sticky="w", padx=(30, 8), pady=2)
            val_lbl = _make_label(stats_grid, "--", fg=TEXT, font=("Segoe UI", 10, "bold"))
            val_lbl.grid(row=i, column=3, sticky="w", pady=2)
            setattr(self, attr, val_lbl)

        # === Traffic Light Asset Status ===
        traffic_frame = _make_section_frame(scroll_frame, "Estado de Activos")
        traffic_frame.pack(fill="x", padx=10, pady=5)

        self._traffic_lights = {}
        tl_row = tk.Frame(traffic_frame, bg=BG_PANEL)
        tl_row.pack(fill="x", pady=3)

        all_assets = ["ORO", "EUR/USD", "USD/JPY", "GBP/JPY", "NASDAQ", "S&P 500"]
        for asset in all_assets:
            af = tk.Frame(tl_row, bg=BG_PANEL)
            af.pack(side="left", padx=8, pady=2)
            light_canvas = tk.Canvas(af, width=16, height=16, bg=BG_PANEL,
                                     highlightthickness=0)
            light_canvas.pack(side="left", padx=(0, 4))
            oval_id = light_canvas.create_oval(2, 2, 14, 14, fill=TEXT_SEC, outline="")
            _make_label(af, asset, fg=TEXT, font=("Segoe UI", 9, "bold")).pack(side="left")
            self._traffic_lights[asset] = (light_canvas, oval_id)

        # === Section 3: Conexiones ===
        conn_frame = _make_section_frame(scroll_frame, "Conexiones")
        conn_frame.pack(fill="x", padx=10, pady=5)

        conn_row = tk.Frame(conn_frame, bg=BG_PANEL)
        conn_row.pack(fill="x")

        self._conn_mt5 = _make_label(conn_row, "MT5  --", fg=TEXT_SEC, font=("Segoe UI", 11))
        self._conn_mt5.pack(side="left", padx=20)
        self._conn_telegram = _make_label(conn_row, "Telegram  --", fg=TEXT_SEC,
                                          font=("Segoe UI", 11))
        self._conn_telegram.pack(side="left", padx=20)
        self._conn_web = _make_label(conn_row, "Web Sync  --", fg=TEXT_SEC,
                                     font=("Segoe UI", 11))
        self._conn_web.pack(side="left", padx=20)

        # === Section 4: Rendimiento por Activo ===
        rend_frame = _make_section_frame(scroll_frame, "Rendimiento por Activo")
        rend_frame.pack(fill="x", padx=10, pady=5)

        columns = ("activo", "operaciones", "ganadas", "perdidas", "pips", "winpct")
        self._dash_tree = ttk.Treeview(rend_frame, columns=columns, show="headings", height=7)
        self._dash_tree.heading("activo", text="Activo")
        self._dash_tree.heading("operaciones", text="Operaciones")
        self._dash_tree.heading("ganadas", text="Ganadas")
        self._dash_tree.heading("perdidas", text="Perdidas")
        self._dash_tree.heading("pips", text="Pips")
        self._dash_tree.heading("winpct", text="Win%")

        self._dash_tree.column("activo", width=140, anchor="w")
        self._dash_tree.column("operaciones", width=100, anchor="center")
        self._dash_tree.column("ganadas", width=90, anchor="center")
        self._dash_tree.column("perdidas", width=90, anchor="center")
        self._dash_tree.column("pips", width=120, anchor="center")
        self._dash_tree.column("winpct", width=90, anchor="center")

        self._dash_tree.tag_configure("positive", foreground=WIN_COLOR)
        self._dash_tree.tag_configure("negative", foreground=LOSS_COLOR)
        self._dash_tree.tag_configure("neutral", foreground=TEXT)
        self._dash_tree.tag_configure("alt_positive", foreground=WIN_COLOR, background=BG_ROW_ALT)
        self._dash_tree.tag_configure("alt_negative", foreground=LOSS_COLOR, background=BG_ROW_ALT)
        self._dash_tree.tag_configure("alt_neutral", foreground=TEXT, background=BG_ROW_ALT)

        self._dash_tree.pack(fill="x", pady=(0, 5))

        # === Performance Panel (improvement 4) ===
        perf_frame = _make_section_frame(scroll_frame, "Rendimiento Detallado")
        perf_frame.pack(fill="x", padx=10, pady=5)

        perf_grid = tk.Frame(perf_frame, bg=BG_PANEL)
        perf_grid.pack(fill="x")
        perf_grid.columnconfigure(1, weight=1)
        perf_grid.columnconfigure(3, weight=1)

        perf_left = [
            ("Win Rate Hoy:", "_perf_wr_today"),
            ("Win Rate Semana:", "_perf_wr_week"),
            ("Win Rate Mes:", "_perf_wr_month"),
        ]
        perf_right = [
            ("Mejor Trade Hoy:", "_perf_best_trade"),
            ("Peor Trade Hoy:", "_perf_worst_trade"),
            ("Total Pips Hoy:", "_perf_total_pips"),
        ]

        for i, (lbl_text, attr) in enumerate(perf_left):
            _make_label(perf_grid, lbl_text, fg=TEXT_SEC, font=("Segoe UI", 10)).grid(
                row=i, column=0, sticky="w", padx=(0, 8), pady=2)
            val_lbl = _make_label(perf_grid, "--", fg=TEXT, font=("Segoe UI", 10, "bold"))
            val_lbl.grid(row=i, column=1, sticky="w", pady=2)
            setattr(self, attr, val_lbl)

        for i, (lbl_text, attr) in enumerate(perf_right):
            _make_label(perf_grid, lbl_text, fg=TEXT_SEC, font=("Segoe UI", 10)).grid(
                row=i, column=2, sticky="w", padx=(30, 8), pady=2)
            val_lbl = _make_label(perf_grid, "--", fg=TEXT, font=("Segoe UI", 10, "bold"))
            val_lbl.grid(row=i, column=3, sticky="w", pady=2)
            setattr(self, attr, val_lbl)

        # Footer: Acerca de
        footer = tk.Frame(scroll_frame, bg=BG_MAIN)
        footer.pack(fill="x", padx=10, pady=(5, 15))
        _make_button(footer, "Acerca de", self._show_about, bg=BG_INPUT, fg=TEXT).pack(
            side="right")

        # Start MT5 capital refresh timer (every 30s)
        self.root.after(5000, self._refresh_mt5_capital_loop)

        # News fetched from Noticias tab

    def _show_about(self):
        messagebox.showinfo(
            "Acerca de BuySell365 Pro",
            "BuySell365 Pro v5.0\n\n"
            "Bot de trading automatizado con IA\n"
            "Senales para ORO, EUR/USD, USD/JPY, GBP/JPY, NASDAQ, S&P 500\n\n"
            "Creado por Emmanuel Diaz\n"
            "https://buysell365.pro\n\n"
            "(c) 2026 BuySell365. Todos los derechos reservados."
        )

    # --------------------------------------------------------
    #  QUICK CONTROLS (improvement 5)
    # --------------------------------------------------------
    def _cmd_toggle_scalper(self):
        """Toggle scalper pause state via .bot.cmd."""
        if not self._scalper_paused:
            _send_bot_cmd("scalper_pause")
            self._scalper_paused = True
            self._btn_scalper_toggle.config(text="\u25B6 Play Scalper", bg="#238636")
            _log("Scalper PAUSADO por usuario")
        else:
            _send_bot_cmd("scalper_resume")
            self._scalper_paused = False
            self._btn_scalper_toggle.config(text="\u23F8 Pausar Scalper", bg="#6e40c9")
            _log("Scalper REANUDADO por usuario")

    def _cmd_cerrar_todo(self):
        """Emergency close all positions via MT5 in background."""
        if not messagebox.askyesno("Cerrar Todo",
                                   "\u26A0 ATENCION: Esto cerrara TODAS las posiciones abiertas en MT5.\n\n"
                                   "Esta seguro?"):
            return

        def _close_all():
            try:
                import MetaTrader5 as mt5
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
        "USD": ["EUR/USD", "USD/JPY", "GBP/JPY", "ORO", "NASDAQ", "S&P 500"],
        "EUR": ["EUR/USD"],
        "GBP": ["GBP/JPY"],
        "JPY": ["USD/JPY", "GBP/JPY"],
        "CHF": ["EUR/USD"],
        "AUD": ["ORO"],
        "CAD": ["ORO"],
        "NZD": [],
        "CNY": ["ORO", "NASDAQ", "S&P 500"],
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
        self._news_tree.pack(fill="x", pady=(0, 5))

        # Scrollbar
        scrollbar = ttk.Scrollbar(main_frame, orient="vertical", command=self._news_tree.yview)
        self._news_tree.configure(yscrollcommand=scrollbar.set)

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
        """Read MT5 account balance in background, update label."""
        def _fetch():
            try:
                import MetaTrader5 as mt5
                if not mt5.initialize():
                    return None
                info = mt5.account_info()
                if info:
                    return {"balance": info.balance, "equity": info.equity,
                            "profit": info.profit}
                return None
            except Exception:
                return None

        def _update(result):
            if result:
                bal = result["balance"]
                eq = result["equity"]
                profit = result["profit"]
                color = WIN_COLOR if profit >= 0 else LOSS_COLOR
                self._dash_mt5_capital_lbl.config(
                    text=f"MT5: ${eq:,.2f} (P&L: ${profit:+,.2f})", fg=color)
                # Track equity history for curve
                self._equity_history.append((time.time(), eq))
                # Keep last 200 data points
                if len(self._equity_history) > 200:
                    self._equity_history = self._equity_history[-200:]
            else:
                self._dash_mt5_capital_lbl.config(text="MT5 Capital: sin conexion", fg=TEXT_SEC)

        def _bg():
            result = _fetch()
            self.root.after(0, lambda: _update(result))

        threading.Thread(target=_bg, daemon=True).start()
        self.root.after(30000, self._refresh_mt5_capital_loop)

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
            "ORO": (8, 20), "EUR/USD": (8, 18), "USD/JPY": (8, 18),
            "GBP/JPY": (9, 18), "NASDAQ": (15, 22), "S&P 500": (15, 22),
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
        scalper_state = estado.get("scalper_estado", {})
        has_main_ops = len(ops_activas) > 0
        has_scalper_ops = scalper_state.get("posiciones_abiertas", 0) > 0

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

        # Flash scalper tab
        if has_scalper_ops:
            if tick_even:
                self.notebook.tab(self._tab_scalper, text=" \U0001FA92 Scalper \u25CF ")
            else:
                self.notebook.tab(self._tab_scalper, text=" \U0001FA92 Scalper \u26A1 ")
        else:
            self.notebook.tab(self._tab_scalper, text=" \U0001FA92 Scalper ")

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
            ("entrada", "Entrada", 100), ("sl", "S/L", 100), ("tp1", "T/P 1", 100),
            ("score", "Score", 60), ("confianza", "Confianza", 80),
            ("estado", "Estado", 100),
        ]:
            self._senales_active_tree.heading(col, text=heading)
            self._senales_active_tree.column(col, width=w, anchor="center")

        self._senales_active_tree.tag_configure("COMPRA", foreground=WIN_COLOR)
        self._senales_active_tree.tag_configure("VENTA", foreground=LOSS_COLOR)
        self._senales_active_tree.tag_configure("PREMIUM", foreground=PREMIUM_COLOR)
        self._senales_active_tree.pack(fill="x", pady=(0, 5))

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

        assets_ordered = ["ORO", "EUR/USD", "USD/JPY", "GBP/JPY", "NASDAQ", "S&P 500"]
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

        # 4. Riesgo ORO
        row = self._add_trading_scale(params_grid, row, "Riesgo ORO (%):",
                                      "riesgo_oro", tc.get("riesgo_oro", 0.02),
                                      0.01, 0.05, 0.005,
                                      "Riesgo especifico para Oro")

        # 5. Modo
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
        for idx, asset in enumerate(["ORO", "EUR/USD", "USD/JPY", "GBP/JPY", "NASDAQ", "S&P 500"]):
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
        for idx, asset in enumerate(["ORO", "EUR/USD", "USD/JPY", "GBP/JPY", "NASDAQ", "S&P 500"]):
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
        self._add_conn_field(api_grid, 5, "Gemini API Key:", "GEMINI_API_KEY",
                             env.get("GEMINI_API_KEY", ""), show="*")

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
        canvas, scroll_frame = _make_scrollable(self._tab_vip)
        self._scroll_canvases[str(self._tab_vip)] = canvas

        # Section 1: Configuracion VIP
        vip_config_frame = _make_section_frame(scroll_frame, "Configuracion VIP")
        vip_config_frame.pack(fill="x", padx=10, pady=(10, 5))

        vip_grid = tk.Frame(vip_config_frame, bg=BG_PANEL)
        vip_grid.pack(fill="x")

        self._vip_entries = {}
        vip_fields = [
            ("Precio actual ($):", "vip_precio", "149"),
            ("Precio regular ($):", "vip_precio_regular", "299"),
            ("Descuento hasta:", "vip_descuento_hasta", "2026-04-01"),
            ("Wallet USDT (TRC20):", "vip_wallet", ""),
        ]
        for idx, (label, key, default) in enumerate(vip_fields):
            _make_label(vip_grid, label, fg=TEXT).grid(row=idx, column=0, sticky="w",
                                                       padx=5, pady=3)
            e = _make_entry(vip_grid, width=35)
            e.insert(0, default)
            e.grid(row=idx, column=1, sticky="w", padx=5, pady=3)
            self._vip_entries[key] = e

        # Spinboxes
        spin_frame = tk.Frame(vip_config_frame, bg=BG_PANEL)
        spin_frame.pack(fill="x", pady=5)

        _make_label(spin_frame, "Duracion suscripcion (dias):", fg=TEXT).pack(side="left", padx=5)
        self._vip_duracion_var = tk.IntVar(value=7)
        tk.Spinbox(spin_frame, from_=1, to=365, textvariable=self._vip_duracion_var, width=6,
                   bg=BG_INPUT, fg=TEXT, buttonbackground=BG_INPUT,
                   insertbackground=TEXT, font=("Segoe UI", 10),
                   relief="flat").pack(side="left", padx=5)

        _make_label(spin_frame, "Dias de prueba gratis:", fg=TEXT).pack(side="left", padx=(20, 5))
        self._vip_trial_var = tk.IntVar(value=7)
        tk.Spinbox(spin_frame, from_=1, to=30, textvariable=self._vip_trial_var, width=6,
                   bg=BG_INPUT, fg=TEXT, buttonbackground=BG_INPUT,
                   insertbackground=TEXT, font=("Segoe UI", 10),
                   relief="flat").pack(side="left", padx=5)

        _make_button(vip_config_frame, "Guardar Config VIP", self._save_vip_config,
                     bg=ACCENT, fg="#000000").pack(anchor="w", pady=(5, 0))

        # Section 2: Suscriptores VIP Activos
        subs_frame = _make_section_frame(scroll_frame, "Suscriptores VIP Activos")
        subs_frame.pack(fill="x", padx=10, pady=5)

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

        # Section 3: Codigos de Invitacion
        codes_frame = _make_section_frame(scroll_frame, "Codigos de Invitacion")
        codes_frame.pack(fill="x", padx=10, pady=5)

        code_cols = ("codigo", "creado_por", "dias", "usos", "max_usos")
        self._vip_codes_tree = ttk.Treeview(codes_frame, columns=code_cols,
                                            show="headings", height=4)
        for col, heading, w in [
            ("codigo", "Codigo", 180), ("creado_por", "Creado por", 120),
            ("dias", "Dias", 60), ("usos", "Usos", 60), ("max_usos", "Max Usos", 80),
        ]:
            self._vip_codes_tree.heading(col, text=heading)
            self._vip_codes_tree.column(col, width=w, anchor="center")

        self._vip_codes_tree.pack(fill="x", pady=(0, 5))

        # Section 4: Acciones Rapidas
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

        _make_label(form, "Dias de VIP:", fg=TEXT).pack(anchor="w")
        days_var = tk.IntVar(value=7)
        tk.Spinbox(form, from_=1, to=365, textvariable=days_var, width=8,
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
            estado = load_estado()
            subs = estado.get("suscripciones_vip", {})
            now = datetime.now()
            subs[uid] = {
                "nombre": name,
                "username": "",
                "inicio": now.isoformat(),
                "expira": (now + timedelta(days=days)).isoformat(),
                "aviso_enviado": False,
                "monto_pagado": 0,
                "tx_id": "manual_launcher",
                "invite_link": "",
                "es_trial": False,
                "entrada_confirmada": True,
            }
            estado["suscripciones_vip"] = subs
            save_estado(estado)
            self._estado_mtime = 0  # Force reload
            messagebox.showinfo("VIP", f"VIP otorgado a {name} ({uid}) por {days} dias.")
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
            expira_str = info.get("expira", "")
            monto = info.get("monto_pagado", 0)
            monto_str = f"${monto}" if monto else "Trial"

            dias_rest = "--"
            try:
                expira_dt = datetime.fromisoformat(expira_str)
                delta = expira_dt - now
                if expira_dt < now:
                    estado_txt = "Expirado"
                    tag = "expirado"
                    dias_rest = f"{delta.days}d"
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
                estado_txt = "--"
                tag = "neutral"
                expira_show = expira_str[:10] if expira_str else "--"

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

        self._log_filter = tk.StringVar(value="Todos")
        for level in ["Todos", "INFO", "WARNING", "ERROR"]:
            rb = tk.Radiobutton(header, text=level, variable=self._log_filter, value=level,
                                command=self._refresh_logs, bg=BG_MAIN, fg=TEXT,
                                selectcolor=BG_INPUT, activebackground=BG_MAIN,
                                activeforeground=ACCENT, font=("Segoe UI", 9))
            rb.pack(side="left", padx=5)

        _make_button(header, "Abrir carpeta Logs", self._open_logs_folder,
                     bg=BG_INPUT, fg=TEXT).pack(side="right", padx=5)
        _make_button(header, "Limpiar", self._clear_log_view,
                     bg=BG_INPUT, fg=TEXT).pack(side="right", padx=5)
        _make_button(header, "Actualizar", self._refresh_logs,
                     bg=BG_INPUT, fg=TEXT).pack(side="right", padx=5)

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

        # Color tags
        self._log_text.tag_configure("INFO", foreground=TEXT)
        self._log_text.tag_configure("WARNING", foreground=WARN)
        self._log_text.tag_configure("ERROR", foreground=ERR)
        self._log_text.tag_configure("CRITICAL", foreground="#ff0000")
        self._log_text.tag_configure("SUCCESS", foreground=WIN_COLOR)
        self._log_text.tag_configure("SIGNAL", foreground="#FFD700")

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
        if not os.path.exists(LOG_FILE):
            return
        try:
            with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except Exception:
            return

        # Take last 300 lines
        lines = lines[-300:]

        # Filter
        level_filter = self._log_filter.get()
        if level_filter != "Todos":
            filtered = []
            for line in lines:
                upper = line.upper()
                if level_filter.upper() in upper:
                    filtered.append(line)
            lines = filtered

        self._log_text.config(state="normal")
        self._log_text.delete("1.0", "end")

        for line in lines:
            tag = "INFO"
            upper = line.upper()
            if "ERROR" in upper or "TRACEBACK" in upper or "EXCEPTION" in upper:
                tag = "ERROR"
            elif "WARNING" in upper or "WARN" in upper:
                tag = "WARNING"
            elif "CRITICAL" in upper:
                tag = "CRITICAL"
            elif "WIN" in upper or "TP1" in upper or "TP2" in upper:
                tag = "SUCCESS"
            elif "SIGNAL" in upper or "SENAL" in upper or "COMPRA" in upper or "VENTA" in upper:
                tag = "SIGNAL"
            self._log_text.insert("end", line, tag)

        self._log_text.config(state="disabled")
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

    # ============================================================
    #  TAB: SCALPER SILENCIOSO
    # ============================================================
    def _build_scalper(self):
        canvas, scroll_frame = _make_scrollable(self._tab_scalper)
        self._scroll_canvases[str(self._tab_scalper)] = canvas

        # Header
        header = tk.Frame(scroll_frame, bg=BG_MAIN)
        header.pack(fill="x", padx=10, pady=(10, 5))
        _make_button(header, "Actualizar", self._refresh_scalper,
                     bg=BG_INPUT, fg=TEXT).pack(side="left", padx=(0, 10))
        self._scalper_status_lbl = _make_label(header, "Estado: --", fg=TEXT_SEC,
                                                font=("Segoe UI", 9))
        self._scalper_status_lbl.pack(side="left", padx=10)

        # Scalper P&L separate display
        self._sc_pnl_header = _make_label(header, "Scalper P&L: --", fg=ACCENT_BRIGHT,
                                           font=("Segoe UI", 10, "bold"))
        self._sc_pnl_header.pack(side="right", padx=10)

        # === Seccion 1: Estado del Scalper ===
        estado_frame = _make_section_frame(scroll_frame, "Scalper Silencioso - BB+RSI Mean Reversion M5")
        estado_frame.pack(fill="x", padx=10, pady=5)

        estado_grid = tk.Frame(estado_frame, bg=BG_PANEL)
        estado_grid.pack(fill="x")
        estado_grid.columnconfigure(1, weight=1)
        estado_grid.columnconfigure(3, weight=1)

        scalper_left = [
            ("Estado:", "_sc_estado"),
            ("Estrategia:", "_sc_estrategia"),
            ("Trades Hoy:", "_sc_trades_hoy"),
            ("Posiciones Abiertas:", "_sc_posiciones"),
        ]
        scalper_right = [
            ("P&L Scalper Hoy:", "_sc_pnl_hoy"),
            ("Perdidas Seguidas:", "_sc_racha"),
            ("Riesgo/Trade:", "_sc_riesgo"),
            ("Max Loss Diario:", "_sc_max_loss"),
        ]
        for i, (lbl_text, attr) in enumerate(scalper_left):
            _make_label(estado_grid, lbl_text, fg=TEXT_SEC, font=("Segoe UI", 10)).grid(
                row=i, column=0, sticky="w", padx=(0, 8), pady=2)
            val = _make_label(estado_grid, "--", fg=TEXT, font=("Segoe UI", 10, "bold"))
            val.grid(row=i, column=1, sticky="w", pady=2)
            setattr(self, attr, val)
        for i, (lbl_text, attr) in enumerate(scalper_right):
            _make_label(estado_grid, lbl_text, fg=TEXT_SEC, font=("Segoe UI", 10)).grid(
                row=i, column=2, sticky="w", padx=(30, 8), pady=2)
            val = _make_label(estado_grid, "--", fg=TEXT, font=("Segoe UI", 10, "bold"))
            val.grid(row=i, column=3, sticky="w", pady=2)
            setattr(self, attr, val)

        # === Seccion 2: Activos del Scalper (all 7) ===
        activos_frame = _make_section_frame(scroll_frame, "Activos Scalping (7 pares)")
        activos_frame.pack(fill="x", padx=10, pady=5)

        cols_sc = ("activo", "mt5", "horario", "spread_max", "estado")
        self._sc_tree_activos = ttk.Treeview(activos_frame, columns=cols_sc, show="headings", height=7)
        self._sc_tree_activos.heading("activo", text="Activo")
        self._sc_tree_activos.heading("mt5", text="Simbolo MT5")
        self._sc_tree_activos.heading("horario", text="Horario (Andorra)")
        self._sc_tree_activos.heading("spread_max", text="Spread Max")
        self._sc_tree_activos.heading("estado", text="Estado")

        self._sc_tree_activos.column("activo", width=140, anchor="w")
        self._sc_tree_activos.column("mt5", width=120, anchor="center")
        self._sc_tree_activos.column("horario", width=140, anchor="center")
        self._sc_tree_activos.column("spread_max", width=100, anchor="center")
        self._sc_tree_activos.column("estado", width=120, anchor="center")

        self._sc_tree_activos.tag_configure("activo", foreground=WIN_COLOR)
        self._sc_tree_activos.tag_configure("inactivo", foreground=TEXT_SEC)
        self._sc_tree_activos.tag_configure("alt_activo", foreground=WIN_COLOR, background=BG_ROW_ALT)
        self._sc_tree_activos.tag_configure("alt_inactivo", foreground=TEXT_SEC, background=BG_ROW_ALT)

        # All 7 scalper activos
        activos_data = [
            ("EUR/USD", "EURUSD", "09:00 - 18:00", "20", ""),
            ("GBP/USD", "GBPUSD", "09:00 - 18:00", "25", ""),
            ("NASDAQ (US100)", "US100Cash", "15:30 - 22:00", "500", ""),
            ("ORO (XAUUSD)", "GOLD", "09:00 - 19:00", "45", ""),
            ("AUD/CAD", "AUDCAD", "09:00 - 18:00", "25", ""),
            ("EUR/CHF", "EURCHF", "09:00 - 18:00", "20", ""),
            ("USD/CAD", "USDCAD", "09:00 - 18:00", "22", ""),
        ]
        for idx, row in enumerate(activos_data):
            tag_prefix = "alt_" if idx % 2 == 1 else ""
            self._sc_tree_activos.insert("", "end", values=row, tags=(f"{tag_prefix}inactivo",))

        self._sc_tree_activos.pack(fill="x", pady=(0, 5))

        # === Seccion 3: Posiciones Abiertas del Scalper ===
        pos_frame = _make_section_frame(scroll_frame, "Posiciones Scalper Abiertas")
        pos_frame.pack(fill="x", padx=10, pady=5)

        cols_pos = ("ticket", "simbolo", "tipo", "entrada", "sl", "tp", "profit", "tiempo")
        self._sc_tree_pos = ttk.Treeview(pos_frame, columns=cols_pos, show="headings", height=5)
        self._sc_tree_pos.heading("ticket", text="Ticket")
        self._sc_tree_pos.heading("simbolo", text="Simbolo")
        self._sc_tree_pos.heading("tipo", text="Tipo")
        self._sc_tree_pos.heading("entrada", text="Entrada")
        self._sc_tree_pos.heading("sl", text="SL")
        self._sc_tree_pos.heading("tp", text="TP")
        self._sc_tree_pos.heading("profit", text="Profit")
        self._sc_tree_pos.heading("tiempo", text="Tiempo")

        self._sc_tree_pos.column("ticket", width=80, anchor="center")
        self._sc_tree_pos.column("simbolo", width=100, anchor="center")
        self._sc_tree_pos.column("tipo", width=60, anchor="center")
        self._sc_tree_pos.column("entrada", width=100, anchor="center")
        self._sc_tree_pos.column("sl", width=90, anchor="center")
        self._sc_tree_pos.column("tp", width=90, anchor="center")
        self._sc_tree_pos.column("profit", width=80, anchor="center")
        self._sc_tree_pos.column("tiempo", width=100, anchor="center")

        self._sc_tree_pos.tag_configure("profit_pos", foreground=WIN_COLOR)
        self._sc_tree_pos.tag_configure("profit_neg", foreground=LOSS_COLOR)

        self._sc_tree_pos.pack(fill="x", pady=(0, 5))

        # === Seccion 4: Historial Scalper Hoy (color-coded) ===
        hist_frame = _make_section_frame(scroll_frame, "Historial Scalper (Hoy)")
        hist_frame.pack(fill="x", padx=10, pady=(5, 15))

        self._sc_log_text = tk.Text(
            hist_frame, bg=BG_INPUT, fg=TEXT, font=("Consolas", 9),
            wrap="word", height=12, state="disabled", insertbackground=TEXT,
            highlightthickness=0,
        )
        self._sc_log_text.tag_configure("win", foreground=WIN_COLOR)
        self._sc_log_text.tag_configure("loss", foreground=LOSS_COLOR)
        self._sc_log_text.tag_configure("header", foreground=ACCENT_BRIGHT, font=("Consolas", 9, "bold"))
        self._sc_log_text.pack(fill="both", expand=True, pady=(0, 5))

        # Refresh inicial
        self._refresh_scalper()

    def _refresh_scalper(self):
        """Actualiza la pestana Scalper. MT5 I/O en background thread."""
        self._sc_estrategia.config(text="BB(20,2) + RSI(7) Mean Rev. M5")
        self._sc_riesgo.config(text="0.5% por trade")
        self._sc_max_loss.config(text="3% diario")

        # Check if scalper is paused (bot saves 'scalper_activo' in estado.json)
        estado = self._get_estado()
        _scalper_on = estado.get("scalper_activo", True)
        if not _scalper_on:
            self._scalper_paused = True
            self._btn_scalper_toggle.config(text="\u25B6 Play Scalper", bg="#238636")
        else:
            self._scalper_paused = False
            self._btn_scalper_toggle.config(text="\u23F8 Pausar Scalper", bg="#6e40c9")

        _bot_running = self.bot.is_running if hasattr(self, 'bot') else False
        if not _bot_running:
            self._sc_estado.config(text="Bot detenido", fg=ERR)
            for lbl in (self._sc_trades_hoy, self._sc_posiciones, self._sc_pnl_hoy, self._sc_racha):
                lbl.config(text="--")
            self._sc_pnl_header.config(text="Scalper P&L: Bot OFF", fg=TEXT_SEC)
            return

        if self._scalper_paused:
            self._sc_estado.config(text="PAUSADO", fg=WARN)
        else:
            self._sc_estado.config(text="Activo", fg=WIN_COLOR)

        def _mt5_fetch():
            """Lee datos de MT5 en background y devuelve resultados."""
            try:
                import MetaTrader5 as mt5
                if not mt5.initialize():
                    return None
                positions = mt5.positions_get()
                scalper_pos = [p for p in (positions or []) if p.magic == 20260318]
                now = datetime.now()
                from_dt = datetime(now.year, now.month, now.day)
                to_dt = now + timedelta(hours=1)
                deals = mt5.history_deals_get(from_dt, to_dt)
                sc_deals = [d for d in (deals or []) if d.magic == 20260318 and d.entry == 1]
                # Preparar datos serializados (no objetos MT5 crudos)
                pos_data = []
                total_profit = 0
                for p in scalper_pos:
                    profit = p.profit
                    total_profit += profit
                    mins = int((now - datetime.fromtimestamp(p.time)).total_seconds() / 60)
                    pos_data.append({
                        'ticket': p.ticket, 'symbol': p.symbol,
                        'tipo': "BUY" if p.type == 0 else "SELL",
                        'open': f"{p.price_open:.5g}", 'sl': f"{p.sl:.5g}", 'tp': f"{p.tp:.5g}",
                        'profit': profit, 'mins': mins
                    })
                deal_data = []
                deal_profit_total = 0
                wins = 0
                losses = 0
                for d in sc_deals[-30:]:
                    deal_data.append({
                        'time': datetime.fromtimestamp(d.time).strftime("%H:%M"),
                        'symbol': d.symbol,
                        'tipo': "BUY" if d.type == 0 else "SELL",
                        'volume': d.volume, 'profit': d.profit
                    })
                    deal_profit_total += d.profit
                    if d.profit >= 0:
                        wins += 1
                    else:
                        losses += 1
                return {'pos': pos_data, 'deals': deal_data,
                        'total_profit': total_profit, 'deal_profit': deal_profit_total,
                        'n_pos': len(scalper_pos),
                        'n_deals': len(sc_deals), 'now': now,
                        'wins': wins, 'losses': losses}
            except ImportError:
                return 'no_mt5'
            except Exception as e:
                return f'error:{e}'

        def _update_gui(result):
            """Actualiza widgets en el main thread."""
            try:
                if result == 'no_mt5':
                    self._sc_posiciones.config(text="MT5 no disponible")
                    self._sc_pnl_header.config(text="Scalper P&L: MT5 N/A", fg=TEXT_SEC)
                    return
                if isinstance(result, str) and result.startswith('error:'):
                    self._sc_posiciones.config(text=result)
                    return
                if result is None:
                    self._sc_posiciones.config(text="MT5 sin conexion")
                    self._sc_pnl_header.config(text="Scalper P&L: sin conexion", fg=TEXT_SEC)
                    return

                now = result['now']
                hora = now.hour
                self._scalper_status_lbl.config(text=f"Actualizado: {now.strftime('%H:%M:%S')}")
                self._sc_posiciones.config(text=str(result['n_pos']))
                self._sc_trades_hoy.config(text=f"{result['n_deals']} ({result['wins']}W / {result['losses']}L)")

                # Tabla posiciones
                for item in self._sc_tree_pos.get_children():
                    self._sc_tree_pos.delete(item)
                for p in result['pos']:
                    tag = "profit_pos" if p['profit'] >= 0 else "profit_neg"
                    self._sc_tree_pos.insert("", "end", values=(
                        p['ticket'], p['symbol'], p['tipo'],
                        p['open'], p['sl'], p['tp'],
                        f"${p['profit']:.2f}", f"{p['mins']}m"
                    ), tags=(tag,))

                # P&L (separated from main bot)
                tp = result['total_profit']
                dp = result['deal_profit']
                total_sc_pnl = tp + dp
                self._sc_pnl_hoy.config(text=f"${total_sc_pnl:.2f}",
                                         fg=WIN_COLOR if total_sc_pnl >= 0 else LOSS_COLOR)
                pnl_color = WIN_COLOR if total_sc_pnl >= 0 else LOSS_COLOR
                self._sc_pnl_header.config(
                    text=f"Scalper P&L: ${total_sc_pnl:+,.2f} ({result['wins']}W/{result['losses']}L)",
                    fg=pnl_color)

                # Color-coded historial
                self._sc_log_text.config(state="normal")
                self._sc_log_text.delete("1.0", "end")
                if result['deals']:
                    self._sc_log_text.insert("end",
                        f"{'Hora':<8} {'Simbolo':<12} {'Tipo':<6} {'Vol':<6} {'Profit':>10}\n", "header")
                    self._sc_log_text.insert("end", "-" * 50 + "\n", "header")
                for d in result['deals']:
                    ps = f"${d['profit']:+.2f}"
                    tag = "win" if d['profit'] >= 0 else "loss"
                    self._sc_log_text.insert("end",
                        f"[{d['time']}] {d['symbol']:<12} {d['tipo']:<6} {d['volume']:<6.2f} {ps:>10}\n", tag)
                self._sc_log_text.config(state="disabled")

                # Estado activos por hora (update all 7)
                items = self._sc_tree_activos.get_children()
                for idx, item in enumerate(items):
                    vals = list(self._sc_tree_activos.item(item, "values"))
                    horario = vals[2]
                    tag_prefix = "alt_" if idx % 2 == 1 else ""
                    try:
                        h_s = int(horario.split("-")[0].strip().split(":")[0])
                        h_e = int(horario.split("-")[1].strip().split(":")[0])
                        if h_s <= hora < h_e and now.weekday() < 5:
                            vals[4] = "ACTIVO"
                            self._sc_tree_activos.item(item, values=vals,
                                                        tags=(f"{tag_prefix}activo",))
                        else:
                            vals[4] = "Fuera horario"
                            self._sc_tree_activos.item(item, values=vals,
                                                        tags=(f"{tag_prefix}inactivo",))
                    except Exception:
                        pass
                self._sc_racha.config(text="0")
            except Exception as e:
                self._scalper_status_lbl.config(text=f"Error: {e}")

        def _bg_worker():
            result = _mt5_fetch()
            self.root.after(0, lambda: _update_gui(result))

        threading.Thread(target=_bg_worker, daemon=True).start()

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
            self._restart_pending = False
            if self.bot.is_running:
                _log(f"Bot reiniciado OK — PID={self.bot.pid}")
            else:
                _log("Bot reiniciado (verificar estado)")

    def _toggle_autostart(self):
        self.config["autostart_bot"] = self._autostart_var.get()
        save_config(self.config)

    # ============================================================
    #  UPDATE LOOP
    # ============================================================
    def _update_loop(self):
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

            # Analisis (every 10s) — solo si bot activo
            if _bot_on and self._tick_count % 5 == 0:
                try:
                    self._refresh_analisis()
                except Exception:
                    pass

            # Scalper (every 10s) — solo si bot activo
            if _bot_on and self._tick_count % 5 == 0:
                try:
                    self._refresh_scalper()
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
            self._dash_estado_lbl.config(text="Estado: Ejecutando", fg=WIN_COLOR)
            self._dash_pid_lbl.config(text=f"PID: {self.bot.pid}")
            self._dash_uptime_lbl.config(text=f"Uptime: {self.bot.uptime_str()}")
        else:
            self._dash_estado_lbl.config(text="Estado: Detenido", fg=ERR)
            self._dash_pid_lbl.config(text="PID: --")
            self._dash_uptime_lbl.config(text="Uptime: --")

        self._dash_restart_lbl.config(text=f"Reinicios: {self.bot.restart_count}")

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

        # Update window title with P&L info
        self.root.title(f"BuySell365 Pro | ${capital:.0f} | {pips_today:+.1f} pips hoy | {wins_today}W/{losses_today}L")

        self._dash_winrate.config(text=f"{win_rate:.1f}%",
                                  fg=WIN_COLOR if win_rate >= 50 else ERR)
        self._dash_senales_hoy.config(text=f"{total_today} ({wins_today}W / {losses_today}L)")
        self._dash_capital.config(text=f"${capital:,.2f}")
        self._dash_modo.config(text=modo.capitalize())

        if pips_today >= 0:
            self._dash_ganancia.config(text=f"+{pips_today:.1f} pips", fg=WIN_COLOR)
        else:
            self._dash_ganancia.config(text=f"{pips_today:.1f} pips", fg=ERR)

        # Drawdown: worst peak-to-trough in pips today
        pips_loss_today = sum(op.get("pips", 0) for op in today_ops
                             if op.get("resultado") == "LOSS")
        dd = abs(pips_loss_today) if pips_loss_today < 0 else 0
        self._dash_drawdown.config(text=f"{dd:.1f} pips", fg=ERR)

        self._dash_escaneo.config(text=f"{self._scan_countdown}s")

        # Auto-trading from estado or env
        auto_trading = estado.get("mt5_pausado", False)
        env = load_env()
        auto_t = env.get("AUTO_TRADING", "True")
        if auto_trading:
            self._dash_autotrading.config(text="PAUSADO", fg=WARN)
        elif auto_t.lower() == "true":
            self._dash_autotrading.config(text="SI", fg=WIN_COLOR)
        else:
            self._dash_autotrading.config(text="NO", fg=ERR)

        # Connections
        self._update_connections(estado)

        # Performance by asset
        self._update_rendimiento(historial)

        # Traffic lights
        try:
            self._update_traffic_lights(estado)
        except Exception:
            pass

        # Performance panel (win rate by period, best/worst trade)
        try:
            self._update_performance_panel(historial)
        except Exception:
            pass

        # P&L chart - build cumulative pips from today's ops
        try:
            pnl_points = []
            cumulative = 0.0
            for op in today_ops:
                cumulative += op.get("pips", 0)
                label = op.get("hora_salida", op.get("hora", ""))
                pnl_points.append((label, cumulative))
            if not pnl_points:
                pnl_points = [("inicio", 0)]
            self._draw_pnl_chart(pnl_points)
        except Exception:
            pass

        # Equity curve
        try:
            self._draw_equity_curve()
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

        # Status bar
        active_count = len(estado.get("operaciones_activas", {}))
        self._status_bar.config(
            text=f"BuySell365 Pro v5.0 | Bot: {'ON' if self.bot.is_running else 'OFF'} | "
                 f"Activas: {active_count} | Capital: ${capital:,.2f} | "
                 f"{datetime.now().strftime('%H:%M:%S')}"
        )

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

        # Web
        web_url = env.get("WEB_URL", "")
        if web_url and self.bot.is_running:
            self._conn_web.config(text="Web Sync  \u25cf Activo", fg=WIN_COLOR)
        elif web_url:
            self._conn_web.config(text="Web Sync  \u25cf Configurado", fg=WARN)
        else:
            self._conn_web.config(text="Web Sync  \u25cf Sin configurar", fg=ERR)

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
        estado = self._get_estado()

        # Update countdown label
        self._senales_escaneo_lbl.config(text=f"Proximo escaneo: {self._scan_countdown}s")

        # Market status (basic check: Mon-Fri roughly)
        now = datetime.now()
        weekday = now.weekday()
        hour = now.hour
        if weekday < 5 and 0 <= hour < 24:
            self._senales_market_lbl.config(text="Mercado: Abierto", fg=WIN_COLOR)
        else:
            self._senales_market_lbl.config(text="Mercado: Cerrado", fg=ERR)

        # Active signals
        self._senales_active_tree.delete(*self._senales_active_tree.get_children())
        ops_activas = estado.get("operaciones_activas", {})
        active_count = 0

        for key, op in ops_activas.items():
            nombre = _normalize_asset(op.get("nombre", op.get("ticker", key)))
            tipo = op.get("tipo", "--")
            entrada_raw = op.get("entrada", "--")
            sl_raw = op.get("stop_loss", op.get("sl", "--"))
            tp1_raw = op.get("tp1", op.get("take_profit", "--"))
            # Formatear decimales según activo
            ticker_raw = op.get("ticker", "")
            if "JPY" in str(ticker_raw).upper() or "JPY" in str(nombre).upper():
                _dec = 3  # JPY pairs: 3 decimals
            elif "GC" in str(ticker_raw) or "ORO" in str(nombre).upper():
                _dec = 2
            elif "NQ" in str(ticker_raw) or "ES" in str(ticker_raw) or "NASDAQ" in str(nombre).upper() or "S&P" in str(nombre).upper():
                _dec = 2
            else:
                _dec = 5  # EUR/USD and other forex
            entrada = f"{entrada_raw:.{_dec}f}" if isinstance(entrada_raw, (int, float)) else str(entrada_raw)
            sl = f"{sl_raw:.{_dec}f}" if isinstance(sl_raw, (int, float)) else str(sl_raw)
            tp1 = f"{tp1_raw:.{_dec}f}" if isinstance(tp1_raw, (int, float)) else str(tp1_raw)
            score = op.get("score", "--")
            confianza = op.get("confianza", op.get("confianza_multi_ia", "--"))
            if isinstance(confianza, (int, float)):
                confianza = f"{confianza}%"
            hora = op.get("hora", op.get("hora_entrada", "--"))
            estado_op = op.get("estado", "Activa")

            # Determine tag
            tag = tipo.upper() if tipo.upper() in ("COMPRA", "VENTA") else "COMPRA"
            if isinstance(score, (int, float)) and score >= 4:
                tag = "PREMIUM"

            self._senales_active_tree.insert("", "end",
                                             values=(hora, nombre, tipo, entrada, sl, tp1,
                                                     score, confianza, estado_op),
                                             tags=(tag,))
            active_count += 1

        # Closed signals (today)
        self._senales_closed_tree.delete(*self._senales_closed_tree.get_children())
        historial = estado.get("historial_operaciones", [])
        today_str = datetime.now().strftime("%d/%m/%Y")

        today_ops = [op for op in historial if op.get("fecha") == today_str]
        wins_today = 0
        losses_today = 0
        pips_today = 0.0

        for op in today_ops:
            nombre = _normalize_asset(op.get("nombre", ""))
            tipo = op.get("tipo", "--")
            entrada = op.get("entrada", "--")
            salida = op.get("salida", "--")
            pips = op.get("pips", 0)
            resultado = op.get("resultado", "--")
            duracion = op.get("duracion_min", "--")
            hora = op.get("hora_salida", op.get("hora", "--"))

            if isinstance(duracion, (int, float)):
                duracion = f"{duracion:.0f}m"

            tag = "WIN" if resultado == "WIN" else "LOSS"
            if resultado == "WIN":
                wins_today += 1
            elif resultado == "LOSS":
                losses_today += 1
            pips_today += pips

            self._senales_closed_tree.insert("", "end",
                                             values=(hora, nombre, tipo, entrada, salida,
                                                     f"{pips:+.1f}", resultado, duracion),
                                             tags=(tag,))

        # Summary
        total_today = wins_today + losses_today
        self._senales_summary.config(
            text=f"Activas: {active_count} | Hoy: {wins_today}W / {losses_today}L | "
                 f"Pips hoy: {pips_today:+.1f}"
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
    def run(self):
        self.root.mainloop()


# ============================================================
#  MAIN ENTRY POINT
# ============================================================
def _single_instance_check():
    """Prevent multiple instances using Windows Mutex (atomic, no race condition)."""
    import ctypes
    import ctypes.wintypes
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    _launcher_mutex = kernel32.CreateMutexW(None, True, "BuySell365_Launcher_Mutex")
    last_err = ctypes.get_last_error()
    if last_err == 183:  # ERROR_ALREADY_EXISTS
        kernel32.CloseHandle(_launcher_mutex)
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showwarning(
            "BuySell365 Pro",
            "BuySell365 Pro ya esta ejecutandose.\n\n"
            "Cierra la instancia actual antes de abrir otra."
        )
        root.destroy()
        return False
    # Store mutex handle globally so it persists for the process lifetime
    _single_instance_check._mutex_handle = _launcher_mutex
    return True


def main():
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

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
BG_MAIN = "#0d1117"
BG_PANEL = "#161b22"
BG_INPUT = "#21262d"
TEXT = "#e6edf3"
TEXT_SEC = "#8b949e"
ACCENT = "#00d2d3"
WARN = "#f0ad4e"
ERR = "#ff6b6b"
WIN_COLOR = "#00d2d3"
LOSS_COLOR = "#ff6b6b"

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
        try:
            # Try graceful shutdown first (CTRL_BREAK → SIGBREAK on Windows)
            try:
                import signal as _sig
                if os.name == 'nt':
                    os.kill(self._proc.pid, _sig.CTRL_BREAK_EVENT)
                else:
                    self._proc.send_signal(_sig.SIGTERM)
                self._proc.wait(timeout=15)
                _log("Bot detenido gracefully (estado guardado)")
            except subprocess.TimeoutExpired:
                _log("Bot no respondio al cierre graceful, forzando terminate...")
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
                    self._proc.wait(timeout=5)
            except Exception:
                # Fallback: terminate directly
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
                    self._proc.wait(timeout=5)
        except Exception as e:
            _log(f"Error deteniendo bot: {e}")
        self._running = False
        self._cleanup_pid_file()
        _log("Bot detenido")

    def restart(self):
        self.stop()
        time.sleep(2)
        self.start()
        self.restart_count += 1

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

        # Build root window
        self.root = tk.Tk()
        self.root.title("BuySell365 Pro - Consola de Control")
        self.root.geometry("1400x850")
        self.root.minsize(1100, 700)
        self.root.configure(bg=BG_MAIN)

        # Try to set icon
        try:
            if os.path.exists(ICON_PATH):
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

        self.notebook.add(self._tab_dashboard, text="  Dashboard  ")
        self.notebook.add(self._tab_senales, text="  Senales  ")
        self.notebook.add(self._tab_scalper, text="  Scalper  ")
        self.notebook.add(self._tab_analisis, text="  Analisis  ")
        self.notebook.add(self._tab_trading, text="  Trading Config  ")
        self.notebook.add(self._tab_conexiones, text="  Conexiones  ")
        self.notebook.add(self._tab_vip, text="  VIP  ")
        self.notebook.add(self._tab_logs, text="  Logs  ")
        self.notebook.add(self._tab_web, text="  Web  ")

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

        # Mousewheel binding
        self.root.bind_all("<MouseWheel>", self._on_mousewheel)
        self.root.bind_all("<Button-4>", self._on_mousewheel_linux)
        self.root.bind_all("<Button-5>", self._on_mousewheel_linux)

        # Auto-start bot if configured
        if self.config.get("autostart_bot", False):
            self.root.after(1000, self._auto_start_bot)

        # Status bar
        self._status_bar = tk.Label(
            self.root, text="BuySell365 Pro v5.0 - Consola de Control",
            bg="#0a0e14", fg=TEXT_SEC, font=("Segoe UI", 9), anchor="w", padx=8
        )
        self._status_bar.pack(side="bottom", fill="x")

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
                        font=("Segoe UI", 10, "bold"), padding=[14, 6])
        style.map("TNotebook.Tab",
                  background=[("selected", BG_PANEL)],
                  foreground=[("selected", ACCENT)])

        style.configure("Treeview", background=BG_PANEL, foreground=TEXT,
                        fieldbackground=BG_PANEL, borderwidth=0,
                        font=("Segoe UI", 10), rowheight=26)
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

        # === Barra superior con botón Actualizar ===
        dash_header = tk.Frame(scroll_frame, bg=BG_MAIN)
        dash_header.pack(fill="x", padx=10, pady=(10, 0))
        _make_button(dash_header, "Actualizar", lambda: self._update_dashboard(),
                     bg=BG_INPUT, fg=TEXT).pack(side="left", padx=0, pady=3)
        self._dash_last_update_lbl = _make_label(dash_header, "", fg=TEXT_SEC,
                                                  font=("Segoe UI", 9))
        self._dash_last_update_lbl.pack(side="left", padx=10)

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

        self._dash_tree.pack(fill="x", pady=(0, 5))

        # Footer: Acerca de
        footer = tk.Frame(scroll_frame, bg=BG_MAIN)
        footer.pack(fill="x", padx=10, pady=(5, 15))
        _make_button(footer, "Acerca de", self._show_about, bg=BG_INPUT, fg=TEXT).pack(
            side="right")

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
        self._senales_active_tree.tag_configure("PREMIUM", foreground="#FFD700")
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

        title = tk.Label(card, text=asset_name, bg=BG_PANEL, fg=ACCENT,
                         font=("Segoe UI", 13, "bold"), anchor="w")
        title.pack(fill="x")

        price_lbl = tk.Label(card, text="Ultimo: --", bg=BG_PANEL, fg=TEXT,
                             font=("Segoe UI", 10), anchor="w")
        price_lbl.pack(fill="x", pady=(2, 4))

        # Bullish/Bearish bar
        bar_frame = tk.Frame(card, bg=BG_PANEL, height=18)
        bar_frame.pack(fill="x", pady=2)
        bar_frame.pack_propagate(False)

        bull_bar = tk.Frame(bar_frame, bg=WIN_COLOR, width=1)
        bull_bar.place(relx=0, rely=0, relwidth=0.5, relheight=1.0)
        bear_bar = tk.Frame(bar_frame, bg=LOSS_COLOR, width=1)
        bear_bar.place(relx=0.5, rely=0, relwidth=0.5, relheight=1.0)

        pct_frame = tk.Frame(card, bg=BG_PANEL)
        pct_frame.pack(fill="x")
        bull_pct = tk.Label(pct_frame, text="Alcista: --%", bg=BG_PANEL, fg=WIN_COLOR,
                            font=("Segoe UI", 9), anchor="w")
        bull_pct.pack(side="left")
        bear_pct = tk.Label(pct_frame, text="Bajista: --%", bg=BG_PANEL, fg=LOSS_COLOR,
                            font=("Segoe UI", 9), anchor="e")
        bear_pct.pack(side="right")

        trend_lbl = tk.Label(card, text="Tendencia: --", bg=BG_PANEL, fg=TEXT_SEC,
                             font=("Segoe UI", 10), anchor="w")
        trend_lbl.pack(fill="x", pady=(4, 2))

        signal_lbl = tk.Label(card, text="Sin senal activa", bg=BG_PANEL, fg=TEXT_SEC,
                              font=("Segoe UI", 9), anchor="w")
        signal_lbl.pack(fill="x")

        self._analisis_cards[asset_name] = {
            "price": price_lbl,
            "bull_bar": bull_bar,
            "bear_bar": bear_bar,
            "bull_pct": bull_pct,
            "bear_pct": bear_pct,
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
            ("P&L Hoy:", "_sc_pnl_hoy"),
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

        # === Seccion 2: Activos del Scalper ===
        activos_frame = _make_section_frame(scroll_frame, "Activos Scalping")
        activos_frame.pack(fill="x", padx=10, pady=5)

        cols_sc = ("activo", "mt5", "horario", "spread_max", "estado")
        self._sc_tree_activos = ttk.Treeview(activos_frame, columns=cols_sc, show="headings", height=4)
        self._sc_tree_activos.heading("activo", text="Activo")
        self._sc_tree_activos.heading("mt5", text="Simbolo MT5")
        self._sc_tree_activos.heading("horario", text="Horario (Andorra)")
        self._sc_tree_activos.heading("spread_max", text="Spread Max")
        self._sc_tree_activos.heading("estado", text="Estado")

        self._sc_tree_activos.column("activo", width=120, anchor="w")
        self._sc_tree_activos.column("mt5", width=120, anchor="center")
        self._sc_tree_activos.column("horario", width=140, anchor="center")
        self._sc_tree_activos.column("spread_max", width=100, anchor="center")
        self._sc_tree_activos.column("estado", width=120, anchor="center")

        self._sc_tree_activos.tag_configure("activo", foreground=WIN_COLOR)
        self._sc_tree_activos.tag_configure("inactivo", foreground=TEXT_SEC)

        # Llenar activos
        activos_data = [
            ("ORO (XAUUSD)", "GOLD", "09:00 - 19:00", "45", ""),
            ("EUR/USD", "EURUSD", "09:00 - 18:00", "20", ""),
            ("GBP/USD", "GBPUSD", "09:00 - 18:00", "25", ""),
            ("NASDAQ", "US100Cash", "15:30 - 22:00", "500", ""),
        ]
        for row in activos_data:
            self._sc_tree_activos.insert("", "end", values=row)

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

        # === Seccion 4: Historial Scalper Hoy ===
        hist_frame = _make_section_frame(scroll_frame, "Historial Scalper (Hoy)")
        hist_frame.pack(fill="x", padx=10, pady=(5, 15))

        self._sc_log_text = tk.Text(
            hist_frame, bg=BG_INPUT, fg=TEXT, font=("Consolas", 9),
            wrap="word", height=10, state="disabled", insertbackground=TEXT,
            highlightthickness=0,
        )
        self._sc_log_text.pack(fill="both", expand=True, pady=(0, 5))

        # Refresh inicial
        self._refresh_scalper()

    def _refresh_scalper(self):
        """Actualiza los datos de la pestaña Scalper leyendo estado del bot."""
        try:
            import subprocess
            # Leer estado del scalper desde el bot via API interna
            now = datetime.now()
            hora = now.hour

            # Estado basico
            self._sc_estrategia.config(text="BB(20,2) + RSI(7) Mean Rev. M5")
            self._sc_riesgo.config(text="0.5% por trade")
            self._sc_max_loss.config(text="3% diario")

            # Intentar leer del bot si esta corriendo
            _bot_running = self.bot.is_running if hasattr(self, 'bot') else False
            if _bot_running:
                self._sc_estado.config(text="Activo", fg=WIN_COLOR)
                self._scalper_status_lbl.config(text=f"Actualizado: {now.strftime('%H:%M:%S')}")

                # Leer posiciones MT5 del scalper (magic 20260318)
                try:
                    import MetaTrader5 as mt5
                    if mt5.initialize():
                        positions = mt5.positions_get()
                        scalper_pos = [p for p in (positions or []) if p.magic == 20260318]

                        self._sc_posiciones.config(text=str(len(scalper_pos)))

                        # Actualizar tabla de posiciones
                        for item in self._sc_tree_pos.get_children():
                            self._sc_tree_pos.delete(item)

                        total_profit = 0
                        for p in scalper_pos:
                            tipo = "BUY" if p.type == 0 else "SELL"
                            profit = p.profit
                            total_profit += profit
                            mins = int((now - datetime.fromtimestamp(p.time)).total_seconds() / 60)
                            tag = "profit_pos" if profit >= 0 else "profit_neg"
                            self._sc_tree_pos.insert("", "end", values=(
                                p.ticket, p.symbol, tipo,
                                f"{p.price_open:.5g}", f"{p.sl:.5g}", f"{p.tp:.5g}",
                                f"${profit:.2f}", f"{mins}m"
                            ), tags=(tag,))

                        # P&L color
                        pnl_text = f"${total_profit:.2f}"
                        pnl_color = WIN_COLOR if total_profit >= 0 else LOSS_COLOR
                        self._sc_pnl_hoy.config(text=pnl_text, fg=pnl_color)

                        # Contar trades del scalper hoy en historial
                        from_dt = datetime(now.year, now.month, now.day)
                        to_dt = now + timedelta(hours=1)
                        deals = mt5.history_deals_get(from_dt, to_dt)
                        if deals:
                            sc_deals = [d for d in deals if d.magic == 20260318 and d.entry == 1]
                            self._sc_trades_hoy.config(text=str(len(sc_deals)))

                            # Log de trades
                            self._sc_log_text.config(state="normal")
                            self._sc_log_text.delete("1.0", "end")
                            for d in sc_deals[-20:]:
                                t = datetime.fromtimestamp(d.time).strftime("%H:%M")
                                tipo = "BUY" if d.type == 0 else "SELL"
                                profit_str = f"+${d.profit:.2f}" if d.profit >= 0 else f"-${abs(d.profit):.2f}"
                                self._sc_log_text.insert("end",
                                    f"[{t}] {d.symbol} {tipo} | Vol:{d.volume} | {profit_str}\n")
                            self._sc_log_text.config(state="disabled")
                        else:
                            self._sc_trades_hoy.config(text="0")
                except ImportError:
                    self._sc_posiciones.config(text="MT5 no disponible")
                except Exception as e:
                    self._sc_posiciones.config(text=f"Error: {e}")

                # Estado de activos segun hora
                for item in self._sc_tree_activos.get_children():
                    vals = list(self._sc_tree_activos.item(item, "values"))
                    horario = vals[2]  # "09:00 - 19:00"
                    try:
                        h_start = int(horario.split("-")[0].strip().split(":")[0])
                        h_end = int(horario.split("-")[1].strip().split(":")[0])
                        if h_start <= hora < h_end:
                            vals[4] = "ACTIVO"
                            self._sc_tree_activos.item(item, values=vals, tags=("activo",))
                        else:
                            vals[4] = "Fuera horario"
                            self._sc_tree_activos.item(item, values=vals, tags=("inactivo",))
                    except Exception:
                        pass

                self._sc_racha.config(text="0")

            else:
                self._sc_estado.config(text="Bot detenido", fg=ERR)
                self._sc_trades_hoy.config(text="--")
                self._sc_posiciones.config(text="--")
                self._sc_pnl_hoy.config(text="--")
                self._sc_racha.config(text="--")

        except Exception as e:
            self._scalper_status_lbl.config(text=f"Error: {e}")

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
        threading.Thread(target=self.bot.stop, daemon=True).start()

    def _cmd_restart_bot(self):
        def do_restart():
            self.bot.restart()
        threading.Thread(target=do_restart, daemon=True).start()

    def _toggle_autostart(self):
        self.config["autostart_bot"] = self._autostart_var.get()
        save_config(self.config)

    # ============================================================
    #  UPDATE LOOP
    # ============================================================
    def _update_loop(self):
        self._tick_count = getattr(self, '_tick_count', 0) + 1

        # Always update dashboard (every 2s)
        try:
            self._update_dashboard()
        except Exception as e:
            _log(f"Error actualizando dashboard: {e}")

        # Senales (every 4s)
        if self._tick_count % 2 == 0:
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

        # Analisis (every 10s)
        if self._tick_count % 5 == 0:
            try:
                self._refresh_analisis()
            except Exception:
                pass

        # Countdown
        self._scan_countdown = max(0, self._scan_countdown - 2)
        if self._scan_countdown <= 0:
            tc = load_trading_config()
            self._scan_countdown = tc.get("intervalo_escaneo", 120)

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

        # Timestamp de última actualización
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

        for asset, s in sorted_assets:
            ops = s["ops"]
            wins = s["wins"]
            losses = s["losses"]
            pips = s["pips"]
            wp = (wins / ops * 100) if ops > 0 else 0

            tag = "positive" if pips >= 0 else "negative"
            if ops == 0:
                tag = "neutral"

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

        # Update tab title
        if active_count > 0:
            self.notebook.tab(self._tab_senales, text=f"  Senales ({active_count})  ")
        else:
            self.notebook.tab(self._tab_senales, text="  Senales  ")

    # ============================================================
    #  ANALISIS UPDATE
    # ============================================================
    def _refresh_analisis(self, *args):
        estado = self._get_estado()
        ops_activas = estado.get("operaciones_activas", {})
        historial = estado.get("historial_operaciones", [])

        self._last_analisis_time = datetime.now().strftime("%H:%M:%S")
        self._analisis_time_lbl.config(text=f"Ultima actualizacion: {self._last_analisis_time}")

        # Build per-asset analysis from recent history
        for asset_name, widgets in self._analisis_cards.items():
            # Gather ops for this asset
            asset_ops = []
            for op in historial:
                if _normalize_asset(op.get("nombre", "")) == asset_name:
                    asset_ops.append(op)
                elif _normalize_asset(op.get("ticker", "")) == asset_name:
                    asset_ops.append(op)

            recent = asset_ops[-20:] if len(asset_ops) > 20 else asset_ops

            if not recent:
                widgets["price"].config(text="Ultimo: --")
                widgets["bull_pct"].config(text="Alcista: --%")
                widgets["bear_pct"].config(text="Bajista: --%")
                widgets["bull_bar"].place(relx=0, rely=0, relwidth=0.5, relheight=1.0)
                widgets["bear_bar"].place(relx=0.5, rely=0, relwidth=0.5, relheight=1.0)
                widgets["trend"].config(text="Tendencia: --")
                widgets["signal"].config(text="Sin senal activa", fg=TEXT_SEC)
                continue

            # Last price
            last_op = recent[-1]
            last_price = last_op.get("salida", last_op.get("entrada", "--"))
            if isinstance(last_price, (int, float)):
                if asset_name in ("EUR/USD", "GBP/JPY", "USD/JPY"):
                    widgets["price"].config(text=f"Ultimo: {last_price:.5f}")
                else:
                    widgets["price"].config(text=f"Ultimo: {last_price:,.2f}")
            else:
                widgets["price"].config(text=f"Ultimo: {last_price}")

            # Bullish/bearish from recent wins
            buy_wins = sum(1 for op in recent if op.get("tipo") == "COMPRA" and op.get("resultado") == "WIN")
            sell_wins = sum(1 for op in recent if op.get("tipo") == "VENTA" and op.get("resultado") == "WIN")
            buy_total = sum(1 for op in recent if op.get("tipo") == "COMPRA")
            sell_total = sum(1 for op in recent if op.get("tipo") == "VENTA")
            total = buy_total + sell_total

            if total > 0:
                bull_pct = (buy_wins / total) * 100
                bear_pct = (sell_wins / total) * 100
                remainder = 100 - bull_pct - bear_pct
                bull_pct += remainder / 2
                bear_pct += remainder / 2
            else:
                bull_pct = 50
                bear_pct = 50

            bull_ratio = max(0.05, bull_pct / 100)
            bear_ratio = max(0.05, 1 - bull_ratio)

            widgets["bull_bar"].place(relx=0, rely=0, relwidth=bull_ratio, relheight=1.0)
            widgets["bear_bar"].place(relx=bull_ratio, rely=0, relwidth=bear_ratio, relheight=1.0)
            widgets["bull_pct"].config(text=f"Alcista: {bull_pct:.0f}%")
            widgets["bear_pct"].config(text=f"Bajista: {bear_pct:.0f}%")

            # Trend from recent pips
            recent_pips = sum(op.get("pips", 0) for op in recent[-5:])
            if recent_pips > 10:
                trend_text = "ALCISTA"
                trend_color = WIN_COLOR
            elif recent_pips < -10:
                trend_text = "BAJISTA"
                trend_color = ERR
            else:
                trend_text = "LATERAL"
                trend_color = WARN
            widgets["trend"].config(text=f"Tendencia: {trend_text}", fg=trend_color)

            # Check for active signal on this asset
            signal_text = "Sin senal activa"
            signal_color = TEXT_SEC
            for key, op in ops_activas.items():
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
    """Prevent multiple instances using a lock file with PID validation."""
    lock_file = os.path.join(BASE_DIR, ".launcher.lock")
    try:
        if os.path.exists(lock_file):
            with open(lock_file, "r") as f:
                old_pid = int(f.read().strip())
            # Check if that PID is actually BuySell365 Pro still running
            is_running = False
            try:
                import psutil
                if psutil.pid_exists(old_pid):
                    proc = psutil.Process(old_pid)
                    proc_name = proc.name().lower()
                    # Only block if it's actually our process
                    if "buysell365" in proc_name or "launcher" in proc_name or "python" in proc_name:
                        # Extra check: verify command line contains launcher
                        try:
                            cmdline = " ".join(proc.cmdline()).lower()
                            if "launcher" in cmdline or "buysell365" in cmdline:
                                is_running = True
                        except (psutil.AccessDenied, psutil.NoSuchProcess):
                            is_running = True  # Can't check cmdline, assume it's ours
            except ImportError:
                # psutil not available, use ctypes fallback
                import ctypes
                kernel32 = ctypes.windll.kernel32
                handle = kernel32.OpenProcess(0x1000, False, old_pid)
                if handle:
                    kernel32.CloseHandle(handle)
                    is_running = True
            except Exception:
                pass  # On any error, allow launch

            if is_running and old_pid != os.getpid():
                import tkinter as tk
                from tkinter import messagebox
                root = tk.Tk()
                root.withdraw()
                messagebox.showwarning(
                    "BuySell365 Pro",
                    "BuySell365 Pro ya esta ejecutandose.\n\n"
                    f"PID existente: {old_pid}\n"
                    "Cierra la instancia actual antes de abrir otra."
                )
                root.destroy()
                return False
            # PID doesn't exist or is a different program — stale lock, remove it
        # Write our PID
        with open(lock_file, "w") as f:
            f.write(str(os.getpid()))
        return True
    except Exception:
        return True  # Allow launch on any error


def _remove_lock():
    """Remove the lock file on exit."""
    lock_file = os.path.join(BASE_DIR, ".launcher.lock")
    try:
        if os.path.exists(lock_file):
            with open(lock_file, "r") as f:
                pid = int(f.read().strip())
            if pid == os.getpid():
                os.remove(lock_file)
    except Exception:
        pass


def main():
    if not _single_instance_check():
        return
    import atexit
    atexit.register(_remove_lock)
    _log("=== BuySell365 Pro Launcher iniciando ===")
    config = load_config()
    bot = BotManager()
    # Login desactivado temporalmente - acceso libre
    console = ManagementConsole(config, bot)
    console.run()
    _remove_lock()


if __name__ == "__main__":
    main()

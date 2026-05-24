"""Configuración del Web Admin Panel."""
import os
from pathlib import Path

# Ruta a la raíz del bot (carpeta padre de web_admin/)
APP_DIR = Path(__file__).resolve().parent.parent
STATE_DIR = APP_DIR / "state"
LOGS_DIR = APP_DIR / "logs"

# Credenciales admin — configurables vía .env
WEB_ADMIN_USER = os.getenv("WEB_ADMIN_USER", "admin")
WEB_ADMIN_PASSWORD = os.getenv("WEB_ADMIN_PASSWORD", "buysell365")

# Secret para sesiones Flask
SECRET_KEY = os.getenv("WEB_ADMIN_SECRET", "change-me-in-production-please-very-long-random-string-12345")

# Puerto del panel
WEB_ADMIN_PORT = int(os.getenv("WEB_ADMIN_PORT", "5001"))
WEB_ADMIN_HOST = os.getenv("WEB_ADMIN_HOST", "0.0.0.0")

# Sesión 30 días
PERMANENT_SESSION_LIFETIME = 60 * 60 * 24 * 30

# Servicio systemd (para start/stop en VPS Linux)
BOT_SERVICE_NAME = os.getenv("BOT_SERVICE_NAME", "buysell365")

# Archivos JSON críticos
JSON_FILES = {
    "whatsapp_recipients": APP_DIR / "whatsapp_recipients.json",
    "copier_open_signals": APP_DIR / "copier_open_signals.json",
    "copier_stats":        APP_DIR / "copier_stats.json",
    "historial_real":      APP_DIR / "historial_real.json",
    "gift_history":        APP_DIR / "gift_history.json",
    "gift_tracker":        APP_DIR / "gift_tracker.json",
    "llm_features_stats":  APP_DIR / "llm_features_stats.json",
    "mt5_realtime":        APP_DIR / "mt5_realtime.json",
    "launcher_config":     APP_DIR / "launcher_config.json",
    "estado":              APP_DIR / "estado.json",
}

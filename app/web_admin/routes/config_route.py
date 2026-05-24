"""Editor visual de .env — grupos lógicos."""
import os
import shutil
import time
from pathlib import Path
from flask import Blueprint, render_template, request, redirect, url_for, flash
from ..auth import login_required
from ..config import APP_DIR

config_bp = Blueprint("config", __name__)

ENV_FILE = APP_DIR / ".env"

# Agrupación lógica para mostrar bien organizado
ENV_GROUPS = {
    "📨 Telegram": ["TELEGRAM_TOKEN", "CHANNEL_ID", "GROUP_ID", "ADMIN_IDS", "TG_API_ID", "TG_API_HASH"],
    "🧠 Anthropic Claude": ["ANTHROPIC_API_KEY", "LLM_MODEL", "IA_EVAL_MODEL", "VISION_MODEL"],
    "📱 WhatsApp": ["WHATSAPP_PERSONAL_ENABLED", "TEXTMEBOT_APIKEY"],
    "📊 Render Dashboard": ["WEB_URL", "DASHBOARD_URL", "SYNC_SECRET", "API_SECRET_KEY"],
    "💰 Stripe": ["STRIPE_SECRET_KEY", "STRIPE_PUBLIC_KEY", "VIP_PRECIO_EUR"],
    "📡 Otros APIs": ["FINNHUB_KEY", "TWELVE_DATA_KEY", "GROQ_API_KEY", "HF_TOKEN", "BINANCE_API_KEY"],
    "🔒 Web Admin": ["WEB_ADMIN_USER", "WEB_ADMIN_PASSWORD", "WEB_ADMIN_PORT", "WEB_ADMIN_SECRET"],
    "⚙️ Trading config": ["AUTO_TRADING", "MT5_EXECUTION_DISABLED", "MIN_PUBLISH_PROBABILITY",
                          "ANTI_SPAM_SECONDS", "DAILY_SIGNAL_CAP_PER_SYMBOL"],
}


@config_bp.route("/")
@login_required
def index():
    env_data = _parse_env()
    return render_template("config.html", env_groups=ENV_GROUPS, env_data=env_data)


@config_bp.route("/save", methods=["POST"])
@login_required
def save():
    try:
        # Backup automático antes de cambios
        if ENV_FILE.exists():
            bak = APP_DIR / f".env.bak_webadmin_{time.strftime('%Y%m%d_%H%M%S')}"
            shutil.copy2(ENV_FILE, bak)

        env_data = _parse_env()
        for key in list(env_data.keys()):
            new_val = request.form.get(f"env_{key}")
            if new_val is not None:
                env_data[key] = new_val

        _write_env(env_data)
        flash("✅ Configuración guardada. Reinicia el bot para aplicar.", "success")
    except Exception as e:
        flash(f"❌ Error guardando: {e}", "error")
    return redirect(url_for("config.index"))


def _parse_env() -> dict:
    """Lee .env preservando solo pares KEY=VALUE."""
    out = {}
    if not ENV_FILE.exists():
        return out
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def _write_env(data: dict) -> None:
    """Reescribe .env preservando comentarios donde sea posible."""
    if not ENV_FILE.exists():
        ENV_FILE.write_text("\n".join(f"{k}={v}" for k, v in data.items()), encoding="utf-8")
        return

    # Reemplazo línea a línea preservando comentarios
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
    new_lines = []
    keys_written = set()
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            new_lines.append(line)
            continue
        if "=" in stripped:
            k = stripped.split("=", 1)[0].strip()
            if k in data:
                new_lines.append(f"{k}={data[k]}")
                keys_written.add(k)
                continue
        new_lines.append(line)
    # Añadir claves nuevas que no estaban
    for k, v in data.items():
        if k not in keys_written:
            new_lines.append(f"{k}={v}")
    ENV_FILE.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

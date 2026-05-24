"""Control del bot: start / stop / restart."""
import subprocess
import platform
from flask import Blueprint, redirect, url_for, flash, request
from ..auth import login_required
from ..config import BOT_SERVICE_NAME

control_bp = Blueprint("control", __name__)


def _systemctl(action: str) -> tuple[bool, str]:
    """Linux: usa systemctl. Windows local: no-op informativo."""
    if platform.system() != "Linux":
        return False, "Solo disponible en VPS Linux (en local usa el launcher)."
    try:
        r = subprocess.run(
            ["sudo", "systemctl", action, BOT_SERVICE_NAME],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode == 0:
            return True, f"{action} OK"
        return False, r.stderr.strip() or "Error desconocido"
    except subprocess.TimeoutExpired:
        return False, "Timeout esperando systemctl"
    except Exception as e:
        return False, str(e)


@control_bp.route("/start", methods=["POST"])
@login_required
def start():
    ok, msg = _systemctl("start")
    flash(f"{'✅' if ok else '❌'} Iniciar bot: {msg}", "success" if ok else "error")
    return redirect(request.referrer or url_for("dashboard.index"))


@control_bp.route("/stop", methods=["POST"])
@login_required
def stop():
    ok, msg = _systemctl("stop")
    flash(f"{'✅' if ok else '❌'} Detener bot: {msg}", "success" if ok else "error")
    return redirect(request.referrer or url_for("dashboard.index"))


@control_bp.route("/restart", methods=["POST"])
@login_required
def restart():
    ok, msg = _systemctl("restart")
    flash(f"{'✅' if ok else '❌'} Reiniciar bot: {msg}", "success" if ok else "error")
    return redirect(request.referrer or url_for("dashboard.index"))

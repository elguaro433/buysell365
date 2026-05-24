"""Logs viewer — lectura estática + streaming SSE."""
import os
import time
import subprocess
import platform
from flask import Blueprint, render_template, Response, request
from ..auth import login_required
from ..config import LOGS_DIR, BOT_SERVICE_NAME

logs_bp = Blueprint("logs", __name__)


@logs_bp.route("/")
@login_required
def index():
    return render_template("logs.html")


@logs_bp.route("/tail")
@login_required
def tail():
    """Devuelve las últimas N líneas de logs."""
    n = int(request.args.get("n", 200))
    lines = _read_last_lines(n)
    return Response("\n".join(lines), mimetype="text/plain")


@logs_bp.route("/stream")
@login_required
def stream():
    """Stream SSE de logs en vivo. Cliente se conecta y recibe líneas nuevas."""
    def event_stream():
        last_size = 0
        log_file = _get_log_file()
        while True:
            try:
                if log_file and os.path.exists(log_file):
                    size = os.path.getsize(log_file)
                    if size > last_size:
                        with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
                            f.seek(last_size)
                            new = f.read()
                            for line in new.splitlines():
                                if line.strip():
                                    yield f"data: {line}\n\n"
                        last_size = size
                    elif size < last_size:
                        last_size = 0  # archivo rotado
            except Exception as e:
                yield f"data: [STREAM ERROR] {e}\n\n"
            time.sleep(1.5)

    return Response(event_stream(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _get_log_file():
    """Encuentra el archivo de log más reciente."""
    if not LOGS_DIR.exists():
        return None
    log_files = sorted(LOGS_DIR.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    return str(log_files[0]) if log_files else None


def _read_last_lines(n: int = 200) -> list[str]:
    """Lee las últimas N líneas. En Linux puede leer journalctl, en Windows lee archivo."""
    if platform.system() == "Linux":
        try:
            r = subprocess.run(
                ["journalctl", "-u", BOT_SERVICE_NAME, "-n", str(n), "--no-pager"],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0 and r.stdout:
                return r.stdout.splitlines()
        except Exception:
            pass

    log_file = _get_log_file()
    if not log_file:
        return ["(sin archivo de log disponible — busca en logs/ del proyecto)"]
    try:
        with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
            return f.readlines()[-n:]
    except Exception as e:
        return [f"Error leyendo log: {e}"]

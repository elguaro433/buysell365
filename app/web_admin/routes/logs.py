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
    """Devuelve las últimas N líneas de logs.

    FIX 2026-05-25: default 200 -> 100 lineas. El usuario reportaba
    que Chrome marcaba "pagina no responde" al cargar /logs porque
    journalctl con muchos reinicios del servicio tarda 5-15s en
    juntar 200 lineas. Con 100 va mas suave; ademas _read_last_lines
    ahora prioriza archivo (rapido) sobre journalctl (lento).
    """
    n = int(request.args.get("n", 100))
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
    """Encuentra el archivo de log para mostrar en el panel.

    FIX 2026-05-26: antes ordenaba por mtime y elegia el primero, lo que en
    caso de empate (bot.log y bot_stderr.log con mtime identico) devolvia
    cualquiera. bot_stderr.log es ruido (excepciones de librerias, SSL,
    tracebacks); el usuario quiere ver bot.log (log principal del bot con
    [GENERAL], [GEN], [SISTEMA], [USUARIO]).

    Orden de preferencia explicito:
      1) bot.log (log principal)
      2) copier.log (si bot.log no existe — actividad de senales)
      3) Cualquier *.log mas reciente (fallback)
    """
    if not LOGS_DIR.exists():
        return None
    preferred = ["bot.log", "copier.log"]
    for name in preferred:
        p = LOGS_DIR / name
        if p.exists():
            return str(p)
    # Fallback: cualquier .log mas reciente, EXCLUYENDO los stderr (que son ruido)
    candidates = [p for p in LOGS_DIR.glob("*.log") if "stderr" not in p.name and "fault" not in p.name]
    if not candidates:
        return None
    log_files = sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)
    return str(log_files[0])


def _read_last_lines(n: int = 100) -> list[str]:
    """Lee las últimas N líneas.

    FIX 2026-05-25: invertido el orden — archivo primero (50-100ms),
    journalctl como fallback (5-15s con servicio reiniciado N veces).
    Antes la pagina /logs se quedaba colgada porque journalctl es lento
    cuando ha habido muchos restarts del servicio (como hoy: 10+).
    """
    # 1) Intento rapido: archivo bot.log directo (el bot escribe ahi)
    log_file = _get_log_file()
    if log_file:
        try:
            with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
            if lines:
                return [ln.rstrip("\n") for ln in lines[-n:]]
        except Exception:
            pass

    # 2) Fallback Linux: journalctl (con timeout corto)
    if platform.system() == "Linux":
        try:
            r = subprocess.run(
                ["journalctl", "-u", BOT_SERVICE_NAME, "-n", str(n), "--no-pager"],
                capture_output=True, text=True, timeout=4,
            )
            if r.returncode == 0 and r.stdout:
                return r.stdout.splitlines()
        except Exception:
            pass

    return ["(sin archivo de log disponible — busca en logs/ del proyecto)"]

"""Rutas de autenticación."""
import threading
import time

from flask import Blueprint, render_template, request, redirect, url_for, flash
from ..auth import check_credentials, do_login, do_logout

auth_bp = Blueprint("auth", __name__)

# 2026-09-19: rate limit del login. El panel está expuesto a internet y no
# tenía ningún freno: 5 fallos por IP → bloqueo 15 min. Estado en memoria
# (se reinicia con el servicio; suficiente contra fuerza bruta).
_MAX_FAILS = 5
_BLOCK_SECS = 15 * 60
_fails: dict[str, list[float]] = {}
_fails_lock = threading.Lock()


def _client_ip() -> str:
    return (request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
            or request.remote_addr or "?")


def _blocked(ip: str) -> int:
    """Segundos que faltan de bloqueo para esta IP (0 = libre)."""
    now = time.time()
    with _fails_lock:
        recent = [t for t in _fails.get(ip, []) if now - t < _BLOCK_SECS]
        _fails[ip] = recent
        if len(recent) >= _MAX_FAILS:
            return int(_BLOCK_SECS - (now - recent[0]))
    return 0


def _register_fail(ip: str) -> None:
    with _fails_lock:
        _fails.setdefault(ip, []).append(time.time())
        # no dejar crecer el dict con IPs de escáneres
        if len(_fails) > 5000:
            _fails.clear()


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        ip = _client_ip()
        wait = _blocked(ip)
        if wait:
            error = f"Demasiados intentos. Espera {wait // 60 + 1} min."
            time.sleep(1)
            return render_template("login.html", error=error), 429
        user = request.form.get("user", "").strip()
        password = request.form.get("password", "").strip()
        if check_credentials(user, password):
            with _fails_lock:
                _fails.pop(ip, None)
            do_login(user)
            nxt = request.args.get("next") or url_for("dashboard.index")
            return redirect(nxt)
        _register_fail(ip)
        time.sleep(1)  # frena herramientas automáticas sin molestar a un humano
        error = "Usuario o contraseña incorrectos."
    return render_template("login.html", error=error)


@auth_bp.route("/logout")
def logout():
    do_logout()
    return redirect(url_for("auth.login"))

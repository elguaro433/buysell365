"""Auth simple basada en sesión Flask."""
from functools import wraps
from flask import session, redirect, url_for, request, flash
from werkzeug.security import check_password_hash, generate_password_hash
from .config import WEB_ADMIN_USER, WEB_ADMIN_PASSWORD


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("auth.login", next=request.path))
        return f(*args, **kwargs)
    return wrapper


def check_credentials(user: str, password: str) -> bool:
    """Verifica usuario+password contra .env."""
    return user == WEB_ADMIN_USER and password == WEB_ADMIN_PASSWORD


def do_login(user: str) -> None:
    session.permanent = True
    session["logged_in"] = True
    session["user"] = user


def do_logout() -> None:
    session.clear()

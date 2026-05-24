"""Rutas de autenticación."""
from flask import Blueprint, render_template, request, redirect, url_for, flash
from ..auth import check_credentials, do_login, do_logout

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        user = request.form.get("user", "").strip()
        password = request.form.get("password", "").strip()
        if check_credentials(user, password):
            do_login(user)
            nxt = request.args.get("next") or url_for("dashboard.index")
            return redirect(nxt)
        error = "Usuario o contraseña incorrectos."
    return render_template("login.html", error=error)


@auth_bp.route("/logout")
def logout():
    do_logout()
    return redirect(url_for("auth.login"))

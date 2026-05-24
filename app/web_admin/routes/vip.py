"""VIP subscribers — visualización y gestión básica."""
from flask import Blueprint, render_template, request, redirect, url_for, flash
from ..auth import login_required
from .. import data_access as da

vip_bp = Blueprint("vip", __name__)


@vip_bp.route("/")
@login_required
def index():
    vips = da.get_vip_subscribers()
    return render_template("vip.html", vips=vips)


@vip_bp.route("/add", methods=["POST"])
@login_required
def add():
    user_id = request.form.get("user_id", "").strip()
    days = request.form.get("days", "30").strip()
    name = request.form.get("name", "").strip()

    if not user_id:
        flash("❌ User ID es obligatorio.", "error")
        return redirect(url_for("vip.index"))

    try:
        import time
        gifts = da.read_json("gift_history", default={})
        if not isinstance(gifts, dict):
            gifts = {}
        gifts[user_id] = {
            "name": name or user_id,
            "username": "",
            "started_at": time.strftime("%Y-%m-%d"),
            "days_remaining": int(days) if days.isdigit() else 30,
            "tier": "VIP_MANUAL",
            "active": True,
        }
        da.write_json_atomic("gift_history", gifts)
        flash(f"✅ VIP otorgado a {user_id} por {days} días.", "success")
    except Exception as e:
        flash(f"❌ Error: {e}", "error")
    return redirect(url_for("vip.index"))


@vip_bp.route("/revoke/<user_id>", methods=["POST"])
@login_required
def revoke(user_id):
    gifts = da.read_json("gift_history", default={})
    if isinstance(gifts, dict) and user_id in gifts:
        gifts[user_id]["active"] = False
        da.write_json_atomic("gift_history", gifts)
        flash(f"🚫 VIP revocado para {user_id}.", "success")
    else:
        flash("❌ Usuario no encontrado.", "error")
    return redirect(url_for("vip.index"))

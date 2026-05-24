"""Dashboard principal."""
from flask import Blueprint, render_template
from ..auth import login_required
from .. import data_access as da

dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.route("/")
@login_required
def index():
    bot_status = da.get_bot_status()
    copier_stats = da.get_copier_stats()
    llm_stats = da.get_llm_stats()
    connections = da.get_connections_status()
    recent_signals = da.get_recent_signals(limit=10)
    open_signals = da.get_open_signals()
    whatsapp_count = len(da.get_whatsapp_recipients())
    vip_count = len(da.get_vip_subscribers())

    return render_template(
        "dashboard.html",
        bot=bot_status,
        stats=copier_stats,
        llm=llm_stats,
        conns=connections,
        recent=recent_signals,
        open_count=len(open_signals),
        whatsapp_count=whatsapp_count,
        vip_count=vip_count,
    )

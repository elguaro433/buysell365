"""WhatsApp recipients — CRUD completo + test send + bulk actions."""
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from ..auth import login_required
from .. import data_access as da

whatsapp_bp = Blueprint("whatsapp", __name__)

# Eventos disponibles para suscribirse
EVENTS_AVAILABLE = [
    ("new_signal", "Nueva señal publicada"),
    ("tp_hit", "TP alcanzado"),
    ("sl_hit", "SL alcanzado"),
    ("sl_moved", "SL movido (BE/trailing)"),
    ("daily_recap", "Resumen diario 19:00"),
    ("briefing", "Briefing 07:00"),
]

# Pares predefinidos para filtros rápidos
PAIRS_PRESET = [
    "ALL", "GOLD", "XAUUSD", "BTC", "BTCUSD", "ETH", "ETHUSD",
    "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "NZDUSD", "USDCAD",
    "EURJPY", "GBPJPY", "NAS100", "US30", "US500", "GER40",
]


@whatsapp_bp.route("/")
@login_required
def index():
    recipients = da.get_whatsapp_recipients()
    # Añadir índice para edición
    for i, r in enumerate(recipients):
        r["_index"] = i
    return render_template("whatsapp.html", recipients=recipients,
                           events_available=EVENTS_AVAILABLE,
                           pairs_preset=PAIRS_PRESET)


@whatsapp_bp.route("/add", methods=["POST"])
@login_required
def add():
    data = _parse_form()
    if not data.get("name") or not data.get("phone"):
        flash("❌ Nombre y teléfono son obligatorios.", "error")
        return redirect(url_for("whatsapp.index"))

    da.add_whatsapp_recipient(data)
    flash(f"✅ Destinatario '{data['name']}' añadido.", "success")
    return redirect(url_for("whatsapp.index"))


@whatsapp_bp.route("/edit/<int:idx>", methods=["POST"])
@login_required
def edit(idx):
    data = _parse_form()
    if not data.get("name") or not data.get("phone"):
        flash("❌ Nombre y teléfono son obligatorios.", "error")
        return redirect(url_for("whatsapp.index"))

    if da.update_whatsapp_recipient(idx, data):
        flash(f"✅ Destinatario '{data['name']}' actualizado.", "success")
    else:
        flash("❌ Índice inválido.", "error")
    return redirect(url_for("whatsapp.index"))


@whatsapp_bp.route("/delete/<int:idx>", methods=["POST"])
@login_required
def delete(idx):
    recipients = da.get_whatsapp_recipients()
    if 0 <= idx < len(recipients):
        name = recipients[idx].get("name", "?")
        da.delete_whatsapp_recipient(idx)
        flash(f"🗑️ '{name}' eliminado.", "success")
    else:
        flash("❌ Índice inválido.", "error")
    return redirect(url_for("whatsapp.index"))


@whatsapp_bp.route("/toggle/<int:idx>", methods=["POST"])
@login_required
def toggle(idx):
    if da.toggle_whatsapp_recipient(idx):
        recipients = da.get_whatsapp_recipients()
        state = "activado" if recipients[idx].get("enabled") else "pausado"
        flash(f"🔄 '{recipients[idx].get('name')}' {state}.", "success")
    else:
        flash("❌ Índice inválido.", "error")
    return redirect(url_for("whatsapp.index"))


@whatsapp_bp.route("/test/<int:idx>", methods=["POST"])
@login_required
def test_send(idx):
    """Envía un mensaje de prueba real al destinatario."""
    recipients = da.get_whatsapp_recipients()
    if not (0 <= idx < len(recipients)):
        flash("❌ Índice inválido.", "error")
        return redirect(url_for("whatsapp.index"))

    r = recipients[idx]
    try:
        import requests
        import time
        phone = r["phone"].replace("+", "").replace(" ", "")
        apikey = r.get("apikey", "")
        if not apikey:
            flash(f"❌ '{r['name']}' no tiene API key configurada.", "error")
            return redirect(url_for("whatsapp.index"))

        msg = f"🧪 Mensaje de prueba desde BuySell365 Web Admin Panel a las {time.strftime('%H:%M:%S')}"
        url = f"https://api.textmebot.com/send.php?recipient=+{phone}&apikey={apikey}&text={msg}"
        resp = requests.get(url, timeout=15)
        if "Success" in resp.text or "success" in resp.text.lower():
            flash(f"✅ Mensaje de prueba enviado a '{r['name']}' ({r['phone']}).", "success")
        else:
            flash(f"⚠️ TextMeBot respondió: {resp.text[:120]}", "error")
    except Exception as e:
        flash(f"❌ Error enviando: {e}", "error")
    return redirect(url_for("whatsapp.index"))


@whatsapp_bp.route("/bulk/<action>", methods=["POST"])
@login_required
def bulk(action):
    """Bulk actions: pause_all, enable_all."""
    recipients = da.get_whatsapp_recipients()
    if action == "pause_all":
        for r in recipients:
            r["enabled"] = False
        da.save_whatsapp_recipients(recipients)
        flash(f"⏸️ {len(recipients)} destinatarios pausados.", "success")
    elif action == "enable_all":
        for r in recipients:
            r["enabled"] = True
        da.save_whatsapp_recipients(recipients)
        flash(f"✅ {len(recipients)} destinatarios activados.", "success")
    else:
        flash(f"❌ Acción desconocida: {action}", "error")
    return redirect(url_for("whatsapp.index"))


def _parse_form() -> dict:
    """Parsea el formulario de add/edit."""
    pairs_selected = request.form.getlist("filter_pair")
    pairs_custom = request.form.get("filter_pair_custom", "").strip()
    if pairs_custom:
        pairs_selected.extend([p.strip().upper() for p in pairs_custom.split(",") if p.strip()])
    pairs_str = ",".join(sorted(set(pairs_selected))) if pairs_selected else "ALL"

    events_selected = request.form.getlist("events")
    if not events_selected:
        events_selected = ["new_signal"]

    return {
        "name":            request.form.get("name", "").strip(),
        "phone":           request.form.get("phone", "").strip(),
        "apikey":          request.form.get("apikey", "").strip(),
        "backend":         request.form.get("backend", "textmebot").strip(),
        "filter_pair":     pairs_str,
        "events":          events_selected,
        "min_probability": int(request.form.get("min_probability", "0") or 0),
        "quiet_hours":     request.form.get("quiet_hours", "").strip(),
        "notes":           request.form.get("notes", "").strip(),
        "enabled":         request.form.get("enabled") == "on",
    }

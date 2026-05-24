"""Señales — paste manual + listado del día."""
from flask import Blueprint, render_template, request, redirect, url_for, flash
from ..auth import login_required
from .. import data_access as da

signals_bp = Blueprint("signals", __name__)


@signals_bp.route("/")
@login_required
def index():
    open_signals = da.get_open_signals()
    recent = da.get_recent_signals(limit=30)
    return render_template("signals.html", open_signals=open_signals, recent=recent)


@signals_bp.route("/manual", methods=["POST"])
@login_required
def manual():
    raw_text = request.form.get("signal_text", "").strip()
    preview_only = request.form.get("preview_only") == "on"

    if not raw_text:
        flash("❌ Texto de señal vacío.", "error")
        return redirect(url_for("signals.index"))

    try:
        # Reusa lógica de send_manual_signal.py si existe
        import sys, os
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        # NOTA: send_manual_signal.py espera ser invocado como script.
        # Aquí guardamos el texto en manual_signals.json para que el bot lo procese
        # en su próximo ciclo (más seguro que invocar el script con stdin).
        import json, time
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "manual_signals.json")
        try:
            with open(path, "r", encoding="utf-8") as f:
                queue = json.load(f) or []
            if not isinstance(queue, list):
                queue = []
        except Exception:
            queue = []
        queue.append({
            "text": raw_text,
            "submitted_at": time.time(),
            "preview_only": preview_only,
            "source": "web_admin",
        })
        with open(path, "w", encoding="utf-8") as f:
            json.dump(queue, f, indent=2, ensure_ascii=False)

        if preview_only:
            flash("👁️ Señal añadida a la cola (modo PREVIEW — no se publica).", "success")
        else:
            flash("📤 Señal añadida a la cola — el bot la procesará en ~30s.", "success")
    except Exception as e:
        flash(f"❌ Error procesando señal: {e}", "error")

    return redirect(url_for("signals.index"))

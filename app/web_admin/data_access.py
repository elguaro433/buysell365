"""Helpers para leer y escribir los JSON de estado del bot.

NO importa el bot directamente — solo manipula los archivos JSON
que el bot ya genera. Esto permite que el panel funcione en paralelo
sin riesgo de bloqueo.
"""
from __future__ import annotations
import json
import os
import time
import shutil
import tempfile
from pathlib import Path
from typing import Any
from .config import JSON_FILES, APP_DIR


def read_json(name: str, default: Any = None) -> Any:
    """Lee un JSON por nombre lógico. Devuelve default si no existe o falla."""
    path = JSON_FILES.get(name)
    if not path or not path.exists():
        return default if default is not None else ({} if name not in ("copier_open_signals", "historial_real") else [])
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


def write_json_atomic(name: str, data: Any) -> bool:
    """Escribe JSON atomicamente (temp + rename). Hace backup automático."""
    path = JSON_FILES.get(name)
    if not path:
        return False

    # Backup
    try:
        if path.exists():
            bak_dir = APP_DIR / "_backups_web_admin"
            bak_dir.mkdir(exist_ok=True)
            ts = time.strftime("%Y%m%d_%H%M%S")
            shutil.copy2(path, bak_dir / f"{path.name}.{ts}.bak")
            # Keep only last 10 backups per file
            backups = sorted(bak_dir.glob(f"{path.name}.*.bak"))
            for old in backups[:-10]:
                old.unlink(missing_ok=True)
    except Exception:
        pass

    # Atomic write
    try:
        fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, path)
        return True
    except Exception as e:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        raise


# ─── WhatsApp recipients ──────────────────────────────────────────────
def get_whatsapp_recipients() -> list[dict]:
    """Devuelve lista de destinatarios WhatsApp."""
    data = read_json("whatsapp_recipients", default=[])
    if isinstance(data, list):
        return data
    return []


def save_whatsapp_recipients(recipients: list[dict]) -> bool:
    return write_json_atomic("whatsapp_recipients", recipients)


def add_whatsapp_recipient(recipient: dict) -> bool:
    recipients = get_whatsapp_recipients()
    recipients.append(recipient)
    return save_whatsapp_recipients(recipients)


def update_whatsapp_recipient(index: int, recipient: dict) -> bool:
    recipients = get_whatsapp_recipients()
    if 0 <= index < len(recipients):
        recipients[index] = recipient
        return save_whatsapp_recipients(recipients)
    return False


def delete_whatsapp_recipient(index: int) -> bool:
    recipients = get_whatsapp_recipients()
    if 0 <= index < len(recipients):
        recipients.pop(index)
        return save_whatsapp_recipients(recipients)
    return False


def toggle_whatsapp_recipient(index: int) -> bool:
    recipients = get_whatsapp_recipients()
    if 0 <= index < len(recipients):
        recipients[index]["enabled"] = not recipients[index].get("enabled", True)
        return save_whatsapp_recipients(recipients)
    return False


# ─── Estado del bot ────────────────────────────────────────────────────
def get_bot_status() -> dict:
    """Estado del proceso bot. En Linux usa systemctl. En Windows: psutil."""
    import platform
    if platform.system() == "Linux":
        return _get_bot_status_systemd()
    return _get_bot_status_psutil()


def _get_bot_status_systemd() -> dict:
    import subprocess
    from .config import BOT_SERVICE_NAME
    try:
        r = subprocess.run(
            ["systemctl", "show", BOT_SERVICE_NAME,
             "--property=ActiveState,SubState,MainPID,ExecMainStartTimestamp,MemoryCurrent"],
            capture_output=True, text=True, timeout=5,
        )
        out = {}
        for line in r.stdout.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                out[k] = v
        return {
            "running": out.get("ActiveState") == "active",
            "state": out.get("ActiveState", "unknown"),
            "substate": out.get("SubState", ""),
            "pid": int(out.get("MainPID", 0) or 0),
            "started_at": out.get("ExecMainStartTimestamp", ""),
            "memory_mb": int(out.get("MemoryCurrent", 0) or 0) // (1024 * 1024),
            "platform": "linux",
        }
    except Exception as e:
        return {"running": False, "state": "error", "error": str(e), "platform": "linux"}


def _get_bot_status_psutil() -> dict:
    try:
        import psutil
        for p in psutil.process_iter(["pid", "name", "cmdline", "create_time", "memory_info"]):
            try:
                cmd = " ".join(p.info.get("cmdline") or [])
                if "launcher.py" in cmd or "bot.py" in cmd:
                    return {
                        "running": True,
                        "state": "active",
                        "pid": p.info["pid"],
                        "started_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(p.info["create_time"])),
                        "uptime_s": int(time.time() - p.info["create_time"]),
                        "memory_mb": p.info["memory_info"].rss // (1024 * 1024),
                        "platform": "windows",
                    }
            except Exception:
                continue
        return {"running": False, "state": "inactive", "platform": "windows"}
    except ImportError:
        return {"running": False, "state": "unknown", "error": "psutil not available", "platform": "windows"}


# ─── Stats del copier ─────────────────────────────────────────────────
def get_copier_stats() -> dict:
    """Stats del día construidas desde copier_stats.json["trades"] vía compute_day_stats.

    El archivo se persiste como {"trades":[{result, pips_numeric, fecha:"DD/MM/YYYY", ...}, ...]}
    (ver signal_copier._save_copier_stats). Antes este lector esperaba {"YYYY-MM-DD": {...}}
    y devolvía ceros siempre. Ahora agrega los trades de hoy y mapea a las claves
    que consume dashboard.html.
    """
    empty = {
        "signals_received": 0, "signals_processed": 0,
        "tps": 0, "sls": 0, "pips_net": 0,
        "wins": 0, "losses": 0,
    }
    raw = read_json("copier_stats", default={})
    trades = raw.get("trades", []) if isinstance(raw, dict) else []
    if not trades:
        return empty

    try:
        import sys
        if str(APP_DIR) not in sys.path:
            sys.path.insert(0, str(APP_DIR))
        from stats_normalizer import compute_day_stats
    except Exception:
        return empty

    today_dmy = time.strftime("%d/%m/%Y")
    try:
        ds = compute_day_stats(trades, today_dmy)
    except Exception:
        return empty

    tps_u = int(ds.get("tps_unique", 0) or 0)
    sls_u = int(ds.get("sls_unique", 0) or 0)
    net = ds.get("net_total", 0) or 0
    try:
        net = round(float(net), 1)
    except Exception:
        net = 0
    closed_unique = tps_u + sls_u

    # Señales abiertas que se ingestaron hoy (para sumarlas a "recibidas hoy")
    opened_today = 0
    try:
        opens = read_json("copier_open_signals", default={})
        if isinstance(opens, dict):
            start_of_day = time.mktime(time.strptime(time.strftime("%Y-%m-%d"), "%Y-%m-%d"))
            for sd in opens.values():
                if isinstance(sd, dict) and float(sd.get("sent_at", 0) or 0) >= start_of_day:
                    opened_today += 1
    except Exception:
        pass

    return {
        "signals_received": closed_unique + opened_today,
        "signals_processed": closed_unique,
        "tps": tps_u,
        "sls": sls_u,
        "pips_net": net,
        "wins": tps_u,
        "losses": sls_u,
    }


def get_open_signals() -> list[dict]:
    """Señales abiertas en este momento."""
    data = read_json("copier_open_signals", default={})
    if isinstance(data, dict):
        out = []
        for sid, sd in data.items():
            s = sd.get("signal", {}) if isinstance(sd, dict) else {}
            s["_sig_id"] = sid
            out.append(s)
        return out
    return []


_DIR_ES_TO_EN = {"VENTA": "SELL", "COMPRA": "BUY", "SHORT": "SELL", "LONG": "BUY"}


def _normalize_signal(s: dict) -> dict:
    """Mapea campos legacy ES (nombre/tipo/entrada/hora/tag) al esquema EN
    que esperan los templates (pair/direction/entry/time/result). Preserva
    todos los campos originales para no romper consumidores existentes."""
    if not isinstance(s, dict):
        return s
    out = dict(s)
    if not out.get("pair"):
        out["pair"] = out.get("par") or out.get("nombre") or "?"
    if not out.get("direction"):
        d_raw = out.get("direccion") or out.get("tipo") or ""
        out["direction"] = _DIR_ES_TO_EN.get(str(d_raw).upper(), str(d_raw).upper() or "?")
    if not out.get("entry"):
        out["entry"] = out.get("entrada") or "-"
    if not out.get("time"):
        out["time"] = out.get("hora_envio") or out.get("hora") or "-"
    if not out.get("result"):
        out["result"] = out.get("tag") or out.get("resultado") or "-"
    return out


def get_recent_signals(limit: int = 20) -> list[dict]:
    """Últimas N señales reales desde copier_stats.json["trades"] (fuente VIVA).

    FIX 2026-06-06: antes leía historial_real.json — un archivo legacy de la era
    MT5 que NO se actualiza desde marzo (mostraba señales de hace 2 meses, todas
    GOLD y con PROB vacía porque esos registros no llevan 'probability'). Las
    señales vivas (con probabilidad de aliados) están en copier_stats.json.
    """
    import datetime
    raw = read_json("copier_stats", default={})
    trades = raw.get("trades", []) if isinstance(raw, dict) else []
    if not isinstance(trades, list) or not trades:
        return []
    _res_map = {"tp": "TP", "sl": "SL", "close_half": "TP½",
                "close_partial": "TP½", "full_close": "CLOSE"}
    out = []
    for t in reversed(trades[-limit:]):
        if not isinstance(t, dict):
            continue
        ts = t.get("closed_at") or t.get("time") or 0
        try:
            hora = datetime.datetime.fromtimestamp(ts).strftime("%d/%m %H:%M") if ts else "-"
        except Exception:
            hora = "-"
        prob = t.get("probability")
        res = (t.get("result") or "").lower()
        out.append({
            "pair": t.get("pair_display") or t.get("pair") or "?",
            "direction": (t.get("direction") or "?").upper(),
            "entry": t.get("entry") or "-",
            "time": hora,
            "probability": prob if (prob is not None and prob != "") else "-",
            "result": _res_map.get(res, (res.upper() or "-")),
        })
    return out


# ─── LLM stats ─────────────────────────────────────────────────────────
def get_llm_stats() -> dict:
    stats = read_json("llm_features_stats", default={})
    today = time.strftime("%Y-%m-%d")
    if isinstance(stats, dict):
        today_data = stats.get(today, {})
        return {
            "model": os.getenv("LLM_MODEL", "claude-sonnet-4-6"),
            "calls_today": today_data.get("calls", 0),
            "cost_today_usd": round(today_data.get("cost_usd", 0), 4),
            "tokens_in": today_data.get("tokens_in", 0),
            "tokens_out": today_data.get("tokens_out", 0),
            "cache_hits": today_data.get("cache_hits", 0),
        }
    return {"model": "?", "calls_today": 0, "cost_today_usd": 0}


# ─── VIPs ──────────────────────────────────────────────────────────────
def get_vip_subscribers() -> list[dict]:
    """Lista de VIPs activos leida de estado.json["suscripciones_vip"].

    Estructura real: {user_id: {nombre, username, expira, activo, tipo,
    fecha_inicio, es_trial, entrada_confirmada, ...}}
    """
    estado = read_json("estado", default={})
    subs = estado.get("suscripciones_vip", {}) if isinstance(estado, dict) else {}
    out = []
    if isinstance(subs, dict):
        for uid, data in subs.items():
            if not isinstance(data, dict):
                continue
            out.append({
                "id": uid,
                "username": data.get("username") or data.get("nombre") or "?",
                "name": data.get("nombre", ""),
                "started_at": data.get("fecha_inicio", ""),
                "expires_at": data.get("expira", ""),
                "days_remaining": data.get("days_remaining"),
                "tier": data.get("tipo", "VIP"),
                "amount": data.get("monto", ""),
                "is_trial": bool(data.get("es_trial", False)),
                "status": "active" if data.get("activo", True) else "expired",
            })
    return out


# ─── Connections health ────────────────────────────────────────────────
def get_connections_status() -> dict:
    """Estado de cada conexión externa basado en último update de sus state files."""
    now = time.time()
    def _age(path_key):
        p = JSON_FILES.get(path_key)
        if p and p.exists():
            return int(now - p.stat().st_mtime)
        return None
    def _age_path(p):
        try:
            return int(now - p.stat().st_mtime) if p and p.exists() else None
        except Exception:
            return None
    # FIX 2026-06-06: Telethon se mide por el heartbeat del copier (se actualiza cada
    # 20s pase lo que pase), NO por copier_stats.json — ese solo se escribe al cerrar
    # un trade, así que daba falso "Inactivo" cuando no había cierres recientes aunque
    # el copier estuviera perfectamente vivo.
    _copier_hb = _age_path(APP_DIR / ".copier.heartbeat")
    return {
        "telegram": True,  # Asumimos True si bot está corriendo
        "telethon": _copier_hb is not None and _copier_hb < 90,
        "whatsapp": True,  # No tenemos heartbeat directo
        "render_sync": _age("historial_real") is not None and _age("historial_real") < 600,
    }

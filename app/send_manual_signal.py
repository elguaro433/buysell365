"""
BuySell365 - Enviar senal manual al canal VIP con seguimiento automatico.

Tres modos de uso:

1. CLI explicito (mismo de siempre):
     python -X utf8 send_manual_signal.py --pair XAUUSD --dir BUY --entry 4600 --sl 4587 --tp 4604

2. CLI pegando texto crudo (NUEVO 2026-05-01):
     python -X utf8 send_manual_signal.py --paste "#XAUUSD BUY 4600 4596\\nSL 4587\\nTP 4604 TP 4608 TP 4612"

3. Modo interactivo:
     python -X utf8 send_manual_signal.py

Tambien expone process_pasted_signal(text) para que launcher.py (boton GUI) y
bot.py (comando admin Telegram) reusen el mismo pipeline.

Registra automaticamente en:
  - copier_open_signals.json  (seguimiento del signal copier - TP/SL HIT auto)
  - estado.json               (seguimiento del bot scanner)

FIXES 2026-05-01:
  - Formato canal VIP: "Entry" (no "Entrada"), zona con "/", SIN separador miles,
    hasta TP5, sin Markdown bold. Ver feedback_canal_vip_formato_precio.md.
  - register_copier_tracking ahora incluye _tp_levels, _tp_idx, _tps_alcanzados,
    _tp_final, timestamp y entry2 - sin estos el monitor del copier no veia la
    senal y no celebraba TP/SL HIT.
"""
import os
import sys
import json
import time
import argparse
import requests
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

BOT_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))

YF_MAP = {
    "GOLD": "GC=F", "XAUUSD": "GC=F",
    "NAS100": "NQ=F", "NASDAQ": "NQ=F", "US100Cash": "NQ=F",
    "US500Cash": "ES=F", "US30Cash": "YM=F",
    "USOIL": "CL=F", "USOILCash": "CL=F",
    "BTCUSD": "BTC-USD", "BTCUSDm": "BTC-USD",
    "EURUSD": "EURUSD=X", "GBPUSD": "GBPUSD=X",
    "USDJPY": "USDJPY=X", "GBPJPY": "GBPJPY=X",
    "AUDCAD": "AUDCAD=X", "USDCAD": "USDCAD=X",
    "EURCHF": "EURCHF=X", "GBPAUD": "GBPAUD=X",
    "EURJPY": "EURJPY=X", "NZDUSD": "NZDUSD=X",
    "AUDUSD": "AUDUSD=X", "GBPNZD": "GBPNZD=X",
    "AUDNZD": "AUDNZD=X", "GBPCAD": "GBPCAD=X",
    "EURCAD": "EURCAD=X", "USDCHF": "USDCHF=X",
    "NZDJPY": "NZDJPY=X", "CADJPY": "CADJPY=X",
    "GBPCHF": "GBPCHF=X", "EURGBP": "EURGBP=X",
}
NOMBRE_MAP = {
    "GOLD": "GOLD", "XAUUSD": "GOLD",
    "NAS100": "NASDAQ", "NASDAQ": "NASDAQ", "US100Cash": "NASDAQ",
    "US500Cash": "S&P500", "US30Cash": "DOW30",
    "USOIL": "USOIL", "USOILCash": "USOIL",
    "BTCUSD": "BTC/USD", "BTCUSDm": "BTC/USD",
    "EURUSD": "EUR/USD", "GBPUSD": "GBP/USD",
    "USDJPY": "USD/JPY", "GBPJPY": "GBP/JPY",
    "AUDNZD": "AUD/NZD", "NZDJPY": "NZD/JPY",
    "EURCAD": "EUR/CAD", "GBPCAD": "GBP/CAD",
    "USDCHF": "USD/CHF", "CADJPY": "CAD/JPY",
}
DISPLAY_MAP = {
    "GOLD": "ORO", "XAUUSD": "ORO", "XAU": "ORO",
    "NAS100": "NASDAQ", "NASDAQ": "NASDAQ", "US100Cash": "NASDAQ",
    "US500Cash": "S&P 500", "US30Cash": "US30",
    "EURUSD": "EUR/USD", "GBPUSD": "GBP/USD", "USDJPY": "USD/JPY",
    "GBPJPY": "GBP/JPY", "AUDCAD": "AUD/CAD", "USDCAD": "USD/CAD",
}


def fmt_price(price):
    """Formato canal VIP: SIN separador de miles, 2 decimales si >=100, 5 si forex."""
    if price >= 100:
        return f"{price:.2f}"
    return f"{price:.5f}"


def build_message(pair, direction, entry, sl, tp, tp2=0, tp3=0, tp4=0, tp5=0, entry2=0):
    """Mensaje canal VIP en formato canonico (sin Markdown, sin coma miles, hasta TP5)."""
    GREEN = "\U0001f7e2"
    RED = "\U0001f534"
    PIN = "\U0001f4cd"
    TARGET = "\U0001f3af"
    SHIELD = "\U0001f6e1️"
    DASH = "—"

    dir_emoji = GREEN if direction.upper() == "BUY" else RED
    pair_display = DISPLAY_MAP.get(pair.upper(), pair)
    has_multi_tp = any(t > 0 for t in [tp2, tp3, tp4, tp5])
    tp_label = "TP1" if has_multi_tp else "TP"
    dir_label = direction.upper()

    if entry > 0 and entry2 > 0:
        entry_str = f"{fmt_price(entry)} / {fmt_price(entry2)}"
    elif entry > 0:
        entry_str = fmt_price(entry)
    else:
        entry_str = "Precio de Mercado"

    lines = [
        f"{dir_emoji} {dir_label} {DASH} {pair_display}",
        "",
        f"{PIN} Entry: {entry_str}",
    ]
    if tp > 0: lines.append(f"{TARGET} {tp_label}: {fmt_price(tp)}")
    if tp2 > 0: lines.append(f"{TARGET} TP2: {fmt_price(tp2)}")
    if tp3 > 0: lines.append(f"{TARGET} TP3: {fmt_price(tp3)}")
    if tp4 > 0: lines.append(f"{TARGET} TP4: {fmt_price(tp4)}")
    if tp5 > 0: lines.append(f"{TARGET} TP5: {fmt_price(tp5)}")
    lines.append(f"{SHIELD} SL: {fmt_price(sl)}")
    return "\n".join(lines)


def send_to_telegram(msg):
    """Envia mensaje al canal VIP (texto plano, sin Markdown)."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHANNEL_ID, "text": msg}
    for intento in range(3):
        try:
            resp = requests.post(url, json=payload, timeout=10)
            if resp.status_code == 200:
                return resp.json().get("result", {}).get("message_id")
            print(f"  Intento {intento+1} fallo: {resp.status_code} {resp.text[:100]}")
        except Exception as e:
            print(f"  Intento {intento+1} error: {e}")
        time.sleep(2)
    return None


def register_copier_tracking(pair, direction, entry, sl, tp, tp2, tp3, msg_id,
                              tp4=0, tp5=0, entry2=0):
    """Registra en copier_open_signals.json. Incluye _tp_levels, _tp_idx,
    _tps_alcanzados, _tp_final y timestamp para que el monitor del copier
    celebre TP/SL HIT automaticamente."""
    sig_file = BASE_DIR / "copier_open_signals.json"
    data = {}
    if sig_file.exists():
        try:
            data = json.loads(sig_file.read_text(encoding="utf-8"))
        except Exception:
            data = {}

    sig_id = f"{pair.upper()}_{int(time.time())}"
    _now = time.time()
    _tp_levels = [t for t in [tp, tp2, tp3, tp4, tp5] if t and t > 0]
    _tp_final = _tp_levels[-1] if _tp_levels else 0
    data[sig_id] = {
        "signal": {
            "type": "new_signal",
            "pair": pair.upper(),
            "mt5_symbol": pair.upper(),
            "direction": direction.upper(),
            "entry": entry,
            "entry2": entry2,
            "sl": sl,
            "tp": tp,
            "tp2": tp2,
            "tp3": tp3,
            "tp4": tp4,
            "tp5": tp5,
            "source": "Manual",
            "order_type": "Market",
            "is_limit": False,
            "_tp_levels": _tp_levels,
            "_tp_idx": 0,
            "_tps_alcanzados": [],
            "_tp_final": _tp_final,
            "timestamp": _now,
            "raw": "Manual signal",
            "rrr": "",
            "style": "",
        },
        "sent_at": _now,
        "telegram_msg_id": msg_id,
    }

    tmp = str(sig_file) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, str(sig_file))
    print(f"  OK copier_open_signals.json - ID: {sig_id} (TPs trackeados: {len(_tp_levels)})")
    return sig_id


def register_bot_tracking(pair, direction, entry, sl, tp, tp2, tp3, msg_id):
    """Registra en estado.json (bot scanner). Anti-duplicado por ticker.

    FIX 2026-05-01: usa state_lock.locked_update_json para evitar lost-updates
    con bot.py escribiendo simultaneamente al mismo estado.json.
    """
    pair_upper = pair.upper()
    yf_tk = YF_MAP.get(pair_upper)
    if not yf_tk:
        yf_tk = f"{pair_upper}=X" if (len(pair_upper) == 6 and pair_upper.isalpha()) else pair_upper

    nombre = NOMBRE_MAP.get(pair_upper, pair_upper)
    tipo = "COMPRA" if direction.upper() == "BUY" else "VENTA"
    base_tk = yf_tk.replace("=X", "").replace("=F", "").upper()
    op_id_holder = {"id": None}

    def _mutate(estado):
        estado.setdefault("operaciones_activas", {})
        ya_hay = any(
            v.get("ticker", "").replace("=X", "").replace("=F", "").upper() == base_tk
            for v in estado["operaciones_activas"].values()
        )
        if ya_hay:
            print(f"  AVISO estado.json - ya hay op abierta para {base_tk}, no se duplica")
            return estado
        tp1 = tp
        _tp2_local, _tp3_local = tp2, tp3
        if tp1 > 0 and entry > 0:
            dist = abs(tp1 - entry)
            if _tp2_local <= 0:
                _tp2_local = round(tp1 + dist, 5) if tipo == "COMPRA" else round(tp1 - dist, 5)
            if _tp3_local <= 0:
                _tp3_local = round(tp1 + 2 * dist, 5) if tipo == "COMPRA" else round(tp1 - 2 * dist, 5)
        op_id = f"{yf_tk}_{int(time.time() * 1000)}"
        op_id_holder["id"] = op_id
        estado["operaciones_activas"][op_id] = {
            "ticker": yf_tk, "nombre": nombre, "tipo": tipo, "entrada": entry,
            "tp1": tp1, "tp2": _tp2_local, "tp3": _tp3_local, "sl": sl, "score": 3,
            "timestamp": time.time(), "hora": datetime.now().strftime("%H:%M"),
            "tp1_hit": False, "tp2_hit": False, "aviso_sl_enviado": False,
            "trailing_activo": False, "confianza_multi_ia": 0, "confianza": 0,
            "confianza_score_100": 0, "estrategia": "signal_copier",
            "mt5_ejecutado": False, "ticket_mt5": None,
            "skip_mt5_razon": "Manual signal", "premium": False,
            "nivel_senal": "COPIADA", "riesgo_usado": 0,
            "telegram_msg_id": msg_id or 0, "fuente": "Manual",
            "_reservado": False,
        }
        return estado

    try:
        from state_lock import locked_update_json
        locked_update_json(str(BASE_DIR / "estado.json"), _mutate, default={})
    except Exception as _e_lock:
        # Fallback no-locked si state_lock falla (mejor escribir que perder)
        print(f"  AVISO state_lock fallo: {_e_lock} — escritura sin lock")
        estado_path = BASE_DIR / "estado.json"
        estado = {}
        if estado_path.exists():
            try:
                estado = json.loads(estado_path.read_text(encoding="utf-8"))
            except Exception:
                estado = {}
        estado = _mutate(estado)
        tmp = str(estado_path) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(estado, f, ensure_ascii=False, indent=2)
        os.replace(tmp, str(estado_path))

    op_id = op_id_holder["id"]
    if op_id:
        print(f"  OK estado.json - {nombre} {tipo} @ {entry} (TP={tp} SL={sl})")
    return op_id


def process_pasted_signal(raw_text):
    """Pipeline completo para texto pegado:
       1. Parsea con parse_signal del signal_copier (mismo parser que canales aliados)
       2. Construye mensaje canon
       3. Publica al canal VIP
       4. Registra tracking (copier + bot)

    Retorna dict: {ok, msg_id, sig_id, error, parsed}.
    Importable desde launcher.py (GUI) y bot.py (comando admin)."""
    out = {"ok": False, "msg_id": None, "sig_id": None, "error": None, "parsed": None}
    if not raw_text or len(raw_text.strip()) < 5:
        out["error"] = "Texto vacio o demasiado corto"
        return out
    try:
        if str(BASE_DIR) not in sys.path:
            sys.path.insert(0, str(BASE_DIR))
        from signal_copier import parse_signal
    except Exception as e:
        out["error"] = f"No se pudo importar parse_signal: {e}"
        return out
    try:
        parsed = parse_signal(raw_text, chat_title="Manual")
    except Exception as e:
        out["error"] = f"Parser excepcion: {e}"
        return out
    if not parsed or parsed.get("type") != "new_signal":
        _t = parsed.get("type") if parsed else None
        out["error"] = f"Parser no reconocio una senal nueva (type={_t})"
        return out
    p = {
        "pair": parsed.get("pair"),
        "direction": parsed.get("direction"),
        "entry": parsed.get("entry") or 0,
        "entry2": parsed.get("entry2", 0) or 0,
        "sl": parsed.get("sl") or 0,
        "tps": [
            parsed.get("tp", 0) or 0,
            parsed.get("tp2", 0) or 0,
            parsed.get("tp3", 0) or 0,
            parsed.get("tp4", 0) or 0,
            parsed.get("tp5", 0) or 0,
        ],
    }
    out["parsed"] = p
    if not p["pair"] or not p["direction"]:
        out["error"] = "Parser devolvio sin pair/direction"
        return out
    msg = build_message(
        p["pair"], p["direction"], p["entry"], p["sl"],
        p["tps"][0], p["tps"][1], p["tps"][2], p["tps"][3], p["tps"][4], p["entry2"],
    )
    msg_id = send_to_telegram(msg)
    if not msg_id:
        out["error"] = "No se pudo enviar al canal VIP"
        return out
    out["msg_id"] = msg_id
    sig_id = register_copier_tracking(
        p["pair"], p["direction"], p["entry"], p["sl"],
        p["tps"][0], p["tps"][1], p["tps"][2], msg_id,
        p["tps"][3], p["tps"][4], p["entry2"],
    )
    out["sig_id"] = sig_id
    register_bot_tracking(
        p["pair"], p["direction"], p["entry"], p["sl"],
        p["tps"][0], p["tps"][1], p["tps"][2], msg_id,
    )
    out["ok"] = True
    return out


def interactive_input():
    print("\nBuySell365 - Enviar Senal Manual")
    print("=" * 45)
    pair = input("Par (ej: XAUUSD, NAS100): ").strip().upper() or "XAUUSD"
    direction = input("Direccion (BUY/SELL): ").strip().upper() or "SELL"
    entry = float(input("Entry: ").strip() or "0")
    sl = float(input("SL: ").strip() or "0")
    tp = float(input("TP1: ").strip() or "0")
    tp2 = float(input("TP2 (0=skip): ").strip() or "0")
    tp3 = float(input("TP3 (0=skip): ").strip() or "0")
    return pair, direction, entry, sl, tp, tp2, tp3


def main():
    parser = argparse.ArgumentParser(description="BuySell365 - Enviar senal manual al canal VIP")
    parser.add_argument("--pair", type=str)
    parser.add_argument("--dir", type=str)
    parser.add_argument("--entry", type=float)
    parser.add_argument("--entry2", type=float, default=0)
    parser.add_argument("--sl", type=float)
    parser.add_argument("--tp", type=float)
    parser.add_argument("--tp2", type=float, default=0)
    parser.add_argument("--tp3", type=float, default=0)
    parser.add_argument("--tp4", type=float, default=0)
    parser.add_argument("--tp5", type=float, default=0)
    parser.add_argument("--paste", type=str, help="Texto crudo de la senal (sera parseado)")
    parser.add_argument("--yes", "-y", action="store_true", help="Enviar sin confirmacion")
    args = parser.parse_args()

    if args.paste:
        print("\n=== Modo PASTE ===")
        print(args.paste[:300])
        if not args.yes:
            if input("\nProcesar y enviar al canal VIP? (s/n): ").strip().lower() != "s":
                print("Cancelado")
                sys.exit(0)
        result = process_pasted_signal(args.paste)
        if result["ok"]:
            print(f"\nOK Senal publicada msg_id={result['msg_id']} sig_id={result['sig_id']}")
            print(f"   Parsed: {result['parsed']}")
            sys.exit(0)
        else:
            print(f"\nERROR: {result['error']}")
            if result.get("parsed"):
                print(f"   Parsed parcial: {result['parsed']}")
            sys.exit(1)

    if not all([args.pair, args.dir, args.entry, args.sl, args.tp]):
        pair, direction, entry, sl, tp, tp2, tp3 = interactive_input()
        tp4 = tp5 = entry2 = 0
    else:
        pair = args.pair.upper()
        direction = args.dir.upper()
        entry = args.entry
        entry2 = args.entry2
        sl = args.sl
        tp = args.tp
        tp2 = args.tp2
        tp3 = args.tp3
        tp4 = args.tp4
        tp5 = args.tp5

    if direction not in ("BUY", "SELL"):
        print("ERROR: Direccion debe ser BUY o SELL")
        sys.exit(1)
    if entry <= 0 or sl <= 0 or tp <= 0:
        print("ERROR: Entry, SL y TP deben ser > 0")
        sys.exit(1)

    msg = build_message(pair, direction, entry, sl, tp, tp2, tp3, tp4, tp5, entry2)
    print(f"\n{'='*50}\n{msg}\n{'='*50}")
    print(f"Canal: {CHANNEL_ID}  Bot: ...{BOT_TOKEN[-8:]}")

    if not args.yes:
        if input("\nEnviar al canal VIP? (s/n): ").strip().lower() != "s":
            print("Cancelado")
            sys.exit(0)

    print("\nEnviando al canal VIP...")
    msg_id = send_to_telegram(msg)
    if not msg_id:
        print("ERROR: no se pudo enviar al canal")
        sys.exit(1)
    print(f"  OK enviado msg_id={msg_id}")

    print("\nRegistrando seguimiento...")
    register_copier_tracking(pair, direction, entry, sl, tp, tp2, tp3, msg_id, tp4, tp5, entry2)
    register_bot_tracking(pair, direction, entry, sl, tp, tp2, tp3, msg_id)

    print("\nLISTO. El bot anunciara TP HIT / SL HIT automaticamente.\n")


if __name__ == "__main__":
    main()

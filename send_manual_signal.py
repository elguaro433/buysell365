"""
BuySell365 — Enviar señal manual al canal VIP con seguimiento automático.

Uso interactivo:  python -X utf8 send_manual_signal.py
Uso directo:      python -X utf8 send_manual_signal.py --pair XAUUSD --dir SELL --entry 4718.34 --sl 4726.34 --tp 4694.34
Sin confirmación: agregar --yes al final

Registra automáticamente en:
  - copier_open_signals.json  (seguimiento del signal copier)
  - estado.json               (seguimiento del bot scanner — TP/SL HIT)
"""
import os, sys, json, time, argparse, requests
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

BOT_TOKEN  = os.getenv("TELEGRAM_TOKEN", "")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))

# ── Mapas de tickers y nombres (idénticos al signal_copier) ──
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
    "GOLD": "GOLD (XAUUSD)", "XAUUSD": "GOLD (XAUUSD)",
    "NAS100": "NASDAQ (NAS100)", "NASDAQ": "NASDAQ (NAS100)",
    "US100Cash": "NASDAQ (US100)", "US30Cash": "DOW30 (US30)",
    "EURUSD": "EUR/USD", "GBPUSD": "GBP/USD", "USDJPY": "USD/JPY",
    "GBPJPY": "GBP/JPY", "AUDCAD": "AUD/CAD", "USDCAD": "USD/CAD",
}


def fmt_price(price):
    """Formato de precio: 2 decimales si >= 100, 5 si forex."""
    if price >= 100:
        return f"{price:,.2f}"
    return f"{price:.5f}"


def build_message(pair, direction, entry, sl, tp, tp2=0, tp3=0, tp4=0, tp5=0):
    """Construye el mensaje de señal en formato BuySell365."""
    dir_emoji = "\U0001f7e2" if direction == "BUY" else "\U0001f534"
    pair_display = DISPLAY_MAP.get(pair.upper(), pair)
    has_multi_tp = any(t > 0 for t in [tp2, tp3, tp4, tp5])
    tp_label = "TP1" if has_multi_tp else "TP"

    dir_label = "COMPRA" if direction.upper() == "BUY" else "VENTA"
    lines = [
        f"{dir_emoji} *{dir_label} \u2014 {pair_display}*",
        "",
        f"\U0001f4cd Entrada: {fmt_price(entry) if entry > 0 else 'Precio de Mercado'}",
    ]
    if tp > 0:
        lines.append(f"\U0001f3af {tp_label}: {fmt_price(tp)}")
    if tp2 > 0:
        lines.append(f"\U0001f3af TP2: {fmt_price(tp2)}")
    if tp3 > 0:
        lines.append(f"\U0001f3af TP3: {fmt_price(tp3)}")
    if tp4 > 0:
        lines.append(f"\U0001f3af TP4: {fmt_price(tp4)}")
    if tp5 > 0:
        lines.append(f"\U0001f3af TP5: {fmt_price(tp5)}")
    lines.append(f"\U0001f6e1\ufe0f SL: {fmt_price(sl)}")
    return "\n".join(lines)


def send_to_telegram(msg):
    """Envía mensaje al canal VIP. Retorna msg_id o None."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHANNEL_ID,
        "text": msg,
        "parse_mode": "Markdown",
    }
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


def register_copier_tracking(pair, direction, entry, sl, tp, tp2, tp3, msg_id):
    """Registra en copier_open_signals.json para el signal copier."""
    sig_file = BASE_DIR / "copier_open_signals.json"
    data = {}
    if sig_file.exists():
        try:
            data = json.loads(sig_file.read_text(encoding="utf-8"))
        except Exception:
            data = {}

    sig_id = f"{pair.upper()}_{int(time.time())}"
    data[sig_id] = {
        "signal": {
            "type": "new_signal",
            "pair": pair.upper(),
            "mt5_symbol": pair.upper(),
            "direction": direction.upper(),
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "tp2": tp2,
            "tp3": tp3,
            "source": "Manual",
            "order_type": "Market",
            "is_limit": False,
        },
        "sent_at": time.time(),
        "telegram_msg_id": msg_id,
    }

    # Escritura atómica
    tmp = str(sig_file) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, str(sig_file))
    print(f"  \u2705 copier_open_signals.json \u2014 ID: {sig_id}")
    return sig_id


def register_bot_tracking(pair, direction, entry, sl, tp, tp2, tp3, msg_id):
    """Registra en estado.json para el bot scanner (TP/SL HIT automático)."""
    estado_path = BASE_DIR / "estado.json"
    estado = {}
    if estado_path.exists():
        try:
            estado = json.loads(estado_path.read_text(encoding="utf-8"))
        except Exception:
            estado = {}

    estado.setdefault("operaciones_activas", {})

    # Resolver ticker yfinance
    pair_upper = pair.upper()
    yf_tk = YF_MAP.get(pair_upper)
    if not yf_tk:
        yf_tk = f"{pair_upper}=X" if (len(pair_upper) == 6 and pair_upper.isalpha()) else pair_upper

    nombre = NOMBRE_MAP.get(pair_upper, pair_upper)
    tipo = "COMPRA" if direction.upper() == "BUY" else "VENTA"

    # Anti-duplicado: no agregar si ya hay op abierta para este ticker
    base_tk = yf_tk.replace("=X", "").replace("=F", "").upper()
    ya_hay = any(
        v.get("ticker", "").replace("=X", "").replace("=F", "").upper() == base_tk
        for v in estado["operaciones_activas"].values()
    )
    if ya_hay:
        print(f"  \u26a0\ufe0f estado.json \u2014 Ya hay op abierta para {base_tk}, no se duplica")
        return None

    # Proyectar TP2/TP3 si no vienen
    tp1 = tp
    if tp1 > 0 and entry > 0:
        dist = abs(tp1 - entry)
        if tp2 <= 0:
            tp2 = round(tp1 + dist, 5) if tipo == "COMPRA" else round(tp1 - dist, 5)
        if tp3 <= 0:
            tp3 = round(tp1 + 2 * dist, 5) if tipo == "COMPRA" else round(tp1 - 2 * dist, 5)

    op_id = f"{yf_tk}_{int(time.time() * 1000)}"
    estado["operaciones_activas"][op_id] = {
        "ticker": yf_tk,
        "nombre": nombre,
        "tipo": tipo,
        "entrada": entry,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "sl": sl,
        "score": 3,
        "timestamp": time.time(),
        "hora": datetime.now().strftime("%H:%M"),
        "tp1_hit": False,
        "tp2_hit": False,
        "aviso_sl_enviado": False,
        "trailing_activo": False,
        "confianza_multi_ia": 0,
        "confianza": 0,
        "confianza_score_100": 0,
        "estrategia": "signal_copier",
        "mt5_ejecutado": False,
        "ticket_mt5": None,
        "skip_mt5_razon": "Manual signal",
        "premium": False,
        "nivel_senal": "COPIADA",
        "riesgo_usado": 0,
        "telegram_msg_id": msg_id or 0,
        "fuente": "Manual",
        "_reservado": False,
    }

    # Escritura atómica
    tmp = str(estado_path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(estado, f, ensure_ascii=False, indent=2)
    os.replace(tmp, str(estado_path))
    print(f"  \u2705 estado.json \u2014 {nombre} {tipo} @ {entry} (TP={tp1} SL={sl})")
    return op_id


def interactive_input():
    """Pide los datos de la señal al usuario por consola."""
    print("\n\U0001f4e1 BuySell365 \u2014 Enviar Senal Manual")
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
    parser.add_argument("--pair", type=str, help="Par: XAUUSD, NAS100, EURUSD...")
    parser.add_argument("--dir", type=str, help="Direccion: BUY o SELL")
    parser.add_argument("--entry", type=float, help="Precio de entrada")
    parser.add_argument("--sl", type=float, help="Stop Loss")
    parser.add_argument("--tp", type=float, help="Take Profit 1")
    parser.add_argument("--tp2", type=float, default=0, help="Take Profit 2 (opcional)")
    parser.add_argument("--tp3", type=float, default=0, help="Take Profit 3 (opcional)")
    parser.add_argument("--yes", "-y", action="store_true", help="Enviar sin pedir confirmacion")
    args = parser.parse_args()

    # Si faltan argumentos obligatorios, modo interactivo
    if not all([args.pair, args.dir, args.entry, args.sl, args.tp]):
        pair, direction, entry, sl, tp, tp2, tp3 = interactive_input()
    else:
        pair = args.pair.upper()
        direction = args.dir.upper()
        entry = args.entry
        sl = args.sl
        tp = args.tp
        tp2 = args.tp2
        tp3 = args.tp3

    # Validaciones básicas
    if direction not in ("BUY", "SELL"):
        print("\u274c Direccion debe ser BUY o SELL")
        sys.exit(1)
    if entry <= 0 or sl <= 0 or tp <= 0:
        print("\u274c Entry, SL y TP deben ser > 0")
        sys.exit(1)

    # Construir mensaje
    msg = build_message(pair, direction, entry, sl, tp, tp2, tp3)

    print(f"\n{'='*50}")
    print(msg)
    print(f"{'='*50}")
    print(f"Canal: {CHANNEL_ID}  |  Bot: ...{BOT_TOKEN[-8:]}")

    if not args.yes:
        confirm = input("\nEnviar al canal VIP? (s/n): ").strip().lower()
        if confirm != "s":
            print("\u274c Cancelado")
            sys.exit(0)

    # 1. Enviar al canal
    print("\n\U0001f4e4 Enviando al canal VIP...")
    msg_id = send_to_telegram(msg)
    if not msg_id:
        print("\u274c Error: no se pudo enviar al canal")
        sys.exit(1)
    print(f"  \u2705 Enviado! (msg_id={msg_id})")

    # 2. Registrar seguimiento
    print("\n\U0001f4ca Registrando seguimiento...")
    register_copier_tracking(pair, direction, entry, sl, tp, tp2, tp3, msg_id)
    register_bot_tracking(pair, direction, entry, sl, tp, tp2, tp3, msg_id)

    print(f"\n\u2705 Listo! Senal publicada + seguimiento activo")
    print(f"   El bot anunciara TP HIT / SL HIT automaticamente.\n")


if __name__ == "__main__":
    main()

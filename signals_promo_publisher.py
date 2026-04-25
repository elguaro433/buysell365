"""
BuySell365 — Publisher de promo "2 senales gratis cada dia"
============================================================
Envia un mensaje al GRUPO publico con boton inline al bot privado.
Disenado para correr 2 veces al dia (9am y 7pm hora Andorra) desde
el scheduler de bot.py — ver _loop_vip_check.

Invocacion:
  from signals_promo_publisher import publish_signals_promo
  publish_signals_promo()

Test manual:
  python -X utf8 signals_promo_publisher.py           # dry-run
  python -X utf8 signals_promo_publisher.py --live    # publica de verdad
"""
import os
import logging
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

log = logging.getLogger("signals_promo")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
GROUP_ID = os.getenv("GROUP_ID", "@BUYSELL_365_24_7").strip()
BOT_USERNAME = os.getenv("BOT_USERNAME", "Andoperandobot").strip()
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "BuySell365traiding").strip()


PROMO_MSG = (
    "🎁 *2 SEÑALES GRATIS — TODOS LOS DÍAS* 🎁\n"
    "━━━━━━━━━━━━━━━━━━━━\n\n"
    "¿Sabías que aquí en el grupo regalamos *2 señales gratis al día*?\n\n"
    "📡 Con Entry · SL · TP exactos\n"
    "🎯 ORO · Forex · NASDAQ\n"
    "⏰ Listas para copiar a tu broker\n\n"
    "Sin pagos. Sin cursos. Sin trampas.\n"
    "Solo tienes que estar atento al grupo 👀\n\n"
    "💎 ¿Quieres acceso a *todas* las señales + análisis IA?\n"
    "👇 Elige cómo prefieres hablar:"
)


def _build_keyboard() -> dict:
    return {
        "inline_keyboard": [
            [{"text": "🤖 Hablar con el bot", "url": f"https://t.me/{BOT_USERNAME}"}],
            [{"text": "👤 Hablar con Emmanuel (admin)", "url": f"https://t.me/{ADMIN_USERNAME}"}],
        ]
    }


def publish_signals_promo(dry_run: bool = False) -> dict:
    """Publica la promo de 2 senales gratis en el grupo publico.

    Retorna dict con: {ok, msg_id?, error?}
    """
    result = {"ok": False}

    if dry_run:
        log.info("🧪 DRY RUN — promo NO se publica")
        log.info(f"   destino: {GROUP_ID}")
        log.info(f"   mensaje: {len(PROMO_MSG)} chars")
        result["ok"] = True
        result["dry_run"] = True
        return result

    if not TELEGRAM_TOKEN or not GROUP_ID:
        log.warning("TELEGRAM_TOKEN o GROUP_ID no configurados — saltando promo")
        result["error"] = "missing_credentials"
        return result

    try:
        import requests
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={
                "chat_id": GROUP_ID,
                "text": PROMO_MSG,
                "parse_mode": "Markdown",
                "reply_markup": _build_keyboard(),
            },
            timeout=15,
        )
        d = r.json()
        if d.get("ok"):
            mid = d["result"]["message_id"]
            result["ok"] = True
            result["msg_id"] = mid
            log.info(f"✅ Promo 2 senales gratis publicada — msg_id={mid}")
        else:
            result["error"] = d.get("description", "unknown")
            log.warning(f"❌ Telegram error: {result['error']}")
    except Exception as e:
        result["error"] = str(e)
        log.warning(f"❌ Exception publicando promo: {e}")

    return result


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    dry = "--live" not in sys.argv
    if dry:
        print("🧪 Modo DRY-RUN (usa --live para publicar)")
    res = publish_signals_promo(dry_run=dry)
    print(f"\nResultado: {res}")

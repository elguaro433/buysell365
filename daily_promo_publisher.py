"""
BuySell365 — Publisher de publicidad diaria (12:00 Andorra)
===========================================================
Cada día a las 12:00 regenera y publica:
  - Imagen "promo_grupo_redes" → enviada al GRUPO público de Telegram
    (invita a visitar web + Instagram + bot VIP)
  - Imagen "promo_ig_web" → enviada a Instagram Feed
    (invita a visitar la web oficial)

Las imágenes se regeneran con stats actualizados de los últimos 7 días
(calcula total pts, TPs, WR dinámicamente desde copier_stats.json).

Invocación:
  from daily_promo_publisher import publish_daily_promo
  publish_daily_promo()

Test manual:
  python -X utf8 daily_promo_publisher.py           # dry-run
  python -X utf8 daily_promo_publisher.py --live    # publica de verdad
"""
import os
import json
import logging
import random
from pathlib import Path
from datetime import datetime, timedelta

from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

log = logging.getLogger("daily_promo")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
GROUP_ID = os.getenv("GROUP_ID", "@BUYSELL_365_24_7").strip()
IG_ENABLED = bool(os.getenv("IG_USERNAME") and os.getenv("IG_PASSWORD"))

STATS_FILE = BASE_DIR / "copier_stats.json"
IMAGES_DIR = BASE_DIR / "ig_images"
IMAGES_DIR.mkdir(exist_ok=True)

SEGOE = r"C:\Windows\Fonts\segoeui.ttf"
SEGOE_B = r"C:\Windows\Fonts\segoeuib.ttf"
SEGOE_EMOJI = r"C:\Windows\Fonts\seguiemj.ttf"


def _font(sz, bold=False):
    try:
        return ImageFont.truetype(SEGOE_B if bold else SEGOE, sz)
    except Exception:
        return ImageFont.load_default()


def _efont(sz):
    try:
        return ImageFont.truetype(SEGOE_EMOJI, sz)
    except Exception:
        return _font(sz)


def _load_week_stats() -> dict:
    """Stats de los últimos 7 días."""
    if not STATS_FILE.exists():
        return {"total_pts": 0, "total_tp": 0, "wr": 0.0}
    with open(STATS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    trades = data.get("trades", [])

    now = datetime.now()
    fechas_ok = set()
    for i in range(7):
        fechas_ok.add((now - timedelta(days=i)).strftime("%d/%m/%Y"))

    week_trades = [t for t in trades if t.get("fecha") in fechas_ok]
    tps = [t for t in week_trades if t.get("result") == "tp"]
    sls = [t for t in week_trades if t.get("result") == "sl"]
    partials = [t for t in week_trades
                if t.get("result") in ("close_half", "close_partial", "full_close")
                and t.get("pips_numeric", 0) > 0]
    pts = sum(t.get("pips_numeric", 0) for t in tps) + sum(t.get("pips_numeric", 0) for t in partials)
    total_tp = len(tps)
    total_sl = len(sls)
    wr = 100 * total_tp / (total_tp + total_sl) if (total_tp + total_sl) > 0 else 0.0
    return {"total_pts": pts, "total_tp": total_tp, "total_sl": total_sl, "wr": wr}


def _generate_grupo_image(stats: dict) -> Path:
    """Imagen 1080x1350 para Telegram grupo — invita a web + IG + bot."""
    W, H = 1080, 1350
    GOLD, GOLD_L = (255, 199, 44), (255, 220, 100)
    GREEN = (16, 210, 108)
    WHITE, GRAY = (255, 255, 255), (156, 163, 175)
    BLUE_WEB = (77, 159, 255)
    BLUE_IG = (225, 48, 108)

    img = Image.new("RGB", (W, H), (6, 10, 18))
    d = ImageDraw.Draw(img)
    for y in range(H):
        t = y / H
        c = (int(6 * (1 - t) + 15 * t), int(10 * (1 - t) + 22 * t), int(18 * (1 - t) + 32 * t))
        d.line([(0, y), (W, y)], fill=c)
    rng = random.Random(33)
    for _ in range(100):
        x, y = rng.randint(0, W), rng.randint(0, H)
        op = rng.randint(25, 70)
        d.ellipse([(x, y), (x + 2, y + 2)], fill=(op, op + 10, op + 25))

    def em(pos, e, sz, a="mm"):
        try:
            d.text(pos, e, font=_efont(sz), anchor=a, embedded_color=True)
        except Exception:
            d.text(pos, e, font=_font(sz), anchor=a)

    d.rectangle([(0, 0), (W, 180)], fill=(3, 6, 12))
    em((85, 80), "🚀", 80)
    em((W - 85, 80), "💎", 80)
    # FIX 2026-04-26: imagen del grupo en INGLES
    d.text((W // 2, 70), "BUYSELL365 PRO", fill=GOLD, font=_font(52, True), anchor="mm")
    d.text((W // 2, 125), "AI TRADING SIGNALS", fill=GRAY, font=_font(20, True), anchor="mm")
    for x in range(60, W - 60):
        t = (x - 60) / (W - 120)
        g_val = int(199 + (220 - 199) * (t if t < 0.5 else 1 - t) * 2)
        d.line([(x, 177), (x, 180)], fill=(255, g_val, 44))

    d.text((W // 2, 240), "DISCOVER", fill=WHITE, font=_font(62, True), anchor="mm")
    d.text((W // 2, 310), "OUR PLATFORMS", fill=GOLD, font=_font(48, True), anchor="mm")
    d.text((W // 2, 370), "Follow us, see real results, and join", fill=GRAY, font=_font(26), anchor="mm")

    d.rounded_rectangle([(80, 440), (W - 80, 620)], radius=20, fill=(22, 28, 42), outline=BLUE_WEB, width=3)
    em((160, 530), "🌐", 78)
    d.text((260, 485), "OFFICIAL WEBSITE", fill=BLUE_WEB, font=_font(30, True), anchor="lm")
    d.text((260, 525), "buysell365.pro", fill=WHITE, font=_font(44, True), anchor="lm")
    d.text((260, 575), "Live stats · History · VIP Offer", fill=GRAY, font=_font(22), anchor="lm")

    d.rounded_rectangle([(80, 650), (W - 80, 830)], radius=20, fill=(22, 28, 42), outline=BLUE_IG, width=3)
    em((160, 740), "📸", 78)
    d.text((260, 695), "INSTAGRAM", fill=BLUE_IG, font=_font(30, True), anchor="lm")
    d.text((260, 735), "@buysell365.pro_", fill=WHITE, font=_font(38, True), anchor="lm")
    d.text((260, 775), "tradingsignals", fill=WHITE, font=_font(38, True), anchor="lm")

    d.rounded_rectangle([(80, 860), (W - 80, 1040)], radius=20, fill=(22, 28, 42), outline=GOLD, width=3)
    em((160, 950), "🤖", 78)
    d.text((260, 905), "VIP CHANNEL BOT", fill=GOLD, font=_font(30, True), anchor="lm")
    d.text((260, 945), "@Andoperandobot", fill=WHITE, font=_font(38, True), anchor="lm")
    d.text((260, 985), "Real-time trading signals", fill=GRAY, font=_font(22), anchor="lm")

    d.rounded_rectangle([(80, 1090), (W - 80, 1180)], radius=18, fill=GOLD)
    d.text((W // 2, 1135), "FOLLOW US ON ALL OUR CHANNELS", fill=(5, 8, 15), font=_font(26, True), anchor="mm")

    stats_line = f"+{stats['total_pts']:,.0f} pts this week  ·  {stats['total_tp']} TPs  ·  {stats['wr']:.0f}% WR"
    d.text((W // 2, 1225), stats_line, fill=GREEN, font=_font(24, True), anchor="mm")
    d.text((W // 2, 1285), "BuySell365 Pro — AI Trading 24/7", fill=GRAY, font=_font(20), anchor="mm")

    out = IMAGES_DIR / "promo_grupo_redes.jpg"
    img.save(str(out), "JPEG", quality=95)
    return out


def _generate_ig_image(stats: dict) -> Path:
    """Imagen 1080x1350 para Instagram Feed — invita a visitar la web."""
    W, H = 1080, 1350
    GOLD, GOLD_L = (255, 199, 44), (255, 220, 100)
    GREEN = (16, 210, 108)
    WHITE, GRAY = (255, 255, 255), (156, 163, 175)

    img = Image.new("RGB", (W, H), (6, 10, 18))
    d = ImageDraw.Draw(img)
    for y in range(H):
        t = y / H
        c = (int(6 * (1 - t) + 15 * t), int(10 * (1 - t) + 22 * t), int(18 * (1 - t) + 32 * t))
        d.line([(0, y), (W, y)], fill=c)
    rng = random.Random(99)
    for _ in range(100):
        x, y = rng.randint(0, W), rng.randint(0, H)
        op = rng.randint(25, 70)
        d.ellipse([(x, y), (x + 2, y + 2)], fill=(op, op + 10, op + 25))

    def em(pos, e, sz, a="mm"):
        try:
            d.text(pos, e, font=_efont(sz), anchor=a, embedded_color=True)
        except Exception:
            d.text(pos, e, font=_font(sz), anchor=a)

    d.rectangle([(0, 0), (W, 180)], fill=(3, 6, 12))
    em((85, 80), "🌐", 80)
    em((W - 85, 80), "💎", 80)
    d.text((W // 2, 70), "BUYSELL365 PRO", fill=GOLD, font=_font(52, True), anchor="mm")
    d.text((W // 2, 125), "VISIT OUR OFFICIAL WEBSITE", fill=GRAY, font=_font(20, True), anchor="mm")
    for x in range(60, W - 60):
        t = (x - 60) / (W - 120)
        g_val = int(199 + (220 - 199) * (t if t < 0.5 else 1 - t) * 2)
        d.line([(x, 177), (x, 180)], fill=(255, g_val, 44))

    d.text((W // 2, 260), "EVERYTHING IN ONE PLACE", fill=WHITE, font=_font(44, True), anchor="mm")

    d.rounded_rectangle([(60, 340), (W - 60, 510)], radius=24, fill=(22, 28, 42), outline=GOLD, width=3)
    em((160, 425), "🔗", 70)
    d.text((W // 2 + 40, 395), "buysell365.pro", fill=GOLD, font=_font(58, True), anchor="mm")
    d.text((W // 2 + 40, 465), "Official website", fill=GRAY, font=_font(24), anchor="mm")

    d.text((W // 2, 580), "✨ WHAT YOU'LL FIND ✨", fill=GOLD_L, font=_font(28, True), anchor="mm")
    items = [
        ("📊", "Live VIP channel stats"),
        ("🎯", "Full signal history"),
        ("📈", "Performance per asset (GOLD, NAS100, US30, FX, BTC)"),
        ("🎁", "Launch offer — 50% OFF"),
        ("🤖", "Direct access to the trading bot"),
    ]
    y0 = 660
    for i, (ic, tx) in enumerate(items):
        y = y0 + i * 75
        em((130, y), ic, 46)
        d.text((190, y), tx, fill=WHITE, font=_font(26), anchor="lm")

    d.rounded_rectangle([(80, 1060), (W - 80, 1180)], radius=18, fill=(18, 48, 30), outline=GREEN, width=2)
    d.text((W // 2, 1095), "THIS WEEK IN THE VIP CHANNEL", fill=WHITE, font=_font(22, True), anchor="mm")
    stats_line = f"+{stats['total_pts']:,.0f} PTS  ·  {stats['total_tp']} TPs  ·  {stats['wr']:.0f}% WR"
    d.text((W // 2, 1150), stats_line, fill=GREEN, font=_font(34, True), anchor="mm")

    em((W // 2 - 250, 1245), "👉", 50)
    d.text((W // 2 + 20, 1245), "LINK IN BIO", fill=GOLD, font=_font(40, True), anchor="mm")
    d.text((W // 2, 1310), "@buysell365.pro_tradingsignals", fill=GRAY, font=_font(20), anchor="mm")

    out = IMAGES_DIR / "promo_ig_web.jpg"
    img.save(str(out), "JPEG", quality=95)
    return out


def _caption_grupo(stats: dict) -> str:
    # FIX 2026-04-26: caption del grupo (12:00) en INGLES
    return (
        "🚀 <b>Discover all our platforms!</b>\n\n"
        "🌐 <b>OFFICIAL WEBSITE</b> — buysell365.pro\n"
        "   Live stats, history and VIP offer\n\n"
        "📸 <b>INSTAGRAM</b> — @buysell365.pro_tradingsignals\n"
        "   Daily results, reels and stories\n\n"
        "🤖 <b>VIP CHANNEL BOT</b> — @Andoperandobot\n"
        "   Direct access to the premium channel\n\n"
        f"💎 This week: +{stats['total_pts']:,.0f} pts · "
        f"{stats['total_tp']} TPs · {stats['wr']:.0f}% WR\n\n"
        "Follow us EVERYWHERE so you don't miss a thing! 🔥"
    )


def _caption_ig(stats: dict) -> str:
    # FIX 2026-04-26: caption en INGLES — audiencia internacional
    return (
        "🌐 EVERYTHING IN ONE PLACE\n\n"
        "Visit our official website:\n"
        "🔗 buysell365.pro\n\n"
        "✨ What you'll find:\n"
        "📊 Live VIP channel stats\n"
        "🎯 Full signal history\n"
        "📈 Performance by asset\n"
        "🎁 Launch offer — 50% OFF\n"
        "🤖 Direct bot access\n\n"
        f"💎 This week: +{stats['total_pts']:,.0f} pts · "
        f"{stats['total_tp']} TPs · {stats['wr']:.0f}% WR\n\n"
        "👉 Link in bio — buysell365.pro\n\n"
        "#trading #forex #xauusd #gold #nas100\n"
        "#bitcoin #traders #daytrading #aitrading\n"
        "#tradingsignals #buysell365 #signals"
    )


def publish_daily_promo(dry_run: bool = False) -> dict:
    """Publica la promo diaria en Grupo + Instagram Feed."""
    stats = _load_week_stats()
    log.info(f"📊 Stats 7 dias: +{stats['total_pts']:.0f} pts · {stats['total_tp']} TPs · {stats['wr']:.1f}% WR")

    img_grupo = _generate_grupo_image(stats)
    img_ig = _generate_ig_image(stats)
    result = {"stats": stats, "images": {"grupo": str(img_grupo), "ig": str(img_ig)}}

    if dry_run:
        log.info("🧪 DRY RUN — no se publica nada")
        return result

    # ─ GRUPO Telegram ─
    try:
        import requests
        with open(img_grupo, "rb") as f:
            photo = f.read()
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto",
            data={"chat_id": GROUP_ID, "caption": _caption_grupo(stats)[:1020], "parse_mode": "HTML"},
            files={"photo": ("promo.jpg", photo, "image/jpeg")},
            timeout=30,
        )
        if r.status_code == 200:
            mid = r.json().get("result", {}).get("message_id")
            result["grupo_msg_id"] = mid
            log.info(f"✅ Grupo publicado msg_id={mid}")
        else:
            log.warning(f"Grupo fallo: {r.status_code} {r.text[:150]}")
            result["grupo_error"] = r.text[:200]
    except Exception as e:
        log.warning(f"Grupo exception: {e}")
        result["grupo_error"] = str(e)

    # ─ INSTAGRAM Feed ─
    # Respeta cooldown del circuit breaker anti-spam (ig_feed_locked_until.txt)
    import time
    lock_file = BASE_DIR / ".ig_feed_locked_until.txt"
    if lock_file.exists():
        try:
            unlock_ts = float(lock_file.read_text().strip())
            if time.time() < unlock_ts:
                hrs = (unlock_ts - time.time()) / 3600
                log.warning(f"⛔ IG Feed cooldown activo {hrs:.1f}h — saltando post")
                result["ig_feed"] = "cooldown"
                return result
        except Exception:
            pass

    if IG_ENABLED:
        try:
            import instagram_poster as ig
            cl = ig._get_client()
            if cl:
                try:
                    cl.photo_upload(str(img_ig), _caption_ig(stats))
                    result["ig_feed"] = "ok"
                    log.info("✅ Instagram Feed publicado")
                except Exception as e_ig:
                    err = str(e_ig).lower()
                    log.warning(f"IG Feed fallo: {e_ig}")
                    result["ig_feed_error"] = str(e_ig)
                    # Auto cooldown 48h si IG detecta spam
                    if any(k in err for k in ("feedback_required", "is_spam", "try again later", "rate", "policy")):
                        unlock_at = time.time() + (48 * 3600)
                        lock_file.write_text(str(unlock_at))
                        log.warning(f"⛔ IG Feed LOCKED 48h (cooldown automatico)")
        except Exception as e:
            log.warning(f"IG setup fallo: {e}")

    return result


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    dry = "--live" not in sys.argv
    if dry:
        print("🧪 Modo DRY-RUN (usa --live para publicar)")
    res = publish_daily_promo(dry_run=dry)
    print(f"\nImágenes: grupo={res['images']['grupo']}")
    print(f"          ig={res['images']['ig']}")
    if not dry:
        print(f"\nResultados: grupo_msg_id={res.get('grupo_msg_id')}, ig_feed={res.get('ig_feed','?')}")

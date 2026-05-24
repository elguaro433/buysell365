"""
llm_features.py — Centralized Claude Sonnet 4.6 integration for BuySell365 Pro.
FIX 2026-04-29: 10 features built on top of the LLM parser:
  1. VIP conversational assistant (with context: open positions, capital)
  2. Vision: analyze images in DM (charts, MT5 screenshots, aliado cards)
  3. Pre-trade safety check (block bad signals before MT5)
  4. Post-trade analysis (learn from SL hits)
  5. Auto-content generation (IG captions, daily summaries)
  6. Reliability score (evaluate aliados automatically)
  7. Multi-language auto-detect
  8. Lead detection (find hot prospects in public group)
  9. Onboarding VIP (mini-interview for new subscribers)
  10. Smart news alerts (filter relevant news)

All functions are best-effort — if API fails, return None and let caller fallback.
Config via env vars (already documented in signal_copier.py LLM section):
  ANTHROPIC_API_KEY, LLM_PARSER_ENABLED, LLM_MODEL, LLM_VISION_ENABLED
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import time
from typing import Optional

log = logging.getLogger(__name__)

# Cache compartido para todas las features (24h por hash de input)
_features_cache: dict = {}
_features_stats = {
    "vip_chat": 0,
    "vision": 0,
    "pretrade": 0,
    "posttrade": 0,
    "content": 0,
    "reliability": 0,
    "language": 0,
    "lead": 0,
    "onboarding": 0,
    "news": 0,
    "errors": 0,
    "cache_hits": 0,
}

# FIX 2026-05-01: persistir stats a disco para que el dashboard del launcher
# (proceso distinto del copier) vea el coste real. Antes get_features_stats()
# devolvia el dict en memoria — y el launcher importaba el modulo en SU process
# obteniendo siempre el dict vacio. Resultado: dashboard mostraba "$0 hoy" aunque
# el copier hubiera gastado decimas de dolar en API calls.
_STATS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "llm_features_stats.json")
_stats_last_flush = 0.0


def _flush_stats_to_disk(force: bool = False) -> None:
    """Escribe _features_stats a disco. Throttled: max 1 escritura cada 5s salvo force."""
    global _stats_last_flush
    try:
        _now = time.time()
        if not force and (_now - _stats_last_flush) < 5.0:
            return
        _tmp = _STATS_FILE + ".tmp"
        import json as _json
        with open(_tmp, "w", encoding="utf-8") as _f:
            _json.dump({**_features_stats, "_updated_at": _now}, _f, ensure_ascii=False, indent=2)
        os.replace(_tmp, _STATS_FILE)
        _stats_last_flush = _now
    except Exception as _e:
        log.debug(f"flush stats failed: {_e}")


def _load_stats_from_disk() -> dict:
    """Lee stats persistidos. Usado por el launcher (proceso distinto del copier)."""
    try:
        if os.path.exists(_STATS_FILE):
            import json as _json
            with open(_STATS_FILE, "r", encoding="utf-8") as _f:
                return _json.load(_f)
    except Exception as _e:
        log.debug(f"load stats failed: {_e}")
    return {}


_anthropic_client = None


def _get_client():
    """Lazy singleton Anthropic client."""
    global _anthropic_client
    if _anthropic_client is not None:
        return _anthropic_client
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        import anthropic
        _anthropic_client = anthropic.Anthropic(api_key=api_key)
        return _anthropic_client
    except ImportError:
        log.warning("⚠️ anthropic package no instalado. pip install anthropic")
        return None
    except Exception as e:
        log.warning(f"⚠️ Anthropic client init error: {e}")
        return None


def _llm_enabled() -> bool:
    return os.getenv("LLM_PARSER_ENABLED", "false").lower() in ("true", "1", "yes")


def _model() -> str:
    return os.getenv("LLM_MODEL", "claude-sonnet-4-6").strip()


def _call_llm(system: str, user_content, max_tokens: int = 600,
              cache_key: Optional[str] = None, cache_ttl: int = 86400,
              feature: str = "generic") -> Optional[str]:
    """Generic LLM call con prompt caching + fallback Groq + logging granular.
    user_content puede ser string o lista de content blocks (para vision).

    FIX 2026-04-30:
      1. Prompt caching de Anthropic (cache_control ephemeral) en system prompt:
         primer call paga 100%, calls subsiguientes con MISMO system pagan ~10%
         (90% descuento). Solo activa si system >= 1024 tokens (~4000 chars).
      2. Fallback automático a Groq si Anthropic falla (rate limit, sin saldo, 5xx).
      3. Logging granular: tokens in/out por feature + estimación de coste.
    """
    if not _llm_enabled():
        return None
    client = _get_client()
    if client is None:
        return None

    # Cache local (in-memory) — distinto del prompt caching de Anthropic
    if cache_key:
        cached = _features_cache.get(cache_key)
        if cached:
            val, ts = cached
            if time.time() - ts < cache_ttl:
                _features_stats["cache_hits"] += 1
                _flush_stats_to_disk()
                return val

    try:
        if isinstance(user_content, str):
            messages = [{"role": "user", "content": user_content}]
        else:
            messages = [{"role": "user", "content": user_content}]

        # FIX 2026-04-30: Prompt caching de Anthropic — sistema ≥1024 tokens
        # se cachea 5min con 90% descuento en hits. Si <1024 tokens, no se
        # cachea pero la petición no falla (cache_control es ignorado).
        # Threshold conservador: 1500 chars ≈ 400 tokens (suficiente para
        # systems estructurados con ejemplos).
        _system_arg = system
        if len(system) >= 1500:
            _system_arg = [
                {"type": "text", "text": system,
                 "cache_control": {"type": "ephemeral"}}
            ]

        resp = client.messages.create(
            model=_model(),
            max_tokens=max_tokens,
            system=_system_arg,
            messages=messages,
        )
        text = resp.content[0].text.strip()

        # FIX 2026-04-30: logging granular por feature
        try:
            _u = resp.usage
            _in = getattr(_u, "input_tokens", 0)
            _out = getattr(_u, "output_tokens", 0)
            _cw = getattr(_u, "cache_creation_input_tokens", 0)
            _cr = getattr(_u, "cache_read_input_tokens", 0)
            # Sonnet 4.6 pricing: $3/MTok in, $15/MTok out, $3.75/MTok cache_write, $0.30/MTok cache_read
            _cost = (
                (_in - _cr) * 3 / 1_000_000 +
                _out * 15 / 1_000_000 +
                _cw * 3.75 / 1_000_000 +
                _cr * 0.30 / 1_000_000
            )
            if "feature_costs" not in _features_stats:
                _features_stats["feature_costs"] = {}
            if "feature_tokens" not in _features_stats:
                _features_stats["feature_tokens"] = {}
            _features_stats["feature_costs"][feature] = (
                _features_stats["feature_costs"].get(feature, 0.0) + _cost
            )
            _features_stats["feature_tokens"][feature] = (
                _features_stats["feature_tokens"].get(feature, 0) + _in + _out
            )
            if _cr > 0:
                _features_stats["cache_anthropic_hits"] = (
                    _features_stats.get("cache_anthropic_hits", 0) + 1
                )
        except Exception:
            pass

        if cache_key:
            _features_cache[cache_key] = (text, time.time())
        _flush_stats_to_disk()
        return text
    except Exception as e:
        _err = str(e)
        # FIX 2026-05-01: clasificar errores Anthropic + alerta admin si es fatal
        _err_lower = _err.lower()
        _err_type = "unknown"
        _is_fatal = False
        if "credit balance" in _err_lower or "low balance" in _err_lower or "insufficient" in _err_lower:
            _err_type = "no_credit"
            _is_fatal = True  # Necesita atencion admin URGENTE
        elif "rate limit" in _err_lower or "rate_limit" in _err_lower or "429" in _err:
            _err_type = "rate_limit"
        elif "401" in _err or "unauthorized" in _err_lower or "invalid api key" in _err_lower:
            _err_type = "auth_error"
            _is_fatal = True
        elif "5" in _err[:3] and ("server" in _err_lower or "service" in _err_lower or "500" in _err or "502" in _err or "503" in _err):
            _err_type = "server_error"
        elif "timeout" in _err_lower or "connection" in _err_lower:
            _err_type = "network"
        log.warning(f"⚠️ LLM call error ({feature}, type={_err_type}, fatal={_is_fatal}): {_err[:200]}")
        _features_stats["errors"] += 1
        # Contar por tipo de error para diagnostico
        if "errors_by_type" not in _features_stats:
            _features_stats["errors_by_type"] = {}
        _features_stats["errors_by_type"][_err_type] = _features_stats["errors_by_type"].get(_err_type, 0) + 1
        _flush_stats_to_disk()
        # Eventos al journal
        try:
            from events_log import log_event as _log_event
            _log_event("llm.error", source="llm_features",
                      data={"feature": feature, "error_type": _err_type,
                            "fatal": _is_fatal, "msg": _err[:300]})
        except Exception:
            pass
        # Si es fatal (sin saldo / auth) -> alerta admin
        if _is_fatal:
            try:
                import os as _os_alert
                _alert_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                            ".llm_fatal_alert.flag")
                # Solo alertar 1 vez cada hora (cooldown via mtime del flag)
                _alert_recent = False
                if os.path.exists(_alert_path):
                    _alert_recent = (time.time() - os.path.getmtime(_alert_path)) < 3600
                if not _alert_recent:
                    with open(_alert_path, "w") as _af:
                        _af.write(f"{_err_type}: {_err[:200]}")
                    log.error(f"🚨 LLM FATAL ({_err_type}) — admin debe intervenir: {_err[:150]}")
            except Exception:
                pass
        # FIX 2026-04-30: fallback a Groq si Anthropic falla
        try:
            return _fallback_groq(system, user_content, max_tokens, feature)
        except Exception as _e_fb:
            log.debug(f"Fallback Groq también falló: {_e_fb}")
        return None


def _fallback_groq(system: str, user_content, max_tokens: int, feature: str) -> Optional[str]:
    """Fallback a Groq Llama 3.3 70B cuando Anthropic falla.
    No soporta vision (Llama 3.3 es text-only) — vision falla silenciosamente."""
    import os as _os
    _gk = _os.getenv("GROQ_API_KEY", "").strip()
    if not _gk:
        return None
    # Si user_content es lista (vision), no podemos usar Groq → return None
    if not isinstance(user_content, str):
        return None
    try:
        from groq import Groq as _GroqClient
        _gc = _GroqClient(api_key=_gk)
        _resp = _gc.chat.completions.create(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_content},
            ],
            model="llama-3.3-70b-versatile",
            max_tokens=max_tokens,
            temperature=0.5,
            timeout=15,
        )
        _txt = _resp.choices[0].message.content.strip()
        # Loggear que se usó fallback
        _features_stats["groq_fallback"] = _features_stats.get("groq_fallback", 0) + 1
        log.info(f"🔄 LLM fallback a Groq exitoso ({feature})")
        return _txt
    except Exception as e:
        log.debug(f"_fallback_groq error: {e}")
        return None


def _parse_json_response(text: str) -> Optional[dict]:
    """Strip code fences and parse JSON. Returns dict or None."""
    if not text:
        return None
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        log.warning(f"⚠️ JSON parse failed: {text[:100]!r}")
        return None


# ═══════════════════════════════════════════════════════════════════
# FEATURE 1: VIP Conversational Assistant
# ═══════════════════════════════════════════════════════════════════
_VIP_ASSISTANT_SYSTEM = """You are the BuySell365 Pro VIP trading assistant.
Reply in the SAME language the user wrote in.
Be concise (max 4-6 lines), professional, and actionable.

You have access to:
- Open positions (pair, direction, entry, current P&L)
- User's capital and risk settings
- Recent trade history (last 24h)
- Live prices (when provided in context)

Style:
- Use emojis sparingly (📊 🎯 🛡️ ⚠️ ✅ 💰)
- NO disclaimers, NO "consulta un asesor financiero"
- Direct answers — the user is paying for actionable insights
- If asked for prediction, give educated opinion based on technicals
- If asked about strategy/risk, give specific numbers (lot size, SL distance)

DO NOT:
- Promise profits ("vas a ganar X")
- Use markdown headers (#, ##) — only bold when needed
- Quote disclaimers about market risk every message"""


def vip_assistant_reply(user_question: str, user_name: str = "Trader",
                        open_positions: list = None, capital: float = 0,
                        recent_trades: list = None, market_context: dict = None) -> Optional[str]:
    """Genera respuesta personalizada para pregunta de VIP."""
    if not _llm_enabled():
        return None

    ctx_parts = [f"User: {user_name}"]
    if capital > 0:
        ctx_parts.append(f"Capital: ${capital:,.2f}")
    if open_positions:
        op_lines = []
        for p in open_positions[:5]:
            op_lines.append(
                f"  - {p.get('direction','?')} {p.get('pair','?')} entry={p.get('entry',0)} "
                f"sl={p.get('sl',0)} tp={p.get('tp',0)}"
            )
        ctx_parts.append("Open positions:\n" + "\n".join(op_lines))
    else:
        ctx_parts.append("Open positions: none")
    if recent_trades:
        wins = sum(1 for t in recent_trades if t.get("result") == "tp")
        losses = sum(1 for t in recent_trades if t.get("result") == "sl")
        ctx_parts.append(f"Last 24h: {wins} wins, {losses} losses")
    if market_context:
        for k, v in market_context.items():
            ctx_parts.append(f"{k}: {v}")

    ctx = "\n".join(ctx_parts)
    prompt = f"CONTEXT:\n{ctx}\n\nQUESTION: {user_question}\n\nReply concisely:"
    result = _call_llm(_VIP_ASSISTANT_SYSTEM, prompt, max_tokens=400)
    if result:
        _features_stats["vip_chat"] += 1
    return result


# ═══════════════════════════════════════════════════════════════════
# FEATURE 2: Vision Image Analysis
# ═══════════════════════════════════════════════════════════════════
_VISION_SYSTEM = """You analyze trading-related images (charts, MT5 screenshots, signal cards).
Identify what the user is looking at and provide actionable feedback.

Image types you'll see:
- TradingView charts: identify trend, support/resistance, indicators (RSI, MACD)
- MT5 screenshots: read open positions, P&L, account state
- Signal cards from external channels: extract direction/pair/entry/SL/TP
- Random screenshots: be honest if you can't help

Reply concisely (3-6 lines). Use emojis: 📊 📈 📉 🎯 ⚠️ ✅
Reply in the language the user wrote in."""


def analyze_image(image_bytes: bytes, user_caption: str = "",
                  user_language: str = "es") -> Optional[str]:
    """Analiza una imagen con Vision. Returns text response."""
    if not _llm_enabled():
        return None
    if not image_bytes or len(image_bytes) > 5_000_000:
        return None
    if os.getenv("LLM_VISION_ENABLED", "true").lower() not in ("true", "1", "yes"):
        return None

    # Detect mime
    mime = "image/jpeg"
    if image_bytes[:8].startswith(b"\x89PNG"):
        mime = "image/png"
    elif image_bytes[:6] in (b"GIF87a", b"GIF89a"):
        mime = "image/gif"
    elif image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP":
        mime = "image/webp"

    b64 = base64.standard_b64encode(image_bytes).decode("ascii")
    h = hashlib.md5(image_bytes + (user_caption or "").encode("utf-8")).hexdigest()

    user_text = (
        f"User caption: {user_caption}\n"
        f"User's language: {user_language}\n\n"
        f"Analyze this trading image and give actionable feedback. "
        f"Reply in {user_language}."
    ) if user_caption else (
        f"Analyze this trading image and give actionable feedback. "
        f"Reply in {user_language}."
    )

    content = [
        {"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}},
        {"type": "text", "text": user_text},
    ]

    result = _call_llm(_VISION_SYSTEM, content, max_tokens=500,
                       cache_key=f"vision_{h}")
    if result:
        _features_stats["vision"] += 1
    return result


# ═══════════════════════════════════════════════════════════════════
# FEATURE 3: Pre-Trade Safety Check
# ═══════════════════════════════════════════════════════════════════
_PRETRADE_SYSTEM = """You evaluate trading signals BEFORE execution.
Return ONLY valid JSON with these fields:
{
  "safe": true|false,
  "risk_score": 1-10 (1=safe, 10=very risky),
  "concerns": ["concern1", "concern2", ...],
  "block": true|false,
  "reason": "short reason if blocked"
}

Block reasons:
- SL too tight (less than 2x typical spread for that asset)
- SL on wrong side of entry (impossible)
- TP > 5% from entry without R:R justification
- 3+ same-direction positions on correlated pairs (overexposure)
- Asset known for high spread during reported time

Don't block for:
- Normal market volatility
- Aliado we don't trust (that's the user's choice)
- Anything subjective"""


def pretrade_safety_check(signal: dict, open_positions: list = None,
                          live_price: float = 0, atr: float = 0) -> Optional[dict]:
    """Evalua si la senhal es segura para ejecutar. Returns dict or None."""
    if not _llm_enabled():
        return None

    sig_str = json.dumps({
        "pair": signal.get("pair", ""),
        "direction": signal.get("direction", ""),
        "entry": signal.get("entry", 0),
        "sl": signal.get("sl", 0),
        "tp": signal.get("tp", 0),
        "tp2": signal.get("tp2", 0),
        "tp3": signal.get("tp3", 0),
        "order_type": signal.get("order_type", "Market"),
        "source": signal.get("source", ""),
    }, ensure_ascii=False)

    open_summary = []
    if open_positions:
        for p in open_positions[:10]:
            open_summary.append(f"{p.get('direction','?')} {p.get('pair','?')}")
    open_str = ", ".join(open_summary) if open_summary else "none"

    prompt = (
        f"Signal to evaluate: {sig_str}\n"
        f"Open positions: {open_str}\n"
        f"Live price: {live_price}\n"
        f"ATR (volatility): {atr}\n\n"
        f"Evaluate and return JSON only."
    )

    result = _call_llm(_PRETRADE_SYSTEM, prompt, max_tokens=300)
    if not result:
        return None
    parsed = _parse_json_response(result)
    if parsed:
        _features_stats["pretrade"] += 1
    return parsed


# ═══════════════════════════════════════════════════════════════════
# FEATURE 4: Post-Trade Analysis (Learning from SL hits)
# ═══════════════════════════════════════════════════════════════════
_POSTTRADE_SYSTEM = """You analyze why a trade hit Stop Loss.
Be brutally honest and useful — the goal is to learn.
Reply in 2-4 short bullet points covering:
- Likely cause (news, volatility, S/R rebound, weak setup)
- Was it avoidable?
- One specific lesson for next time

Reply in Spanish. Max 5 lines."""


def posttrade_analysis(signal: dict, sl_pips: float, duration_seconds: int = 0,
                        market_context: dict = None) -> Optional[str]:
    """Analiza por que se perdio una senhal."""
    if not _llm_enabled():
        return None

    ctx = []
    ctx.append(f"Pair: {signal.get('pair','')}")
    ctx.append(f"Direction: {signal.get('direction','')}")
    ctx.append(f"Entry: {signal.get('entry',0)}")
    ctx.append(f"SL: {signal.get('sl',0)}")
    ctx.append(f"TP1: {signal.get('tp',0)}")
    ctx.append(f"Source: {signal.get('source','')}")
    ctx.append(f"Pips lost: {sl_pips}")
    if duration_seconds > 0:
        h = int(duration_seconds // 3600)
        m = int((duration_seconds % 3600) // 60)
        ctx.append(f"Duration: {h}h {m}m")
    if market_context:
        for k, v in market_context.items():
            ctx.append(f"{k}: {v}")

    prompt = "Trade lost (SL hit):\n" + "\n".join(ctx) + "\n\nWhy did it fail? What can we learn?"
    result = _call_llm(_POSTTRADE_SYSTEM, prompt, max_tokens=300)
    if result:
        _features_stats["posttrade"] += 1
    return result


# ═══════════════════════════════════════════════════════════════════
# FEATURE 5: Auto-Content Generation
# ═══════════════════════════════════════════════════════════════════
_IG_CAPTION_SYSTEM = """You write Instagram captions for a trading signals account (BuySell365 Pro).
Style:
- Punchy, professional, real (no hype/scam vibes)
- Use emojis tastefully (max 5-7 per caption)
- Spanish primary, English line below if relevant
- 60-150 words
- Always include CTA: Telegram VIP link or buysell365.pro
- Hashtags at the end: 8-12 relevant ones
- Mention real numbers (pips/pts won)

DO NOT:
- Promise profits ("guaranteed gains")
- Use ALL CAPS shouting
- Mention competitor channels"""


def generate_ig_caption(stats: dict, language: str = "es") -> Optional[str]:
    """Genera caption para post de Instagram basado en stats del dia/semana."""
    if not _llm_enabled():
        return None
    stats_str = json.dumps(stats, ensure_ascii=False, default=str)[:1500]
    prompt = (
        f"Generate Instagram caption for these stats:\n{stats_str}\n\n"
        f"Primary language: {language}. Return only the caption, no explanation."
    )
    result = _call_llm(_IG_CAPTION_SYSTEM, prompt, max_tokens=500)
    if result:
        _features_stats["content"] += 1
    return result


_DAILY_SUMMARY_SYSTEM = """You write daily trading summaries for VIP Telegram channel.
Style:
- Direct, professional, no fluff
- Spanish (or English if requested)
- 8-15 lines max
- Use emojis: ✅ ❌ 💰 📊 🎯 ⚠️
- Mention top winners by pair
- If losses, frame as learning (not promise)
- End with motivational line + VIP CTA"""


def generate_daily_summary(stats: dict, language: str = "es") -> Optional[str]:
    """Genera resumen diario natural para el canal VIP."""
    if not _llm_enabled():
        return None
    stats_str = json.dumps(stats, ensure_ascii=False, default=str)[:2000]
    prompt = (
        f"Generate VIP daily summary for these stats:\n{stats_str}\n\n"
        f"Primary language: {language}. Return only the summary."
    )
    result = _call_llm(_DAILY_SUMMARY_SYSTEM, prompt, max_tokens=600)
    if result:
        _features_stats["content"] += 1
    return result


# ═══════════════════════════════════════════════════════════════════
# FEATURE 6: Reliability Score (Aliado evaluation)
# ═══════════════════════════════════════════════════════════════════
_RELIABILITY_SYSTEM = """You evaluate the reliability of a trading signal source (aliado).
Return ONLY valid JSON:
{
  "score": 0-100 (overall reliability),
  "strengths": ["strength1", ...],
  "weaknesses": ["weakness1", ...],
  "recommendation": "trust"|"trust_with_caveats"|"reduce_size"|"disable",
  "notes": "1-2 sentences explaining the verdict"
}

Consider:
- Win rate (>=60% good, 50-60% mediocre, <50% bad)
- R:R average (>=1.5 good)
- Drift between sent entry and actual MT5 fill (<5 pips/pts good)
- Consistency across sessions (London/NY/Asia)
- Size of sample (>=20 trades = significant)"""


def evaluate_aliado_reliability(aliado_name: str, stats: dict) -> Optional[dict]:
    """Evalua reliability de un aliado. stats debe tener trades, wr, etc."""
    if not _llm_enabled():
        return None
    stats_str = json.dumps(stats, ensure_ascii=False, default=str)[:2000]
    prompt = (
        f"Aliado: {aliado_name}\n"
        f"Stats:\n{stats_str}\n\n"
        f"Evaluate reliability. JSON only."
    )
    result = _call_llm(_RELIABILITY_SYSTEM, prompt, max_tokens=500)
    if not result:
        return None
    parsed = _parse_json_response(result)
    if parsed:
        _features_stats["reliability"] += 1
    return parsed


# ═══════════════════════════════════════════════════════════════════
# FEATURE 7: Multi-Language Auto-Detect
# ═══════════════════════════════════════════════════════════════════
def detect_user_language(text: str) -> str:
    """Detecta idioma. Returns ISO 639-1 code (es, en, de, pt, fr, etc).
    Heuristics first (fast), LLM fallback for ambiguous."""
    if not text or len(text) < 4:
        return "es"
    text_low = text.lower()

    # Heuristicas baratas (cubren 95% de casos sin API call)
    spanish_markers = ["el ", "la ", "los ", "que ", "con ", "para ", "señal", "señales", "ñ", "qué", "está", "días"]
    english_markers = ["the ", "is ", "are ", "what ", "with ", "for ", "signal", "trade", "would", "could"]
    portuguese_markers = ["você", "está", "não", "obrigado", "como", "porque"]
    german_markers = ["der ", "die ", "das ", "und ", "ich ", "nicht", "über", "schön"]
    french_markers = ["le ", "la ", "les ", "vous ", "êtes", "merci", "bonjour", "où"]
    arabic = any(0x0600 <= ord(c) <= 0x06FF for c in text)
    chinese = any(0x4E00 <= ord(c) <= 0x9FFF for c in text)
    russian = any(0x0400 <= ord(c) <= 0x04FF for c in text)
    italian_markers = ["il ", "lo ", "gli ", "che ", "sono ", "perché", "grazie"]

    if arabic: return "ar"
    if chinese: return "zh"
    if russian: return "ru"

    scores = {
        "es": sum(1 for m in spanish_markers if m in text_low),
        "en": sum(1 for m in english_markers if m in text_low),
        "pt": sum(1 for m in portuguese_markers if m in text_low),
        "de": sum(1 for m in german_markers if m in text_low),
        "fr": sum(1 for m in french_markers if m in text_low),
        "it": sum(1 for m in italian_markers if m in text_low),
    }
    best_lang, best_score = max(scores.items(), key=lambda kv: kv[1])
    if best_score >= 1:
        _features_stats["language"] += 1
        return best_lang
    return "es"  # default


# ═══════════════════════════════════════════════════════════════════
# FEATURE 8: Lead Detection (in public group)
# ═══════════════════════════════════════════════════════════════════
_LEAD_SYSTEM = """You detect if a Telegram message is from a HOT LEAD interested in VIP signals.
Return ONLY JSON:
{
  "is_lead": true|false,
  "score": 1-10 (10=very hot),
  "intent": "asking_about_vip"|"complaining_losses"|"showing_capital"|"asking_strategy"|"general"|"none",
  "suggested_action": "DM admin"|"reply with VIP info"|"ignore"
}

HOT signals:
- "¿cuánto cuesta el VIP?", "tell me about premium"
- "perdí $500 esta semana", "no gano nada con mis señales"
- "tengo $1000 para empezar"
- "qué señales recomiendan", "necesito ayuda con mi estrategia"

NOT a lead:
- General greetings ("hola"), emojis, jokes
- Promotional spam from other accounts
- Replies to other users (not bot)"""


def detect_lead(message: str, user_name: str = "") -> Optional[dict]:
    """Detecta si un mensaje del grupo publico es un lead caliente."""
    if not _llm_enabled():
        return None
    if not message or len(message) < 5:
        return None
    h = hashlib.md5(message.encode("utf-8")).hexdigest()
    prompt = f"User: {user_name}\nMessage: {message[:500]}\n\nIs this a lead? JSON only."
    result = _call_llm(_LEAD_SYSTEM, prompt, max_tokens=200,
                       cache_key=f"lead_{h}", cache_ttl=86400)
    if not result:
        return None
    parsed = _parse_json_response(result)
    if parsed and parsed.get("is_lead"):
        _features_stats["lead"] += 1
    return parsed


# ═══════════════════════════════════════════════════════════════════
# FEATURE 9: VIP Onboarding (mini-interview)
# ═══════════════════════════════════════════════════════════════════
_ONBOARDING_SYSTEM = """You generate the next question for VIP onboarding interview.
Goal: collect (in order) capital, experience level, preferred pairs, risk tolerance.
Each question must be SHORT (1-2 lines), warm, with example answers.
Use the user's language. Use emojis sparingly (👋 💰 📊 🎯 🛡️).

Return ONLY the question text (no JSON, no metadata)."""


def onboarding_next_question(answers_so_far: dict, language: str = "es") -> Optional[str]:
    """Genera la siguiente pregunta del flow de onboarding VIP."""
    if not _llm_enabled():
        return None
    answered_str = json.dumps(answers_so_far, ensure_ascii=False) if answers_so_far else "{}"
    prompt = (
        f"Onboarding state (answers given so far): {answered_str}\n"
        f"Language: {language}\n\n"
        f"What's the next question? (or 'DONE' if all data collected)"
    )
    result = _call_llm(_ONBOARDING_SYSTEM, prompt, max_tokens=200)
    if result:
        _features_stats["onboarding"] += 1
    return result


_ONBOARDING_SUMMARY_SYSTEM = """You summarize a VIP onboarding interview into actionable setup.
Return ONLY JSON:
{
  "recommended_lot_size": 0.01,
  "recommended_pairs": ["XAUUSD", "EURUSD"],
  "max_risk_per_trade_pct": 1.0,
  "session_recommendation": "London+NY",
  "personalized_welcome": "Hola Juan, basado en tu perfil..."
}"""


def onboarding_summary(answers: dict, language: str = "es") -> Optional[dict]:
    """Convierte respuestas en setup personalizado."""
    if not _llm_enabled():
        return None
    prompt = (
        f"Onboarding answers: {json.dumps(answers, ensure_ascii=False)}\n"
        f"Language: {language}\n\nReturn JSON setup only."
    )
    result = _call_llm(_ONBOARDING_SUMMARY_SYSTEM, prompt, max_tokens=500)
    if not result:
        return None
    return _parse_json_response(result)


# ═══════════════════════════════════════════════════════════════════
# FEATURE 10: Smart News Alerts
# ═══════════════════════════════════════════════════════════════════
_NEWS_SYSTEM = """You filter financial news for trading relevance.
Given a news headline + open trading positions, return ONLY JSON:
{
  "relevant": true|false,
  "impact": "high"|"medium"|"low",
  "affected_pairs": ["XAUUSD", "EURUSD"],
  "action_recommended": "move_sl_to_entry"|"close_partial"|"watch"|"none",
  "summary": "1 line in Spanish",
  "minutes_to_watch": 30
}

Relevant (impact=high):
- Fed/ECB/BOE/BOJ decisions or speeches
- NFP, CPI, GDP releases
- War/geopolitical (oil, gold)
- Major company earnings if pair is index

Not relevant:
- Crypto news (unless pair is crypto)
- Local political news (unless major)
- Stock-specific (unless index pair open)"""


def analyze_news_relevance(headline: str, open_pairs: list,
                            news_time_utc: str = "", language: str = "es") -> Optional[dict]:
    """Filtra noticias por relevancia para los pares abiertos."""
    if not _llm_enabled():
        return None
    if not headline or not open_pairs:
        return None
    pairs_str = ", ".join(open_pairs[:10])
    prompt = (
        f"Headline: {headline[:300]}\n"
        f"News time: {news_time_utc}\n"
        f"Open pairs: {pairs_str}\n"
        f"Language: {language}\n\nJSON only."
    )
    h = hashlib.md5((headline + pairs_str).encode("utf-8")).hexdigest()
    result = _call_llm(_NEWS_SYSTEM, prompt, max_tokens=300,
                       cache_key=f"news_{h}", cache_ttl=3600)  # 1h cache for news
    if not result:
        return None
    parsed = _parse_json_response(result)
    if parsed:
        _features_stats["news"] += 1
    return parsed


# ═══════════════════════════════════════════════════════════════════
# FEATURE 12: Pre-Publish Second Opinion — pilar de confianza
# Antes de publicar una senal al canal VIP, Sonnet evalua coherencia integral.
# En modo SHADOW por defecto (LLM_PREPUBLISH_MODE=shadow): LOGUEA que haria
# pero NO bloquea. Tras 7 dias de shadow OK, cambiar a "active" en .env.
# ═══════════════════════════════════════════════════════════════════
_PREPUBLISH_SYSTEM = """You are a final pre-publish validator for a Telegram VIP signals channel.

You receive a signal that already passed regex/LLM parser, auto-fix, and stale checks.
IMPORTANT: The stale-entry check already rejected signals with entry >0.8% away from market.
So if this signal reached you, the entry is within range — do NOT re-reject for entry distance.

ONLY block (publish=false) for CLEAR structural errors:
1. TP on WRONG SIDE of entry (BUY with TP < entry, SELL with TP > entry) → publish=false
2. SL on WRONG SIDE of entry (BUY with SL > entry, SELL with SL < entry) → publish=false
3. Pair name is gibberish or clearly not a real instrument → publish=false

DO NOT block based on absolute price values. Your knowledge cutoff means you do NOT
know current market prices — gold may be at 3000, 4000, 5000+ depending on date.
The system already validated entry-vs-current-price externally (current_price field).
If current_price is provided and entry differs by <20% from it, the entry is fine
regardless of how "high" or "low" the number looks to you.

ALWAYS publish=true for:
- BUY LIMIT (entry below current price): VALID pending order, do not block
- SELL STOP (entry below current price for SELL): VALID breakout strategy, do not block
- SELL LIMIT (entry above current price for SELL): VALID pending order, do not block
- BUY STOP (entry above current price for BUY): VALID breakout strategy, do not block
- Tight SL (even 5-8 pips on gold): valid if SL is on the correct side
- Source win rate: NEVER use as a blocking reason — we publish all ally signals regardless

Reply ONLY with this JSON:
{
  "publish": true|false,
  "confidence": 0-100,
  "reason": "1-line explanation",
  "concerns": ["concern 1", "concern 2"]
}

Default to publish=true unless there is a CLEAR structural error listed above.
When in doubt → publish=true."""


def prepublish_second_opinion(signal: dict, current_price: float = 0,
                               source_recent_wr: float = -1) -> Optional[dict]:
    """Sonnet evalua una senal antes de publicarla al canal VIP.

    Args:
        signal: dict con pair, direction, entry, sl, tp, tp2..tp5, source
        current_price: precio MT5/yfinance actual
        source_recent_wr: Win Rate reciente del aliado (0-1) o -1 si desconocido

    Returns:
        dict {publish, confidence, reason, concerns} o None si LLM no respondio.
        En SHADOW mode el caller IGNORA el campo publish (solo loguea).
    """
    if not _llm_enabled():
        return None
    _info = {
        "pair": signal.get("pair"),
        "direction": signal.get("direction"),
        "entry": signal.get("entry"),
        "entry2": signal.get("entry2"),
        "sl": signal.get("sl"),
        "tp": signal.get("tp"),
        "tp2": signal.get("tp2"),
        "tp3": signal.get("tp3"),
        "tp4": signal.get("tp4"),
        "tp5": signal.get("tp5"),
        "source": signal.get("source"),
        "current_price": current_price,
        "source_recent_wr": source_recent_wr if source_recent_wr >= 0 else "unknown",
    }
    prompt = f"Signal to validate:\n{json.dumps(_info, ensure_ascii=False, default=str)}\n\nReturn JSON."
    result = _call_llm(_PREPUBLISH_SYSTEM, prompt, max_tokens=400)
    if not result:
        return None
    parsed = _parse_json_response(result)
    if parsed:
        _features_stats["prepublish"] = _features_stats.get("prepublish", 0) + 1
    return parsed


# ═══════════════════════════════════════════════════════════════════
# FEATURE 13: Wick-vs-Real-Hit Validator — pilar de confianza
# Cuando monitor dispara TP/SL HIT, Sonnet valida que no fue una mecha
# transitoria (precio cruzo y rebotó en <5s).
# Modo SHADOW por defecto (LLM_WICK_VALIDATOR_MODE=shadow).
# ═══════════════════════════════════════════════════════════════════
_WICK_VALIDATOR_SYSTEM = """You are a price-action validator for trading signal celebrations.

You receive: signal info, hit type (TP or SL), and recent price ticks (last 30 seconds).

DECIDE: was the cross sustained or a momentary wick that already reversed?

A SUSTAINED hit:
- Price crossed and stayed past the level for >5 seconds
- Multiple ticks confirm the level was breached

A WICK (false hit):
- Single tick crossed, immediately reverted
- Volatile spike during news, instantly reversed
- Price is now BACK on the wrong side of the level

Reply ONLY with this JSON:
{
  "is_wick": true|false,
  "confidence": 0-100,
  "reason": "1-line",
  "wait_seconds": 0|30|60
}

If wait_seconds > 0, monitor should re-check after that delay before celebrating.
Default to is_wick=false unless evidence is clear."""


def wick_validator(signal: dict, hit_type: str, hit_level: float,
                   recent_ticks: list) -> Optional[dict]:
    """Valida que un TP/SL HIT no es una mecha transitoria.

    Args:
        signal: pair, direction, entry, sl, tp
        hit_type: "TP" o "SL"
        hit_level: el precio TP o SL que se cruzo
        recent_ticks: lista de [(timestamp, price)] ultimos 30s

    Returns:
        dict {is_wick, confidence, reason, wait_seconds} o None.
    """
    if not _llm_enabled():
        return None
    _info = {
        "pair": signal.get("pair"),
        "direction": signal.get("direction"),
        "entry": signal.get("entry"),
        "hit_type": hit_type,
        "hit_level": hit_level,
        "recent_ticks": [{"ts": t, "p": p} for t, p in (recent_ticks or [])[-15:]],
    }
    prompt = f"Hit to validate:\n{json.dumps(_info, ensure_ascii=False, default=str)}\n\nReturn JSON."
    result = _call_llm(_WICK_VALIDATOR_SYSTEM, prompt, max_tokens=300)
    if not result:
        return None
    parsed = _parse_json_response(result)
    if parsed:
        _features_stats["wick_validator"] = _features_stats.get("wick_validator", 0) + 1
    return parsed


# ═══════════════════════════════════════════════════════════════════
# FEATURE 14: Self-Test cada 15 minutos — pilar de confianza
# Sonnet recibe snapshot mini del estado del sistema y dice
# "healthy" o "anomaly X". Detecta degradacion temprana.
# ═══════════════════════════════════════════════════════════════════
_SELFTEST_SYSTEM = """You are a runtime health checker for a Telegram trading signals system.

You receive a snapshot of the system state every 15 minutes. Detect REAL anomalies
that suggest the system is degrading or stuck — NOT normal idle periods.

INDICATORS to evaluate:
- open_signals_count: typical 1-15. >30 = backlog. 0 with recent activity = monitor stuck?
- mt5_connected: should be true unless market closed
- last_signal_published_age_min: 999 = sentinel meaning "no signals yet OR > 24h ago".
  This is NORMAL during weekends, holidays, or quiet periods. Only flag if > 24h
  during clearly active trading hours (Mon-Fri 8h-22h London time).
- last_heartbeat_age_sec: should be <30. >90 = process stuck
- error_count_last_15min: 0-2 normal. >10 = degradation
- outbox_pending: 0-3 normal. >10 = backed up
- llm_calls_last_15min: depends. 0 calls IS NORMAL when 0 events come in.

CRITICAL RULE: Do NOT flag WARN/ALERT when the system is simply IDLE
(no events, no signals, no errors, heartbeat fresh, MT5 connected).
That is healthy operation during quiet market periods.

Only flag WARN when:
- error_count_last_15min > 5
- last_heartbeat_age_sec > 90
- open_signals_count > 30 (backlog)
- outbox_pending > 10

Only flag ALERT when:
- mt5_connected = false during market hours
- error_count_last_15min > 20
- last_heartbeat_age_sec > 300 (5 min — process likely dead)

Reply ONLY with this JSON:
{
  "healthy": true|false,
  "anomaly": null | "description",
  "severity": "OK|WARN|ALERT",
  "action_recommended": null | "DM admin" | "restart copier" | "investigate"
}"""


def selftest_check(snapshot: dict) -> Optional[dict]:
    """Sonnet evalua snapshot del sistema cada 15 min."""
    if not _llm_enabled():
        return None
    prompt = f"System snapshot:\n{json.dumps(snapshot, ensure_ascii=False, default=str)}\n\nReturn JSON."
    result = _call_llm(_SELFTEST_SYSTEM, prompt, max_tokens=250)
    if not result:
        return None
    parsed = _parse_json_response(result)
    if parsed:
        _features_stats["selftest"] = _features_stats.get("selftest", 0) + 1
    return parsed


# ═══════════════════════════════════════════════════════════════════
# FEATURE 15: Recovery LLM tras reinicio — pilar de confianza
# Tras boot del bot/copier, Sonnet revisa los 3 JSONs + posiciones MT5 +
# ultimos mensajes del canal y sugiere reconciliacion (orphans, stale, etc).
# ═══════════════════════════════════════════════════════════════════
_RECOVERY_SYSTEM = """You are a recovery analyst for a trading signals system that just restarted.

You receive snapshots of the system state RIGHT AFTER restart:
- estado.json snapshot (operaciones_activas, VIPs, etc.)
- copier_open_signals.json (signals being tracked)
- mt5_positions: live positions in broker
- recent_channel_msgs: last 20 events from events.jsonl (3h window) including
  signal.published, signal.mt5_result, signal.tp_hit, signal.sl_hit

CRITICAL RULES — read carefully before flagging anything:

RULE 1 — CLEAN STATE: If open_signals_count=0 AND mt5_positions is empty,
the system is CLEAN. Any published signals without closure events were already
closed normally, or were never executed in MT5. Return needs_recovery=false,
summary="HEALTHY", no critical actions.

RULE 2 — NON-EXECUTED SIGNALS: The bot publishes ALL signals to the VIP channel
(even Forex pairs like EURUSD, GBPUSD, USDCAD, CADCHF, AUDCHF, etc.) but only
executes certain instruments in MT5 (primarily GOLD/XAUUSD, US30, NAS100).
If a signal.published event has mt5_executed=false, OR if a signal.mt5_result
event shows executed=false, that signal was NEVER placed in MT5. Do NOT flag it
as an orphan. It is informational-only publication.

RULE 3 — TRUE ORPHAN: A signal is a TRUE orphan ONLY if:
  a) signal.mt5_result shows executed=true (position WAS placed in MT5), AND
  b) There is no signal.tp_hit or signal.sl_hit closure event for it, AND
  c) There is no matching live MT5 position for that pair+direction.
Only TRUE orphans should be flagged with confidence>=90.

RULE 4 — OLD EVENTS WITHOUT mt5_result: If a signal.published event has no
corresponding signal.mt5_result event (older events before this feature was added),
and mt5_positions is empty, treat it as NON-EXECUTED (RULE 2 applies).

DETECT and SUGGEST actions for:
1. TRUE ORPHAN signals (see RULE 3 above — strict criteria)
2. ORPHAN positions: live MT5 positions with MAGIC_COPIER but no entry in
   copier_open_signals.json -> need to add tracking entry.
3. STALE channel-vs-tracker: tp_hit/sl_hit event exists but signal still open in tracker.

Reply ONLY with this JSON:
{
  "needs_recovery": true|false,
  "actions": [
    {"action": "remove_orphan_signal|add_tracking|mark_resolved|notify_admin",
     "target": "sig_id or position_ticket",
     "reason": "explanation",
     "confidence": 0-100}
  ],
  "summary": "1-line: HEALTHY | NEEDS_RECONCILE | CRITICAL"
}

If confidence > 90 the bot can apply auto. Else send to admin for approval."""


def recovery_check(estado_snap: dict, open_signals_snap: dict,
                   mt5_positions: list = None,
                   recent_channel_msgs: list = None) -> Optional[dict]:
    """Sonnet analiza estado tras reinicio y sugiere reconciliacion."""
    if not _llm_enabled():
        return None
    _info = {
        "estado_summary": {
            "operaciones_activas_count": len(estado_snap.get("operaciones_activas", {})),
            "vips_count": len(estado_snap.get("suscripciones_vip", {})),
            "last_modified": estado_snap.get("_last_modified", "unknown"),
        },
        "open_signals_count": len(open_signals_snap),
        "open_signals_sample": [
            {"sig_id": k, "pair": v.get("signal", v).get("pair") if isinstance(v, dict) else None,
             "direction": v.get("signal", v).get("direction") if isinstance(v, dict) else None,
             "sent_at": v.get("sent_at") if isinstance(v, dict) else None}
            for k, v in list(open_signals_snap.items())[:15]
        ],
        "mt5_positions": (mt5_positions or [])[:15],
        "recent_channel_msgs": (recent_channel_msgs or [])[-20:],
    }
    prompt = f"Post-restart state:\n{json.dumps(_info, ensure_ascii=False, default=str)}\n\nReturn JSON."
    result = _call_llm(_RECOVERY_SYSTEM, prompt, max_tokens=1500)
    if not result:
        return None
    parsed = _parse_json_response(result)
    if parsed:
        _features_stats["recovery"] = _features_stats.get("recovery", 0) + 1
    return parsed


# ═══════════════════════════════════════════════════════════════════
# FEATURE 16: Source-trust scorer — pilar de confianza
# Antes de procesar senal de aliado X, Sonnet ve sus ultimas 30 senales y
# sugiere multiplier 0.5-2.0 para size (o "skip" si demasiado malo).
# ═══════════════════════════════════════════════════════════════════
_SOURCE_TRUST_SYSTEM = """You are a source reliability scorer for a Telegram signals copier.

You receive: source name + last 30 signals (entry/result/pips).

EVALUATE the source's recent performance:
- Win Rate
- Avg pips per win vs avg loss
- Streak (last 10 in a row losses?)
- Consistency
- Volume (too few signals = unreliable, too many = spam)

Reply ONLY with this JSON:
{
  "trust_score": 0-100,
  "size_multiplier": 0.0-2.0,
  "skip": true|false,
  "reason": "1-line"
}

Guidelines:
- WR > 60% with R:R > 1: multiplier 1.0-1.5
- WR 40-60% with R:R > 1.5: multiplier 0.7-1.0
- WR < 40% OR last 10 all losses: multiplier 0.0-0.3 or skip=true
- < 5 historical signals: multiplier 0.5 (cautious until we know)"""


def source_trust_score(source_name: str, recent_signals: list) -> Optional[dict]:
    """Sonnet evalua fiabilidad de un canal aliado."""
    if not _llm_enabled():
        return None
    _info = {
        "source": source_name,
        "signals_count": len(recent_signals),
        "signals_sample": recent_signals[-30:],
    }
    prompt = f"Evaluate source:\n{json.dumps(_info, ensure_ascii=False, default=str)}\n\nReturn JSON."
    result = _call_llm(_SOURCE_TRUST_SYSTEM, prompt, max_tokens=300,
                       cache_key=f"source_trust_{source_name}_{len(recent_signals)}",
                       cache_ttl=3600)  # cache 1h por fuente
    if not result:
        return None
    parsed = _parse_json_response(result)
    if parsed:
        _features_stats["source_trust"] = _features_stats.get("source_trust", 0) + 1
    return parsed


# ═══════════════════════════════════════════════════════════════════
# FEATURE 11: Daily Auditor — pilar de confianza
# ═══════════════════════════════════════════════════════════════════
_DAILY_AUDITOR_SYSTEM = """You are a forensic auditor for a Telegram trading signals system.

Your job: review ALL activity from the last 24h and detect inconsistencies that humans
might miss. The system publishes signals from allied channels to a VIP channel, and
tracks TP/SL hits automatically.

You will receive:
- Events log (signal.published, signal.tp_hit, signal.sl_hit, etc.)
- Open signals state snapshot
- Stats (wins, losses, pips)
- Recent errors
- Outbox state (pending/dropped messages)

DETECT (in priority order):
1. CRITICAL: signals published but never tracked (no tp_hit/sl_hit/cancel event)
2. CRITICAL: tp_hit OR sl_hit emitted twice for the same signal (double celebration)
3. CRITICAL: messages dropped from outbox (notification lost forever)
4. HIGH: signal published with sl=0 (risk unbounded)
5. HIGH: TP hit reported but price logs show price never crossed TP
6. MEDIUM: signals from low-WR allies (multiple losses in a row)
7. MEDIUM: gap > 1h between events (system was down or stuck)
8. LOW: cosmetic issues (flag wrong, formatting)

Reply ONLY with this JSON (no markdown, no extra text):
{
  "summary": "1-line assessment: HEALTHY | WARNING | CRITICAL",
  "issues": [
    {"severity": "CRITICAL|HIGH|MEDIUM|LOW", "type": "...", "details": "...", "evidence": "..."}
  ],
  "recommendations": ["action 1", "action 2"],
  "metrics_observed": {"signals_total": N, "wins": N, "losses": N, "errors": N}
}

Be brutally honest. If nothing wrong, say so plainly. Don't invent issues."""


def daily_audit(events_today: list, open_signals: dict, stats: dict,
                outbox_state: dict, recent_errors: list = None) -> Optional[dict]:
    """Auditor LLM nocturno. Recibe el dump del dia y reporta inconsistencias.

    Llamar 23:30 Andorra desde bot.py scheduler. El resultado se envia por DM
    al admin con cualquier issue CRITICAL/HIGH detectado.

    Args:
        events_today: lista de eventos del events_log (read_events(since=hoy_00))
        open_signals: dict de copier_open_signals.json actual
        stats: dict de copier_stats.json
        outbox_state: dict {pending, dropped} del outbox
        recent_errors: lista de errores recientes (opcional)

    Returns:
        dict {summary, issues, recommendations, metrics_observed} o None si fallo.
    """
    if not _llm_enabled():
        return None
    # Comprimir input (Sonnet tiene 200K context pero queremos eficiencia)
    _events_compact = []
    for e in events_today[-300:]:  # ultimos 300 eventos
        _events_compact.append({
            "ts": e.get("iso", ""),
            "type": e.get("type"),
            "data": {k: v for k, v in (e.get("data") or {}).items() if k in
                     ("pair", "direction", "msg_id", "tp_num", "error", "tag", "destination")},
        })
    _open_compact = []
    for sid, sd in list(open_signals.items())[:30]:
        _s = sd.get("signal", sd) if isinstance(sd, dict) else {}
        _open_compact.append({
            "sig_id": sid,
            "pair": _s.get("pair"),
            "direction": _s.get("direction"),
            "entry": _s.get("entry"),
            "tp_idx": _s.get("_tp_idx"),
            "tps_alcanzados": len(_s.get("_tps_alcanzados", [])),
        })
    prompt = (
        f"=== EVENTS LAST 24H ({len(events_today)} total, showing last 300) ===\n"
        f"{json.dumps(_events_compact, ensure_ascii=False, default=str)}\n\n"
        f"=== OPEN SIGNALS RIGHT NOW ({len(open_signals)} total) ===\n"
        f"{json.dumps(_open_compact, ensure_ascii=False, default=str)}\n\n"
        f"=== STATS TODAY ===\n"
        f"{json.dumps(stats, ensure_ascii=False, default=str)}\n\n"
        f"=== OUTBOX STATE ===\n"
        f"{json.dumps(outbox_state, ensure_ascii=False, default=str)}\n\n"
        f"=== RECENT ERRORS ===\n"
        f"{json.dumps((recent_errors or [])[-20:], ensure_ascii=False, default=str)}\n\n"
        f"Audit this and return the JSON."
    )
    result = _call_llm(_DAILY_AUDITOR_SYSTEM, prompt, max_tokens=2000, cache_key=None)
    if not result:
        return None
    parsed = _parse_json_response(result)
    if parsed:
        _features_stats["audit"] = _features_stats.get("audit", 0) + 1
    return parsed


# ═══════════════════════════════════════════════════════════════════
# Stats access
# ═══════════════════════════════════════════════════════════════════
def get_features_stats() -> dict:
    """Acceso publico a metricas de features.

    FIX 2026-05-01: Si el dict en memoria esta vacio (caller en proceso distinto
    del que hace las API calls — ej: launcher leyendo stats del copier), cae al
    JSON persistido. Asi el dashboard del launcher ve el coste real, no $0.
    """
    _mem = dict(_features_stats)
    _has_activity_in_mem = any(v for k, v in _mem.items() if isinstance(v, (int, float)) and k != "errors")
    if _has_activity_in_mem:
        return _mem
    _disk = _load_stats_from_disk()
    return _disk if _disk else _mem


def reset_features_stats() -> None:
    for k in _features_stats:
        _features_stats[k] = 0
    _flush_stats_to_disk(force=True)

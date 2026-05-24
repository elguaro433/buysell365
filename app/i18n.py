"""
i18n.py — Internacionalización per-usuario para BuySell365 Pro.

FIX 2026-04-30: detecta idioma del usuario via Telegram language_code y devuelve
plantillas en su idioma. Cubre 8 idiomas top: ES, EN, PT, AR, FR, DE, IT, RU.
Si el código es desconocido → fallback a English.

Uso:
    from i18n import t, get_user_lang
    lang = get_user_lang(user_dict)  # user_dict del update Telegram
    msg = t("welcome_group", lang, name="Ahmed")
"""

# Mapeo de language_code (ISO) → bucket de idioma soportado
_LANG_MAP = {
    "es": "es", "es-es": "es", "es-mx": "es", "es-ar": "es", "es-co": "es",
    "es-cl": "es", "es-pe": "es", "es-ve": "es", "es-uy": "es", "es-ec": "es",
    "en": "en", "en-us": "en", "en-gb": "en", "en-au": "en", "en-ca": "en",
    "pt": "pt", "pt-br": "pt", "pt-pt": "pt",
    "ar": "ar", "ar-sa": "ar", "ar-eg": "ar", "ar-ae": "ar", "ar-ma": "ar",
    "fr": "fr", "fr-fr": "fr", "fr-ca": "fr", "fr-be": "fr",
    "de": "de", "de-de": "de", "de-at": "de", "de-ch": "de",
    "it": "it", "it-it": "it",
    "ru": "ru", "ru-ru": "ru",
    "uk": "ru",  # Ucraniano — fallback ruso (lo entienden)
    "be": "ru",  # Bielorruso — fallback ruso
}

DEFAULT_LANG = "en"


def get_user_lang(user_dict: dict) -> str:
    """Extrae el idioma del user_dict de Telegram.
    user_dict viene de update.message.from o similar.
    Retorna código de bucket: 'es', 'en', 'pt', 'ar', 'fr', 'de', 'it', 'ru'.
    Default 'en' si no se detecta.
    """
    if not user_dict:
        return DEFAULT_LANG
    code = (user_dict.get("language_code") or "").strip().lower()
    if not code:
        return DEFAULT_LANG
    # Match exacto primero
    if code in _LANG_MAP:
        return _LANG_MAP[code]
    # Match por prefijo (e.g., "es-419" → "es")
    base = code.split("-")[0]
    return _LANG_MAP.get(base, DEFAULT_LANG)


# ════════════════════════════════════════════════════════════════
#  TRADUCCIONES
# ════════════════════════════════════════════════════════════════
#
# Cada key tiene 8 versiones. Placeholders: {name}, {wr}, {ops}, etc.
# Markdown de Telegram: *bold*, _italic_, [text](url), `code`.
# Emojis funcionan igual en todos los idiomas.
#

TRANSLATIONS = {
    # ─────────────────────────────────────────────────────
    # welcome_group — DM al nuevo miembro del grupo público
    # ─────────────────────────────────────────────────────
    "welcome_group": {
        "en": (
            "👋 *Hi {name}!*\n\n"
            "Welcome to *BuySell365 Pro* 🚀\n\n"
            "You're in the right place if you love trading. The group is "
            "*100% free* — feel free to explore, watch the signals we post, "
            "the analysis, and the real numbers we share each week.\n\n"
            "🎁 *Every day we give away 2 free signals* in the group — "
            "with exact Entry, SL and TP. Just stay tuned.\n\n"
            "Got any questions? Tap the buttons below 👇"
        ),
        "es": (
            "👋 *¡Hola {name}!*\n\n"
            "Bienvenido a *BuySell365 Pro* 🚀\n\n"
            "Estás en el lugar correcto si te apasiona el trading. El grupo es "
            "*100% gratis* — explora, mira las señales que publicamos, los "
            "análisis y los resultados reales que compartimos cada semana.\n\n"
            "🎁 *Cada día regalamos 2 señales gratis* en el grupo — con "
            "Entry, SL y TP exactos. Solo mantente atento.\n\n"
            "¿Alguna duda? Pulsa los botones abajo 👇"
        ),
        "pt": (
            "👋 *Olá {name}!*\n\n"
            "Bem-vindo ao *BuySell365 Pro* 🚀\n\n"
            "Você está no lugar certo se ama trading. O grupo é *100% "
            "gratuito* — explore, veja os sinais que publicamos, as análises "
            "e os resultados reais que compartilhamos toda semana.\n\n"
            "🎁 *Todos os dias damos 2 sinais grátis* no grupo — com Entry, "
            "SL e TP exatos. Fique atento.\n\n"
            "Alguma dúvida? Toque nos botões abaixo 👇"
        ),
        "ar": (
            "👋 *مرحبًا {name}!*\n\n"
            "أهلاً بك في *BuySell365 Pro* 🚀\n\n"
            "أنت في المكان الصحيح إذا كنت تحب التداول. المجموعة *مجانية "
            "100%* — استكشف الإشارات التي ننشرها والتحليلات والأرقام "
            "الحقيقية التي نشاركها كل أسبوع.\n\n"
            "🎁 *كل يوم نقدم إشارتين مجانيتين* في المجموعة — مع Entry "
            "و SL و TP دقيقة. ابقَ متابعًا.\n\n"
            "أي سؤال؟ اضغط على الأزرار أدناه 👇"
        ),
        "fr": (
            "👋 *Salut {name} !*\n\n"
            "Bienvenue sur *BuySell365 Pro* 🚀\n\n"
            "Tu es au bon endroit si tu aimes le trading. Le groupe est "
            "*100% gratuit* — explore, regarde les signaux que nous "
            "publions, les analyses et les vrais chiffres que nous partageons "
            "chaque semaine.\n\n"
            "🎁 *Chaque jour nous offrons 2 signaux gratuits* dans le "
            "groupe — avec Entry, SL et TP exacts. Reste attentif.\n\n"
            "Une question ? Appuie sur les boutons ci-dessous 👇"
        ),
        "de": (
            "👋 *Hallo {name}!*\n\n"
            "Willkommen bei *BuySell365 Pro* 🚀\n\n"
            "Du bist hier richtig, wenn du Trading liebst. Die Gruppe ist "
            "*100% kostenlos* — erkunde die Signale, die wir veröffentlichen, "
            "die Analysen und die echten Zahlen, die wir jede Woche teilen.\n\n"
            "🎁 *Täglich verschenken wir 2 kostenlose Signale* in der "
            "Gruppe — mit exakten Entry, SL und TP. Bleib dran.\n\n"
            "Fragen? Tippe auf die Buttons unten 👇"
        ),
        "it": (
            "👋 *Ciao {name}!*\n\n"
            "Benvenuto su *BuySell365 Pro* 🚀\n\n"
            "Sei nel posto giusto se ami il trading. Il gruppo è *100% "
            "gratuito* — esplora, guarda i segnali che pubblichiamo, le "
            "analisi e i numeri reali che condividiamo ogni settimana.\n\n"
            "🎁 *Ogni giorno regaliamo 2 segnali gratuiti* nel gruppo — "
            "con Entry, SL e TP esatti. Resta sintonizzato.\n\n"
            "Domande? Tocca i pulsanti qui sotto 👇"
        ),
        "ru": (
            "👋 *Привет, {name}!*\n\n"
            "Добро пожаловать в *BuySell365 Pro* 🚀\n\n"
            "Ты в правильном месте, если любишь трейдинг. Группа *100% "
            "бесплатная* — изучай сигналы, которые мы публикуем, анализы и "
            "реальные результаты, которыми делимся каждую неделю.\n\n"
            "🎁 *Каждый день мы дарим 2 бесплатных сигнала* в группе — "
            "с точным Entry, SL и TP. Просто следи.\n\n"
            "Есть вопросы? Нажми кнопки ниже 👇"
        ),
    },

    # ─────────────────────────────────────────────────────
    # welcome_vip — DM al nuevo miembro del canal VIP
    # ─────────────────────────────────────────────────────
    "welcome_vip": {
        "en": (
            "🏆 *Welcome to VIP, {name}!*\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "I'm your personal AI trading assistant. The channel is read-only "
            "— here in private I assist you directly.\n\n"
            "━━ *Your VIP access includes* ━━\n\n"
            "📡 Signals with exact Entry · SL · TP\n"
            "🔍 Real-time AI analysis: GOLD, NASDAQ, S&P500, Forex and more\n"
            "📊 Live dashboard with stats and positions\n"
            "💬 On-demand analysis of any asset\n"
            "🛠️ 24/7 personal assistant — me, right here\n\n"
            "━━ *Use the buttons to start* ━━\n"
            "Or just message me any question ✅"
        ),
        "es": (
            "🏆 *¡Bienvenido al VIP, {name}!*\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "Soy tu asistente personal de trading con IA. El canal es solo "
            "lectura — aquí en privado te atiendo a ti directamente.\n\n"
            "━━ *Tu acceso VIP incluye* ━━\n\n"
            "📡 Señales con Entry · SL · TP exactos\n"
            "🔍 Análisis IA en tiempo real: ORO, NASDAQ, S&P500, Forex y más\n"
            "📊 Dashboard en vivo con stats y posiciones\n"
            "💬 Análisis bajo petición de cualquier activo\n"
            "🛠️ Asistente personal 24/7 — yo, aquí mismo\n\n"
            "━━ *Usa los botones para empezar* ━━\n"
            "O escríbeme directamente cualquier pregunta ✅"
        ),
        "pt": (
            "🏆 *Bem-vindo ao VIP, {name}!*\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "Sou seu assistente pessoal de trading com IA. O canal é apenas "
            "leitura — aqui no privado te atendo diretamente.\n\n"
            "━━ *Seu acesso VIP inclui* ━━\n\n"
            "📡 Sinais com Entry · SL · TP exatos\n"
            "🔍 Análise IA em tempo real: GOLD, NASDAQ, S&P500, Forex e mais\n"
            "📊 Dashboard ao vivo com stats e posições\n"
            "💬 Análise sob pedido de qualquer ativo\n"
            "🛠️ Assistente pessoal 24/7 — eu, aqui mesmo\n\n"
            "━━ *Use os botões para começar* ━━\n"
            "Ou me envie qualquer pergunta ✅"
        ),
        "ar": (
            "🏆 *مرحبًا بك في VIP، {name}!*\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "أنا مساعدك الشخصي للتداول بالذكاء الاصطناعي. القناة للقراءة فقط "
            "— هنا في الخاص أخدمك مباشرة.\n\n"
            "━━ *وصولك VIP يشمل* ━━\n\n"
            "📡 إشارات مع Entry · SL · TP دقيقة\n"
            "🔍 تحليل ذكاء اصطناعي مباشر: GOLD، NASDAQ، S&P500، Forex والمزيد\n"
            "📊 لوحة معلومات حية مع إحصائيات ومراكز\n"
            "💬 تحليل عند الطلب لأي أصل\n"
            "🛠️ مساعد شخصي 24/7 — أنا، هنا\n\n"
            "━━ *استخدم الأزرار للبدء* ━━\n"
            "أو أرسل لي أي سؤال ✅"
        ),
        "fr": (
            "🏆 *Bienvenue au VIP, {name} !*\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "Je suis ton assistant personnel de trading avec IA. Le canal est "
            "en lecture seule — ici en privé je m'occupe de toi directement.\n\n"
            "━━ *Ton accès VIP inclut* ━━\n\n"
            "📡 Signaux avec Entry · SL · TP exacts\n"
            "🔍 Analyse IA en temps réel : GOLD, NASDAQ, S&P500, Forex et plus\n"
            "📊 Dashboard en direct avec stats et positions\n"
            "💬 Analyse à la demande de n'importe quel actif\n"
            "🛠️ Assistant personnel 24/7 — moi, ici même\n\n"
            "━━ *Utilise les boutons pour commencer* ━━\n"
            "Ou envoie-moi n'importe quelle question ✅"
        ),
        "de": (
            "🏆 *Willkommen im VIP, {name}!*\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "Ich bin dein persönlicher KI-Trading-Assistent. Der Kanal ist "
            "nur lesbar — hier privat betreue ich dich direkt.\n\n"
            "━━ *Dein VIP-Zugang umfasst* ━━\n\n"
            "📡 Signale mit exaktem Entry · SL · TP\n"
            "🔍 KI-Analyse in Echtzeit: GOLD, NASDAQ, S&P500, Forex und mehr\n"
            "📊 Live-Dashboard mit Stats und Positionen\n"
            "💬 Analyse auf Anfrage für jeden Wert\n"
            "🛠️ 24/7 persönlicher Assistent — ich, hier\n\n"
            "━━ *Verwende die Buttons zum Starten* ━━\n"
            "Oder schreib mir einfach deine Frage ✅"
        ),
        "it": (
            "🏆 *Benvenuto nel VIP, {name}!*\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "Sono il tuo assistente personale di trading con IA. Il canale è "
            "solo lettura — qui in privato ti seguo direttamente.\n\n"
            "━━ *Il tuo accesso VIP include* ━━\n\n"
            "📡 Segnali con Entry · SL · TP esatti\n"
            "🔍 Analisi IA in tempo reale: GOLD, NASDAQ, S&P500, Forex e altro\n"
            "📊 Dashboard live con statistiche e posizioni\n"
            "💬 Analisi su richiesta di qualsiasi asset\n"
            "🛠️ Assistente personale 24/7 — io, proprio qui\n\n"
            "━━ *Usa i pulsanti per iniziare* ━━\n"
            "O inviami direttamente qualsiasi domanda ✅"
        ),
        "ru": (
            "🏆 *Добро пожаловать в VIP, {name}!*\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "Я твой персональный ИИ-ассистент по трейдингу. Канал только для "
            "чтения — здесь в личных я обслуживаю тебя напрямую.\n\n"
            "━━ *Твой VIP-доступ включает* ━━\n\n"
            "📡 Сигналы с точным Entry · SL · TP\n"
            "🔍 ИИ-анализ в реальном времени: GOLD, NASDAQ, S&P500, Forex и больше\n"
            "📊 Live-дашборд со статистикой и позициями\n"
            "💬 Анализ по запросу любого актива\n"
            "🛠️ Персональный ассистент 24/7 — я, прямо здесь\n\n"
            "━━ *Используй кнопки для начала* ━━\n"
            "Или просто напиши мне любой вопрос ✅"
        ),
    },

    # ─────────────────────────────────────────────────────
    # button labels — etiquetas de botones inline
    # ─────────────────────────────────────────────────────
    "btn_view_vip": {
        "en": "💎 VIEW VIP CHANNEL",
        "es": "💎 VER CANAL VIP",
        "pt": "💎 VER CANAL VIP",
        "ar": "💎 عرض قناة VIP",
        "fr": "💎 VOIR CANAL VIP",
        "de": "💎 VIP-KANAL ANSEHEN",
        "it": "💎 VEDI CANALE VIP",
        "ru": "💎 СМОТРЕТЬ VIP КАНАЛ",
    },
    "btn_dashboard": {
        "en": "🌐 Live Dashboard",
        "es": "🌐 Dashboard en Vivo",
        "pt": "🌐 Dashboard ao Vivo",
        "ar": "🌐 لوحة المعلومات الحية",
        "fr": "🌐 Dashboard en Direct",
        "de": "🌐 Live-Dashboard",
        "it": "🌐 Dashboard Live",
        "ru": "🌐 Live Дашборд",
    },
    "btn_talk_admin": {
        "en": "👤 Talk to the Admin",
        "es": "👤 Hablar con el Admin",
        "pt": "👤 Falar com o Admin",
        "ar": "👤 تحدث مع المسؤول",
        "fr": "👤 Parler à l'Admin",
        "de": "👤 Admin kontaktieren",
        "it": "👤 Parla con l'Admin",
        "ru": "👤 Связаться с Админом",
    },
    "btn_subscribe_vip": {
        "en": "💎 SUBSCRIBE TO VIP",
        "es": "💎 SUSCRIBIRSE AL VIP",
        "pt": "💎 ASSINAR VIP",
        "ar": "💎 اشترك في VIP",
        "fr": "💎 S'ABONNER AU VIP",
        "de": "💎 VIP ABONNIEREN",
        "it": "💎 ABBONATI AL VIP",
        "ru": "💎 ПОДПИСАТЬСЯ НА VIP",
    },
    "btn_live_prices": {
        "en": "📊 Live Prices",
        "es": "📊 Precios en Vivo",
        "pt": "📊 Preços ao Vivo",
        "ar": "📊 الأسعار الحية",
        "fr": "📊 Prix en Direct",
        "de": "📊 Live-Preise",
        "it": "📊 Prezzi Live",
        "ru": "📊 Live Цены",
    },
    "btn_news": {
        "en": "📅 News",
        "es": "📅 Noticias",
        "pt": "📅 Notícias",
        "ar": "📅 الأخبار",
        "fr": "📅 Actualités",
        "de": "📅 News",
        "it": "📅 Notizie",
        "ru": "📅 Новости",
    },
}


def t(key: str, lang: str = "en", **fmt) -> str:
    """Devuelve la traducción de `key` en idioma `lang`. Aplica .format(**fmt).
    Si key/lang no existen, fallback a english."""
    bucket = TRANSLATIONS.get(key)
    if not bucket:
        return f"[missing key: {key}]"
    text = bucket.get(lang) or bucket.get(DEFAULT_LANG, "")
    if fmt:
        try:
            return text.format(**fmt)
        except Exception:
            return text
    return text

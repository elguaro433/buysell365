import json

with open('web/translations.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

updates = {
    'es': {
        'pricing.copy_desc': 'Tu cuenta MT5 replica automaticamente nuestras operaciones con XM — el broker mas usado del mundo',
        'pricing.cp1': 'Copia automatica de todas nuestras operaciones',
        'pricing.cp2': 'Entry, SL y TP exactos — sin hacer nada tu',
        'pricing.cp3': '+20 activos — Oro, Forex e Indices',
        'pricing.cp4': 'Broker XM regulado internacionalmente',
        'pricing.cp5': 'Sin cuota mensual — pagas solo si ganas',
        'pricing.cp6': 'Ves cada operacion en tiempo real',
        'pricing.cp7': 'Retira tu capital cuando quieras',
        'pricing.cp8': 'Totalmente automatico — sin experiencia requerida',
        'faq.q_copy': 'Como funciona el Copy Trading con XM?',
        'faq.a_copy': 'El Copy Trading replica automaticamente todas nuestras operaciones en tu cuenta MT5. Solo necesitas: 1) Abrir cuenta en XM (broker regulado), 2) Conectar tu cuenta a nuestro perfil de copy, 3) Elegir tu nivel de riesgo. A partir de ahi cada operacion se copia en tu cuenta con los mismos SL y TP.',
        'faq.q7': 'Cuanto capital minimo necesito?',
        'faq.a7': 'Para el Copy Trading recomendamos un minimo de $100 USD, aunque XM permite empezar con menos. Para señales VIP puedes empezar con el capital que tengas, aplicando siempre una gestion de riesgo adecuada.',
        'faq.q8': 'Puedo retirar mi dinero cuando quiera?',
        'faq.a8': 'Si. Tu capital esta en tu propia cuenta del broker — nosotros nunca lo tocamos. Puedes retirar en cualquier momento directamente desde XM, sin restricciones ni penalizaciones de nuestra parte.',
        'faq.q9': 'Que pasa si una operacion tiene perdidas?',
        'faq.a9': 'El trading siempre conlleva riesgo. Todas nuestras operaciones llevan Stop Loss automatico para limitar la exposicion. En el Copy Trading, si una operacion cierra en negativo no pagas ninguna comision — solo se cobra un pequeño % cuando hay ganancias reales.',
    },
    'en': {
        'pricing.copy_desc': 'Your MT5 account automatically replicates our trades with XM — the world\'s most used broker',
        'pricing.cp1': 'Automatic copy of all our operations',
        'pricing.cp2': 'Exact Entry, SL and TP — you do nothing',
        'pricing.cp3': '20+ assets — Gold, Forex and Indices',
        'pricing.cp4': 'XM broker — internationally regulated',
        'pricing.cp5': 'No monthly fee — pay only if you profit',
        'pricing.cp6': 'See every trade in real time',
        'pricing.cp7': 'Withdraw your capital whenever you want',
        'pricing.cp8': 'Fully automatic — no experience required',
        'faq.q_copy': 'How does Copy Trading with XM work?',
        'faq.a_copy': 'Copy Trading automatically replicates all our operations in your MT5 account. You just need to: 1) Open an account at XM (regulated broker), 2) Connect your account to our copy profile, 3) Choose your risk level. From there, every trade is copied to your account with the same SL and TP.',
        'faq.q7': 'What is the minimum capital required?',
        'faq.a7': 'For Copy Trading we recommend a minimum of $100 USD, although XM allows you to start with less. For VIP signals you can start with any capital, always applying proper risk management.',
        'faq.q8': 'Can I withdraw my money anytime?',
        'faq.a8': 'Yes. Your capital is in your own broker account — we never touch it. You can withdraw at any time directly from XM, with no restrictions or penalties from us.',
        'faq.q9': 'What happens if a trade is a loss?',
        'faq.a9': 'Trading always carries risk. All our operations include an automatic Stop Loss to limit exposure. In Copy Trading, if a trade closes negative you pay no commission — only a small % is charged when there are real profits.',
    },
    'pt': {
        'pricing.copy_desc': 'Sua conta MT5 replica automaticamente nossas operacoes com XM — o broker mais usado do mundo',
        'pricing.cp1': 'Copia automatica de todas as nossas operacoes',
        'pricing.cp2': 'Entry, SL e TP exatos — sem fazer nada',
        'pricing.cp3': '+20 ativos — Ouro, Forex e Indices',
        'pricing.cp4': 'Broker XM regulado internacionalmente',
        'pricing.cp5': 'Sem mensalidade — paga so se ganhar',
        'pricing.cp6': 'Veja cada operacao em tempo real',
        'pricing.cp7': 'Retire seu capital quando quiser',
        'pricing.cp8': 'Totalmente automatico — sem experiencia necessaria',
        'faq.q_copy': 'Como funciona o Copy Trading com XM?',
        'faq.a_copy': 'O Copy Trading replica automaticamente todas as nossas operacoes na sua conta MT5. Voce so precisa: 1) Abrir conta na XM (broker regulado), 2) Conectar sua conta ao nosso perfil de copy, 3) Escolher seu nivel de risco. A partir dai, cada operacao e copiada na sua conta com os mesmos SL e TP.',
        'faq.q7': 'Qual e o capital minimo necessario?',
        'faq.a7': 'Para Copy Trading recomendamos um minimo de $100 USD, embora a XM permita comecar com menos. Para sinais VIP voce pode comecar com o capital que tiver, aplicando sempre uma gestao de risco adequada.',
        'faq.q8': 'Posso retirar meu dinheiro quando quiser?',
        'faq.a8': 'Sim. Seu capital esta na sua propria conta do broker — nos nunca o tocamos. Voce pode retirar a qualquer momento diretamente pela XM, sem restricoes ou penalidades da nossa parte.',
        'faq.q9': 'O que acontece se uma operacao tiver prejuizo?',
        'faq.a9': 'O trading sempre envolve riscos. Todas as nossas operacoes tem Stop Loss automatico para limitar a exposicao. No Copy Trading, se uma operacao fechar no negativo voce nao paga nenhuma comissao — apenas uma pequena % e cobrada quando ha lucros reais.',
    },
    'fr': {
        'pricing.copy_desc': 'Votre compte MT5 replique automatiquement nos operations avec XM — le broker le plus utilise au monde',
        'pricing.cp1': 'Copie automatique de toutes nos operations',
        'pricing.cp2': 'Entry, SL et TP exacts — sans rien faire',
        'pricing.cp3': '+20 actifs — Or, Forex et Indices',
        'pricing.cp4': 'Broker XM reglemente internationalement',
        'pricing.cp5': 'Sans frais mensuels — vous payez seulement si vous gagnez',
        'pricing.cp6': 'Voyez chaque operation en temps reel',
        'pricing.cp7': 'Retirez votre capital quand vous voulez',
        'pricing.cp8': 'Entierement automatique — sans experience requise',
        'faq.q_copy': 'Comment fonctionne le Copy Trading avec XM ?',
        'faq.a_copy': 'Le Copy Trading replique automatiquement toutes nos operations sur votre compte MT5. Vous avez juste besoin: 1) D\'ouvrir un compte chez XM (broker reglemente), 2) Connecter votre compte a notre profil de copy, 3) Choisir votre niveau de risque. Ensuite chaque operation est copiee sur votre compte avec les memes SL et TP.',
        'faq.q7': 'Quel est le capital minimum requis ?',
        'faq.a7': 'Pour le Copy Trading nous recommandons un minimum de $100 USD, bien que XM permette de commencer avec moins. Pour les signaux VIP vous pouvez commencer avec le capital que vous avez, en appliquant toujours une gestion du risque appropriee.',
        'faq.q8': 'Puis-je retirer mon argent quand je veux ?',
        'faq.a8': 'Oui. Votre capital est dans votre propre compte chez le broker — nous ne le touchons jamais. Vous pouvez retirer a tout moment directement depuis XM, sans restrictions ni penalites de notre part.',
        'faq.q9': 'Que se passe-t-il si une operation est perdante ?',
        'faq.a9': 'Le trading comporte toujours des risques. Toutes nos operations ont un Stop Loss automatique pour limiter l\'exposition. En Copy Trading, si une operation se cloture en negatif vous ne payez aucune commission — seulement un petit % est preleve lorsqu\'il y a de vrais benefices.',
    },
}

for lang, keys in updates.items():
    for k, v in keys.items():
        data[lang][k] = v

with open('web/translations.json', 'w', encoding='utf-8') as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print('Done')

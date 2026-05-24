#!/usr/bin/env python3
"""Actualiza el menu de comandos Telegram para Cloud mode (sin MT5)."""

PATH = '/opt/buysell365/app/bot.py'
with open(PATH) as f:
    src = f.read()

# El bloque viejo (lineas ~17847-17869 del listado)
old_block = '''        _cmds_admin = _cmds_default + [
            {"command": "senales",     "description": "Ver señales activas"},
            {"command": "estado",      "description": "Estado del bot y mercado"},
            {"command": "resumen",     "description": "Resumen del día"},
            {"command": "noticias",    "description": "Calendario económico"},
            {"command": "tendencia",   "description": "Tendencias del mercado"},
            {"command": "precios",     "description": "Precios en tiempo real"},
            {"command": "analisis",    "description": "Análisis técnico de un activo"},
            {"command": "sentimiento", "description": "Fear & Greed index"},
            {"command": "web",         "description": "Dashboard web en vivo"},
            {"command": "admin",       "description": "🛡️ Panel de administrador"},
            {"command": "pausar",      "description": "⏸️ Pausar scanner + MT5"},
            {"command": "reanudar",    "description": "▶️ Reanudar scanner + MT5"},
            {"command": "pausarmt5",   "description": "⏸️ Pausar solo MT5"},
            {"command": "whasaoro",    "description": "📱 Toggle WhatsApp ORO"},
            {"command": "filtros",     "description": "🛡️ Senales bloqueadas por LLM"},
            {"command": "reiniciar",   "description": "🔄 Reiniciar proceso del bot"},
            {"command": "apagar",      "description": "🔴 Apagar el bot"},
            {"command": "cuenta",      "description": "💰 Cuenta MT5 en tiempo real"},
            {"command": "vips",        "description": "👑 Ver suscriptores VIP activos"},
        ]'''

new_block = '''        _cmds_admin = _cmds_default + [
            # ── Info y consultas ──
            {"command": "senales",     "description": "📡 Ver señales activas"},
            {"command": "estado",      "description": "📊 Estado del bot y mercado"},
            {"command": "resumen",     "description": "📋 Resumen del día"},
            {"command": "copier",      "description": "📡 Stats del copier hoy"},
            {"command": "logs",        "description": "📜 Últimos errores"},
            {"command": "noticias",    "description": "📰 Calendario económico"},
            {"command": "tendencia",   "description": "📈 Tendencias del mercado"},
            {"command": "precios",     "description": "💲 Precios en tiempo real"},
            {"command": "analisis",    "description": "🔍 Análisis técnico de un activo"},
            {"command": "sentimiento", "description": "🌡️ Fear & Greed index"},
            # ── Web / paneles ──
            {"command": "web",         "description": "🌐 Dashboard público (buysell365.pro)"},
            {"command": "panel",       "description": "🎛️ Panel admin del VPS"},
            {"command": "admin",       "description": "🛡️ Panel de administrador"},
            # ── Control del bot (sin MT5 — corre en VPS Cloud) ──
            {"command": "pausar",      "description": "⏸️ Pausar scanner de señales"},
            {"command": "reanudar",    "description": "▶️ Reanudar scanner"},
            {"command": "whasaoro",    "description": "📱 Toggle WhatsApp ORO"},
            {"command": "filtros",     "description": "🛡️ Señales bloqueadas por LLM"},
            {"command": "reiniciar",   "description": "🔄 Reiniciar bot"},
            {"command": "apagar",      "description": "🔴 Apagar bot"},
            # ── VIPs / regalos ──
            {"command": "vips",        "description": "👑 Ver VIPs activos"},
            {"command": "regalos",     "description": "🎁 Ver regalos del día"},
        ]'''

if old_block in src:
    src = src.replace(old_block, new_block, 1)
    with open(PATH, 'w') as f:
        f.write(src)
    print('PATCHED — comandos admin actualizados (Cloud mode, sin MT5)')
else:
    print('NO MATCH — el bloque ya fue modificado o cambio el formato')
    # Try to find the old commands manually
    for cmd in ['pausarmt5', 'cuenta', 'Cuenta MT5']:
        if cmd in src:
            print(f'  todavia contiene: "{cmd}"')

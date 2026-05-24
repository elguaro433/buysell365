#!/usr/bin/env python3
"""Actualiza el mensaje PANEL DE CONTROL para Cloud mode."""

PATH = '/opt/buysell365/app/bot.py'
with open(PATH) as f:
    src = f.read()

old = '''            start_txt = (
                f"👑 *PANEL DE CONTROL — PROPIETARIO*\\n"
                f"━━━━━━━━━━━━━━━━\\n\\n"
                f"👤 *Administrador* — BuySell365.pro\\n\\n"
                f"📊 *ESTADO REAL DEL SISTEMA:*\\n"
                f"   📡 Copier: 🟢 ACTIVO · {_cop_open_count} abiertas\\n"
                f"   💹 MT5: {_mt5_icon}\\n"
                f"   💰 Balance MT5: {_capital_p}\\n"
                f"   🎁 Regalos hoy: ORO {_gift_oro_icon}  ·  OTRA {_gift_other_icon}\\n\\n"
                f"📈 *HOY · Canal VIP:*\\n"
                f"   🎯 {_cop_today_tps + _cop_today_sls} señales decididas\\n"
                f"   📊 Win Rate: *{_cop_wr}%*  ·  Net: *{_cop_pips_net:+.0f} pts*\\n"
                f"   ✓ {_cop_today_tps} TPs  ·  ● {_cop_today_sls} SLs\\n"
                f"{_best_line}\\n"
                f"🛠️ *CONTROLES:*\\n"
                f"   /reiniciar — Reiniciar bot+copier\\n"
                f"   /apagar — Apagar bot\\n"
                f"   /mt5_on — Habilitar ejecución MT5\\n"
                f"   /mt5_off — Deshabilitar ejecución MT5\\n\\n"
                f"📋 *INFO:*\\n"
                f"   /estado — Estado detallado\\n"
                f"   /copier — Stats copier hoy\\n"
                f"   /vips — Lista VIPs\\n"
                f"   /logs — Últimos errores\\n"
                f"   /resumen — Resumen completo del día\\n\\n"
                f"🎁 *SEÑALES REGALO:*\\n"
                f"   /regalos — Ver regalos de hoy\\n\\n"
                f"💬 *Escribe cualquier comando o pregunta*"
            )'''

new = '''            start_txt = (
                f"👑 *PANEL DE CONTROL — PROPIETARIO*\\n"
                f"━━━━━━━━━━━━━━━━\\n\\n"
                f"👤 *Administrador* — BuySell365.pro\\n"
                f"☁️ Modo: *Cloud VPS* (sin MT5 — ejecución manual desde móvil)\\n\\n"
                f"📊 *ESTADO REAL DEL SISTEMA:*\\n"
                f"   📡 Copier: 🟢 ACTIVO · {_cop_open_count} abiertas\\n"
                f"   🎁 Regalos hoy: ORO {_gift_oro_icon}  ·  OTRA {_gift_other_icon}\\n\\n"
                f"📈 *HOY · Canal VIP:*\\n"
                f"   🎯 {_cop_today_tps + _cop_today_sls} señales decididas\\n"
                f"   📊 Win Rate: *{_cop_wr}%*  ·  Net: *{_cop_pips_net:+.0f} pts*\\n"
                f"   ✓ {_cop_today_tps} TPs  ·  ● {_cop_today_sls} SLs\\n"
                f"{_best_line}\\n"
                f"🛠️ *CONTROLES:*\\n"
                f"   /reiniciar — Reiniciar bot\\n"
                f"   /apagar — Apagar bot\\n"
                f"   /pausar — Pausar scanner\\n"
                f"   /reanudar — Reanudar scanner\\n\\n"
                f"📋 *INFO:*\\n"
                f"   /estado — Estado detallado\\n"
                f"   /copier — Stats copier hoy\\n"
                f"   /vips — Lista VIPs\\n"
                f"   /logs — Últimos errores\\n"
                f"   /resumen — Resumen completo del día\\n\\n"
                f"🌐 *PANEL WEB:*\\n"
                f"   /panel — Panel admin VPS (gestión visual)\\n"
                f"   /web — Dashboard público buysell365.pro\\n\\n"
                f"🎁 *SEÑALES REGALO:*\\n"
                f"   /regalos — Ver regalos de hoy\\n\\n"
                f"💬 *Escribe cualquier comando o pregunta*"
            )'''

if old in src:
    src = src.replace(old, new, 1)
    with open(PATH, 'w') as f:
        f.write(src)
    print('PATCHED — PANEL DE CONTROL actualizado para Cloud mode')
else:
    print('NO MATCH')

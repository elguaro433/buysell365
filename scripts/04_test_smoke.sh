#!/bin/bash
# ============================================================
# 04_test_smoke.sh
# Health check post-instalación en Linux VPS
# ============================================================

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(dirname "$SCRIPT_DIR")/app"
VENV_DIR="$APP_DIR/.venv"
ERRORS=0
WARNINGS=0

ok()   { echo -e "  ${GREEN}✅ $1${NC}"; }
fail() { echo -e "  ${RED}❌ $1${NC}"; ERRORS=$((ERRORS+1)); }
warn() { echo -e "  ${YELLOW}⚠️  $1${NC}"; WARNINGS=$((WARNINGS+1)); }
section() { echo ""; echo -e "${CYAN}── $1 ──${NC}"; }

echo -e "${CYAN}╔══════════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║      BuySell365 — Smoke Test del VPS Linux               ║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════════════════════╝${NC}"

# ─── Python ───
section "Python y venv"
if [ -d "$VENV_DIR" ]; then
    ok "venv presente: $VENV_DIR"
else
    fail "venv NO existe — ejecuta 02_install_deps.sh"
fi

PY_VER=$("$VENV_DIR/bin/python" --version 2>&1 || echo "no")
if [[ "$PY_VER" == *"3.11"* ]]; then
    ok "Python: $PY_VER"
else
    fail "Python 3.11 no disponible (got: $PY_VER)"
fi

# ─── Estructura ───
section "Estructura archivos"
for f in launcher.py bot.py signal_copier.py btc_eth_generator.py price_feed.py requirements.txt .env; do
    if [ -f "$APP_DIR/$f" ]; then ok "$f presente"; else fail "$f FALTA"; fi
done

# State files
STATE_COUNT=$(find "$APP_DIR/state" -name "*.json" 2>/dev/null | wc -l)
if [ "$STATE_COUNT" -gt 0 ]; then
    ok "state/ con $STATE_COUNT JSON files"
else
    warn "state/ vacío — restaura snapshot fresco antes del switch"
fi

# Sessions Telethon
section "Sesiones Telethon"
for s in signal_copier_session.session userbot_session.session; do
    if [ -f "$APP_DIR/$s" ]; then ok "$s ($(stat -c%s "$APP_DIR/$s") bytes)"; else fail "$s FALTA"; fi
done
[ -f "$APP_DIR/ig_session.json" ] && ok "ig_session.json presente" || warn "ig_session.json falta"

# ─── .env críticas ───
section "Variables .env críticas"
ENV_FILE="$APP_DIR/.env"
if [ -f "$ENV_FILE" ]; then
    for v in TELEGRAM_TOKEN ANTHROPIC_API_KEY SYNC_SECRET; do
        if grep -qE "^${v}=.+" "$ENV_FILE"; then ok "$v configurada"; else fail "$v vacía o falta"; fi
    done
else
    fail ".env no encontrado"
fi

# Verificar que MT5_* están comentadas
MT5_ACTIVE=$(grep -cE "^MT5_[A-Z_]+=" "$ENV_FILE" 2>/dev/null || echo 0)
if [ "$MT5_ACTIVE" -eq 0 ]; then
    ok "Variables MT5_* correctamente desactivadas (refactor sin MT5)"
else
    warn "Hay $MT5_ACTIVE variables MT5_* activas (esperado: 0 tras refactor)"
fi

# ─── Imports Python ───
section "Imports Python críticos"
cd "$APP_DIR"
for mod in telethon anthropic pandas numpy yfinance dotenv flask requests; do
    if "$VENV_DIR/bin/python" -c "import $mod" 2>/dev/null; then ok "$mod"; else fail "$mod no importable"; fi
done

# Confirmar metatrader5 NO instalado (Linux no lo soporta)
if "$VENV_DIR/bin/python" -c "import MetaTrader5" 2>/dev/null; then
    warn "metatrader5 instalado (no debería estar en Linux)"
else
    ok "metatrader5 NO instalado (correcto en Linux)"
fi

# price_feed
if "$VENV_DIR/bin/python" -c "import price_feed; price_feed.get_tick('BTCUSD')" 2>/dev/null; then
    ok "price_feed importable y get_tick() funcional"
else
    warn "price_feed import ok pero get_tick falla — revisar conectividad red"
fi

# ─── Telegram API ───
section "Telegram Bot API"
TG_OUT=$("$VENV_DIR/bin/python" -c "
import os, requests
from dotenv import load_dotenv
load_dotenv()
tok = os.getenv('TELEGRAM_TOKEN','')
if not tok: print('NO_TOKEN'); exit()
try:
    r = requests.get(f'https://api.telegram.org/bot{tok}/getMe', timeout=10)
    d = r.json()
    print(f'OK @{d[\"result\"][\"username\"]}' if d.get('ok') else f'FAIL {d}')
except Exception as e:
    print(f'FAIL {e}')
" 2>&1)
if [[ "$TG_OUT" == OK* ]]; then ok "Telegram: $TG_OUT"
elif [[ "$TG_OUT" == NO_TOKEN ]]; then fail "TELEGRAM_TOKEN vacío"
else fail "Telegram: $TG_OUT"; fi

# ─── Anthropic ───
section "Anthropic API"
AN_OUT=$("$VENV_DIR/bin/python" -c "
import os, anthropic
from dotenv import load_dotenv
load_dotenv()
key = os.getenv('ANTHROPIC_API_KEY','')
if not key: print('NO_KEY'); exit()
try:
    c = anthropic.Anthropic(api_key=key)
    r = c.messages.create(model='claude-haiku-4-5-20251001', max_tokens=10, messages=[{'role':'user','content':'ping'}])
    print(f'OK tokens={r.usage.input_tokens}')
except Exception as e:
    print(f'FAIL {type(e).__name__}: {str(e)[:80]}')
" 2>&1)
if [[ "$AN_OUT" == OK* ]]; then ok "Anthropic: $AN_OUT"
elif [[ "$AN_OUT" == NO_KEY ]]; then fail "ANTHROPIC_API_KEY vacío"
else fail "Anthropic: $AN_OUT"; fi

# ─── Render dashboard ───
section "Render dashboard"
RD_OUT=$("$VENV_DIR/bin/python" -c "
import os, requests
from dotenv import load_dotenv
load_dotenv()
url = os.getenv('WEB_URL','') or os.getenv('DASHBOARD_URL','')
if not url: print('NO_URL'); exit()
try:
    r = requests.get(url, timeout=15)
    print(f'OK {r.status_code}')
except Exception as e:
    print(f'FAIL {e}')
" 2>&1)
[[ "$RD_OUT" == "OK 200" ]] && ok "Render: $RD_OUT" || warn "Render: $RD_OUT"

# ─── systemd services ───
section "systemd services"
if systemctl list-unit-files | grep -q "buysell365.service"; then
    STATE=$(systemctl is-enabled buysell365 2>/dev/null || echo "disabled")
    ACT=$(systemctl is-active buysell365 2>/dev/null || echo "inactive")
    ok "Servicio buysell365 (bot) registrado (enabled=$STATE, active=$ACT)"
else
    warn "Servicio buysell365 no registrado — ejecuta 03_setup_systemd.sh"
fi

if systemctl list-unit-files | grep -q "buysell365_admin.service"; then
    STATE=$(systemctl is-enabled buysell365_admin 2>/dev/null || echo "disabled")
    ACT=$(systemctl is-active buysell365_admin 2>/dev/null || echo "inactive")
    ok "Servicio buysell365_admin (panel web) registrado (enabled=$STATE, active=$ACT)"
else
    warn "Servicio buysell365_admin no registrado — ejecuta 03_setup_systemd.sh"
fi

# ─── Web Admin Panel reachable ───
section "Web Admin Panel"
if command -v curl &>/dev/null; then
    PANEL_PORT="${WEB_ADMIN_PORT:-5001}"
    PANEL_CODE=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:$PANEL_PORT/login" --max-time 5 || echo "000")
    if [ "$PANEL_CODE" = "200" ]; then
        ok "Panel web responde en puerto $PANEL_PORT (HTTP 200 en /login)"
    elif [ "$PANEL_CODE" = "000" ]; then
        warn "Panel web no responde — ¿está arrancado? sudo systemctl start buysell365_admin"
    else
        warn "Panel web devuelve HTTP $PANEL_CODE (esperado 200)"
    fi
else
    warn "curl no disponible — no puedo testear el panel"
fi

# ─── Resumen ───
echo ""
echo -e "${CYAN}╔══════════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║                      RESUMEN                              ║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════════════════════╝${NC}"
if [ $ERRORS -eq 0 ] && [ $WARNINGS -eq 0 ]; then
    echo -e "${GREEN}🎉 TODO OK — VPS listo para producción${NC}"
elif [ $ERRORS -eq 0 ]; then
    echo -e "${YELLOW}✅ $WARNINGS warnings — puedes arrancar pero revisa primero${NC}"
else
    echo -e "${RED}❌ $ERRORS errores críticos — NO arrancar hasta resolver${NC}"
fi
echo ""
exit $ERRORS

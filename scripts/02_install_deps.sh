#!/bin/bash
# ============================================================
# 02_install_deps.sh
# Crea venv y instala dependencias Python del proyecto
# ============================================================

set -e
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(dirname "$SCRIPT_DIR")/app"
VENV_DIR="$APP_DIR/.venv"
REQ_FILE="$APP_DIR/requirements.txt"

if [ ! -f "$REQ_FILE" ]; then
    echo -e "${RED}ERROR: No encuentro $REQ_FILE${NC}"
    exit 1
fi

echo -e "${YELLOW}Creando virtualenv en $VENV_DIR...${NC}"
python3.11 -m venv "$VENV_DIR"
source "$VENV_DIR/bin/activate"

echo -e "${YELLOW}Actualizando pip/setuptools/wheel...${NC}"
pip install --upgrade pip setuptools wheel --quiet

echo -e "${YELLOW}Instalando dependencias (~5-8 min, compila numpy/pandas)...${NC}"
pip install -r "$REQ_FILE"

# Verificar paquetes críticos
echo ""
echo -e "${YELLOW}Verificando paquetes críticos...${NC}"
CRITICAL=(telethon anthropic pandas numpy yfinance requests dotenv flask)
FAILED=()
for pkg in "${CRITICAL[@]}"; do
    if python -c "import $pkg" 2>/dev/null; then
        echo -e "  ${GREEN}✅ $pkg${NC}"
    else
        echo -e "  ${RED}❌ $pkg NO importable${NC}"
        FAILED+=("$pkg")
    fi
done

# Verificar que metatrader5 NO está (no debe estar en Linux)
if python -c "import MetaTrader5" 2>/dev/null; then
    echo -e "  ${YELLOW}⚠️  metatrader5 instalado (debería NO estar en Linux refactor)${NC}"
else
    echo -e "  ${GREEN}✅ metatrader5 NO presente (correcto)${NC}"
fi

# Verificar price_feed (nuestro reemplazo)
cd "$APP_DIR"
if python -c "import price_feed; print('OK')" 2>/dev/null; then
    echo -e "  ${GREEN}✅ price_feed (reemplazo MT5) importable${NC}"
else
    echo -e "  ${RED}❌ price_feed no importable — refactor incompleto${NC}"
fi

if [ ${#FAILED[@]} -gt 0 ]; then
    echo -e "${RED}❌ Paquetes críticos fallaron: ${FAILED[*]}${NC}"
    exit 1
fi

echo ""
echo -e "${GREEN}✅ Dependencias instaladas${NC}"
echo "  Para activar venv manual:  source $VENV_DIR/bin/activate"

#!/bin/bash
# ============================================================
# VPS_SETUP.sh  —  Master installer para BuySell365 en Linux
# ============================================================
# Ejecuta todos los pasos de instalación en orden:
#   1. Python 3.11
#   2. Dependencias pip
#   3. systemd service para auto-arranque
#   4. Smoke test
#
# REQUISITOS:
#   - VPS Linux Debian 12 / Ubuntu 22.04+ limpio
#   - Acceso root o sudo
#   - Carpeta del proyecto subida a /opt/buysell365/
#
# USO:
#   sudo bash /opt/buysell365/scripts/VPS_SETUP.sh
# ============================================================

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(dirname "$SCRIPT_DIR")/app"

# Colores
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

header() {
    echo ""
    echo -e "${CYAN}=============================================================${NC}"
    echo -e "${CYAN} $1${NC}"
    echo -e "${CYAN}=============================================================${NC}"
}

step() {
    echo ""
    echo -e "${YELLOW}>>> Paso $1: $2${NC}"
}

# ─── Validaciones ───
header "BuySell365 — Instalación VPS Linux"

if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}ERROR: Ejecutar con sudo o como root.${NC}"
    echo "  sudo bash $0"
    exit 1
fi

if [ ! -d "$APP_DIR" ]; then
    echo -e "${RED}ERROR: No encuentro $APP_DIR${NC}"
    echo "  Verifica que el proyecto está en /opt/buysell365/"
    exit 1
fi

echo -e "${GREEN}✅ Ejecutando como root${NC}"
echo -e "${GREEN}✅ Carpeta app/ encontrada: $APP_DIR${NC}"
echo ""
echo "El proceso completo tomará ~10-15 minutos."
echo ""
read -p "¿Continuar? (s/N): " ans
if [[ ! "$ans" =~ ^[SsYy]$ ]]; then
    echo "Cancelado."
    exit 0
fi

# ─── Pasos ───
step 1 "Instalando Python 3.11 y dependencias del sistema"
bash "$SCRIPT_DIR/01_install_python.sh"

step 2 "Instalando dependencias Python (pip)"
bash "$SCRIPT_DIR/02_install_deps.sh"

step 3 "Configurando systemd service (auto-arranque)"
bash "$SCRIPT_DIR/03_setup_systemd.sh"

step 4 "Smoke test"
bash "$SCRIPT_DIR/04_test_smoke.sh"

header "✅ INSTALACIÓN COMPLETA"
echo ""
echo -e "${GREEN}Próximos pasos:${NC}"
echo "  1. Revisar /opt/buysell365/app/.env (claves API y secrets)"
echo "  2. Arrancar el servicio:    sudo systemctl start buysell365"
echo "  3. Ver logs en vivo:        sudo journalctl -u buysell365 -f"
echo "  4. Estado del servicio:     sudo systemctl status buysell365"
echo "  5. Auto-arranca al reiniciar el VPS:  sudo systemctl enable buysell365"
echo ""

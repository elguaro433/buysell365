#!/bin/bash
# ============================================================
# 01_install_python.sh
# Instala Python 3.11 y dependencias del sistema en Debian/Ubuntu
# ============================================================

set -e
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Detectar distro
if [ -f /etc/os-release ]; then
    . /etc/os-release
    DISTRO=$ID
else
    echo "ERROR: no detecto /etc/os-release"
    exit 1
fi

echo -e "${GREEN}Distro detectada: $DISTRO${NC}"

# Actualizar índice
apt-get update -qq

# Paquetes base
PACKAGES=(
    python3.11
    python3.11-venv
    python3.11-dev
    python3-pip
    build-essential
    libssl-dev
    libffi-dev
    git
    curl
    wget
    ca-certificates
    tzdata
    # Para matplotlib/PIL
    libfreetype6-dev
    libjpeg-dev
    zlib1g-dev
    # Para numpy/scipy
    libopenblas-dev
    gfortran
)

echo -e "${YELLOW}Instalando paquetes del sistema (5-10 min)...${NC}"
apt-get install -y -qq "${PACKAGES[@]}" || {
    # Fallback: si python3.11 no está en el repo, usar deadsnakes (Ubuntu)
    if [[ "$DISTRO" == "ubuntu" ]]; then
        echo -e "${YELLOW}Python 3.11 no en repo principal, añadiendo deadsnakes...${NC}"
        apt-get install -y -qq software-properties-common
        add-apt-repository -y ppa:deadsnakes/ppa
        apt-get update -qq
        apt-get install -y -qq python3.11 python3.11-venv python3.11-dev
    else
        echo "Fallo instalando Python 3.11 — revisa manualmente"
        exit 1
    fi
}

# Verificar
PY_VER=$(python3.11 --version 2>&1 || true)
if [[ "$PY_VER" == *"3.11"* ]]; then
    echo -e "${GREEN}✅ $PY_VER instalado${NC}"
else
    echo "❌ Python 3.11 no disponible"
    exit 1
fi

# Asegurar pip actualizado
python3.11 -m pip install --upgrade pip --quiet

# Configurar zona horaria
timedatectl set-timezone Europe/Madrid 2>/dev/null || echo "  (TZ skipped — set manually if needed)"

echo -e "${GREEN}✅ Sistema base preparado${NC}"

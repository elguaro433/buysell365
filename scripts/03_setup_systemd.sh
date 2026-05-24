#!/bin/bash
# ============================================================
# 03_setup_systemd.sh
# Crea DOS servicios systemd:
#   1. buysell365.service        — el bot (launcher.py)
#   2. buysell365_admin.service  — el web admin panel (puerto 5001)
# ============================================================

set -e
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(dirname "$SCRIPT_DIR")/app"
VENV_DIR="$APP_DIR/.venv"

if [ ! -d "$VENV_DIR" ]; then
    echo "ERROR: venv no encontrado en $VENV_DIR. Ejecuta 02_install_deps.sh primero."
    exit 1
fi

RUN_USER="${SUDO_USER:-root}"
echo -e "${YELLOW}Servicios se ejecutarán como usuario: $RUN_USER${NC}"

# ─── Servicio 1: BOT ───────────────────────────────────────────────
cat > /etc/systemd/system/buysell365.service <<EOF
[Unit]
Description=BuySell365 Trading Bot (sin MT5)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$RUN_USER
Group=$RUN_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$APP_DIR/.env
ExecStart=$VENV_DIR/bin/python $APP_DIR/launcher.py

Restart=on-failure
RestartSec=10
StartLimitInterval=300
StartLimitBurst=5

StandardOutput=journal
StandardError=journal
SyslogIdentifier=buysell365

LimitNOFILE=4096
MemoryMax=4G

[Install]
WantedBy=multi-user.target
EOF
chmod 644 /etc/systemd/system/buysell365.service
echo -e "${GREEN}✅ /etc/systemd/system/buysell365.service creado${NC}"

# ─── Servicio 2: WEB ADMIN PANEL ───────────────────────────────────
cat > /etc/systemd/system/buysell365_admin.service <<EOF
[Unit]
Description=BuySell365 Web Admin Panel (puerto 5001)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$RUN_USER
Group=$RUN_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$APP_DIR/.env
ExecStart=$VENV_DIR/bin/python -m web_admin

Restart=on-failure
RestartSec=10

StandardOutput=journal
StandardError=journal
SyslogIdentifier=buysell365_admin

LimitNOFILE=2048
MemoryMax=512M

[Install]
WantedBy=multi-user.target
EOF
chmod 644 /etc/systemd/system/buysell365_admin.service
echo -e "${GREEN}✅ /etc/systemd/system/buysell365_admin.service creado${NC}"

# ─── Permitir al usuario ejecutar systemctl sin password (para que el panel pueda reiniciar) ───
SUDOERS_FILE="/etc/sudoers.d/buysell365"
cat > "$SUDOERS_FILE" <<EOF
# Permite al usuario $RUN_USER controlar los servicios buysell365 sin password
# (necesario para que el Web Admin Panel pueda usar los botones de control)
$RUN_USER ALL=(ALL) NOPASSWD: /bin/systemctl start buysell365
$RUN_USER ALL=(ALL) NOPASSWD: /bin/systemctl stop buysell365
$RUN_USER ALL=(ALL) NOPASSWD: /bin/systemctl restart buysell365
$RUN_USER ALL=(ALL) NOPASSWD: /bin/systemctl status buysell365
EOF
chmod 440 "$SUDOERS_FILE"
echo -e "${GREEN}✅ Permisos sudoers configurados para control desde el panel${NC}"

# Recargar systemd
systemctl daemon-reload

# Habilitar ambos (auto-arranque al boot)
systemctl enable buysell365 buysell365_admin

echo ""
echo -e "${GREEN}═══════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  SERVICIOS CREADOS Y HABILITADOS${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════════════════${NC}"
echo ""
echo "🤖 buysell365 — el bot principal"
echo "🌐 buysell365_admin — el panel web (puerto 5001)"
echo ""
echo "Comandos:"
echo "  Arrancar ambos:        sudo systemctl start buysell365 buysell365_admin"
echo "  Parar ambos:           sudo systemctl stop buysell365 buysell365_admin"
echo "  Reiniciar bot:         sudo systemctl restart buysell365"
echo "  Reiniciar panel:       sudo systemctl restart buysell365_admin"
echo "  Logs bot en vivo:      sudo journalctl -u buysell365 -f"
echo "  Logs panel en vivo:    sudo journalctl -u buysell365_admin -f"
echo "  Estado ambos:          sudo systemctl status buysell365 buysell365_admin"
echo ""
echo -e "${YELLOW}NOTA: Servicios NO arrancados todavía. Cuando estés listo:${NC}"
echo "  sudo systemctl start buysell365 buysell365_admin"
echo ""
echo -e "${YELLOW}Acceso al panel web:${NC}"
echo "  http://TU_IP_VPS:5001"
echo "  Usuario:     admin (cambiar vía WEB_ADMIN_USER en .env)"
echo "  Contraseña:  buysell365 (cambiar vía WEB_ADMIN_PASSWORD en .env)"
echo ""

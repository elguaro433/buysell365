#!/bin/bash
# ============================================================
# restore_state.sh
# Ejecutado EN EL VPS Linux — Restaura snapshot fresco del estado
# generado en el PC con snap_state_now.ps1.
#
# USO:
#   sudo bash restore_state.sh /path/to/state_snapshot_XXX.zip
# ============================================================

set -e
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

if [ -z "$1" ]; then
    echo "ERROR: Falta argumento ZIP."
    echo "  Uso: sudo bash $0 /ruta/state_snapshot_YYYYMMDD_HHMMSS.zip"
    exit 1
fi

SNAPSHOT_ZIP="$1"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(dirname "$SCRIPT_DIR")/app"
STATE_DIR="$APP_DIR/state"

if [ ! -f "$SNAPSHOT_ZIP" ]; then
    echo -e "${RED}ERROR: No encuentro $SNAPSHOT_ZIP${NC}"
    exit 1
fi

# Verificar que el bot está parado
if systemctl is-active --quiet buysell365 2>/dev/null; then
    echo -e "${RED}❌ El servicio buysell365 está corriendo. Páralo primero:${NC}"
    echo "  sudo systemctl stop buysell365"
    exit 1
fi

mkdir -p "$STATE_DIR"

# Extraer a temporal
TEMP_DIR=$(mktemp -d)
echo -e "${YELLOW}Extrayendo $SNAPSHOT_ZIP a $TEMP_DIR...${NC}"
unzip -qo "$SNAPSHOT_ZIP" -d "$TEMP_DIR"

# Mostrar manifest si existe
if [ -f "$TEMP_DIR/_manifest.json" ]; then
    echo ""
    echo "📋 Manifest del snapshot:"
    python3 -c "import json; m=json.load(open('$TEMP_DIR/_manifest.json')); print(f'  Timestamp: {m.get(\"timestamp\")}'); print(f'  Files: {m.get(\"files_copied\")}'); print(f'  Bot was running: {m.get(\"bot_was_running\")}')"
fi

# Validar JSONs
echo ""
echo -e "${YELLOW}Validando JSONs...${NC}"
INVALID=()
for jf in "$TEMP_DIR"/*.json; do
    [ "$(basename "$jf")" = "_manifest.json" ] && continue
    if ! python3 -c "import json; json.load(open('$jf'))" 2>/dev/null; then
        INVALID+=("$(basename "$jf")")
        echo -e "  ${RED}❌ $(basename "$jf") JSON inválido${NC}"
    fi
done
if [ ${#INVALID[@]} -gt 0 ]; then
    rm -rf "$TEMP_DIR"
    echo -e "${RED}❌ Snapshot inválido — abortando${NC}"
    exit 1
fi
JSON_COUNT=$(ls -1 "$TEMP_DIR"/*.json 2>/dev/null | grep -v _manifest | wc -l)
echo -e "  ${GREEN}✅ $JSON_COUNT JSONs validados${NC}"

# Backup del estado previo
BACKUP_DIR="$APP_DIR/state_backup_pre_restore_$(date +%Y%m%d_%H%M%S)"
if [ -n "$(ls -A "$STATE_DIR" 2>/dev/null)" ]; then
    echo ""
    echo -e "${YELLOW}Backing up estado actual a $BACKUP_DIR${NC}"
    mkdir -p "$BACKUP_DIR"
    cp -p "$STATE_DIR"/* "$BACKUP_DIR/" 2>/dev/null || true
    # Tambien copia los que viven en raiz de app/
    for jf in "$TEMP_DIR"/*.json; do
        [ "$(basename "$jf")" = "_manifest.json" ] && continue
        if [ -f "$APP_DIR/$(basename "$jf")" ]; then
            cp -p "$APP_DIR/$(basename "$jf")" "$BACKUP_DIR/"
        fi
    done
    echo -e "  ${GREEN}✅ Backup OK${NC}"
fi

# Aplicar
echo ""
echo -e "${YELLOW}Aplicando restore...${NC}"
RESTORED=0
for jf in "$TEMP_DIR"/*.json; do
    [ "$(basename "$jf")" = "_manifest.json" ] && continue
    cp -p "$jf" "$STATE_DIR/"
    cp -p "$jf" "$APP_DIR/"  # codigo lee de raiz
    RESTORED=$((RESTORED+1))
done

# Cleanup
rm -rf "$TEMP_DIR"

echo ""
echo -e "${GREEN}═══════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  ✅ RESTORE COMPLETO${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════════════════${NC}"
echo "  Archivos restaurados: $RESTORED"
echo "  Backup previo en:     $BACKUP_DIR"
echo ""
echo -e "${YELLOW}Arrancar el bot:${NC}"
echo "  sudo systemctl start buysell365"
echo "  sudo journalctl -u buysell365 -f    # ver logs"
echo ""

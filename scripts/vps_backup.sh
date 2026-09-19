#!/bin/bash
# vps_backup.sh — Backup nocturno cifrado del estado de BuySell365 (VPS).
# Añadido 2026-09-19. Instalado en cron: 04:10 cada día.
#   Qué guarda: .env, *.session (Telethon), *.json de estado, whatsapp_recipients,
#               launcher_config, ssl_*.pem. NO guarda código (está en git) ni logs.
#   Dónde:      /opt/backups/buysell365/bs365_<fecha>.tar.enc  (AES-256, openssl)
#   Clave:      /root/.backup_pass (chmod 600). Copia local en Desktop\BuySell365_Backups.
#   Retención:  14 días.
#   Restaurar:  openssl enc -d -aes-256-cbc -pbkdf2 -pass file:/root/.backup_pass \
#                 -in bs365_X.tar.enc | tar xz -C /tmp/restore
set -euo pipefail
APP=/opt/buysell365/app
DST=/opt/backups/buysell365
PASS=/root/.backup_pass
KEEP_DAYS=14
mkdir -p "$DST"; chmod 700 "$DST"
[ -s "$PASS" ] || { echo "falta $PASS"; exit 1; }
TS=$(date +%Y%m%d_%H%M)
OUT="$DST/bs365_$TS.tar.enc"
cd "$APP"
# Copiar primero a un staging para no leer JSONs a medio escribir por el bot
STG=$(mktemp -d)
cp -p .env "$STG"/ 2>/dev/null || true
cp -p *.session *.json *.pem "$STG"/ 2>/dev/null || true
tar czf - -C "$STG" . | openssl enc -aes-256-cbc -pbkdf2 -salt -pass "file:$PASS" -out "$OUT"
rm -rf "$STG"
chmod 600 "$OUT"
find "$DST" -name 'bs365_*.tar.enc' -mtime +$KEEP_DAYS -delete
echo "$(date '+%F %T') backup OK $(du -h "$OUT" | cut -f1) $OUT" >> "$DST/backup.log"

# Staging del deploy fallido del 2026-05-24

Esta carpeta contiene las **versiones merged** que se intentaron desplegar
al VPS y fueron revertidas. Sirven como punto de partida para el próximo
intento en otra sesión.

## Qué hay aquí

Los 6 archivos del VPS (descargados a las 21:52) con mis 8 ediciones aplicadas:

| Archivo | Cambios |
|---|---|
| `bot.py` | 5 textos UI: quitadas menciones MT5 no-XM en menú "5 pasos VIP", "Full transparency", descripción BotFather, re-engagement |
| `signal_copier.py` | Bloque transparency 20:15 borrado, 3 captions IG promo 14:00 sin MT5, frase "same broker same MT5" → "no upsells no tricks", "VIP = alerts + MT5" → "alerts + WhatsApp", "MT5 audited" → quitado, "verified on MT5" → "real time" |
| `daily_promo_publisher.py` | Captions grupo + IG: quitado bloque MT5+credentials, hashtag #transparency quitado |
| `daily_summary_publisher.py` | Caption grupo sin bloque MT5, regla solo-positivo global en `publicar_resumen_diario()` |
| `weekly_summary_publisher.py` | Regla solo-positivo global aplicada |
| `monthly_summary_publisher.py` | Regla solo-positivo global aplicada |

Y también pendiente: borrar `/opt/buysell365/app/transparency_publisher.py` (publicaba credenciales investor).

## Qué pasó en el deploy

1. 21:57:29 — Se hicieron backups en VPS: `*.bak_pre_deploy_20260524_215729`
2. 21:57:30 — Se subieron los 6 archivos vía scp
3. 21:57:32 — Se movió `transparency_publisher.py` a `.bak_deleted_20260524_215729`
4. 21:58:03 — `systemctl restart buysell365`
5. **El launcher quedó colgado** sin spawn `bot.py`, `signal_copier.py`, `monitor_real.py`
6. 22:02 — Se hizo rollback: copiar `.bak_*` de vuelta + `mv transparency_publisher.py.bak_deleted_* transparency_publisher.py` + restart
7. Bot recuperado y operativo

## Investigación pendiente

- `bot.py` importa OK (`python -c "import bot"` funciona)
- `signal_copier.py` importa OK
- Sintaxis Python válida (compile check OK)
- Encoding y line endings iguales al original
- Diff exacto coincide con los 5 textos UI esperados en bot.py
- 11 hunks en signal_copier.py coinciden con los esperados

**Sin embargo el launcher no spawn hijos.** Posibles causas a investigar:
- Race condition con `xvfb-run`
- Algún lock file que mi versión no limpia adecuadamente
- Dependencia oculta entre los archivos (¿alguno importa transparency_publisher por nombre y rompe en silencio?)
- Diferencia de timing del launcher al detectar el directorio actualizado

## Cómo reintentar (estrategia recomendada)

1. **Deploy archivo a archivo con restart entre cada uno.** Si el problema es uno solo, se aísla rápido.
2. Después de cada subida + restart, esperar 30s y verificar:
   ```bash
   ssh -i ~/.ssh/id_ed25519_buysell365 root@208.73.204.188 \
     "ps aux | grep -E 'launcher|bot.py|signal_copier' | grep -v grep | wc -l"
   ```
   Tiene que dar 4+ (launcher + bot + copier + opcionales).
3. Si vuelve a fallar, capturar log INMEDIATO del launcher y stack del proceso.
4. Si todo OK con el primer archivo, seguir con el siguiente.
5. Orden sugerido (menor riesgo → mayor riesgo):
   - `daily_promo_publisher.py` (más simple, no afecta arranque)
   - `daily_summary_publisher.py`
   - `weekly_summary_publisher.py`
   - `monthly_summary_publisher.py`
   - `signal_copier.py` (el más complejo de los publishers)
   - `bot.py` (el riesgo más alto)
   - Por último: `rm transparency_publisher.py`

## Comandos clave

Restaurar de backups si vuelve a fallar:
```bash
ssh -i ~/.ssh/id_ed25519_buysell365 root@208.73.204.188 \
  "cd /opt/buysell365/app && \
   for f in bot.py signal_copier.py daily_promo_publisher.py \
            daily_summary_publisher.py weekly_summary_publisher.py \
            monthly_summary_publisher.py; do \
     cp \$f.bak_pre_deploy_20260524_215729 \$f && echo restaurado_\$f; \
   done && systemctl restart buysell365"
```

# 🖥️ PANEL ADMIN — ACCESO RÁPIDO

## 🌐 URL PRINCIPAL
```
http://208.73.204.188:5001/
```

---

## 🔑 CREDENCIALES
Revisa tu archivo `.env` en esta carpeta:
- `ADMIN_USER` = username
- `ADMIN_PASSWORD` = password

---

## 📊 PÁGINAS DEL PANEL

| Página | URL | Qué Ver |
|--------|-----|---------|
| **Dashboard** | http://208.73.204.188:5001/ | Estado del bot, stats, últimas señales |
| **Logs** | http://208.73.204.188:5001/logs | Logs en vivo, errores, warnings |
| **Control** | http://208.73.204.188:5001/control | Start/Stop/Restart bot |
| **Signals** | http://208.73.204.188:5001/signals | Historial de trades |
| **WhatsApp** | http://208.73.204.188:5001/whatsapp | Gestionar destinatarios |
| **VIP** | http://208.73.204.188:5001/vip | Ver suscriptores VIP |
| **Config** | http://208.73.204.188:5001/config | Editar .env |

---

## 🎯 ACCESO RÁPIDO (Click para abrir)

**En desarrollo / test local:**
```bash
# Si estás en Linux/Mac/WSL:
curl http://208.73.204.188:5001/
```

**Desde navegador:**
1. Abre el navegador
2. Copia y pega: `http://208.73.204.188:5001/`
3. Entra con user/password de `.env`

---

## 📈 QUÉ SIGNIFICA CADA MÉTRICA

### Dashboard
- **Bot Status**: 🟢 running / 🔴 stopped
- **Memory**: RAM que usa el bot (debería ser <500MB)
- **CPU**: Tiempo total de CPU consumido
- **Copier Stats**: Total de trades, pips ganados, win rate
- **LLM Stats**: Llamadas a Claude, costo en USD

### Logs
- **[INFO]** — información normal
- **[WARNING]** — advertencias (no es error)
- **[ERROR]** — errores que deberían investigarse
- **[COPIER]** — logs del signal copier
- **[BOT]** — logs del bot principal

### Signals
- **TP** — Trade Profit (ganancia, ✅)
- **SL** — Stop Loss (pérdida, 🛑)
- **close_partial** — Cierre parcial
- **close_half** — Cierre al 50%

---

## ⚠️ PROBLEMAS COMUNES

**No puedo acceder (timeout)**
→ El panel está protegido con auth básica. Verifica:
- IP del servidor es correcta: `208.73.204.188`
- Puerto es correcto: `5001`
- User/password son correctos (en `.env`)

**Logs antiguos**
→ Normal si el bot ha estado corriendo mucho tiempo. Ve a `/logs` para ver logs en vivo.

**El bot aparece stopped**
→ Ir a `/control` y hacer click en "Start Bot"

---

## 🚀 PRÓXIMOS PASOS

1. **Verificar status**: Abre Dashboard
2. **Ver últimas señales**: Ir a Signals
3. **Revisar logs**: Ir a Logs si hay problemas
4. **Editar config**: Ir a Config si necesitas cambiar variables

---

**Actualizado:** 2026-06-21
**Bot version:** sin MT5 (Telethon only)
**Procesos activos:** bot.py, signal_copier.py, monitor_real.py

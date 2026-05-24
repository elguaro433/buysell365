#!/usr/bin/env python3
"""Parche data_access.py para leer VIPs y signals de los archivos correctos."""
import re

PATH = '/opt/buysell365/app/web_admin/data_access.py'
src = open(PATH).read()

# Patch 1: get_vip_subscribers — leer de estado.json["suscripciones_vip"]
new_vip_func = '''def get_vip_subscribers() -> list[dict]:
    """Lista de VIPs activos desde estado.json["suscripciones_vip"]."""
    import json, os
    from datetime import datetime
    estado_path = '/opt/buysell365/app/estado.json'
    try:
        with open(estado_path) as f:
            estado = json.load(f)
    except Exception:
        return []
    suscripciones = estado.get('suscripciones_vip', {})
    out = []
    if isinstance(suscripciones, dict):
        for uid, data in suscripciones.items():
            if not isinstance(data, dict):
                continue
            if not data.get('activo', True):
                continue
            # calcular dias restantes desde expira
            expira = data.get('expira', '')
            days_remaining = None
            try:
                dt = datetime.strptime(expira, '%Y-%m-%d %H:%M')
                days_remaining = max(0, (dt - datetime.now()).days)
            except Exception:
                pass
            out.append({
                'id': uid,
                'username': data.get('username') or data.get('nombre') or uid,
                'name': data.get('nombre', ''),
                'started_at': data.get('fecha_inicio', ''),
                'expires_at': expira,
                'days_remaining': days_remaining,
                'tier': data.get('tipo', 'VIP').upper(),
                'amount': data.get('monto', ''),
                'status': 'active',
            })
    return out
'''

# Replace existing function
src = re.sub(
    r'def get_vip_subscribers\(\)[^}]+?(?=\n(?:def |class |\Z))',
    new_vip_func,
    src,
    count=1,
    flags=re.DOTALL,
)

open(PATH, 'w').write(src)
print('OK patched get_vip_subscribers')

# Verify
import importlib, sys
sys.path.insert(0, '/opt/buysell365/app/web_admin')
spec = open(PATH).read()
print('VIPs func now contains:', 'estado.json' in spec, 'suscripciones_vip' in spec)

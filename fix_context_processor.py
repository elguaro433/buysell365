#!/usr/bin/env python3
"""Inyecta bot status en todos los templates via context_processor."""

PATH = '/opt/buysell365/app/web_admin/app.py'
with open(PATH) as f:
    src = f.read()

if '@app.context_processor' in src:
    print('SKIP - ya existe')
    raise SystemExit

old = '    return app'
new_lines = [
    '    # Inyectar bot status en TODOS los templates (no solo dashboard)',
    '    @app.context_processor',
    '    def _inject_bot_status():',
    '        try:',
    '            from . import data_access as da',
    '            return {"bot": da.get_bot_status()}',
    '        except Exception:',
    '            return {"bot": None}',
    '',
    '    return app',
]
new = '\n'.join(new_lines)

src = src.replace(old, new, 1)
with open(PATH, 'w') as f:
    f.write(src)
print('PATCHED context_processor')

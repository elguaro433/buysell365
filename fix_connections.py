#!/usr/bin/env python3
"""Reemplaza get_connections_status para usar heartbeats reales."""

PATH = '/opt/buysell365/app/web_admin/data_access.py'
with open(PATH) as f:
    lines = f.readlines()

start = None
end = None
for i, line in enumerate(lines):
    if line.startswith('def get_connections_status'):
        start = i
    elif start is not None and (line.startswith('def ') or line.startswith('class ')) and i > start:
        end = i
        break
if end is None:
    end = len(lines)
print(f'get_connections_status at lines {start+1}-{end}')

new_func = (
    "def get_connections_status() -> dict:\n"
    "    \"\"\"Estado de cada conexion: usa heartbeats reales (.bot.heartbeat, .copier.heartbeat) que se refrescan cada 20s.\"\"\"\n"
    "    import os\n"
    "    now = time.time()\n"
    "    APP = '/opt/buysell365/app'\n"
    "    def _age(path):\n"
    "        try:\n"
    "            return int(now - os.stat(path).st_mtime)\n"
    "        except Exception:\n"
    "            return None\n"
    "    bot_hb = _age(f'{APP}/.bot.heartbeat')\n"
    "    cop_hb = _age(f'{APP}/.copier.heartbeat')\n"
    "    return {\n"
    "        'telegram': bot_hb is not None and bot_hb < 60,\n"
    "        'telethon': cop_hb is not None and cop_hb < 60,\n"
    "        'whatsapp': bot_hb is not None and bot_hb < 60,\n"
    "        'render_sync': bot_hb is not None and bot_hb < 60,\n"
    "        'mt5': False,\n"
    "    }\n\n"
)

new_lines = lines[:start] + [new_func] + lines[end:]
with open(PATH, 'w') as f:
    f.writelines(new_lines)
print('PATCHED')

#!/usr/bin/env python3
"""Replace 'import winsound' with conditional import for Linux compatibility."""
import re

path = '/opt/buysell365/app/launcher.py'
content = open(path).read()

new_block = (
    "try:\n"
    "    import winsound\n"
    "except ImportError:\n"
    "    class _WinsoundStub:\n"
    "        def Beep(self, *a, **k): pass\n"
    "        def PlaySound(self, *a, **k): pass\n"
    "        SND_ASYNC = 0\n"
    "        SND_FILENAME = 0\n"
    "        SND_LOOP = 0\n"
    "    winsound = _WinsoundStub()"
)

content2 = re.sub(r'^import winsound\s*$', new_block, content, count=1, flags=re.MULTILINE)
if content2 != content:
    open(path, 'w').write(content2)
    print('REPLACED')
else:
    print('NO MATCH')

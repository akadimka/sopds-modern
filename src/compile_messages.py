"""Компилирует все .po → .mo без GNU gettext (использует polib)."""
import os
import sys

try:
    import polib
except ImportError:
    sys.exit("polib not installed. Run: pip install polib")

BASE = os.path.dirname(__file__)

compiled = 0
for root, dirs, files in os.walk(BASE):
    for name in files:
        if name.endswith('.po'):
            po_path = os.path.join(root, name)
            mo_path = po_path[:-3] + '.mo'
            po = polib.pofile(po_path)
            po.save_as_mofile(mo_path)
            print(f"  {os.path.relpath(mo_path, BASE)}")
            compiled += 1

print(f"\nCompiled {compiled} file(s).")

#!/usr/bin/env python3
import re, pathlib, sys

# Ruta correcta a tus plantillas
TEMPLATES_DIR = pathlib.Path(__file__).parent.parent / "src" / "templates"

if not TEMPLATES_DIR.exists():
    print(f"ERROR: no existe {TEMPLATES_DIR}", file=sys.stderr)
    sys.exit(1)

for path in TEMPLATES_DIR.rglob("*.html"):
    txt = path.read_text(encoding="utf-8")
    for n, line in enumerate(txt.splitlines(), 1):
        if 'style="' in line:
            # Muestra la ruta relativa desde la raíz del proyecto
            rel = path.relative_to(pathlib.Path(__file__).parent.parent)
            print(f"{rel}:{n}: {line.strip()}")

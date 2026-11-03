#!/usr/bin/env python3
"""Statische Pruefung: keine ungeescapten ${...}-Werte in HTML-Attributen von index.html
(XSS ueber IDs/Namen). Lauf: python tests/test_html_escape.py"""
import re, sys
from pathlib import Path

src = Path(__file__).resolve().parent.parent / "index.html"
text = src.read_text(encoding="utf-8")
ATTR = re.compile(r'(\s(?:data-[\w-]+|value|id|for|name|title|href|src|class)=")([^"`\n]*\$\{[^"`\n]*)"')
SIMPLE = re.compile(r'\$\{([A-Za-z_]\w*(?:\??\.\w+|\[[^\[\]]*\])*)\}')
bad = []
for m in ATTR.finditer(text):
    for k in SIMPLE.finditer(m.group(2)):
        line = text.count("\n", 0, m.start()) + 1
        bad.append((line, m.group(1).strip() + k.group(0)))
if "C=escapeHtml(ci.color)" not in text:
    bad.append((0, "Druckansicht: ci.color nicht escaped"))
for line, what in bad[:50]:
    print(f"FAIL index.html:{line}: unescaped {what}")
if bad:
    sys.exit(1)
print("OK: keine ungeescapten Attributwerte in index.html")

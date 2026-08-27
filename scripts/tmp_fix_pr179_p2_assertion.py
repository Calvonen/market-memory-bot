from pathlib import Path

path = Path("tests/test_mobile_source.py")
text = path.read_text(encoding="utf-8")
old = '''        self.assertIn("!status", loading_fallback)\n        self.assertIn("? 'Ladataan...'", loading_fallback)\n'''
new = '''        self.assertRegex(\n            loading_fallback,\n            r"!status\\s*\\?\\s*'Ladataan\\.\\.\\.'",\n        )\n'''
if old not in text:
    raise SystemExit("loading assertion anchor missing")
text = text.replace(old, new, 1)
path.write_text(text, encoding="utf-8")

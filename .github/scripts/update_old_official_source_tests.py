from pathlib import Path

path = Path('tests/test_official_release_source_repository.py')
text = path.read_text()
text = text.replace('repository.set(source, expected_version=1)', 'repository.set(source, expected_version=1, actor="marko")')
text = text.replace('repository.set(source, expected_version=-1)', 'repository.set(source, expected_version=-1, actor="marko")')
text = text.replace('repository.clear(self.EVENT_ID, expected_version=3)', 'repository.clear(self.EVENT_ID, expected_version=3, actor="marko")')
text = text.replace('repository.clear(self.EVENT_ID, expected_version=0)', 'repository.clear(self.EVENT_ID, expected_version=0, actor="marko")')
text = text.replace('"set_event_official_release_source"', '"set_event_official_release_source_approved"')
text = text.replace('"clear_event_official_release_source"', '"clear_event_official_release_source_approved"')
text = text.replace('        self.assertEqual(payload["input_expected_version"], 1)\n', '        self.assertEqual(payload["input_expected_version"], 1)\n        self.assertEqual(payload["input_actor"], "marko")\n')
text = text.replace('                "input_expected_version": 3,\n', '                "input_expected_version": 3,\n                "input_actor": "marko",\n')
path.write_text(text)

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import preferences
import showpass_adapter as adapter


class ShowpassAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.settings = self.root / "settings.json"
        patcher = patch.object(preferences, "SETTINGS_PATH", self.settings)
        patcher.start()
        self.addCleanup(patcher.stop)
        adapter._modules.clear()
        self.addCleanup(adapter._modules.clear)

    def checkout(self, name):
        path = self.root / name
        tool = path / "scripts/workspace"
        tool.mkdir(parents=True)
        (path / ".git").mkdir()
        (tool / "cli").write_text("")
        (tool / "lifecycle.py").write_text(
            "from dataclasses import dataclass\n"
            "@dataclass\nclass RepositoryContext:\n    name: str = 'test'\n"
            f"def load_all_states():\n    return [{{'name': {name!r}}}]\n"
        )
        return path

    def test_disabled_has_no_managed_states_or_machine_path(self):
        self.assertIsNone(adapter.configured_checkout())
        self.assertEqual(adapter.load_all_states(), [])
        with self.assertRaisesRegex(adapter.AdapterUnavailable, "Settings"):
            adapter.get_lifecycle()
        self.assertFalse(self.settings.exists())

    def test_explicit_checkout_loads_and_caches_module(self):
        path = self.checkout("chosen")
        preferences.save_settings([], showpass_checkout=str(path))
        module = adapter.get_lifecycle()
        self.assertIs(module, adapter.get_lifecycle())
        self.assertEqual(adapter.load_all_states(), [{"name": "chosen"}])
        self.assertEqual(adapter.RepositoryContext().name, "test")
        self.assertEqual(Path(module.__file__).parents[2], path.resolve())
        with self.assertRaises(AttributeError):
            getattr(adapter, "__path__")

    def test_switch_checkout_and_disable(self):
        one = self.checkout("one")
        two = self.checkout("two")
        preferences.save_settings([], showpass_checkout=str(one))
        first = adapter.get_lifecycle()
        preferences.save_settings([], showpass_checkout=str(two))
        self.assertIsNot(adapter.get_lifecycle(), first)
        self.assertEqual(adapter.load_all_states(), [{"name": "two"}])
        preferences.save_settings([], showpass_checkout="")
        self.assertEqual(adapter.load_all_states(), [])

    def test_missing_checkout_gives_actionable_error(self):
        self.settings.write_text(json.dumps({"directories": [], "showpass_checkout": str(self.root / "removed")}))
        with self.assertRaisesRegex(adapter.AdapterUnavailable, "Cannot load Showpass"):
            adapter.load_all_states()


if __name__ == "__main__":
    unittest.main()

import json
from concurrent.futures import ThreadPoolExecutor
import threading
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

import preferences


class PreferencesTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.path = self.root / "config/settings.json"
        self.patcher = patch.object(preferences, "SETTINGS_PATH", self.path)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_missing_settings_is_machine_independent(self):
        self.assertEqual(preferences.get_settings(), {"directories": [], "showpass_checkout": "", "docker_socket": "", "appearance": "system"})
        self.assertFalse(self.path.exists())

    def test_save_normalizes_deduplicates_and_persists(self):
        directory = self.root / "workspaces"
        directory.mkdir()
        alias = self.root / "alias"
        alias.symlink_to(directory, target_is_directory=True)
        expected = {"directories": [str(directory.resolve())], "showpass_checkout": "", "docker_socket": "", "appearance": "system"}
        self.assertEqual(preferences.save_settings([str(alias), str(directory)]), expected)
        self.assertEqual(preferences.get_settings(), expected)
        self.assertEqual(json.loads(self.path.read_text()), expected)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_home_expansion_and_empty_array(self):
        with patch.dict("os.environ", {"HOME": str(self.root)}):
            self.assertEqual(preferences.save_settings(["~"]), {"directories": [str(self.root.resolve())], "showpass_checkout": "", "docker_socket": "", "appearance": "system"})
        self.assertEqual(preferences.save_settings([]), {"directories": [], "showpass_checkout": "", "docker_socket": "", "appearance": "system"})
        self.assertEqual(preferences.get_settings(), {"directories": [], "showpass_checkout": "", "docker_socket": "", "appearance": "system"})

    def test_invalid_directories_leave_saved_settings_unchanged(self):
        preferences.save_settings([])
        file_path = self.root / "file"
        file_path.write_text("file")
        invalid = [None, {}, "array", [None], [1], [""], [" "], ["relative"], ["/" * 4097], ["/bad\0path"], [str(file_path)], [str(self.root / "missing")], [str(self.root)] * 33]
        for directories in invalid:
            with self.subTest(directories=directories), self.assertRaises(ValueError):
                preferences.save_settings(directories)
            self.assertEqual(preferences.get_settings(), {"directories": [], "showpass_checkout": "", "docker_socket": "", "appearance": "system"})
        with patch.object(preferences.os, "access", return_value=False), self.assertRaisesRegex(ValueError, "readable"):
            preferences.save_settings([str(self.root)])

    def test_loading_allows_removed_directories(self):
        directory = self.root / "workspaces"
        directory.mkdir()
        expected = preferences.save_settings([str(directory)])
        directory.rmdir()
        self.assertEqual(preferences.get_settings(), expected)

    def test_failed_atomic_replace_keeps_previous_settings_and_removes_temporary_file(self):
        preferences.save_settings([])
        with patch.object(preferences.os, "replace", side_effect=OSError("disk failure")), self.assertRaises(OSError):
            preferences.save_settings([str(self.root)])
        self.assertEqual(preferences.get_settings(), {"directories": [], "showpass_checkout": "", "docker_socket": "", "appearance": "system"})
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_legacy_directory_settings_get_optional_defaults(self):
        self.path.parent.mkdir()
        self.path.write_text(json.dumps({"directories": []}))
        self.assertEqual(preferences.get_settings(), {"directories": [], "showpass_checkout": "", "docker_socket": "", "appearance": "system"})

    def test_optional_settings_validate_and_preserve_when_omitted(self):
        checkout = self.root / "checkout"
        tool = checkout / "scripts/workspace"
        tool.mkdir(parents=True)
        (checkout / ".git").mkdir()
        (tool / "lifecycle.py").write_text("raise RuntimeError('must not execute')")
        (tool / "cli").write_text("cli")
        sock = socket.socket(socket.AF_UNIX)
        self.addCleanup(sock.close)
        socket_path = self.root / "docker.sock"
        sock.bind(str(socket_path))
        expected = preferences.save_settings([], str(checkout), str(socket_path))
        self.assertEqual(expected["showpass_checkout"], str(checkout.resolve()))
        self.assertEqual(expected["docker_socket"], str(socket_path.resolve()))
        self.assertEqual(preferences.save_settings([]), expected)
        self.assertEqual(preferences.save_settings([], "", ""), {"directories": [], "showpass_checkout": "", "docker_socket": "", "appearance": "system"})

    def test_invalid_optional_settings_do_not_replace_previous_file(self):
        expected = preferences.save_settings([])
        for key, values in (("showpass_checkout", ["relative", str(self.root), 1, " "]),
                            ("docker_socket", ["tcp://localhost:2375", str(self.root), 1, " "])):
            for value in values:
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    preferences.save_settings([], **{key: value})
                self.assertEqual(preferences.get_settings(), expected)

    def test_appearance_preserves_settings_and_removed_directories(self):
        directory = self.root / "workspaces"
        directory.mkdir()
        expected = preferences.save_settings([str(directory)])
        directory.rmdir()
        for appearance in ("dark", "light", "system"):
            expected["appearance"] = appearance
            self.assertEqual(preferences.save_appearance(appearance), expected)
            self.assertEqual(preferences.get_settings(), expected)
        preferences.save_appearance("dark")
        self.assertEqual(preferences.save_settings([])["appearance"], "dark")

    def test_invalid_appearance_does_not_change_settings(self):
        expected = preferences.save_appearance("dark")
        for value in (None, "auto", "Dark", "", 1, [], {}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                preferences.save_appearance(value)
            self.assertEqual(preferences.get_settings(), expected)
        with patch.object(preferences.os, "replace", side_effect=OSError("disk full")), self.assertRaises(OSError):
            preferences.save_appearance("light")
        self.assertEqual(preferences.get_settings(), expected)

    def test_concurrent_appearance_and_directory_saves_preserve_both(self):
        preferences.save_settings([])
        first_write = threading.Event()
        release_write = threading.Event()
        second_started = threading.Event()
        second_read = threading.Event()
        write = preferences._write_settings
        read = preferences.get_settings

        def delayed_write(settings):
            if settings["directories"]:
                first_write.set()
                self.assertTrue(release_write.wait(5))
            return write(settings)

        def tracked_read():
            if second_started.is_set():
                second_read.set()
            return read()

        def appearance_save():
            second_started.set()
            return preferences.save_appearance("dark")

        with ThreadPoolExecutor(max_workers=2) as pool, patch.object(preferences, '_write_settings', delayed_write), patch.object(preferences, 'get_settings', tracked_read):
            first = pool.submit(preferences.save_settings, [str(self.root)])
            try:
                self.assertTrue(first_write.wait(5))
                second = pool.submit(appearance_save)
                self.assertTrue(second_started.wait(5))
                self.assertFalse(second_read.wait(0.1), "Second update must wait for the first transaction")
            finally:
                release_write.set()
            first.result(timeout=5)
            second.result(timeout=5)
        self.assertEqual(preferences.get_settings()['directories'], [str(self.root.resolve())])
        self.assertEqual(preferences.get_settings()['appearance'], 'dark')

    def test_corrupt_settings_reports_error(self):
        self.path.parent.mkdir()
        for contents in ("broken", "[]", "{}", '{"directories": "wrong"}', '{"directories": ["relative"]}'):
            self.path.write_text(contents)
            with self.subTest(contents=contents), self.assertRaisesRegex(ValueError, "settings"):
                preferences.get_settings()


if __name__ == "__main__":
    unittest.main()

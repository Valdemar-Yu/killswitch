import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("killswitch_config", ROOT / "lib/config.py")
config = importlib.util.module_from_spec(spec)
spec.loader.exec_module(config)


class ConfigTests(unittest.TestCase):
    def create(self, path, *args, env=None):
        return subprocess.run(
            [sys.executable, str(ROOT / "lib/config.py"), "create", str(path), *args],
            env=env, text=True, capture_output=True,
        )

    def test_uid_and_socket_are_derived_from_sudo_user(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            result = self.create(path, env={**os.environ, "SUDO_UID": "1007"})
            self.assertEqual(result.returncode, 0, result.stderr)
            data = config.read(path)
            self.assertEqual(data["target_uid"], 1007)
            self.assertEqual(data["api_socket"], "/var/run/clash-verge-service/users/1007/verge-mihomo.sock")

    def test_reenable_preserves_config_and_custom_core_uid(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            first = self.create(path, "--uid", "1007", "--core-uid", "1007", "--core-path", "/Applications/Custom Core/mihomo")
            self.assertEqual(first.returncode, 0, first.stderr)
            before = config.read(path)
            restored = Path(directory) / "restored.json"
            result = self.create(restored, "--reuse", str(path), env={**os.environ, "SUDO_UID": "1020"})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(config.read(restored), before)

    def test_changing_target_uid_updates_default_socket(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            self.assertEqual(self.create(path, "--uid", "1007").returncode, 0)
            another = Path(directory) / "another.json"
            result = self.create(another, "--reuse", str(path), "--uid", "1020")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("/users/1020/", config.read(another)["api_socket"])

    def test_invalid_settings_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            self.assertEqual(self.create(path, "--uid", "1007").returncode, 0)
            good = config.read(path)
            for key, value in (
                ("target_uid", 0), ("core_uid", True), ("poll_interval", 0),
                ("api_timeout", float("inf")), ("core_path", "relative"),
                ("api_socket", "/tmp/socket\ninjected-command"),
            ):
                with self.subTest(key=key):
                    with self.assertRaises(ValueError):
                        config.validate({**good, key: value})


if __name__ == "__main__":
    unittest.main()

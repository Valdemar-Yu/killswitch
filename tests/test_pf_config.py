import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("killswitch_pf_config", ROOT / "lib/pf_config.py")
pf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pf)
BASE = (
    '# Test-only macOS PF fixture\n'
    'scrub-anchor "com.apple/*"\n'
    'nat-anchor "com.apple/*"\n'
    'rdr-anchor "com.apple/*"\n'
    'anchor "com.apple/*"\n'
)


class PfConfigTests(unittest.TestCase):
    def test_round_trip_and_idempotence(self):
        installed = pf.install(BASE, "/tmp/test-seed.rules")
        self.assertEqual(pf.install(installed, "/tmp/test-seed.rules"), installed)
        self.assertEqual(pf.strip(installed), BASE)
        self.assertEqual(pf.strip(BASE), BASE)

    def test_anchor_precedes_filter_rules(self):
        installed = pf.install(BASE + "pass out all\n", "/tmp/test-seed.rules")
        own = installed.index(f'anchor "{pf.ANCHOR}" quick')
        self.assertLess(own, installed.index('\nanchor "com.apple/*"'))
        self.assertLess(own, installed.index("pass out all"))

    def test_rollback_keeps_changes_added_by_another_admin(self):
        installed = pf.install(BASE, "/tmp/test-seed.rules")
        added = "\n# Another admin's change\nblock out quick to 192.0.2.10\n"
        self.assertEqual(pf.strip(installed + added), BASE + added)

    def test_mac_native_syntax(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            seed = directory / "blocked.rules"
            seed.write_text(subprocess.check_output(
                ["/bin/sh", str(ROOT / "watchdog.sh"), "--rules", "blocked"], text=True,
            ))
            main = directory / "pf.conf"
            main.write_text(pf.install(BASE, str(seed)))
            result = subprocess.run(["/sbin/pfctl", "-vnf", str(main)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()

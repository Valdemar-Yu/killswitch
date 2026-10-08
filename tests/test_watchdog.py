import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
CORE = "/Library/Application Support/clash-verge-service/cores/verge-mihomo"
HELPER = "/Library/PrivilegedHelperTools/clash-verge-service"
LABEL = "io.github.valdemar-yu.killswitch"


class WatchdogTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="killswitch-tests-")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        shutil.copytree(ROOT / "lib", self.directory / "lib")
        self.config = self.directory / "config.json"
        result = subprocess.run([
            sys.executable, str(ROOT / "lib/config.py"), "create", str(self.config), "--uid", "1007",
        ], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.env = {**os.environ, "KILLSWITCH_CONFIG": str(self.config)}
        self.fixture = {"processes": f"101 0 S {CORE}\n"}
        self.header = (
            "#!" + sys.executable + "\nimport json,os,pathlib,signal,sys\n"
            + "d=pathlib.Path(" + repr(str(self.directory)) + ")\n"
        )
        reader = r'''
if (d/'frames.json').exists():
    frames=json.loads((d/'frames.json').read_text())
    frame=min(int((d/'frame').read_text()),len(frames)-1)
    fixture=frames[frame]
else:
    fixture=json.loads((d/'fixture.json').read_text())
'''
        self.ps = self.executable("ps", self.header + reader + "\nprint(fixture['processes'],end='')\n")
        curl = self.executable("curl", self.header + reader + r'''
if fixture.get('api_error'): sys.exit(7)
if '--unix-socket' not in sys.argv or '--noproxy' not in sys.argv:
    raise SystemExit('API must use a local Unix socket and bypass proxy settings')
print(json.dumps(fixture.get('config',{'tun':{'enable':True,'device':'utun1024'}})))
''')
        ifconfig = self.executable("ifconfig", self.header + reader + r'''
if not fixture.get('exists',True) or sys.argv[1] != fixture.get('present_device','utun1024'):
    sys.exit(1)
print(fixture.get('flags','utun1024: flags=0<>'))
''')
        self.source = (
            (ROOT / "watchdog.sh").read_text()
            .replace("/bin/ps -Aww", str(self.ps) + " -Aww")
            .replace("/usr/bin/curl", str(curl))
            .replace("/sbin/ifconfig", str(ifconfig))
        )
        self.checker = self.directory / "watchdog.sh"
        self.checker.write_text(self.source)
        self.write_fixture(self.fixture)

    def executable(self, name, content):
        path = self.directory / name
        path.write_text(content)
        path.chmod(0o755)
        return path

    def write_fixture(self, fixture):
        (self.directory / "fixture.json").write_text(json.dumps(fixture))

    def check(self, option="--check"):
        return subprocess.run(
            ["/bin/sh", str(self.checker), option], env=self.env,
            capture_output=True, text=True, timeout=5,
        )

    def test_core_identity_and_exit(self):
        cases = (
            ("awake", f"101 0 R {CORE}\n", 0),
            ("sleeping", f"101 0 I {CORE}\n", 0),
            ("stopped", f"101 0 T {CORE}\n", 0),
            ("helper_only", f"100 0 S {HELPER}\n", 1),
            ("zombie", f"101 0 Z {CORE}\n", 1),
            ("wrong_user", f"101 1007 S {CORE}\n", 1),
            ("different_binary", f"101 0 S {CORE}-other\n", 1),
            ("absent", "", 1),
        )
        for name, processes, expected in cases:
            with self.subTest(name=name):
                self.write_fixture({"processes": processes})
                result = self.check("--check-core")
                self.assertEqual(result.returncode, expected, result.stderr)

    def test_custom_core_uid(self):
        data = json.loads(self.config.read_text())
        data["core_uid"] = 1007
        self.config.write_text(json.dumps(data))
        self.write_fixture({"processes": f"101 1007 S {CORE}\n"})
        self.assertEqual(self.check("--check-core").returncode, 0)

    def test_process_listing_error(self):
        self.ps.write_text("#!/bin/sh\nexit 1\n")
        result = self.check("--check-core")
        self.assertEqual(result.returncode, 2)
        result = self.check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("reason=process_list_unreadable", result.stdout)

    def test_tun_conditions_and_network_flags(self):
        cases = (
            ("online", {"flags": "utun1024: flags=<UP,RUNNING>"}, 0, "core_and_tun_present"),
            ("offline", {"flags": "utun1024: flags=0<>"}, 0, "core_and_tun_present"),
            ("reconnect", {"flags": "utun1024: flags=<POINTOPOINT>"}, 0, "core_and_tun_present"),
            ("disabled_but_interface_lingers", {"config": {"tun": {"enable": False}}}, 1, "tun_disabled"),
            ("interface_missing", {"exists": False}, 1, "tun_interface_missing"),
            ("unrelated_utun", {"present_device": "utun77"}, 1, "tun_interface_missing"),
            ("missing_config", {"config": {}}, 1, "tun_state_unreadable"),
            ("api_unavailable", {"api_error": True}, 1, "tun_state_unreadable"),
            ("physical_interface", {"config": {"tun": {"enable": True, "device": "en0"}}}, 1, "tun_state_unreadable"),
            ("nonboolean_enable", {"config": {"tun": {"enable": "true", "device": "utun1024"}}}, 1, "tun_state_unreadable"),
            ("helper_only", {"processes": f"100 0 S {HELPER}\n"}, 1, "core_absent"),
        )
        for name, settings, expected, reason in cases:
            with self.subTest(name=name):
                self.write_fixture({**self.fixture, **settings})
                result = self.check()
                self.assertEqual(result.returncode, expected, result.stderr)
                self.assertIn("reason=" + reason, result.stdout)

    def run_monitor(self, frames):
        (self.directory / "frames.json").write_text(json.dumps(frames))
        (self.directory / "frame").write_text("0")
        pfctl = self.executable("pfctl", self.header + r'''
args=sys.argv[1:]
if '-a' in args and '-f' in args:
    content=pathlib.Path(args[args.index('-f')+1]).read_text()
    rules='\n'.join(line for line in content.splitlines() if line and not line.startswith('#'))
    (d/'active_rules').write_text(rules)
    with (d/'events').open('a') as f:
        f.write(json.dumps({'frame':int((d/'frame').read_text()),'mode':'blocked' if 'block drop' in rules else 'running'})+'\n')
elif '-a' in args and '-sr' in args:
    print((d/'active_rules').read_text())
elif args == ['-sr']:
    print('anchor "io.github.valdemar-yu.killswitch" quick all')
elif args == ['-s','info']:
    print('Status: Enabled')
elif args == ['-F','states']:
    with (d/'flushes').open('a') as f: f.write((d/'frame').read_text()+'\n')
else:
    raise SystemExit('Unexpected PF command: '+repr(args))
''')
        identity = self.executable("id", "#!/bin/sh\nprintf '0\\n'\n")
        pause = self.executable("sleep", self.header + r'''
frame=int((d/'frame').read_text())+1
(d/'frame').write_text(str(frame))
if frame >= len(json.loads((d/'frames.json').read_text())):
    os.kill(os.getppid(),signal.SIGTERM)
''')
        monitor = (
            self.source
            .replace("STATE_DIR='/Library/Application Support/killswitch'", "STATE_DIR=" + repr(str(self.directory / "state")))
            .replace("/sbin/pfctl", str(pfctl))
            .replace("/usr/bin/id", str(identity))
            .replace("/bin/sleep", str(pause))
        )
        self.checker.write_text(monitor)
        result = subprocess.run(
            ["/bin/sh", str(self.checker)], env=self.env, capture_output=True,
            text=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0, (result.stderr, result.stdout))
        return [json.loads(line) for line in (self.directory / "events").read_text().splitlines()]

    def test_state_changes_and_state_flushes(self):
        alive = self.fixture
        events = self.run_monitor([
            {**alive, "flags": "utun1024: flags=<UP,RUNNING>"},
            {**alive, "flags": "utun1024: flags=0<>"},
            {**alive, "flags": "utun1024: flags=<POINTOPOINT>"},
            {**alive, "config": {"tun": {"enable": False}}},
            {**alive, "exists": False},
            alive,
            {"processes": f"100 0 S {HELPER}\n"},
            {"processes": f"101 0 Z {CORE}\n"},
            {"processes": f"202 0 S {CORE}\n"},
        ])
        self.assertEqual(events, [
            {"frame": 0, "mode": "blocked"}, {"frame": 0, "mode": "running"},
            {"frame": 3, "mode": "blocked"}, {"frame": 5, "mode": "running"},
            {"frame": 6, "mode": "blocked"}, {"frame": 8, "mode": "running"},
            {"frame": 9, "mode": "blocked"},
        ])
        flushes = set((self.directory / "flushes").read_text().splitlines())
        self.assertFalse({"1", "2"} & flushes)
        self.assertTrue({"3", "6"} <= flushes)

    def test_api_failure_blocks_and_recovers(self):
        events = self.run_monitor([
            self.fixture, {**self.fixture, "api_error": True}, self.fixture,
        ])
        self.assertEqual(events, [
            {"frame": 0, "mode": "blocked"}, {"frame": 0, "mode": "running"},
            {"frame": 1, "mode": "blocked"}, {"frame": 2, "mode": "running"},
            {"frame": 3, "mode": "blocked"},
        ])

    def test_native_pf_rule_syntax(self):
        for mode in ("running", "blocked"):
            with self.subTest(mode=mode):
                rules = self.directory / (mode + ".rules")
                rules.write_text(subprocess.check_output(
                    ["/bin/sh", str(ROOT / "watchdog.sh"), "--rules", mode], text=True,
                ))
                result = subprocess.run(["/sbin/pfctl", "-vnf", str(rules)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()

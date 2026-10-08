import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
LABEL = "io.github.valdemar-yu.killswitch"
BASE = '# Mock host PF\nscrub-anchor "com.apple/*"\nanchor "com.apple/*"\n'
FOREIGN_TOKEN = "18446744000000000001"


class LifecycleTests(unittest.TestCase):
    """Run the real shell lifecycle against a temporary, unprivileged mock host."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="killswitch-lifecycle-")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.source = self.directory / "source"
        self.runtime = self.directory / "Runtime With Spaces"
        self.commands = self.directory / "commands"
        self.daemons = self.directory / "LaunchDaemons"
        self.pf = self.directory / "pf.conf"
        self.pf.write_text(BASE)
        self.daemons.mkdir()
        self.commands.mkdir()
        self.source.mkdir()
        (self.source / "lib").mkdir()
        (self.source / "bin").mkdir()
        self.state = self.directory / "mock_state.json"
        self.state.write_text(json.dumps({"refs": [FOREIGN_TOKEN], "next": 18446744000000000010, "loaded": False, "main": BASE, "anchor": ""}))
        header = (
            "#!" + sys.executable + "\nimport json,os,pathlib,shutil,subprocess,sys\n"
            + "d=pathlib.Path(" + repr(str(self.directory)) + ")\n"
        )
        install = self.executable("mock-install", header + r'''
args=sys.argv[1:]; directories=False; mode=0o755; paths=[]
index=0
while index < len(args):
    word=args[index]
    if word == '-d': directories=True
    elif word in ('-o','-g','-m'):
        index+=1
        if word == '-m': mode=int(args[index],8)
    else: paths.append(word)
    index+=1
if directories:
    for p in paths:
        pathlib.Path(p).mkdir(parents=True,exist_ok=True)
        pathlib.Path(p).chmod(mode)
else:
    source,target=map(pathlib.Path,paths)
    shutil.copy2(source,target)
    target.chmod(mode)
''')
        pfctl = self.executable("mock-pfctl", header + r'''
args=sys.argv[1:]
state=json.loads((d/'mock_state.json').read_text())
if '-vnf' in args:
    result=subprocess.run(['/sbin/pfctl','-vnf',args[args.index('-vnf')+1]],capture_output=True,text=True)
    if result.returncode:
        print(result.stderr,file=sys.stderr); sys.exit(result.returncode)
elif '-a' in args and '-f' in args:
    state['anchor']=pathlib.Path(args[args.index('-f')+1]).read_text()
elif args[:2] == ['-a','io.github.valdemar-yu.killswitch'] and '-F' in args:
    state['anchor']=''
elif args == ['-F','states']: pass
elif args[:1] == ['-f']:
    state['main']=pathlib.Path(args[1]).read_text()
elif args == ['-E']:
    token=str(state['next']); state['next']+=1
    state['refs'].append(token); print('Token : '+token)
elif args[:1] == ['-X']:
    if args[1] not in state['refs']: sys.exit(1)
    state['refs'].remove(args[1])
elif args == ['-s','info']:
    print('Status: '+('Enabled' if state['refs'] else 'Disabled'))
elif args == ['-sr']: print(state['main'])
elif args == ['-s','References']:
    print('PID Process Name TOKEN TIMESTAMP')
    for token in state['refs']: print('999 SystemFirewall '+token+' 0')
elif '-a' in args and '-sr' in args: print(state['anchor'])
else: raise SystemExit('Unexpected PF operation: '+repr(args))
(d/'mock_state.json').write_text(json.dumps(state))
''')
        launchctl = self.executable("mock-launchctl", header + r'''
args=sys.argv[1:]
state=json.loads((d/'mock_state.json').read_text())
if args[:1] == ['print']:
    if args[1] != 'system/io.github.valdemar-yu.killswitch' or not state['loaded']: sys.exit(1)
    print('pid = 12345')
elif args[:1] == ['bootout']:
    if not state['loaded']: sys.exit(1)
    state['loaded']=False
elif args[:1] == ['bootstrap']:
    if (d/'fail-bootstrap').exists(): sys.exit(1)
    state['loaded']=True
    runtime=d/'Runtime With Spaces'
    (runtime/'status').write_text('version=0.1.0\nmode=running\nreason=core_and_tun_present\n')
    state['anchor']=''
else: raise SystemExit('Unexpected launchd operation: '+repr(args))
(d/'mock_state.json').write_text(json.dumps(state))
''')
        identity = self.executable("mock-id", "#!/bin/sh\nprintf '0\\n'\n")
        kill = self.executable("mock-kill", "#!/bin/sh\nexit 0\n")
        sleep = self.executable("mock-sleep", "#!/bin/sh\nexit 0\n")
        replacements = {
            "/Library/Application Support/killswitch": str(self.runtime),
            "/Library/LaunchDaemons": str(self.daemons),
            "/usr/local/bin": str(self.commands),
            "/etc/pf.conf": str(self.pf),
            "/usr/bin/install": str(install),
            "/sbin/pfctl": str(pfctl),
            "/bin/launchctl": str(launchctl),
            "/usr/bin/id": str(identity),
            "/bin/kill ": str(kill) + " ",
            "/bin/sleep ": str(sleep) + " ",
        }
        for relative in ("install.sh", "rollback.sh", "watchdog.sh", "status.sh", "bin/killswitch", "launchd.plist"):
            content = (ROOT / relative).read_text()
            for old, new in replacements.items():
                content = content.replace(old, new)
            (self.source / relative).write_text(content)
        for relative in ("lib/config.py", "lib/pf_config.py"):
            shutil.copy2(ROOT / relative, self.source / relative)
        self.env = {**os.environ, "SUDO_UID": "1007"}

    def executable(self, name, content):
        path = self.directory / name
        path.write_text(content)
        path.chmod(0o755)
        return path

    def run_script(self, name, *args):
        return subprocess.run(
            ["/bin/sh", str(self.source / name), *args], env=self.env,
            capture_output=True, text=True, timeout=10,
        )

    def read_state(self):
        return json.loads(self.state.read_text())

    def test_install_rollback_and_offline_reenable(self):
        installed = self.run_script("install.sh")
        self.assertEqual(installed.returncode, 0, installed.stderr)
        self.assertIn("successfully enable！", installed.stdout)
        self.assertTrue(self.read_state()["loaded"])
        self.assertEqual(len(self.read_state()["refs"]), 2)
        settings = json.loads((self.runtime / "config.json").read_text())
        self.assertEqual(settings["target_uid"], 1007)
        self.assertIn("/users/1007/", settings["api_socket"])

        # Another administrator edits PF after this package is installed.
        extra = "\n# Admin change after install\nblock out quick to 192.0.2.10\n"
        with self.pf.open("a") as file:
            file.write(extra)
        rolled_back = self.run_script("rollback.sh")
        self.assertEqual(rolled_back.returncode, 0, rolled_back.stderr)
        self.assertIn("successffully rollback！", rolled_back.stdout)
        self.assertFalse(self.read_state()["loaded"])
        self.assertEqual(self.read_state()["refs"], [FOREIGN_TOKEN])
        self.assertEqual(self.pf.read_text(), BASE + extra)
        self.assertFalse((self.daemons / (LABEL + ".plist")).exists())

        # Delete the download to prove the installed copy can re-enable offline.
        shutil.rmtree(self.source)
        enabled = subprocess.run(
            ["/bin/sh", str(self.commands / "killswitch"), "enable"],
            env=self.env, capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(enabled.returncode, 0, enabled.stderr)
        self.assertIn("successfully enable！", enabled.stdout)
        self.assertTrue(self.read_state()["loaded"])
        self.assertEqual(len(self.read_state()["refs"]), 2)
        self.assertEqual(json.loads((self.runtime / "config.json").read_text()), settings)

    def test_bootstrap_failure_keeps_blocked_rules_and_no_success(self):
        (self.directory / "fail-bootstrap").touch()
        installed = self.run_script("install.sh")
        self.assertNotEqual(installed.returncode, 0)
        self.assertNotIn("successfully enable！", installed.stdout)
        self.assertIn("block drop quick all", self.read_state()["anchor"])
        rolled_back = self.run_script("rollback.sh")
        self.assertEqual(rolled_back.returncode, 0, rolled_back.stderr)
        self.assertEqual(self.read_state()["refs"], [FOREIGN_TOKEN])
        self.assertEqual(self.pf.read_text(), BASE)

    def test_invalid_options_do_not_mutate_firewall(self):
        result = self.run_script("install.sh", "--uid", "0")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("successfully enable！", result.stdout)
        self.assertEqual(self.pf.read_text(), BASE)
        self.assertEqual(self.read_state()["refs"], [FOREIGN_TOKEN])

    def test_repeat_enable_holds_only_one_owned_pf_reference(self):
        for _ in range(2):
            result = self.run_script("install.sh")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(len(self.read_state()["refs"]), 2)
        first = self.run_script("rollback.sh")
        self.assertEqual(first.returncode, 0, first.stderr)
        second = self.run_script("rollback.sh")
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(self.read_state()["refs"], [FOREIGN_TOKEN])
        self.assertEqual(self.pf.read_text(), BASE)


if __name__ == "__main__":
    unittest.main()

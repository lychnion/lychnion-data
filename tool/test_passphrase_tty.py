"""The passphrase prompt of tool/records.dart on a real terminal (a pty):
Ctrl-C and Ctrl-D cancel, Enter gives the passphrase, and the terminal's
echo and signal keys are set back afterwards (lychnion/lychnion#118)."""
import os
import pty
import re
import select
import signal
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(ROOT, 'tool', 'records.dart')
PASS = 'a long test passphrase'


@unittest.skipUnless(sys.platform != 'win32', 'needs a pty')
class PassphraseOnATerminal(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which('dart'):
            if os.environ.get('CI'):
                raise AssertionError('dart is missing in CI')
            raise unittest.SkipTest('needs dart')
        cls.tmp = tempfile.mkdtemp()
        keygen = subprocess.run(['dart', TOOL, 'keygen'], cwd=ROOT,
                                capture_output=True, text=True, check=True)
        seed = keygen.stdout.strip()
        cls.public = keygen.stderr.strip().removeprefix('public key: ')
        pf = os.path.join(cls.tmp, 'pass')
        with open(pf, 'w') as f:
            f.write(f'{PASS}\n{PASS}\n')
        cls.backup = os.path.join(cls.tmp, 'root.lychnion-key')
        subprocess.run(
            ['dart', TOOL, 'backup', '--key', '-', '--out', cls.backup],
            cwd=ROOT, input=seed, text=True, capture_output=True, check=True,
            env={**os.environ, 'LYCHNION_TEST': '1',
                 'LYCHNION_PASSPHRASE_FILE': pf})

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def run_on_pty(self, keys, command=None, steps=None):
        """Runs [command] (default `public --key BACKUP`) on a pty. For each
        (prompt, keys) of [steps] (default one: 'Passphrase:', [keys]) it
        waits for the prompt and types the keys. Returns everything shown,
        then the terminal settings after."""
        command = command or f'public --key "{self.backup}"'
        steps = list(steps or [(b'Passphrase:', keys)])
        script = (f'dart "{TOOL}" {command}; '
                  'echo "exit=$?"; stty -a')
        pid, fd = pty.fork()
        if pid == 0:
            os.chdir(ROOT)
            os.environ.pop('LYCHNION_PASSPHRASE_FILE', None)
            os.execvp('sh', ['sh', '-c', script])
        out = b''
        deadline = time.time() + 60
        seen = 0
        while time.time() < deadline:
            ready, _, _ = select.select([fd], [], [], 1)
            if not ready:
                continue
            try:
                chunk = os.read(fd, 4096)
            except OSError:
                break
            if not chunk:
                break
            out += chunk
            if steps and out.find(steps[0][0], seen) >= 0:
                seen = out.find(steps[0][0], seen) + len(steps[0][0])
                time.sleep(0.5)
                os.write(fd, steps.pop(0)[1])
        else:
            os.kill(pid, signal.SIGKILL)
        os.waitpid(pid, 0)
        os.close(fd)
        text = out.decode(errors='replace')
        self.assertIn('exit=', text, 'timed out: ' + text[-300:])
        return text

    def settings_after(self, out):
        tokens = set(re.split(r'[\s;]+', out.split('exit=')[-1]))
        return tokens

    def assert_terminal_set_back(self, out):
        tokens = self.settings_after(out)
        self.assertIn('echo', tokens, 'echo is still off')
        self.assertIn('isig', tokens, 'signal keys are still off')

    def test_enter_gives_the_passphrase(self):
        out = self.run_on_pty(PASS.encode() + b'\r')
        self.assertIn(self.public, out)
        self.assertNotIn(PASS, out, 'the passphrase was shown')
        self.assert_terminal_set_back(out)

    def test_backspace_deletes(self):
        keys = (PASS[:-1] + 'X\x7f' + PASS[-1]).encode() + b'\r'
        out = self.run_on_pty(keys)
        self.assertIn(self.public, out)
        self.assert_terminal_set_back(out)

    def test_backspace_takes_a_whole_character(self):
        keys = (PASS + 'あ').encode() + b'\x7f\r'
        out = self.run_on_pty(keys)
        self.assertIn(self.public, out)

    def test_pasted_cr_lf(self):
        out = self.run_on_pty(PASS.encode() + b'\r\n')
        self.assertIn(self.public, out)
        self.assert_terminal_set_back(out)

    def test_cr_lf_paste_does_not_reach_the_next_prompt(self):
        # Three prompts: the old passphrase pasted with CR LF, then the
        # new one twice. Without the drain the LF would answer "New".
        new = 'another long passphrase'
        out_file = os.path.join(self.tmp, 'again.lychnion-key')
        out = self.run_on_pty(None, command=(
            f'backup --key "{self.backup}" --out "{out_file}"'), steps=[
            (b'Passphrase:', PASS.encode() + b'\r\n'),
            (b'New passphrase', new.encode() + b'\r'),
            (b'Again:', new.encode() + b'\r'),
        ])
        self.assertIn('written and opened again', out)
        self.assertIn('exit=0', out)
        self.assert_terminal_set_back(out)

    def test_arrow_key_cancels(self):
        out = self.run_on_pty(b'abc\x1b[D')
        self.assertIn('special keys', out)
        self.assert_terminal_set_back(out)

    def test_ctrl_c_cancels(self):
        out = self.run_on_pty(b'abc\x03')
        self.assertIn('Cancelled', out)
        self.assertIn('exit=1', out)
        self.assert_terminal_set_back(out)

    def test_ctrl_d_cancels(self):
        out = self.run_on_pty(b'\x04')
        self.assertIn('Cancelled', out)
        self.assert_terminal_set_back(out)


if __name__ == '__main__':
    unittest.main()

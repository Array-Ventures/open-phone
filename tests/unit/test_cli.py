"""Installed-package smoke checks; no device ownership or optional video import."""
import subprocess
import sys
import tempfile
import unittest

from openphone.cli import COMMANDS


class CLITests(unittest.TestCase):
    def test_all_command_help_works_outside_the_checkout(self):
        with tempfile.TemporaryDirectory() as directory:
            for command in [None, *COMMANDS]:
                with self.subTest(command=command):
                    args = [sys.executable, '-m', 'openphone']
                    if command:
                        args.append(command)
                    result = subprocess.run(args + ['--help'], cwd=directory,
                                            capture_output=True, text=True, timeout=10)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn('usage:', result.stdout)


if __name__ == '__main__':
    unittest.main()

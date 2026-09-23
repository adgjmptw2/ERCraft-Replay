import pathlib
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from process_tree import run_process_tree


class ProcessTreeTests(unittest.TestCase):
    def test_normal_child_exit_and_stdout_are_returned(self):
        result = run_process_tree(
            [sys.executable, '-c', "print('process-tree-ok')"],
            timeout=3,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), 'process-tree-ok')

    def test_timeout_terminates_child_and_grandchild(self):
        with tempfile.TemporaryDirectory(prefix='replay-tree-test-') as folder:
            marker = pathlib.Path(folder) / 'grandchild-finished'
            handshake = pathlib.Path(folder) / 'grandchild-started'
            grandchild = (
                f"import pathlib,time; time.sleep(2); "
                f"pathlib.Path({str(marker)!r}).write_text('alive')"
            )
            code = (
                f"import pathlib,subprocess,sys,time; "
                f"subprocess.Popen([sys.executable,'-c',{grandchild!r}]); "
                f"pathlib.Path({str(handshake)!r}).write_text('started'); time.sleep(30)"
            )
            outcome = []
            def invoke():
                try:
                    outcome.append(run_process_tree([sys.executable, '-c', code], timeout=0.4))
                except BaseException as error:
                    outcome.append(error)
            thread = threading.Thread(target=invoke)
            thread.start()
            deadline = time.monotonic() + 3
            while not handshake.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(handshake.exists())
            thread.join(5)
            self.assertEqual(len(outcome), 1)
            self.assertIsInstance(outcome[0], subprocess.TimeoutExpired)
            time.sleep(2.5)
            self.assertFalse(marker.exists())


if __name__ == '__main__':
    unittest.main()

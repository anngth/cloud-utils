import os
import signal
import subprocess
import sys
import textwrap
import unittest


BUD_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run_script(source: str, *, timeout: float = 2) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", source, BUD_DIR],
        capture_output=True,
        text=True,
        timeout=timeout,
        start_new_session=True,
        check=False,
    )


class ExecuteTests(unittest.TestCase):
    def test_keyboard_interrupt_exits_130_without_traceback(self):
        result = _run_script(
            textwrap.dedent(
                """
                import sys
                sys.path.insert(0, sys.argv[1])
                from cli import execute

                def entry():
                    raise KeyboardInterrupt

                execute(entry)
                """
            )
        )

        self.assertEqual(result.returncode, 130)
        self.assertNotIn("Traceback", result.stderr)
        self.assertNotIn("KeyboardInterrupt", result.stderr)
        self.assertIn("Cancelled.", result.stderr)
        self.assertTrue(result.stderr.endswith("\n"))
        self.assertNotIn("Cancelled.", result.stdout)

    def test_execute_preserves_return_code(self):
        result = _run_script(
            textwrap.dedent(
                """
                import sys
                sys.path.insert(0, sys.argv[1])
                from cli import execute

                execute(lambda: 7)
                """
            )
        )

        self.assertEqual(result.returncode, 7)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout, "")

    def test_sigint_during_parallel_probe_exits_quietly(self):
        script = textwrap.dedent(
            """
            import subprocess
            import sys
            import time

            sys.path.insert(0, sys.argv[1])
            from brew import load_brew_state
            from cli import execute

            class Slow:
                def run(self, args, ui):
                    print("READY", flush=True)
                    time.sleep(30)
                    return subprocess.CompletedProcess(list(args), 0, "", "")

            def entry():
                load_brew_state(Slow())
                return 0

            execute(entry)
            """
        )
        proc = subprocess.Popen(
            [sys.executable, "-c", script, BUD_DIR],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        assert proc.stdout is not None
        assert proc.stderr is not None
        ready = proc.stdout.readline()
        self.assertEqual(ready, "READY\n")
        proc.send_signal(signal.SIGINT)
        try:
            stdout, stderr = proc.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate()
            self.fail(f"still running after SIGINT\nstderr:\n{stderr}\nstdout:\n{stdout}")

        combined = stderr + stdout
        self.assertEqual(proc.returncode, 130)
        self.assertNotIn("Traceback", combined)
        self.assertNotIn("KeyboardInterrupt", combined)
        self.assertIn("Cancelled.", stderr)

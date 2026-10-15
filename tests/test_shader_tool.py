import os
import pathlib
import shlex
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from holodeck_shader_check import shader_tool


class ShaderToolResolutionTests(unittest.TestCase):
    def run_fake_tool(self, body, *, version_timeout=5, operation=None, operation_timeout=5):
        with tempfile.TemporaryDirectory(prefix="shader tool ") as temp:
            directory = pathlib.Path(temp)
            interpreter = directory / "python with spaces"
            interpreter.symlink_to(sys.executable)
            script = directory / "fake body.py"
            script.write_text(body, encoding="utf-8")
            tool = directory / "fake-tool"
            tool.write_text(f'#!/bin/sh\nexec {shlex.quote(str(interpreter))} {shlex.quote(str(script))} "$@"\n', encoding="utf-8")
            tool.chmod(0o700)
            driver = f"""
from holodeck_shader_check import shader_tool
shader_tool.VERSION_TIMEOUT_SECONDS = {version_timeout!r}
try:
    arguments = shader_tool.command({operation or 'render'!r})
    if {operation!r} is not None:
        shader_tool.run(arguments, operation={operation!r}, label="fixture scene")
except ValueError as error:
    print(type(error.__cause__).__name__)
    print(error)
    raise SystemExit(1)
print("OK")
"""
            env = dict(os.environ, HOLODECK_SHADER_TOOL=str(tool), PYTHONPATH=str(pathlib.Path(__file__).resolve().parents[1] / "src"))
            for variable, _ in shader_tool.TIMEOUTS.values():
                env.pop(variable, None)
            if operation:
                env[shader_tool.TIMEOUTS[operation][0]] = str(operation_timeout)
            # Keep the parent's pipe open: inherited stdin must not look like EOF.
            process = subprocess.Popen([sys.executable, "-c", driver], env=env, stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate()
                self.fail("Tool execution exceeded its bounded subprocess backstop")
            stdout, stderr = process.communicate()
            return process.returncode, stdout.decode(), stderr.decode()

    @unittest.skipUnless(os.name == "posix", "Executable fixtures require POSIX shell launchers")
    def test_hanging_version_probe_reports_timeout(self):
        code, stdout, stderr = self.run_fake_tool("import time\ntime.sleep(30)\n", version_timeout=0.25)
        self.assertEqual(code, 1, stderr)
        self.assertIn("TimeoutExpired", stdout)
        self.assertIn("version check", stdout)
        self.assertIn("timed out after 0.25 seconds", stdout)
        self.assertNotIn(shader_tool.INSTALL, stdout)

    @unittest.skipUnless(os.name == "posix", "Executable fixtures require POSIX shell launchers")
    def test_version_probe_disconnects_stdin_with_spaced_interpreter_path(self):
        code, stdout, stderr = self.run_fake_tool('import sys\nassert sys.stdin.read() == ""\nprint("holodeck-shader-tool 1.0.0")\n')
        self.assertEqual(code, 0, stdout + stderr)
        self.assertEqual(stdout.strip(), "OK")

    @unittest.skipUnless(os.name == "posix", "Executable fixtures require POSIX shell launchers")
    def test_invalid_version_encoding_reports_installation(self):
        code, stdout, stderr = self.run_fake_tool('import sys\nsys.stdout.buffer.write(b"\\xff")\n')
        self.assertEqual(code, 1, stderr)
        self.assertIn("UnicodeDecodeError", stdout)
        self.assertIn("not valid UTF-8", stdout)
        self.assertIn(shader_tool.INSTALL, stdout)

    @unittest.skipUnless(os.name == "posix", "Executable fixtures require POSIX shell launchers")
    def test_render_and_probe_disconnect_stdin(self):
        body = 'import sys\nassert sys.stdin.read() == ""\nif "--version" in sys.argv:\n    print("holodeck-shader-tool 1.0.0")\n'
        for operation in ("render", "probe"):
            with self.subTest(operation=operation):
                code, stdout, stderr = self.run_fake_tool(body, operation=operation)
                self.assertEqual(code, 0, stdout + stderr)
                self.assertEqual(stdout.strip(), "OK")

    @unittest.skipUnless(os.name == "posix", "Executable fixtures require POSIX shell launchers")
    def test_hanging_render_and_probe_report_operation_label_and_deadline(self):
        body = 'import sys, time\nif "--version" in sys.argv:\n    print("holodeck-shader-tool 1.0.0")\nelse:\n    time.sleep(30)\n'
        for operation in ("render", "probe"):
            with self.subTest(operation=operation):
                code, stdout, stderr = self.run_fake_tool(body, operation=operation, operation_timeout=0.25)
                self.assertEqual(code, 1, stderr)
                self.assertIn("TimeoutExpired", stdout)
                self.assertIn(f"{operation} for fixture scene timed out after 0.25 seconds", stdout)

    def test_nonabsolute_overrides_do_not_probe_or_fall_back(self):
        for value in ("", "holodeck-shader-tool", "./tool", "relative/tool"):
            with self.subTest(value=value), patch.dict(os.environ, {"HOLODECK_SHADER_TOOL": value}, clear=True), \
                 patch.object(shader_tool.shutil, "which") as which, patch.object(shader_tool.subprocess, "run") as run:
                with self.assertRaisesRegex(ValueError, "absolute executable path"):
                    shader_tool.command("render")
                which.assert_not_called()
                run.assert_not_called()

    def test_override_is_used_without_path_fallback(self):
        version = subprocess.CompletedProcess([], 0, "holodeck-shader-tool 1.0.0\n", "")
        with patch.dict(os.environ, {"HOLODECK_SHADER_TOOL": "/explicit/tool"}, clear=True), \
             patch.object(shader_tool.shutil, "which") as which, \
             patch.object(shader_tool.subprocess, "run", return_value=version) as run:
            self.assertEqual(shader_tool.command("probe"), ["/explicit/tool", "probe"])
            which.assert_not_called()
            self.assertEqual(run.call_args.args[0], ["/explicit/tool", "--version"])
            self.assertEqual(run.call_args.kwargs["timeout"], 5)
            self.assertEqual(run.call_args.kwargs["stdin"], subprocess.DEVNULL)

    def test_missing_invalid_and_incompatible_tools_report_installation(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(shader_tool.shutil, "which", return_value=None):
            with self.assertRaisesRegex(ValueError, "install"):
                shader_tool.command("render")
        with patch.dict(os.environ, {"HOLODECK_SHADER_TOOL": "/invalid/tool"}, clear=True), \
             patch.object(shader_tool.shutil, "which") as which, \
             patch.object(shader_tool.subprocess, "run", side_effect=FileNotFoundError):
            with self.assertRaisesRegex(ValueError, "Cannot run.*install"):
                shader_tool.command("render")
            which.assert_not_called()
        for output in ("holodeck-shader-tool 2.0.0", "unexpected output"):
            with patch.dict(os.environ, {}, clear=True), patch.object(shader_tool.shutil, "which", return_value="/tool"), \
                 patch.object(shader_tool.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, output, "")):
                with self.assertRaisesRegex(ValueError, "major version 1"):
                    shader_tool.command("render")

    def test_nonzero_version_exit_reports_execution_failure(self):
        with patch.dict(os.environ, {"HOLODECK_SHADER_TOOL": "/tool"}, clear=True), \
             patch.object(shader_tool.subprocess, "run", side_effect=subprocess.CalledProcessError(7, ["/tool", "--version"])):
            with self.assertRaisesRegex(ValueError, "Cannot run.*exit status 7.*install"):
                shader_tool.command("render")

    def test_execution_uses_default_and_configured_deadlines(self):
        for operation, default in (("render", 120), ("probe", 300)):
            for value in (None, "0.5", "900"):
                variable = shader_tool.TIMEOUTS[operation][0]
                env = {} if value is None else {variable: value}
                with self.subTest(operation=operation, value=value), patch.dict(os.environ, env, clear=True), \
                     patch.object(shader_tool.subprocess, "run") as run:
                    arguments = ["/tool", operation]
                    shader_tool.run(arguments, operation=operation, label="test scene")
                    run.assert_called_once_with(arguments, check=True, stdin=subprocess.DEVNULL,
                                                timeout=default if value is None else float(value))

    def test_invalid_deadlines_fail_before_any_tool_execution(self):
        for operation, (variable, _) in shader_tool.TIMEOUTS.items():
            for value in ("", " ", "text", "0", "-1", "nan", "inf", "-inf", "1e309"):
                with self.subTest(operation=operation, value=value), patch.dict(os.environ, {variable: value}, clear=True), \
                     patch.object(shader_tool.subprocess, "run") as run:
                    with self.assertRaisesRegex(ValueError, variable + " must be a positive finite number"):
                        shader_tool.command(operation)
                    with self.assertRaisesRegex(ValueError, variable + " must be a positive finite number"):
                        shader_tool.run(["/tool", operation], operation=operation, label="test scene")
                    run.assert_not_called()

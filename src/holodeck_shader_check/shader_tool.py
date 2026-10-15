"""Resolve the installed Holodeck GPU CLI. Static authoring needs no CLI."""
from .errors import ToolingError

import math
import os
import pathlib
import re
import shutil
import subprocess

INSTALL = "Build holodeck-shader-tool from HolodeckCore and install it on PATH, or set HOLODECK_SHADER_TOOL to its executable path."
VERSION_TIMEOUT_SECONDS = 5
TIMEOUTS = {"render": ("HOLODECK_RENDER_TIMEOUT_SECONDS", 120),
            "probe": ("HOLODECK_PROBE_TIMEOUT_SECONDS", 300)}


def timeout_seconds(operation):
    variable, default = TIMEOUTS[operation]
    value = os.environ.get(variable, str(default))
    try:
        seconds = float(value)
    except ValueError:
        raise ToolingError(f"{variable} must be a positive finite number of seconds") from None
    if not math.isfinite(seconds) or seconds <= 0:
        raise ToolingError(f"{variable} must be a positive finite number of seconds")
    return seconds


def run(arguments, *, operation, label):
    """Run an already-resolved GPU command with no interactive input."""
    seconds = timeout_seconds(operation)
    try:
        return subprocess.run(arguments, check=True, stdin=subprocess.DEVNULL, timeout=seconds)
    except subprocess.TimeoutExpired as error:
        raise ToolingError(f"Holodeck {operation} for {label} timed out after {seconds:g} seconds") from error


def command(*arguments, executable=None):
    if arguments and arguments[0] in TIMEOUTS:
        timeout_seconds(arguments[0])
    override = executable if executable is not None else os.environ.get("HOLODECK_SHADER_TOOL")
    if override is not None and not pathlib.Path(override).is_absolute():
        raise ToolingError("HOLODECK_SHADER_TOOL must be an absolute executable path. " + INSTALL)
    executable = override if override is not None else shutil.which("holodeck-shader-tool")
    if not executable:
        raise ToolingError("Holodeck shader tool is unavailable. " + INSTALL)
    try:
        result = subprocess.run([executable, "--version"], check=True, capture_output=True, text=True, encoding="utf-8",
                                timeout=VERSION_TIMEOUT_SECONDS, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired as error:
        raise ToolingError(f"Holodeck shader tool version check for {executable} timed out after {VERSION_TIMEOUT_SECONDS:g} seconds") from error
    except UnicodeDecodeError as error:
        raise ToolingError(f"Holodeck shader tool version output from {executable} is not valid UTF-8. " + INSTALL) from error
    except (OSError, subprocess.CalledProcessError) as error:
        raise ToolingError(f"Cannot run Holodeck shader tool {executable}: {error}. " + INSTALL) from error
    if re.fullmatch(r"holodeck-shader-tool 1\.\d+\.\d+", result.stdout.strip()) is None:
        raise ToolingError("Holodeck shader tool requires major version 1. " + INSTALL)
    return [executable, *arguments]

"""Command interface shared by wheel installs and the isolated zipapp."""
import argparse
import contextlib
import json
import pathlib
import subprocess
import sys
from . import __version__, INTERFACE_VERSION
from .errors import ToolingError
from .library import Library
from .licenses import audit_notices
from .profiles import run_profiles


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--version", action="version", version="holodeck-shader-check " + __version__)
    commands = result.add_subparsers(dest="command", required=True)
    info = commands.add_parser("info")
    info.add_argument("--json", action="store_true")
    validate = commands.add_parser("validate")
    validate.add_argument("root")
    validate.add_argument("--compile", action="store_true")
    validate.add_argument("--gpu", action="store_true")
    validate.add_argument("--gpu-only", action="store_true", help="Run GPU profiles and static checks without SDK compilation")
    validate.add_argument("--json", action="store_true")
    validate.add_argument("--shader-tool")
    for name in ("assemble", "render"):
        command = commands.add_parser(name)
        command.add_argument("root")
        command.add_argument("kind", choices=("shader", "snippet"))
        command.add_argument("id")
        if name == "assemble":
            command.add_argument("--output", required=True)
        else:
            command.add_argument("--times", default="0,3,10")
            command.add_argument("--width", type=int, default=1280)
            command.add_argument("--height", type=int, default=720)
            command.add_argument("--output-dir")
            command.add_argument("--shader-tool")
    return result


def main(arguments=None):
    args = parser().parse_args(arguments)
    if getattr(args, "gpu_only", False) and (args.compile or args.gpu):
        parser().error("--gpu-only cannot be combined with --compile or --gpu")
    if sys.version_info < (3, 9):
        message = "The shader checker requires Python 3.9 or later"
        if getattr(args, "json", False):
            print(json.dumps({"valid": False, "error": message, "exitCode": 2}))
        else:
            print(message, file=sys.stderr)
        return 2
    try:
        if args.command == "info":
            value = {"checkerVersion": __version__, "interfaceVersion": INTERFACE_VERSION}
            print(json.dumps(value) if args.json else "holodeck-shader-check " + __version__ + " (interface 1)")
            return 0
        library = Library(args.root, getattr(args, "shader_tool", None))
        if args.command == "validate":
            with contextlib.redirect_stdout(sys.stderr):
                items = library.validate(args.compile or args.gpu)
                checks = run_profiles(library, gpu=args.gpu or args.gpu_only)
            value = {"valid": True, "assets": len(items), "profileChecks": checks}
            print(json.dumps(value) if args.json else f"Validated {len(items)} authoring assets; {checks} profile checks")
        elif args.command == "assemble":
            pathlib.Path(args.output).write_bytes(library.assemble(args.kind, args.id).encode("utf-8"))
        else:
            library.render(args.kind, args.id, args.times, args.width, args.height, args.output_dir)
        return 0
    except (ToolingError, FileNotFoundError) as error:
        code = 2 if isinstance(error, ToolingError) else 1
        message = str(error)
    except (ValueError, KeyError, TypeError, OSError, AssertionError, subprocess.CalledProcessError) as error:
        code, message = 1, str(error)
    if getattr(args, "json", False):
        print(json.dumps({"valid": False, "error": message, "exitCode": code}))
    else:
        print("holodeck-shader-check: " + message, file=sys.stderr)
    return code

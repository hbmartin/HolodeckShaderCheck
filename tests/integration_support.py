"""Compatibility adapters for migrated contract tests; only explicit fixture roots."""
import contextlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from holodeck_shader_check.library import Library
from holodeck_shader_check import licenses, shader_tool

ROOT = os.environ.get("HOLODECK_LIBRARY_ROOT")
current = Library(ROOT) if ROOT else None


class Proxy:
    def __getattr__(self, name):
        return current.root if name == "ROOT" else shader_tool if name == "shader_tool" else getattr(current, name)

    @staticmethod
    def write_json(path, value):
        path.write_text(json.dumps(value), encoding="utf-8")


authoring = library = Proxy()


@contextlib.contextmanager
def fixture_root(*folders):
    global current
    previous = current
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        for folder in folders:
            shutil.copytree(previous.root / folder, root / folder)
        for name in ("index.json", "authoring.json"):
            shutil.copyfile(previous.root / name, root / name)
        current = Library(root)
        try:
            yield root
        finally:
            current = previous


def audit_notices(records=None, upstreams=None):
    return licenses.audit_notices(current, records, upstreams)

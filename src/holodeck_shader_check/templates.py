"""Exercise template assets as data in disposable, independently validated libraries."""
import json
import re
import shutil
import tempfile
from pathlib import Path
from .errors import ToolingError


PLACEHOLDER = re.compile(r"__[A-Z][A-Z0-9_]*__")


def validate_templates(library, compile_metal=False):
    from .library import KINDS, Library
    from .licenses import audit_notices

    templates = library.root / "templates"
    if templates.is_symlink():
        raise ValueError("Template symlink forbidden: templates")
    if not templates.exists():
        return
    if not templates.is_dir():
        raise ValueError("templates must be a directory")
    for entry in templates.rglob("*"):
        if entry.is_symlink():
            raise ValueError(f"Template symlink forbidden: {entry.relative_to(library.root)}")
        if not entry.is_dir() and not entry.is_file():
            raise ValueError(f"Invalid template file: {entry.relative_to(library.root)}")
    for template in sorted(templates.iterdir()):
        if not template.is_dir():
            continue
        kind = template.name
        if kind not in KINDS:
            raise ValueError(f"Unknown template kind: {kind}")
        try:
            with tempfile.TemporaryDirectory(prefix="holodeck-template-") as directory:
                root = Path(directory)
                for folder in ("shared", "shaders", "snippets", "references"):
                    shutil.copytree(library.root / folder, root / folder)
                for name in ("index.json", "authoring.json"):
                    shutil.copyfile(library.root / name, root / name)
                folder, _ = KINDS[kind]
                asset_id = "template-check-" + kind
                while (root / folder / asset_id).exists():
                    asset_id += "-x"
                destination = root / folder / asset_id
                shutil.copytree(template, destination)
                for file in destination.rglob("*"):
                    relative = file.relative_to(destination)
                    if PLACEHOLDER.search(str(relative)):
                        raise ValueError(f"Unresolved placeholder in path: {relative}")
                    # Upstream originals are immutable, even in a template harness.
                    if not file.is_file() or relative.parts[0] == "original":
                        continue
                    text = file.read_bytes().decode("utf-8")
                    text = text.replace("__ID__", asset_id).replace("__NAME__", "Template Check")
                    if PLACEHOLDER.search(text):
                        raise ValueError(f"Unresolved placeholder in {relative}")
                    file.write_bytes(text.encode("utf-8"))
                candidate = Library(root, library.shader_tool_path)
                item = candidate.find(kind, asset_id)
                configuration = candidate.configuration()
                # These are explicit original template sources in this disposable fixture,
                # never inferred provenance additions to the caller's library.
                sources = []
                if kind == "shader":
                    sources = [item["path"] + "/body.metal"]
                elif kind == "snippet":
                    spec = item["specification"]
                    sources = [spec["examplePath"]]
                    if spec.get("kind") == "recipe":
                        sources.append(spec["source"]["path"])
                for source in sources:
                    configuration["sourceRequirements"][source] = []
                (root / "authoring.json").write_text(json.dumps(configuration), encoding="utf-8")
                candidate.validate()
                audit_notices(candidate)
                if compile_metal and kind in ("shader", "snippet"):
                    candidate.compile_source(candidate.assemble(kind, asset_id), "template " + kind)
        except ToolingError:
            raise
        except (ValueError, KeyError, TypeError, OSError) as error:
            raise ValueError(f"Invalid {kind} template: {error}") from error

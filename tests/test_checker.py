import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipapp
from unittest.mock import patch
from holodeck_shader_check.cli import main
from holodeck_shader_check.errors import ToolingError
from holodeck_shader_check.library import Library
from holodeck_shader_check.licenses import audit_notices, mit_notices
from holodeck_shader_check.profiles import run_profiles
from support import fixture, write


class CheckerTests(unittest.TestCase):
    def setUp(self):
        self.context = fixture()
        self.library = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)
        self.root = self.library.root

    def run_cli(self, *arguments):
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = main(list(arguments))
        return code, output.getvalue(), errors.getvalue()

    def test_static_validation_needs_no_tooling(self):
        with patch("subprocess.run", side_effect=AssertionError("No subprocess for static checks")):
            code, output, _ = self.run_cli("validate", str(self.root), "--json")
        self.assertEqual(code, 0, output)
        self.assertEqual(json.loads(output), {"valid": True, "assets": 1, "profileChecks": 0})

    def test_independent_roots_do_not_leak(self):
        with fixture() as other:
            write(other.root, "shared/common.metal", "// other\n")
            self.assertNotEqual(other.assemble("shader", "example"), self.library.assemble("shader", "example"))
        self.assertIn("café", self.library.assemble("shader", "example"))

    def test_exact_utf8_crlf_assembly(self):
        components = [self.root / name for name in ("shared/common.metal", "shaders/example/body.metal", "shared/fragment.metal")]
        for newline in ("\n", "\r\n"):
            expected = b""
            for index, file in enumerate(components):
                data = (f"// café 雪 {index}" + newline).encode()
                file.write_bytes(data); expected += data
            target = self.root / "result.metal"
            self.assertEqual(self.run_cli("assemble", str(self.root), "shader", "example", "--output", str(target))[0], 0)
            self.assertEqual(target.read_bytes(), expected)

    def test_invalid_utf8_and_empty_assembly(self):
        for file in ("shared/common.metal", "shaders/example/body.metal", "shared/fragment.metal"):
            path = self.root / file; before = path.read_bytes(); path.write_bytes(b"\xff")
            with self.assertRaisesRegex(ValueError, "Invalid UTF-8"):
                self.library.assemble("shader", "example")
            path.write_bytes(before)
        for file in ("shared/common.metal", "shaders/example/body.metal", "shared/fragment.metal"):
            (self.root / file).write_bytes(b"")
        with self.assertRaisesRegex(ValueError, "Empty source"):
            self.library.assemble("shader", "example")

    def test_unlisted_shader_is_validated(self):
        shutil.copytree(self.root / "shaders/example", self.root / "shaders/unlisted")
        with self.assertRaisesRegex(ValueError, "Invalid or duplicate"):
            self.library.validate()

    def test_catalog_and_metadata_rejections(self):
        index = self.library.read_json(self.root / "index.json")
        for change in ({"shaders": ["example", "example"]}, {"defaultShaderID": "missing"}, {"collections": [{"id": "all", "name": "All", "description": "All", "shaderIDs": ["example"]}]}):
            write(self.root, "index.json", dict(index, **change))
            self.assertEqual(self.run_cli("validate", str(self.root))[0], 1)
        write(self.root, "index.json", index)
        file = self.root / "shaders/example/metadata.json"; metadata = self.library.read_json(file)
        for key, value in (("category", "UNKNOWN"), ("colors", [[0, 1]]), ("shared", ["../outside", "fragment"]), ("reuse", {"snippets": ["missing"], "references": []})):
            write(self.root, str(file.relative_to(self.root)), dict(metadata, **{key: value}))
            self.assertEqual(self.run_cli("validate", str(self.root))[0], 1)

    def test_duplicate_json_and_invalid_configuration(self):
        (self.root / "authoring.json").write_text('{"schemaVersion":1,"schemaVersion":1}')
        with self.assertRaisesRegex(ValueError, "Duplicate JSON key"):
            self.library.configuration()

    def test_missing_unknown_and_required_profile_assets(self):
        config = self.library.configuration()
        for profile in ("unknown-v1", "matrix-corridor-v1", "holodeck-grid-v1"):
            write(self.root, "authoring.json", dict(config, regressionProfiles=[profile]))
            with self.assertRaises(ValueError):
                run_profiles(self.library)

    def test_missing_stale_and_invalid_provenance(self):
        config = self.library.configuration()
        for records in ({}, dict(config["sourceRequirements"], **{"obsolete.metal": []}), dict(config["sourceRequirements"], **{"shared/common.metal": ["undeclared"]})):
            write(self.root, "authoring.json", dict(config, sourceRequirements=records))
            self.assertEqual(self.run_cli("validate", str(self.root))[0], 1)

    def add_reference(self):
        original = b"original bytes\r\n"
        write(self.root, "references/fixture/reference.json", {"schemaVersion": 1, "id": "fixture", "name": "Fixture", "summary": "Fixture", "language": "MSL", "tags": [], "searchTerms": [], "requirements": [], "adaptationNotes": "Original", "origin": {"author": "Fixture", "url": None, "license": "unknown"}, "files": {"file.metal": hashlib.sha256(original).hexdigest()}})
        write(self.root, "references/fixture/original/file.metal", "")
        (self.root / "references/fixture/original/file.metal").write_bytes(original)

    def test_reference_inventory_and_changed_bytes(self):
        self.add_reference(); self.library.validate()
        (self.root / "references/fixture/original/file.metal").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            self.library.validate()
        write(self.root, "references/fixture/original/extra", "extra")
        with self.assertRaisesRegex(ValueError, "inventory changed"):
            self.library.validate()

    def test_reference_symlinks_including_broken_links(self):
        self.add_reference()
        for name in ("broken", "directory"):
            file = self.root / "references/fixture" / name
            file.symlink_to(self.root / "absent" if name == "broken" else self.root, target_is_directory=name == "directory")
            with self.assertRaisesRegex(ValueError, "symlink forbidden"):
                self.library.validate()
            file.unlink()

    def test_canonical_snippet_assembly(self):
        write(self.root, "snippets/recipe/snippet.json", {"schemaVersion": 1, "id": "recipe", "name": "Recipe", "summary": "Fixture", "kind": "recipe", "tags": [], "searchTerms": [], "source": {"path": "snippets/recipe/code.metal", "symbols": ["hdRecipe"]}, "requiredShared": ["common"], "examplePath": "snippets/recipe/example.metal", "origin": {"shaderIDs": [], "attribution": "Original", "license": "Original"}})
        write(self.root, "snippets/recipe/code.metal", "float hdRecipe() { return 0; }\r\n")
        write(self.root, "snippets/recipe/example.metal", "// example\r\n")
        write(self.root, "snippets/recipe/README.md", "Interface Coordinates Tuning Limitations Cost")
        expected = b"\n".join((self.root / path).read_bytes() for path in ("shared/common.metal", "snippets/recipe/code.metal", "snippets/recipe/example.metal", "shared/fragment.metal"))
        self.assertEqual(self.library.assemble("snippet", "recipe").encode(), expected)
        spec = self.library.read_json(self.root / "snippets/recipe/snippet.json"); spec["source"]["path"] = "shared/common.metal"
        write(self.root, "snippets/recipe/snippet.json", spec)
        with self.assertRaisesRegex(ValueError, "canonical source"):
            self.library.assemble("snippet", "recipe")

    def test_sdk_unavailability_is_operational(self):
        with patch("shutil.which", return_value=None):
            code, output, _ = self.run_cli("validate", str(self.root), "--compile", "--json")
        self.assertEqual(code, 2, output)

    def test_incomplete_mit_notices(self):
        for notice in ("Copyright fixture", "Copyright fixture Permission is hereby granted SOFTWARE."):
            with self.assertRaises(ValueError): mit_notices(notice)

    def test_reference_asset_metadata_and_notice_symlinks(self):
        self.add_reference()
        directory=self.root / "references/fixture"
        alias=self.root / "references/alias"
        alias.symlink_to(directory, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink forbidden"): self.library.validate()
        alias.unlink()
        metadata=directory / "reference.json"
        data=metadata.read_bytes(); metadata.unlink()
        target=self.root / "metadata-copy.json"; target.write_bytes(data); metadata.symlink_to(target)
        with self.assertRaisesRegex(ValueError, "symlink forbidden"): self.library.validate()
        metadata.unlink(); metadata.write_bytes(data)
        (directory / "NOTICE").symlink_to(directory / "original/file.metal")
        with self.assertRaisesRegex(ValueError, "symlink forbidden"): self.library.validate()

    def test_zipapp_ignores_checkout_import_hooks(self):
        marker=self.root / "executed"
        for name in ("json.py", "sitecustomize.py", "usercustomize.py"):
            write(self.root, name, f"from pathlib import Path; Path({str(marker)!r}).touch(); raise RuntimeError('candidate code')")
        with tempfile.TemporaryDirectory() as directory:
            stage=Path(directory)/"stage"; stage.mkdir()
            shutil.copytree(Path(__file__).resolve().parents[1]/"src/holodeck_shader_check", stage/"holodeck_shader_check", ignore=shutil.ignore_patterns("__pycache__"))
            target=Path(directory)/"checker.pyz"
            zipapp.create_archive(stage, target, main="holodeck_shader_check.cli:main")
            result=subprocess.run([sys.executable,"-I",str(target),"validate",str(self.root),"--json"],cwd=self.root,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertTrue(json.loads(result.stdout)["valid"])
            self.assertFalse(marker.exists())

    def test_no_checkout_python_is_imported(self):
        marker = self.root / "executed"
        for name in ("authoring.py", "library.py", "shader_tool.py", "json.py", "sitecustomize.py"):
            write(self.root, name, f"raise RuntimeError({str(marker)!r})")
        self.assertEqual(self.run_cli("validate", str(self.root))[0], 0)

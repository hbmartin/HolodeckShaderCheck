import hashlib
import json
import shutil
import unittest
from unittest.mock import patch
from holodeck_shader_check.errors import ToolingError
from holodeck_shader_check.library import Library
from support import fixture, write


class TemplateTests(unittest.TestCase):
    def setUp(self):
        context = fixture()
        self.library = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        self.root = self.library.root
        metadata = self.library.read_json(self.root / "shaders/example/metadata.json")
        metadata.update(id="__ID__", name="__NAME__")
        write(self.root, "templates/shader/metadata.json", metadata)
        write(self.root, "templates/shader/body.metal", "float3 shade(float2 p, float t, float2 pixel) { return float3(0.5); }\r\n")
        write(self.root, "templates/snippet/snippet.json", {
            "schemaVersion": 1, "id": "__ID__", "name": "__NAME__", "summary": "Template", "kind": "recipe",
            "tags": [], "searchTerms": [], "source": {"path": "snippets/__ID__/code.metal", "symbols": ["hdRecipe"]},
            "requiredShared": ["common"], "examplePath": "snippets/__ID__/example.metal",
            "origin": {"shaderIDs": [], "attribution": "Original", "license": "Original"},
        })
        write(self.root, "templates/snippet/code.metal", "float3 hdRecipe() { return float3(0.5); }\n")
        write(self.root, "templates/snippet/example.metal", "float3 shade(float2 p, float t, float2 pixel) { return hdRecipe(); }\n")
        write(self.root, "templates/snippet/README.md", "Interface Coordinates Tuning Limitations Cost")
        write(self.root, "templates/reference/reference.json", {
            "schemaVersion": 1, "id": "__ID__", "name": "__NAME__", "summary": "Template", "language": "unknown",
            "tags": [], "searchTerms": [], "requirements": [], "adaptationNotes": "Original",
            "origin": {"author": "unknown", "url": None, "license": "unknown"}, "files": {},
        })

    def snapshot(self):
        return {str(file.relative_to(self.root)): file.read_bytes() for file in self.root.rglob("*") if file.is_file()}

    def test_all_templates_validate_without_tooling_or_checkout_changes(self):
        before = self.snapshot()
        with patch("subprocess.run", side_effect=AssertionError("Static validation must not invoke native tools")):
            self.assertEqual(len(self.library.validate()), 1)
        self.assertEqual(self.snapshot(), before)

    def test_broken_json_and_invalid_substituted_metadata_fail(self):
        for kind, filename in (("shader", "metadata.json"), ("snippet", "snippet.json"), ("reference", "reference.json")):
            file = self.root / "templates" / kind / filename
            original = file.read_bytes()
            for value in ("{ broken JSON", json.dumps(dict(json.loads(original), id="wrong-id"))):
                with self.subTest(kind=kind, value=value):
                    file.write_text(value)
                    with self.assertRaisesRegex(ValueError, f"Invalid {kind} template"):
                        self.library.validate()
            file.write_bytes(original)

    def test_missing_required_files_fail(self):
        for relative in ("shader/metadata.json", "shader/body.metal", "snippet/code.metal", "snippet/example.metal", "snippet/README.md", "reference/reference.json"):
            with self.subTest(file=relative):
                file = self.root / "templates" / relative
                before = file.read_bytes()
                file.unlink()
                with self.assertRaisesRegex(ValueError, "Invalid .* template"):
                    self.library.validate()
                file.write_bytes(before)

    def test_unknown_placeholder_fails(self):
        write(self.root, "templates/shader/body.metal", "// __UNRESOLVED__\n")
        with self.assertRaisesRegex(ValueError, "Unresolved placeholder"):
            self.library.validate()

    def test_template_symlinks_are_rejected_including_broken_links(self):
        templates = self.root / "templates"
        for target in (self.root / "missing", self.root / "shared"):
            link = templates / "shader/link"
            link.symlink_to(target)
            with self.assertRaisesRegex(ValueError, "Template symlink forbidden"):
                self.library.validate()
            link.unlink()
        templates.rename(self.root / "saved-templates")
        templates.symlink_to(self.root / "saved-templates", target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "Template symlink forbidden"):
            self.library.validate()

    def test_templates_compile_alongside_assets_and_tooling_errors_remain_operational(self):
        with patch.object(Library, "compile_source") as compile_source:
            self.library.validate(compile_metal=True)
        self.assertEqual({call.args[1] for call in compile_source.call_args_list}, {"example", "template shader", "template snippet"})
        with patch.object(Library, "compile_source", side_effect=ToolingError("SDK unavailable")):
            with self.assertRaises(ToolingError):
                from holodeck_shader_check.templates import validate_templates
                validate_templates(self.library, compile_metal=True)

    def test_no_templates_remains_valid(self):
        shutil.rmtree(self.root / "templates")
        self.library.validate()

    def test_reference_originals_keep_their_bytes(self):
        original = b"// upstream __ID__ remains unchanged\r\n"
        write(self.root, "templates/reference/original/source.metal", "")
        (self.root / "templates/reference/original/source.metal").write_bytes(original)
        file = self.root / "templates/reference/reference.json"
        metadata = json.loads(file.read_text())
        metadata["files"] = {"source.metal": hashlib.sha256(original).hexdigest()}
        write(self.root, "templates/reference/reference.json", metadata)
        before = self.snapshot()
        self.library.validate()
        self.assertEqual(self.snapshot(), before)

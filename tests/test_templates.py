import hashlib
import json
import os
import subprocess
import tempfile
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

    def test_compiler_macros_and_user_tokens_are_not_placeholders(self):
        write(self.root, "templates/shader/body.metal", "#if __METAL_VERSION__ >= 300\n#endif\n// __FILE__ __LINE__ __UNRESOLVED__\n")
        self.library.validate()

    def test_reserved_placeholders_in_paths_fail(self):
        write(self.root, "templates/shader/__ID__.metal", "")
        with self.assertRaisesRegex(ValueError, "Unresolved placeholder in path"):
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

    def test_hidden_incidental_files_and_binary_assets_are_preserved_safely(self):
        (self.root/'templates/.idea').mkdir()
        (self.root/'templates/shader/.DS_Store').write_bytes(b'\xff\xfe')
        binary=self.root/'templates/shader/illustration.png';binary.write_bytes(b'\xff\x00__ID__')
        before=binary.read_bytes()
        self.library.validate()
        self.assertEqual(binary.read_bytes(),before)

    def test_unused_external_preview_link_is_never_copied(self):
        with tempfile.TemporaryDirectory() as directory:
            (self.root/'shaders/example/preview.png').symlink_to(directory,target_is_directory=True)
            with patch('shutil.copytree',side_effect=AssertionError('Recursive checkout copying is forbidden')):
                self.library.validate()

    def test_required_source_symlink_and_special_template_files_fail(self):
        source=self.root/'shared/common.metal';before=source.read_bytes();source.unlink()
        external=self.root/'outside.metal';external.write_bytes(before);source.symlink_to(external)
        with self.assertRaisesRegex(ValueError,'fixture input.*symbolic links'):
            self.library.validate()
        source.unlink();os.mkfifo(source)
        with self.assertRaisesRegex(ValueError,'regular file'):self.library.validate()
        source.unlink();source.write_bytes(before)
        os.mkfifo(self.root/'templates/shader/fifo')
        with self.assertRaisesRegex(ValueError,'Invalid template file'):
            self.library.validate()

    def test_notice_path_outside_standard_folders_is_copied(self):
        write(self.root,'references/notice/reference.json',{'schemaVersion':1,'id':'notice','name':'Notice','summary':'Fixture','language':'MSL',
            'tags':[],'searchTerms':[],'requirements':[],'adaptationNotes':'Fixture','origin':{'author':'Fixture','license':'MIT'},'files':{}})
        write(self.root,'LICENSES/notice.txt','Copyright Fixture Permission is hereby granted THE SOFTWARE IS PROVIDED SOFTWARE.')
        config=self.library.configuration();config['upstreamRequirements']['notice']={'license':'MIT','noticePath':'LICENSES/notice.txt'}
        write(self.root,'authoring.json',config)
        self.library.validate()

    def test_directory_replacement_cannot_escape_pinned_inputs(self):
        from holodeck_shader_check.inputs import InputReader
        directory=self.root/'nested';directory.mkdir();write(self.root,'nested/source','approved')
        outside=self.root/'outside';outside.mkdir();write(self.root,'outside/source','external')
        original=os.open
        def swapped(path,*args,**kwargs):
            if path=='nested':
                directory.rename(self.root/'saved-nested');directory.symlink_to(outside,target_is_directory=True)
            return original(path,*args,**kwargs)
        with InputReader(self.root) as reader, patch('holodeck_shader_check.inputs.os.open',side_effect=swapped):
            with self.assertRaisesRegex(ValueError,'without following symbolic links'):reader.read('nested/source')

    def test_compile_failures_include_template_label_sdk_and_diagnostic(self):
        def compile(args,**kwargs):
            if '--version' in args:return subprocess.CompletedProcess(args,0,b'',b'')
            return subprocess.CompletedProcess(args,1,b'',b'bad fixture expression')
        with patch('holodeck_shader_check.library.shutil.which',return_value='/usr/bin/xcrun'),patch('subprocess.run',side_effect=compile):
            with self.assertRaisesRegex(ValueError,'template shader.*macosx.*bad fixture expression'):
                self.library.compile_source('bad metal','template shader')

    def test_validation_scans_base_assets_once_and_uses_one_fixture(self):
        with patch.object(self.library,'assets',wraps=self.library.assets) as assets, patch('holodeck_shader_check.templates.tempfile.TemporaryDirectory',wraps=tempfile.TemporaryDirectory) as temporary:
            self.library.validate()
        self.assertEqual(assets.call_count,1);self.assertEqual(temporary.call_count,1)

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

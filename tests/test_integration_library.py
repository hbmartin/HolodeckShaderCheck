from integration_support import ROOT, authoring, library, fixture_root, audit_notices
from holodeck_shader_check.licenses import mit_notices
import contextlib
import copy
from collections import Counter
import io
import json
import os
import pathlib
import shutil
import tempfile
import sys
import unittest
from unittest.mock import patch

@unittest.skipUnless(ROOT, 'Set HOLODECK_LIBRARY_ROOT for real-library integration tests')
class LibraryTests(unittest.TestCase):

    def setUp(self):
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        self.root = stack.enter_context(fixture_root('shaders', 'shared', 'snippets', 'references', 'templates', 'docs'))
        self.asset_count = len(library.assets())

    def test_broken_specs_links_and_paths(self):
        file = self.root / 'snippets/polar-fold/snippet.json'
        original = authoring.read_json(file)
        for value in ('../escape', 'shared/common.metal', 'snippets/missing/code.metal'):
            spec = dict(original, source={'path': value, 'symbols': ['hdPolarFold']})
            authoring.write_json(file, spec)
            with self.assertRaises(ValueError):
                library.validate()
        authoring.write_json(file, original)
        (self.root / 'snippets/polar-fold/example.metal').unlink()
        with self.assertRaises(ValueError):
            library.validate()

    def test_snippet_sources_preserve_component_bytes_and_harness_separators(self):
        for snippet_id in ('value-noise', 'glowing-ribbons'):
            with self.subTest(snippet_id=snippet_id):
                item = library.find('snippet', snippet_id)
                spec = item['specification']
                components = [self.root / 'shared' / f'{part}.metal' for part in spec['requiredShared']]
                if spec['kind'] == 'recipe':
                    components.append(self.root / spec['source']['path'])
                components.extend([self.root / spec['examplePath'], self.root / 'shared/fragment.metal'])
                expected = []
                for path in components:
                    data = b'// caf\xc3\xa9\r\n' + path.read_bytes().replace(b'\r\n', b'\n').replace(b'\n', b'\r\n')
                    path.write_bytes(data)
                    expected.append(data)
                self.assertEqual(library.snippet_source(item).encode('utf-8'), b'\n'.join(expected))

    def test_render_filenames_preserve_distinct_times_and_integer_names(self):
        paths = []
        with patch.object(library.shader_tool, 'command', return_value=['renderer', 'render']), patch.object(library.shader_tool, 'run', side_effect=lambda args, **kwargs: paths.append(pathlib.Path(args[3]).name)), contextlib.redirect_stdout(io.StringIO()):
            library.render('shader', 'plasma', '0,3,10,1000000,1000001,0.12345671,0.12345672,3')
        self.assertEqual(paths[:3], ['1280x720-t0.png', '1280x720-t3.png', '1280x720-t10.png'])
        self.assertEqual(len(set(paths)), 7)
        self.assertEqual(paths[1], paths[-1])

    def test_missing_specification_and_duplicate_id_are_rejected(self):
        directory = self.root / 'snippets/copy'
        shutil.copytree(self.root / 'snippets/fbm', directory)
        with self.assertRaisesRegex(ValueError, 'ID'):
            library.validate()
        shutil.rmtree(directory)
        (self.root / 'snippets/fbm/snippet.json').unlink()
        with self.assertRaisesRegex(ValueError, 'asset'):
            library.assets('snippet')

from integration_support import ROOT, authoring, library, fixture_root, audit_notices
from holodeck_shader_check.licenses import mit_notices
import copy
import contextlib
import hashlib
import json
import shutil
import unittest

@unittest.skipUnless(ROOT, 'Set HOLODECK_LIBRARY_ROOT for real-library integration tests')
class AuthoringValidationTests(unittest.TestCase):

    def setUp(self):
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        self.root = stack.enter_context(fixture_root('shaders', 'shared', 'snippets', 'references'))

    def write(self, path, value):
        (self.root / path).write_text(json.dumps(value))

    def test_duplicate_id_and_missing_default_are_rejected(self):
        index = authoring.read_json(self.root / 'index.json')
        index['shaders'].append(index['shaders'][0])
        self.write('index.json', index)
        with self.assertRaises(ValueError):
            authoring.entries()
        index['shaders'].pop()
        index['defaultShaderID'] = 'missing'
        self.write('index.json', index)
        with self.assertRaises(ValueError):
            authoring.entries()

    def test_invalid_metadata_and_paths_are_rejected(self):
        original = authoring.read_json(self.root / 'shaders/plasma/metadata.json')
        for key, value in [('colors', [[0, 1]]), ('category', 'UNKNOWN'), ('id', 'other'), ('shared', ['../secret', 'fragment'])]:
            metadata = copy.deepcopy(original)
            metadata[key] = value
            self.write('shaders/plasma/metadata.json', metadata)
            with self.assertRaises(ValueError):
                authoring.entries()

    def test_collections_and_discovery_are_checked(self):
        original = authoring.read_json(self.root / 'index.json')
        for mutation in ('reserved', 'duplicate', 'unpublished', 'empty'):
            index = copy.deepcopy(original)
            collection = index['collections'][0]
            if mutation == 'reserved':
                collection['id'] = 'all'
            if mutation == 'duplicate':
                collection['shaderIDs'].append(collection['shaderIDs'][0])
            if mutation == 'unpublished':
                collection['shaderIDs'].append('unknown')
            if mutation == 'empty':
                collection['shaderIDs'] = []
            self.write('index.json', index)
            with self.assertRaises(ValueError):
                authoring.entries()
        self.write('index.json', original)
        metadata = authoring.read_json(self.root / 'shaders/plasma/metadata.json')
        metadata['discovery']['motion'] = 'instant'
        self.write('shaders/plasma/metadata.json', metadata)
        with self.assertRaises(ValueError):
            authoring.entries()

    def test_source_assembly_preserves_utf8_bytes_and_order(self):
        for shader_id in ('plasma', 'brushed-gold'):
            metadata = authoring.read_json(self.root / f'shaders/{shader_id}/metadata.json')
            components = [self.root / 'shared' / f'{part}.metal' for part in metadata['shared'][:-1]]
            components.extend([self.root / f'shaders/{shader_id}/body.metal', self.root / 'shared/fragment.metal'])
            for newline in ('\n', '\r\n'):
                with self.subTest(shader_id=shader_id, newline=newline):
                    expected = b''
                    for index, path in enumerate(components):
                        data = f'// café 雪 component {index}{newline}'.encode('utf-8')
                        path.write_bytes(data)
                        expected += data
                    _, source = authoring.shader_entry(shader_id)
                    self.assertEqual(source.encode('utf-8'), expected)
                    self.assertEqual(authoring.digest(source.encode('utf-8')), hashlib.sha256(expected).hexdigest())

    def test_invalid_utf8_and_empty_assembled_sources_are_rejected(self):
        components = [self.root / 'shared/common.metal', self.root / 'shaders/plasma/body.metal', self.root / 'shared/fragment.metal']
        for path in components:
            with self.subTest(path=path):
                original = path.read_bytes()
                try:
                    path.write_bytes(b'\xff')
                    with self.assertRaisesRegex(ValueError, 'Invalid UTF-8 source') as error:
                        authoring.shader_entry('plasma')
                    self.assertIn(str(path), str(error.exception))
                    self.assertIsInstance(error.exception.__cause__, UnicodeDecodeError)
                finally:
                    path.write_bytes(original)
        for path in components:
            path.write_bytes(b'')
        with self.assertRaisesRegex(ValueError, 'Empty source'):
            authoring.shader_entry('plasma')

    def test_source_larger_than_one_mib_is_accepted(self):
        path = self.root / 'shaders/plasma/body.metal'
        path.write_bytes(path.read_bytes() + b'\n//' + b'x' * 1048576 + b'\n')
        _, entries = authoring.entries()
        source = next((source for metadata, source in entries if metadata['id'] == 'plasma'))
        self.assertGreater(len(source.encode('utf-8')), 1048576)

    def test_aggregate_sources_larger_than_sixteen_mib_are_accepted(self):
        ids = []
        for index in range(17):
            shader_id = f'large-{index}'
            directory = self.root / 'shaders' / shader_id
            shutil.copytree(self.root / 'shaders/plasma', directory)
            metadata = authoring.read_json(directory / 'metadata.json')
            metadata['id'] = shader_id
            authoring.write_json(directory / 'metadata.json', metadata)
            body = directory / 'body.metal'
            body.write_bytes(body.read_bytes() + b'\n//' + b'x' * 1000000 + b'\n')
            ids.append(shader_id)
        self.write('index.json', {'shaders': ids, 'defaultShaderID': ids[0]})
        _, entries = authoring.entries()
        sizes = [len(source.encode('utf-8')) for _, source in entries]
        self.assertTrue(all((size < 1048576 for size in sizes)))
        self.assertGreater(sum(sizes), 16777216)

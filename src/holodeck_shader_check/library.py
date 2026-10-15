"""Library inspection, byte-preserving assembly and SDK compilation."""
import math
import pathlib
import re
import shutil
import subprocess
import tempfile
from .authoring import Authoring
from . import shader_tool
from .errors import ToolingError

KINDS = {"shader": ("shaders", "metadata.json"), "snippet": ("snippets", "snippet.json"), "reference": ("references", "reference.json")}


class Library(Authoring):
    def __init__(self, root, shader_tool_path=None):
        super().__init__(root)
        self.shader_tool_path = shader_tool_path

    @staticmethod
    def reject_reference_symlink(value, label):
        if value.is_symlink():
            raise ValueError(f"Reference symlink forbidden: {label}")

    def gpu_command(self, *arguments):
        return shader_tool.command(*arguments, executable=self.shader_tool_path)

    gpu_run = staticmethod(shader_tool.run)

    def assemble(self, kind, asset_id):
        if kind == "shader":
            return self.shader_entry(asset_id)[1]
        if kind == "snippet":
            return self.snippet_source(self.find(kind, asset_id))
        raise ValueError("Only shaders and snippets can be assembled")

    def path(self, relative):
        if not isinstance(relative, str) or pathlib.PurePosixPath(relative).is_absolute() or '..' in pathlib.PurePosixPath(relative).parts:
            raise ValueError(f'Invalid asset path: {relative}')
        value = self.root / relative
        if not value.resolve().is_relative_to(self.root.resolve()) or not value.is_file():
            raise ValueError(f'Missing or external asset: {relative}')
        return value

    def assets(self, kind=None, *, shader_metadata=None):
        result = []
        for asset_kind, (folder, filename) in KINDS.items():
            if kind and asset_kind != kind:
                continue
            for directory in sorted((self.root / folder).iterdir()):
                if asset_kind == 'reference' and (not directory.name.startswith('.')):
                    self.reject_reference_symlink(directory, directory.name)
                if not directory.is_dir() or directory.name.startswith('.'):
                    continue
                if asset_kind == 'reference':
                    self.reject_reference_symlink(directory / filename, directory.name + '/' + filename)
                file = self.path(f'{folder}/{directory.name}/{filename}')
                spec = shader_metadata.get(directory.name) if asset_kind == 'shader' and shader_metadata is not None else None
                if spec is None:
                    spec = self.read_json(file)
                if not isinstance(spec, dict) or not self.valid_id(spec.get('id')) or spec['id'] != file.parent.name:
                    raise ValueError(f'Invalid or duplicate asset ID: {file}')
                result.append({'kind': asset_kind, 'id': spec['id'], 'name': spec.get('name', ''), 'summary': spec.get('summary', spec.get('description', '')), 'path': str(file.parent.relative_to(self.root)), 'specification': spec})
        return sorted(result, key=lambda item: (item['id'], item['kind']))

    def find(self, kind, asset_id):
        if kind not in KINDS or not self.valid_id(asset_id):
            raise ValueError('Invalid kind or ID')
        for item in self.assets(kind):
            if item['id'] == asset_id:
                return item
        raise ValueError(f'Unknown {kind}: {asset_id}')

    def validate_snippet(self, item):
        spec = item['specification']
        if spec.get('schemaVersion') != 1 or spec.get('kind') not in ('shared-helper', 'recipe'):
            raise ValueError(f"Invalid snippet kind/schema: {item['id']}")
        for field in ('name', 'summary'):
            if not self.text_field(spec.get(field)):
                raise ValueError(f"Missing snippet {field}: {item['id']}")
        for field in ('tags', 'searchTerms'):
            if not self.string_list(spec.get(field)):
                raise ValueError(f"Invalid snippet {field}: {item['id']}")
        shared = spec.get('requiredShared')
        if shared not in (['common'], ['common', 'material']):
            raise ValueError(f"Invalid snippet helpers: {item['id']}")
        source = spec.get('source')
        if not isinstance(source, dict) or not self.string_list(source.get('symbols')) or (not source['symbols']):
            raise ValueError(f"Missing snippet symbols: {item['id']}")
        allowed = [f'shared/{part}.metal' for part in shared] if spec['kind'] == 'shared-helper' else [item['path'] + '/code.metal']
        if source.get('path') not in allowed:
            raise ValueError(f"Invalid canonical source: {item['id']}")
        code = self.read_source(self.path(source['path']))
        if any((symbol not in code for symbol in source['symbols'])):
            raise ValueError(f"Missing declared symbol: {item['id']}")
        if spec.get('examplePath') != item['path'] + '/example.metal':
            raise ValueError(f"Invalid example path: {item['id']}")
        self.path(spec['examplePath'])
        docs = self.path(item['path'] + '/README.md').read_text()
        for heading in ('Interface', 'Coordinates', 'Tuning', 'Limitations', 'Cost'):
            if heading not in docs:
                raise ValueError(f"Missing {heading} contract: {item['id']}")
        origin = spec.get('origin')
        if not isinstance(origin, dict) or not self.string_list(origin.get('shaderIDs')) or any((not self.text_field(origin.get(field)) for field in ('attribution', 'license'))):
            raise ValueError(f"Invalid snippet origin: {item['id']}")
        for shader_id in origin['shaderIDs']:
            if not self.valid_id(shader_id):
                raise ValueError('Invalid origin shader ID')
            self.path(f'shaders/{shader_id}/body.metal')

    def snippet_source(self, item):
        self.validate_snippet(item)
        spec = item['specification']
        parts = [self.read_source(self.path(f'shared/{part}.metal')) for part in spec['requiredShared']]
        if spec['kind'] == 'recipe':
            parts.append(self.read_source(self.path(spec['source']['path'])))
        parts.extend([self.read_source(self.path(spec['examplePath'])), self.read_source(self.path('shared/fragment.metal'))])
        return '\n'.join(parts)

    def validate_reference_specification(self, item):
        spec = item['specification']
        if not isinstance(spec, dict):
            raise ValueError(f"Invalid reference: {item['id']}")
        if not self.valid_id(item['id']) or spec.get('id') != item['id']:
            raise ValueError(f"Invalid reference id: {item['id']}")
        if spec.get('schemaVersion') != 1:
            raise ValueError(f"Invalid reference schemaVersion: {item['id']}")
        for field in ('name', 'summary', 'language', 'adaptationNotes'):
            if not self.text_field(spec.get(field)):
                raise ValueError(f"Invalid reference {field}: {item['id']}")
        for field in ('tags', 'searchTerms', 'requirements'):
            if not self.string_list(spec.get(field)):
                raise ValueError(f"Invalid reference {field}: {item['id']}")
        if not isinstance(spec.get('origin'), dict):
            raise ValueError(f"Invalid reference origin: {item['id']}")
        for field in ('author', 'license'):
            if not self.text_field(spec['origin'].get(field)):
                raise ValueError(f"Invalid reference origin.{field}: {item['id']}")
        if spec['origin'].get('url') is not None and (not isinstance(spec['origin']['url'], str)):
            raise ValueError(f"Invalid reference origin.url: {item['id']}")
        if 'notes' in spec and (not isinstance(spec['notes'], str) or not spec['notes'].strip()):
            raise ValueError(f"Invalid reference notes: {item['id']}")
        files = spec.get('files')
        if not isinstance(files, dict):
            raise ValueError(f"Invalid reference files: {item['id']}")
        for filename, sha in files.items():
            if not isinstance(filename, str) or not pathlib.PurePosixPath(filename).parts or pathlib.PurePosixPath(filename).is_absolute() or ('..' in pathlib.PurePosixPath(filename).parts):
                raise ValueError(f"Invalid reference files path: {item['id']}/{filename}")
            if not isinstance(sha, str) or re.fullmatch('[0-9a-f]{64}', sha) is None:
                raise ValueError(f"Invalid reference files checksum: {item['id']}/{filename}")

    def validate_reference(self, item):
        self.validate_reference_specification(item)
        spec = item['specification']
        files = spec['files']
        directory = self.root / item['path']
        self.reject_reference_symlink(directory, item['id'])
        for entry in directory.rglob('*'):
            self.reject_reference_symlink(entry, item['id'] + '/' + str(entry.relative_to(directory)))
        if (directory / 'README.md').exists():
            raise ValueError(f"Obsolete reference README.md: {item['id']}; move guidance into reference.json notes")
        original = self.root / item['path'] / 'original'
        original_paths = list(original.rglob('*')) if original.exists() else []
        actual = {str(p.relative_to(original)) for p in original_paths if p.is_file()}
        if actual != set(files):
            raise ValueError(f"Reference file inventory changed: {item['id']}")
        for filename, sha in files.items():
            if self.digest(self.path(item['path'] + '/original/' + filename).read_bytes()) != sha:
                raise ValueError(f"Reference checksum mismatch: {item['id']}/{filename}")

    def compile_source(self, source, label):
        if shutil.which('xcrun') is None:
            raise ToolingError('SDK compilation requires Xcode and xcrun')
        for sdk in ('macosx', 'appletvos'):
            check = subprocess.run(['xcrun', '-sdk', sdk, '--find', 'metal'], capture_output=True)
            if check.returncode:
                raise ToolingError(f'Metal compiler unavailable for {sdk}')
        with tempfile.TemporaryDirectory() as temp:
            file = pathlib.Path(temp) / 'example.metal'
            file.write_bytes(source.encode('utf-8'))
            for sdk in ('macosx', 'appletvos'):
                subprocess.run(['xcrun', '-sdk', sdk, 'metal', '-c', str(file), '-o', str(pathlib.Path(temp) / f'{sdk}.air')], check=True)


    def validate(self, compile_metal=False):
        _, shaders = self.entries()
        shader_metadata = {entry[0]['id']: entry[0] for entry in shaders}
        shader_sources = {entry[0]['id']: entry[1] for entry in shaders}
        items = self.assets(shader_metadata=shader_metadata)
        for item in items:
            if item['kind'] == 'shader':
                source = self.shader_entry(item['id'], metadata=item['specification'])[1] if item['id'] not in shader_metadata else shader_sources[item['id']]
                if compile_metal:
                    self.compile_source(source, item['id'])
            elif item['kind'] == 'snippet':
                source = self.snippet_source(item)
                if compile_metal:
                    self.compile_source(source, item['id'])
            else:
                self.validate_reference(item)
        return items

    def render(self, kind, asset_id, times, width=1280, height=720, output_dir=None):
        if kind not in ('shader', 'snippet'):
            raise ValueError('Only Metal shaders and snippet examples can be rendered')
        samples = [float(t) for t in times.split(',')]
        if not samples or any((not math.isfinite(t) or t < 0 for t in samples)) or (not 1 <= width <= 8192) or (not 1 <= height <= 8192) or (width * height > 16777216):
            raise ValueError('Invalid render settings')
        source = self.shader_entry(asset_id)[1] if kind == 'shader' else self.snippet_source(self.find(kind, asset_id))
        renderer = self.gpu_command('render')
        output = pathlib.Path(output_dir) if output_dir else self.root / '.cache/library' / f'{kind}-{asset_id}'
        output.mkdir(parents=True, exist_ok=True)
        file = output / 'source.metal'
        file.write_bytes(source.encode('utf-8'))
        for sample in samples:
            stamp = repr(sample).removesuffix('.0')
            image = output / f'{width}x{height}-t{stamp}.png'
            shader_tool.run([*renderer, str(file), str(image), '--width', str(width), '--height', str(height), '--time', str(sample)], operation='render', label=f'{kind} {asset_id} at {sample} seconds')
            print(image)
        return output

    def configuration(self):
        from .configuration import configuration
        return configuration(self)

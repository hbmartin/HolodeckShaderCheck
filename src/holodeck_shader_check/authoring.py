"""Explicit-root authoring contracts, extracted from HolodeckShaders tools/authoring.py."""
import hashlib
import json
import pathlib
import re

MOODS = {"calm", "dreamy", "energetic"}
MOTIONS = {"slow", "steady", "fast"}


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


class Authoring:
    def __init__(self, root):
        self.root = pathlib.Path(root).resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError("Library root must be a directory")

    def valid_id(self, value):
        return isinstance(value, str) and len(value) <= 100 and (re.fullmatch('[a-z0-9]+(?:-[a-z0-9]+)*', value) is not None)

    def text_field(self, value, limit=2000):
        return isinstance(value, str) and bool(value.strip()) and (len(value) <= limit)

    def string_list(self, value, limit=32):
        return isinstance(value, list) and len(value) <= limit and all((self.text_field(v, 100) for v in value)) and (len(value) == len(set(value)))

    def validate_metadata(self, metadata, shader_id):
        if not isinstance(metadata, dict) or not self.valid_id(shader_id) or metadata.get('id') != shader_id or (metadata.get('category') not in ('PROCEDURAL', '3D MATERIAL')):
            raise ValueError(f'Invalid metadata for {shader_id}')
        if not self.text_field(metadata.get('name'), 200) or not self.text_field(metadata.get('description')):
            raise ValueError(f'Missing name/description for {shader_id}')
        colors = metadata.get('colors')
        if not isinstance(colors, list) or len(colors) != 2 or any((not isinstance(c, list) or len(c) != 3 or any((type(v) not in (int, float) or not 0 <= v <= 1 for v in c)) for c in colors)):
            raise ValueError(f'Invalid colors for {shader_id}')
        if metadata.get('shared') not in (['common', 'fragment'], ['common', 'material', 'fragment']):
            raise ValueError('Shared files must declare common, optional material, then fragment')
        if 'discovery' in metadata:
            discovery = metadata['discovery']
            if not isinstance(discovery, dict) or not self.string_list(discovery.get('tags')) or (not self.string_list(discovery.get('moods'), 8)) or (not set(discovery['moods']) <= MOODS) or (discovery.get('motion') not in MOTIONS):
                raise ValueError(f'Invalid discovery for {shader_id}')
        if 'reuse' in metadata:
            reuse = metadata['reuse']
            if not isinstance(reuse, dict) or set(reuse) != {'snippets', 'references'}:
                raise ValueError(f'Invalid reuse for {shader_id}')
            for kind, filename in (('snippets', 'snippet.json'), ('references', 'reference.json')):
                ids = reuse[kind]
                if not self.string_list(ids) or any((not self.valid_id(i) or not (self.root / kind / i / filename).is_file() for i in ids)):
                    raise ValueError(f'Broken {kind} reuse link for {shader_id}')

    def validate_collections(self, collections, shader_ids):
        if not isinstance(collections, list) or len(collections) > 100:
            raise ValueError('Invalid collections')
        seen = set()
        for collection in collections:
            if not isinstance(collection, dict):
                raise ValueError('Invalid collection')
            cid = collection.get('id')
            members = collection.get('shaderIDs')
            if not self.valid_id(cid) or cid == 'all' or cid in seen or (not self.text_field(collection.get('name'), 200)) or (not self.text_field(collection.get('description'))) or (not self.string_list(members, 500)) or (not members) or (not set(members) <= set(shader_ids)):
                raise ValueError(f'Invalid collection: {cid}')
            seen.add(cid)

    def shader_entry(self, shader_id, *, metadata=None):
        if not self.valid_id(shader_id):
            raise ValueError('Invalid shader ID')
        directory = self.root / 'shaders' / shader_id
        if metadata is None:
            metadata = self.read_json(directory / 'metadata.json')
        self.validate_metadata(metadata, shader_id)
        shared = metadata['shared']
        components = [self.root / 'shared' / f'{part}.metal' for part in shared[:-1]]
        components.extend([directory / 'body.metal', self.root / 'shared' / 'fragment.metal'])
        source = ''.join((self.read_source(component) for component in components))
        if not source:
            raise ValueError(f'Empty source for {shader_id}')
        return (metadata, source)

    def digest(self, data):
        return hashlib.sha256(data).hexdigest()

    def read_source(self, path):
        try:
            return path.read_bytes().decode('utf-8')
        except UnicodeDecodeError as error:
            raise ValueError(f'Invalid UTF-8 source {path}: {error}') from error

    def read_json(self, path):
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)

    def entries(self):
        index = self.read_json(self.root / 'index.json')
        ids = index['shaders']
        if not isinstance(ids, list) or not 1 <= len(ids) <= 500 or any((not self.valid_id(i) for i in ids)) or (len(ids) != len(set(ids))) or (index['defaultShaderID'] not in ids):
            raise ValueError('Catalog must be nonempty with unique IDs and an existing default')
        if 'collections' in index:
            self.validate_collections(index['collections'], ids)
        result = [self.shader_entry(shader_id) for shader_id in ids]
        return (index, result)

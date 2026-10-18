"""Validate template instances against one bounded, immutable base-input fixture."""
import copy
import json
import shutil
import tempfile
from pathlib import Path
from .errors import ToolingError
from .inputs import InputReader


PLACEHOLDERS = ('__ID__', '__NAME__')
TEXT_INPUTS = {
    'shader': {'metadata.json', 'body.metal', 'README.md'},
    'snippet': {'snippet.json', 'code.metal', 'example.metal', 'README.md'},
    'reference': {'reference.json'},
}


def fixture_inputs(items, configuration):
    paths = {'index.json', 'authoring.json', *configuration['sourceRequirements']}
    paths.update(value['noticePath'] for value in configuration['upstreamRequirements'].values() if 'noticePath' in value)
    for item in items:
        folder, spec = item['path'], item['specification']
        if item['kind'] == 'shader': paths.add(folder + '/metadata.json')
        elif item['kind'] == 'snippet': paths.update((folder + '/snippet.json', folder + '/README.md'))
        else:
            paths.add(folder + '/reference.json')
            paths.update(folder + '/original/' + name for name in spec['files'])
    return paths


def validate_templates(library, compile_metal=False, *, items=None, configuration=None):
    from .library import KINDS, Library
    from .licenses import check_coverage, require_notices, source_inventory

    with InputReader(library.root) as reader:
        kinds = reader.template_kinds(KINDS)
        if not kinds: return
        items = library.assets() if items is None else items
        configuration = library.configuration() if configuration is None else configuration
        with tempfile.TemporaryDirectory(prefix='holodeck-template-') as directory:
            root = Path(directory)
            for folder, _ in KINDS.values(): (root / folder).mkdir()
            try:
                for name in sorted(fixture_inputs(items, configuration)):
                    data = reader.read(name)
                    destination = root / name
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(data)
            except (ValueError, OSError) as error:
                raise ValueError(f'Cannot construct template fixture from library inputs: {error}') from error
            for kind in kinds:
                folder, filename = KINDS[kind]
                asset_id = 'template-check-' + kind
                while (root / folder / asset_id).exists(): asset_id += '-x'
                destination = root / folder / asset_id
                try:
                    for relative, data in reader.template_files(kind):
                        if any(token in relative for token in PLACEHOLDERS):
                            raise ValueError(f'Unresolved placeholder in path: {relative}')
                        if relative in TEXT_INPUTS[kind]:
                            text = data.decode('utf-8').replace('__ID__', asset_id).replace('__NAME__', 'Template Check')
                            if any(token in text for token in PLACEHOLDERS):
                                raise ValueError(f'Unresolved placeholder in {relative}')
                            data = text.encode('utf-8')
                        output = destination / relative
                        output.parent.mkdir(parents=True, exist_ok=True); output.write_bytes(data)
                    candidate = Library(root, library.shader_tool_path)
                    candidate._checked_compilers = library._checked_compilers
                    spec = candidate.read_json(destination / filename)
                    item = {'kind': kind, 'id': asset_id, 'path': f'{folder}/{asset_id}', 'specification': spec}
                    source = candidate.validate_item(item, compile_metal, label='template ' + kind)
                    current = copy.deepcopy(configuration)
                    new_sources = []
                    if kind == 'shader': new_sources = [item['path'] + '/body.metal']
                    elif kind == 'snippet':
                        new_sources = [spec['examplePath']]
                        if spec['kind'] == 'recipe': new_sources.append(spec['source']['path'])
                    for name in new_sources: current['sourceRequirements'][name] = []
                    records, upstreams = current['sourceRequirements'], current['upstreamRequirements']
                    check_coverage(candidate, items + [item], records, upstreams)
                    sources = source_inventory([item])
                    for name in sources:
                        require_notices(candidate, name, candidate.path(name).read_text(encoding='utf-8'), records[name], upstreams)
                    if source is not None:
                        required = [ref for name in sources for ref in records[name]]
                        require_notices(candidate, 'template ' + kind, source, required, upstreams)
                except ToolingError:
                    raise
                except (ValueError, KeyError, TypeError, OSError) as error:
                    raise ValueError(f'Invalid {kind} template: {error}') from error
                finally:
                    if destination.exists(): shutil.rmtree(destination)

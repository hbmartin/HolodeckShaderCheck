"""Independent-source and assembled-source notice coverage."""
import re


def normalized(text):
    return ' '.join(re.sub('^\\s*// ?', '', text, flags=re.MULTILINE).split())

def mit_notices(text):
    """Retain every complete block, including multiple copyrights within a block."""
    text = normalized(text)
    matches = list(re.finditer('\\bCopyright\\b.*?\\bSOFTWARE\\.', text))
    if not matches:
        raise ValueError('No complete MIT notice')
    for match in matches:
        if 'Permission is hereby granted' not in match.group() or 'THE SOFTWARE IS PROVIDED' not in match.group():
            raise ValueError('Incomplete MIT notice')
    remaining = re.sub('\\bCopyright\\b.*?\\bSOFTWARE\\.', '', text)
    if re.search('\\bCopyright\\b', remaining):
        raise ValueError('Incomplete trailing MIT notice')
    return [match.group() for match in matches]

def license_kinds(label):
    kinds = set()
    if re.search('\\bMIT\\b', label):
        kinds.add('MIT')
    if 'CC BY-NC-SA' in label:
        kinds.add('CC BY-NC-SA 3.0')
    if re.search('CC BY(?!-NC-SA)\\b', label):
        kinds.add('CC BY 3.0')
    return kinds

def source_inventory(items):
    paths = set()
    for item in items:
        spec = item['specification']
        if item['kind'] == 'shader':
            paths.add(item['path'] + '/body.metal')
            paths.update((f'shared/{part}.metal' for part in spec['shared']))
        elif item['kind'] == 'snippet':
            paths.update((spec['source']['path'], spec['examplePath']))
            paths.update((f'shared/{part}.metal' for part in spec['requiredShared']))
    return paths

def check_coverage(library, items, records, upstreams):
    inventory = source_inventory(items)
    missing, stale = (inventory - records.keys(), records.keys() - inventory)
    if missing:
        raise ValueError(f'Unregistered sources: {sorted(missing)}')
    if stale:
        raise ValueError(f'Stale source records: {sorted(stale)}')
    references = {item['id']: item for item in items if item['kind'] == 'reference'}
    for reference, requirement in upstreams.items():
        if reference not in references:
            raise ValueError(f'Stale upstream record: {reference}')
        declared = license_kinds(references[reference]['specification']['origin']['license'])
        if requirement['license'] not in declared:
            raise ValueError(f'Upstream license disagrees with reference: {reference}')
    for source, required in records.items():
        if len(required) != len(set(required)):
            raise ValueError(f'Duplicate source requirement: {source}')
        for reference in required:
            if reference not in upstreams:
                raise ValueError(f'Unregistered upstream {reference} in {source}')
    for item in items:
        spec = item['specification']
        if item['kind'] == 'reference':
            if license_kinds(spec['origin']['license']) and item['id'] not in upstreams:
                raise ValueError(f"Unregistered licensed reference: {item['id']}")
        elif item['kind'] == 'snippet':
            expected = license_kinds(spec['origin']['license'])
            registered = {upstreams[ref]['license'] for ref in records[spec['source']['path']]}
            if not expected <= registered:
                raise ValueError(f"Unregistered declared snippet licenses: {item['id']}")

def require_notices(library, label, text, references, upstreams):
    text = normalized(text)
    for reference in sorted(set(references)):
        requirement = upstreams[reference]
        if requirement['license'] == 'MIT':
            notices = mit_notices(library.path(requirement['noticePath']).read_text())
            if any((notice not in text for notice in notices)):
                raise ValueError(f'Missing {reference} MIT notice in {label}')
        elif requirement['uri'] not in text:
            raise ValueError(f'Missing {reference} CC URI in {label}')

def audit_notices(library, records=None, upstreams=None, *, items=None, shaders=None):
    records = library.configuration()['sourceRequirements'] if records is None else records
    upstreams = library.configuration()['upstreamRequirements'] if upstreams is None else upstreams
    items = library.assets() if items is None else items
    check_coverage(library, items, records, upstreams)
    for source, references in records.items():
        require_notices(library, source, library.path(source).read_text(), references, upstreams)
    for item in items:
        if item['kind'] != 'snippet':
            continue
        spec = item['specification']
        paths = [spec['source']['path'], spec['examplePath']]
        paths.extend((f'shared/{part}.metal' for part in spec['requiredShared']))
        references = [ref for path in paths for ref in records[path]]
        require_notices(library, 'example ' + item['id'], library.snippet_source(item), references, upstreams)
    for metadata, source, *_ in (library.entries()[1] if shaders is None else shaders):
        paths = [f"shaders/{metadata['id']}/body.metal"]
        paths.extend((f'shared/{part}.metal' for part in metadata['shared']))
        references = [ref for path in paths for ref in records[path]]
        require_notices(library, 'assembled ' + metadata['id'], source, references, upstreams)

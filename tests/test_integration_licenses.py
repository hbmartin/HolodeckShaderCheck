from integration_support import ROOT, authoring, library, fixture_root, audit_notices
from holodeck_shader_check.licenses import mit_notices
SOURCE_REQUIREMENTS = authoring.configuration()["sourceRequirements"] if ROOT else {}
UPSTREAM_REQUIREMENTS = authoring.configuration()["upstreamRequirements"] if ROOT else {}
"""Test-only license coverage for independent sources and their delivery assemblies."""
import copy
import re
import shutil
import unittest

def license_fixture_root():
    return fixture_root('shaders', 'shared', 'snippets', 'references')

def remove_mit_notice(path, holder):
    pattern = '// Copyright \\(c\\) ' + re.escape(holder) + '.*?// (?:THE )?SOFTWARE\\.\\n'
    text, count = re.subn(pattern, '', path.read_text(), count=1, flags=re.DOTALL)
    if count != 1:
        raise AssertionError(f'Fixture did not remove the expected notice: {holder}')
    path.write_text(text)

@unittest.skipUnless(ROOT, 'Set HOLODECK_LIBRARY_ROOT for real-library integration tests')
class LicenseDeliveryTests(unittest.TestCase):

    def test_complete_coverage_and_delivery(self):
        audit_notices()

    def test_missing_and_stale_source_records_are_rejected(self):
        for missing in (True, False):
            with self.subTest(missing=missing):
                records = copy.deepcopy(SOURCE_REQUIREMENTS)
                if missing:
                    del records['snippets/value-noise/example.metal']
                else:
                    records['snippets/obsolete/code.metal'] = []
                with self.assertRaisesRegex(ValueError, 'Unregistered sources' if missing else 'Stale source records'):
                    audit_notices(records)

    def test_unregistered_licensed_references_are_rejected(self):
        for original in ('inferno', 'sparks-from-fire'):
            with self.subTest(original=original), license_fixture_root() as root:
                destination = root / 'references/unregistered'
                shutil.copytree(root / 'references' / original, destination)
                spec = authoring.read_json(destination / 'reference.json')
                spec['id'] = 'unregistered'
                authoring.write_json(destination / 'reference.json', spec)
                with self.assertRaisesRegex(ValueError, 'Unregistered licensed reference'):
                    audit_notices()

    def test_declared_mit_cc_and_mixed_snippets_cannot_have_empty_requirements(self):
        for original in ('color-ramps', 'kali-starfield', 'rune-glyph'):
            with self.subTest(original=original), license_fixture_root() as root:
                destination = root / 'snippets/unregistered'
                shutil.copytree(root / 'snippets' / original, destination)
                spec = authoring.read_json(destination / 'snippet.json')
                spec.update(id='unregistered', examplePath='snippets/unregistered/example.metal')
                spec['source']['path'] = 'snippets/unregistered/code.metal'
                authoring.write_json(destination / 'snippet.json', spec)
                records = dict(SOURCE_REQUIREMENTS, **{'snippets/unregistered/code.metal': [], 'snippets/unregistered/example.metal': []})
                with self.assertRaisesRegex(ValueError, 'Unregistered declared snippet licenses'):
                    audit_notices(records)

    def test_secondary_notice_is_required_independently(self):
        with license_fixture_root() as root:
            remove_mit_notice(root / 'snippets/dot-curtain/code.metal', '2017-present gl-transitions contributors')
            with self.assertRaisesRegex(ValueError, 'Missing gl-transitions-polka MIT notice'):
                audit_notices()

    def test_shared_notice_is_required(self):
        with license_fixture_root() as root:
            remove_mit_notice(root / 'shared/common.metal', '2014 David Hoskins')
            with self.assertRaisesRegex(ValueError, 'Missing hoskins-hash MIT notice in shared/common'):
                audit_notices()

    def test_assembled_common_notice_cannot_hide_missing_body_notice(self):
        with license_fixture_root() as root:
            remove_mit_notice(root / 'shaders/matrix-corridor/body.metal', '2014 David Hoskins')
            with self.assertRaisesRegex(ValueError, 'Missing hoskins-hash MIT notice in shaders/matrix'):
                audit_notices()

    def test_missing_cc_uri_is_rejected(self):
        with license_fixture_root() as root:
            path = root / 'snippets/rune-glyph/code.metal'
            path.write_text(path.read_text().replace(UPSTREAM_REQUIREMENTS['inside-the-matrix']['uri'], ''))
            with self.assertRaisesRegex(ValueError, 'Missing inside-the-matrix CC URI'):
                audit_notices()

    def test_every_mit_block_is_required_and_incomplete_blocks_are_rejected(self):
        with license_fixture_root() as root:
            path = root / 'references/inferno/original/LICENSE'
            first = mit_notices(path.read_text())[0]
            second = first.replace('2023 Paul Hudson and other authors.', '2001 Fixture Author.')
            self.assertEqual(len(mit_notices(first + '\n' + second)), 2)
            path.write_text(first + '\n' + second)
            with self.assertRaisesRegex(ValueError, 'Missing inferno MIT notice'):
                audit_notices()
            with self.assertRaisesRegex(ValueError, 'Incomplete trailing MIT notice'):
                mit_notices(first + '\nCopyright 2001 incomplete')

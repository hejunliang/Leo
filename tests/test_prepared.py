import io
import json
import os
from pathlib import Path
import struct
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
import ci
from archive_input import extract
from source import prepared
from source.compatibility import verify_generation
from source.configure import configure
from fixtures import git, package, source


class PreparedTests(unittest.TestCase):
    def test_existing_package_uses_selected_native_sources_and_fresh_build_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            upstream, directory, info = package(root)
            (upstream / 'dm/runtime.c').write_text('updated runtime\n')
            git(upstream, 'add', '.')
            git(upstream, 'commit', '-m', 'runtime update')
            with patch.dict(os.environ, GITHUB_RUN_NUMBER='7', GITHUB_RUN_ATTEMPT='2', SOURCE_REPOSITORY='owner/fork', INPUT_REVISION='f' * 64):
                config = configure(root)
                with patch.object(ci, 'ROOT', root), patch.object(ci, 'CONFIG', config):
                    prepared.prepare(root)
            report = json.loads((root / 'outputs/source-input-report.json').read_text())
            self.assertEqual(report['source_commit'], git(upstream, 'rev-parse', 'HEAD'))
            self.assertEqual(report['prepared_from_source'], info['producer']['source_commit'])
            self.assertEqual(json.loads((root / 'outputs/build-context.json').read_text())['build_number'], '7.2')
            with tarfile.open(root / 'outputs/native-inputs.tar.gz') as archive:
                self.assertEqual(archive.extractfile('upstream/dm/runtime.c').read(), b'updated runtime\n')

    def test_generation_changes_require_updated_input(self):
        for name in ('dm/tools/helper.py', 'dm/gfx/tools/kernel.py', 'dm/data/new-table.tsv'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                upstream, commit, _, _ = source(Path(temporary))
                path = upstream / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('changed_generation = True\n')
                with self.assertRaisesRegex(ValueError, 'updated prepared package'):
                    verify_generation(upstream, commit)

    def test_modified_payload_or_wrong_program_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            upstream, directory, info = package(root)
            with patch.object(ci, 'ROOT', root):
                with self.assertRaisesRegex(ValueError, 'game programs'):
                    prepared.verify_source(directory, info, upstream, {'nso_sha256': {'main': '0' * 64}})
            (directory / 'source-payload.zip').write_bytes(b'corrupt')
            with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                prepared.verify_directory(directory)

    def test_zip_and_tar_paths_and_links_are_checked_before_extraction(self):
        for kind, name, link in (('zip', '../escape', False), ('zip', 'link', True),
                                 ('tar', '/escape', False), ('tar', 'link', True)):
            with self.subTest(kind=kind, name=name, link=link), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                path = root / 'input'
                if kind == 'zip':
                    with zipfile.ZipFile(path, 'w') as z:
                        item = zipfile.ZipInfo(name)
                        if link:
                            item.external_attr = 0o120777 << 16
                        z.writestr(item, b'data')
                else:
                    with tarfile.open(path, 'w') as tf:
                        item = tarfile.TarInfo(name)
                        if link:
                            item.type = tarfile.SYMTYPE
                            item.linkname = '../escape'
                        else:
                            item.size = 4
                        tf.addfile(item, None if link else io.BytesIO(b'data'))
                before = path.read_bytes()
                with self.assertRaises(ValueError):
                    extract(path, root / 'expanded')
                self.assertEqual(path.read_bytes(), before)

    def test_download_cache_reuses_verified_bytes_and_recovers_from_corruption(self):
        content = b'prepared archive'
        def fetch(url, destination, *args, **kwargs):
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
        with tempfile.TemporaryDirectory() as temporary, patch.object(prepared, 'settings', return_value=('https://storage.invalid/input.zip', None)), patch.object(prepared, 'download_file', side_effect=fetch) as download:
            root = Path(temporary)
            prepared.download(root)
            prepared.download(root)
            self.assertEqual(download.call_count, 1)
            (root / '.prepared-downloads/prepared-input.archive').write_bytes(b'corrupt')
            prepared.download(root)
            self.assertEqual(download.call_count, 2)

    def test_macho_platform_and_load_commands(self):
        def binary(platform=2):
            return struct.pack('<8I6I', 0xfeedfacf, 0x0100000c, 0, 2, 1, 24, 0, 0, 0x32, 24, platform, 18 << 16, 18 << 16, 0)
        self.assertEqual(ci.macho_minimum_os(binary()), (18, 0, 0))
        for data in (binary(7), binary()[:40], bytes(56)):
            with self.assertRaises(ValueError):
                ci.macho_minimum_os(data)

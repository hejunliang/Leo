"""Reuse existing game programs and Metal libraries, adding original runtime-generation inputs."""
import json
from pathlib import Path
import shutil
import zipfile

import ci
from source.compatibility import verify_generation
from source.prepared import verify_directory, write
from source.romfs_input import create
from source.runtime_assets import BASE, heavy_member


def upgrade(directory, original, legacy_index, upstream):
    directory, upstream = Path(directory), Path(upstream)
    info = verify_directory(directory)
    verify_generation(upstream, info['producer']['source_commit'], heavy=True)
    legacy = directory / 'legacy'
    legacy.mkdir(exist_ok=True)
    with zipfile.ZipFile(directory / 'source-payload.zip') as source:
        (legacy / 'pack.json').write_bytes(source.read(BASE + 'romfs_overlay_astc/pack.json'))
        shutil.copy2(legacy_index, legacy / 'pack_index.tsv')
        if (legacy / 'pack_index.tsv').read_text().splitlines()[0].split('\t')[:2] != ['# pack', info['payload']['legacy_pack']['name']]:
            raise ValueError('Legacy index does not match the existing prepared package')
        temporary = directory / 'heavy-payload.zip'
        with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as output:
            names = {f'msl/b-{info["payload"]["shader_chain"]}/', 'Frameworks/Guest.framework/'}
            for item in source.infolist():
                if not item.is_dir() and heavy_member(item.filename):
                    output.writestr(item, source.read(item))
                    if item.filename.startswith(BASE + 'dm/'):
                        names.add(item.filename.removeprefix(BASE))
            output.writestr(BASE + 'dm/shell_info.txt', source.read(BASE + 'dm/shell_info.txt'))
            output.writestr(BASE + 'dm/shell_manifest.txt', '\n'.join(sorted(names)) + '\n')
    temporary.replace(directory / 'source-payload.zip')
    size = create(original, directory / 'romfs-inputs.zip')
    (directory / 'generated/ios/Sources/dm_touch_native_art.c').unlink()
    shutil.rmtree(directory / 'generated/ios')
    info['schema_version'] = 2
    info['payload']['payload_sha256'] = ci.sha256(directory / 'source-payload.zip')
    info['runtime_input_bytes'] = size
    info['files'] = {p.relative_to(directory).as_posix(): {'bytes': p.stat().st_size, 'sha256': ci.sha256(p)}
                     for p in sorted(directory.rglob('*')) if p.is_file() and p.name != 'prepared.json'}
    write(directory / 'prepared.json', info)
    verify_directory(directory)
    print(f'Prepared schema 2: {size} bytes of original generation inputs; game programs and Metal reused', flush=True)
    return info


def upgrade_macos(directory, original, upstream):
    directory = Path(directory)
    info = json.loads((directory / 'manifest.json').read_text())
    if info['format'] != 'd2r-mobile-macos-prepared' or info['schema_version'] != 1:
        raise ValueError('Expected a schema 1 Mac prepared package')
    for name, digest in info['files'].items():
        if ci.sha256(directory / name) != digest:
            raise ValueError('Mac prepared package checksum mismatch')
    verify_generation(Path(upstream), info['source_commit'], heavy=True)
    size = create(original, directory / 'romfs-inputs.zip')
    for name in ('romfs_overlay', 'romfs_overlay_variants'):
        shutil.rmtree(directory / name, ignore_errors=True)
    info['schema_version'] = 2
    info['runtime_input_bytes'] = size
    info['files'] = {p.relative_to(directory).as_posix(): ci.sha256(p)
                     for p in sorted(directory.rglob('*')) if p.is_file() and p.name != 'manifest.json'}
    write(directory / 'manifest.json', info)
    return info

"""Small synthetic build inputs, without game assets or Apple toolchains."""
import json
import subprocess
import zipfile

import ci
from source.prepared import GENERATED

BASE = 'Payload/DmIOS.app/'
CHAIN = 'a' * 12


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], text=True, stderr=subprocess.DEVNULL).strip()


def source(root):
    upstream = root / 'upstream'
    upstream.mkdir()
    git(upstream, 'init', '-b', 'main')
    git(upstream, 'config', 'user.name', 'Build tests')
    git(upstream, 'config', 'user.email', 'tests@example.invalid')
    git(upstream, 'config', 'commit.gpgsign', 'false')
    git(upstream, 'config', 'tag.gpgsign', 'false')
    programs = {'main': 'a' * 64, 'sdk': 'b' * 64, 'subsdk0': 'c' * 64}
    rows = '\n'.join(f'{digest} {name}' for name, digest in programs.items())
    resources = [{'name': f'{n:02}.data', 'bytes': n + 1, 'sha256': 'd' * 64} for n in range(18)]
    files = {
        'setup.sh': 'done <<SUMS\n' + rows + '\nSUMS\n',
        'dm/PRODUCT': 'fixture-product\n',
        'dm/runtime.c': 'runtime source\n',
        'dm/tools/patch_nso.py': 'import helper\n',
        'dm/tools/helper.py': 'rules = 1\n',
        'dm/gfx/tools/kernel.py': 'shader_rules = 1\n',
        'dm/gfx/tools/dmsh_chain.py': "FILES = ['tools/dmsh_chain.py', 'tools/kernel.py']\n" +
            "import pathlib\np=pathlib.Path(__file__).parent\n" +
            f"(p.parent/'nvn/dmsh_chain.h').write_text('#define DMSH_CHAIN \\\"{CHAIN}\\\"\\n')\nprint('{CHAIN}')\n",
        'dm/ios/tools/shell_stubs.py': '# fixture stubs\n',
        'dm/gfx/nvn/stub.c': 'stub source\n',
        'dm/gfx/nvn/shader_map.tsv': 'fixture shader map\n',
        'dm/gfx/nvn/pso_manifest.tsv': '# fixture pipeline seed\n',
        'dm/ios/asset_pointers.txt': '# fixture download pointers\n',
        'dm/host/romfs_validate.c': 'static const char *need[] = {' +
            ','.join('"' + f['name'] + '"' for f in resources) + '};\nstatic const long long size[] = {' +
            ','.join(str(f['bytes']) for f in resources) + '};\n',
    }
    for name, text in files.items():
        path = upstream / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    git(upstream, 'add', '.')
    git(upstream, 'commit', '-m', 'fixture source')
    git(upstream, 'tag', 'v1.2.3')
    return upstream, git(upstream, 'rev-parse', 'HEAD'), programs, resources


def package(root):
    upstream, commit, programs, resources = source(root)
    directory = root / 'package'
    directory.mkdir()
    pack = {'name': 'fixture-legacy', 'version': 1}
    report = {'product': 'fixture-product', 'shader_chain': CHAIN, 'shader_source_chain': CHAIN,
              'legacy_pack': pack, 'shader_source_sha16': 'a' * 16, 'metal_precompiled': True,
              'metal_libraries': 1, 'metal_unique_libraries': 1,
              'metal_toolchain': {'compile_record': 'fixture-metal'}, 'phone_shader_compilation_required': False}
    prefix = BASE + 'msl/b-' + CHAIN + '/'
    with zipfile.ZipFile(directory / 'source-payload.zip', 'w') as archive:
        archive.writestr(BASE + 'Frameworks/Guest.framework/Guest', bytes.fromhex('cffaedfe0c000001'))
        archive.writestr(BASE + 'romfs_overlay_astc/pack.json', json.dumps(pack))
        archive.writestr(prefix + 'one_b.metallib', b'MTLBfixture shader')
        archive.writestr(prefix + 'one_b.json', json.dumps({'chain': CHAIN, 'chain_metal': 'fixture-metal'}))
        archive.writestr(BASE + 'dm/shell_info.txt', f'PRODUCT=fixture-product\nCHAIN={CHAIN}\n')
        archive.writestr(BASE + 'dm/shell_manifest.txt', f'msl/b-{CHAIN}/\nFrameworks/Guest.framework/Guest\n')
        archive.writestr(BASE + 'dm/c1_full.tsv', f'# chain b-{CHAIN} fixture\n')
    report['payload_sha256'] = ci.sha256(directory / 'source-payload.zip')
    (directory / 'shader-chain.lock').write_text('fixture toolchain\n')
    manifest = {'game_version': 'fixture-game', 'switch_version': 'fixture-update', 'files': resources}
    (directory / 'resource-manifest.json').write_text(json.dumps(manifest))
    for name in GENERATED:
        path = directory / 'generated' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('generated fixture\n')
    info = {'format': 'd2r-mobile-prepared', 'schema_version': 1, **{k: manifest[k] for k in ('game_version', 'switch_version')},
            'nso_sha256': programs, 'producer': {'source_commit': commit}, 'payload': report,
            'files': {p.relative_to(directory).as_posix(): {'bytes': p.stat().st_size, 'sha256': ci.sha256(p)}
                      for p in directory.rglob('*') if p.is_file()}}
    (directory / 'prepared.json').write_text(json.dumps(info))
    archive = root / '.prepared-downloads/prepared-input.archive'
    archive.parent.mkdir()
    with zipfile.ZipFile(archive, 'w') as z:
        for path in directory.rglob('*'):
            if path.is_file():
                z.write(path, path.relative_to(directory))
    return upstream, directory, info

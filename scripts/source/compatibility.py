"""Compare payload-generation inputs with the package's source revision."""
import ast
from pathlib import Path
import posixpath
import re
import subprocess

ROOTS = ('LICENSE', 'dm/PRODUCT', 'dm/data', 'dm/ios/tools/native_art',
         'dm/ios/docs', 'dm/ios/NativeHUD', 'dm/gfx/tex', 'dm/gfx/nvn/include',
         'dm/gfx/nvn/nvn_tex.c', 'dm/gfx/nvn/nvn_tex.h', 'dm/third_party',
         'dm/ios/Sources/TouchArt/native_art_manifest.json')
SCRIPTS = ('dm/tools/patch_nso.py', 'dm/ios/tools/gen_assets.py',
           'dm/tools/d2rm_pack.py', 'dm/ios/tools/gen_romfs_overlay.py',
           'dm/ios/tools/gen_bright_levels.py', 'dm/ios/tools/shell_stubs.py',
           'dm/tools/dmx.py', 'dm/gfx/tools/nometal_run.py')


def git(upstream, *args):
    return subprocess.check_output(['git', '-C', str(upstream), *args],
                                   stderr=subprocess.DEVNULL).decode()


def generation_paths(upstream, revision, *, heavy=False):
    if revision:
        names = set(git(upstream, 'ls-tree', '-r', '--name-only', '-z', revision).split('\0')) - {''}
    else:
        names = {n for n in git(upstream, 'ls-files', '-z', '--cached', '--others', '--exclude-standard').split('\0')
                 if n and (upstream / n).is_file()}

    def read(name):
        return git(upstream, 'show', revision + ':' + name) if revision else (upstream / name).read_text()

    roots = ('dm/PRODUCT', 'dm/tools/product.sh', 'dm/gfx/tools/msl_retarget.sh') if heavy else ROOTS
    paths = {n for n in names if any(n == p or n.startswith(p + '/') for p in roots)}
    if heavy:
        paths.update(n for n in names if n.startswith('dm/data/bootlist_'))
    scripts = ('dm/tools/patch_nso.py', 'dm/tools/guest_dylib.py',
               'dm/ios/tools/gen_embed.py', 'dm/gfx/tools/c1_guard.py') if heavy else SCRIPTS
    pending = [n for n in scripts if n in names]
    while pending:
        name = pending.pop()
        if name in paths:
            continue
        paths.add(name)
        tree = ast.parse(read(name))
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(n.name.replace('.', '/') + '.py' for n in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module.replace('.', '/') + '.py')
            elif isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.endswith('.py'):
                imports.append(node.value)
        for child in imports:
            for directory in (posixpath.dirname(name), 'dm/ios/tools', 'dm/tools'):
                candidate = posixpath.normpath(directory + '/' + child)
                if candidate in names and candidate not in paths:
                    pending.append(candidate)

    chain = 'dm/gfx/tools/dmsh_chain.py'
    tree = ast.parse(read(chain))
    assignments = [n for n in tree.body if isinstance(n, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == 'FILES' for t in n.targets)]
    if len(assignments) != 1:
        raise ValueError('Cannot determine upstream shader dependencies')
    paths.add(chain)
    paths.update(posixpath.normpath('dm/gfx/' + n) for n in ast.literal_eval(assignments[0].value))
    paths.discard('dm/gfx/tools/dmsh_chain.lock')
    return paths


def verify_generation(upstream, baseline, *, heavy=False):
    upstream = Path(upstream)
    if not isinstance(baseline, str) or not re.fullmatch('[a-f0-9]{40}', baseline):
        raise ValueError('Prepared package source commit is invalid')
    try:
        git(upstream, 'cat-file', '-e', baseline + '^{commit}')
    except subprocess.CalledProcessError:
        raise ValueError('Prepared package source commit is missing from this source repository') from None
    changed = set(git(upstream, 'diff', '--name-only', '-z', baseline, '--').split('\0'))
    changed.update(git(upstream, 'ls-files', '--others', '--exclude-standard', '-z').split('\0'))
    changed.discard('')
    if not changed:
        return
    dependencies = generation_paths(upstream, baseline, heavy=heavy) | generation_paths(upstream, None, heavy=heavy)
    affected = sorted(changed & dependencies)
    if affected:
        raise ValueError('Selected source changed generation inputs (' + ', '.join(affected[:5]) +
                         '); supply an updated prepared package')

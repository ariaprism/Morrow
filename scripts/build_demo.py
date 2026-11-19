"""Build the public, browser-local demo from an explicit source allowlist.

Never imports Flask, loads .env, opens a database, or copies runtime data.
"""
import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def build(output):
    output = Path(output).resolve()
    if output == ROOT or ROOT in output.parents and output.parts[-1] in {'static', 'demo'}:
        raise ValueError('Choose a separate build output directory')
    output.mkdir(parents=True, exist_ok=True)
    source = (ROOT / 'static/index.html').read_text(encoding='utf-8')
    # Strip network font dependencies; original bundled/system fallbacks remain.
    source = re.sub(r'\s*<link\b[^>]*https://cdn\.jsdelivr\.net[^>]*>', '', source)
    # The production worker expects authenticated push APIs. It is not part of a static demo.
    marker = "<script>\n  if ('serviceWorker' in navigator)"
    start = source.find(marker)
    if start < 0:
        raise ValueError('Production worker registration changed; review demo build')
    end = source.index('</script>', start) + len('</script>')
    source = source[:start] + source[end:]
    source = source.replace('navigator.serviceWorker.register(', 'window.MorrowDemo.unavailableWorker(')
    # Keep all original preference interactions, isolated in a demo namespace.
    source = re.sub(r'\blocalStorage\b', 'window.MorrowDemo.storage', source)
    source = source.replace('<head>', '<head>\n  <link rel="stylesheet" href="/demo/demo.css">\n  <script src="/demo/seed.js"></script>\n  <script src="/demo/runtime.js"></script>', 1)
    source = source.replace('<title>Morrow</title>', '<title>Morrow · 演示空间</title>')
    (output / 'index.html').write_text(source, encoding='utf-8')
    # Copy only public UI assets, never all of static/ or any private runtime folders.
    for name in ('fonts', 'icons', 'backgrounds'):
        shutil.copytree(ROOT / 'static' / name, output / 'static' / name, dirs_exist_ok=True)
    for name in ('paper-grain.svg', 'manifest.json'):
        shutil.copy2(ROOT / 'static' / name, output / 'static' / name)
    shutil.copytree(ROOT / 'demo', output / 'demo', dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns('README*', '*test*'))
    headers = ('/*\n  X-Content-Type-Options: nosniff\n  Referrer-Policy: no-referrer\n'
               '  Permissions-Policy: camera=(), microphone=(), geolocation=()\n'
               '  Content-Security-Policy: default-src \'self\'; script-src \'self\' \'unsafe-inline\'; '
               'style-src \'self\' \'unsafe-inline\'; img-src \'self\' data: blob:; font-src \'self\'; '
               'connect-src \'self\' https:; media-src \'self\' blob:; object-src \'none\'; '
               'base-uri \'self\'; form-action \'self\'; frame-ancestors \'none\'\n'
               '/index.html\n  Cache-Control: no-cache\n/demo/*\n  Cache-Control: no-cache\n')
    (output / '_headers').write_text(headers, encoding='utf-8')
    (output / 'build-info.json').write_text(json.dumps({
        'source_sha256': hashlib.sha256((ROOT / 'static/index.html').read_bytes()).hexdigest(),
        'mode': 'browser-local-demo', 'storage_version': 1,
    }, indent=2), encoding='utf-8')
    allowed = {'index.html', '_headers', 'build-info.json'}
    for name in ('fonts', 'icons', 'backgrounds'):
        allowed.update((Path('static') / name / p.relative_to(ROOT / 'static' / name)).as_posix()
                       for p in (ROOT / 'static' / name).rglob('*') if p.is_file())
    allowed.update({'static/paper-grain.svg', 'static/manifest.json'})
    allowed.update((Path('demo') / p.relative_to(ROOT / 'demo')).as_posix()
                   for p in (ROOT / 'demo').rglob('*')
                   if p.is_file() and not p.name.startswith('README') and 'test' not in p.name)
    actual = {p.relative_to(output).as_posix() for p in output.rglob('*') if p.is_file()}
    if actual - allowed:
        raise ValueError('Unexpected files in demo output; choose a clean build directory')
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default=str(ROOT / 'data/demo-site/dist'))
    args = parser.parse_args()
    print('Demo built:', build(args.output))

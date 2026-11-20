import importlib.util
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path


class StaticDemoBuildTest(unittest.TestCase):
    def test_worker_configuration_deploys_only_built_static_assets(self):
        config = json.loads(Path('wrangler.json').read_text(encoding='utf-8'))
        date.fromisoformat(config['compatibility_date'])
        self.assertTrue(config['name'])
        self.assertEqual(Path(config['assets']['directory']), Path('data/demo-site/dist'))
        self.assertFalse(set(config) & {
            'main', 'vars', 'secrets', 'account_id', 'routes',
            'd1_databases', 'kv_namespaces', 'r2_buckets',
        })

    def test_build_has_only_public_assets_and_no_backend_dependency(self):
        source = Path('static/index.html').read_bytes()
        spec = importlib.util.spec_from_file_location('build_static_demo', 'scripts/build_demo.py')
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        with tempfile.TemporaryDirectory() as temporary:
            output = builder.build(Path(temporary) / 'dist')
            page = (output / 'index.html').read_text(encoding='utf-8')
            self.assertIn('/demo/runtime.js', page)
            self.assertLess(page.index('/demo/runtime.js'), page.index('/* 偏好：'))
            self.assertIn('window.MorrowDemo.storage.getItem', page)
            self.assertTrue('navigator.serviceWorker.register' not in page)
            self.assertNotIn('href="https://cdn.jsdelivr.net', page)
            self.assertFalse((output / 'static/sw.js').exists())
            self.assertEqual(json.loads((output / 'build-info.json').read_text())['mode'], 'browser-local-demo')
            names = [p.relative_to(output).as_posix() for p in output.rglob('*') if p.is_file()]
            self.assertFalse(any(p.endswith(('.db', '.py', '.env', '.sqlite')) for p in names))
            self.assertFalse(any('uploads/' in p or 'demo-data/' in p for p in names))
            self.assertTrue((output / 'demo/assets/user.jpg').is_file())
        self.assertEqual(Path('static/index.html').read_bytes(), source)

    def test_build_refuses_product_source_directory(self):
        spec = importlib.util.spec_from_file_location('build_static_demo', 'scripts/build_demo.py')
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        with self.assertRaises(ValueError):
            builder.build(Path('static'))

    def test_build_fails_if_stale_private_files_are_in_output(self):
        spec = importlib.util.spec_from_file_location('build_static_demo', 'scripts/build_demo.py')
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'dist'
            output.mkdir()
            (output / 'private.db').write_bytes(b'synthetic-sentinel')
            with self.assertRaisesRegex(ValueError, 'Unexpected files'):
                builder.build(output)

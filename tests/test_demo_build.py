import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("build_demo", ROOT / "scripts/build_demo.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class DemoBuildTest(unittest.TestCase):
    def test_output_contains_adapted_shell_and_assets_without_backend_or_worker(self):
        with tempfile.TemporaryDirectory() as root:
            target = module.build(Path(root) / "dist")
            page = (target / "index.html").read_text()
            self.assertLess(page.index('src="/demo/demo.js"'), page.index('const api='))
            self.assertGreater(page.index('href="/demo/demo.css"'), page.index('<style>'))
            self.assertNotIn("serviceWorker.register", page)
            self.assertNotIn("cdn.jsdelivr.net", page)
            self.assertFalse((target / "static/sw.js").exists())
            self.assertFalse((target / "static/index.html").exists())
            self.assertFalse((target / "app.py").exists())
            self.assertFalse((target / "data").exists())
            self.assertTrue((target / "static/icons/icon.svg").exists())
            self.assertIn("connect-src 'self'", (target / "_headers").read_text())

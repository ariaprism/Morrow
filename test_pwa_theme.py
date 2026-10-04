import json
import re
import unittest
from pathlib import Path


class PwaThemeTest(unittest.TestCase):
    def test_installed_app_has_an_opaque_android_status_bar_fallback(self):
        manifest = json.loads(Path("static/manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["theme_color"], "#eadcc6")

        page = Path("static/index.html").read_text(encoding="utf-8")
        self.assertIn('<meta name="theme-color" content="#eadcc6" id="themeColor">', page)
        sync = re.search(
            r"function syncSystemBars\(theme\)\{(?P<body>.*?)\n\}",
            page,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(sync)
        body = sync.group("body")
        self.assertNotIn('standalone?"transparent"', body)
        self.assertIn('m.content=colors[theme]||colors.paper', body)
        self.assertNotIn("pwa-standalone #app::after", page)

    def test_service_worker_update_bypasses_stale_shell_cache(self):
        page = Path("static/index.html").read_text(encoding="utf-8")
        worker = Path("static/sw.js").read_text(encoding="utf-8")
        self.assertIn("updateViaCache:'none'", page)
        self.assertIn("registration.update()", page)
        self.assertIn("navigator.serviceWorker.controller", page)
        self.assertIn("controllerchange", page)
        self.assertIn("nook-pwa-v2", worker)
        self.assertIn("caches.delete", worker)

    def test_data_page_only_exposes_github_cloud_backup(self):
        page = Path("static/index.html").read_text(encoding="utf-8")
        self.assertIn("GitHub 云端备份", page)
        self.assertNotIn("Supabase 云端镜像", page)
        self.assertNotIn("cloudSyncCard", page)
        self.assertNotIn("loadCloudSyncStatus", page)


if __name__ == "__main__":
    unittest.main()

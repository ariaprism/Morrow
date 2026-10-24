import json
import re
import struct
from urllib.parse import urlsplit
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

    def test_cat_icon_assets_cover_pwa_ios_favicon_and_notification_sizes(self):
        manifest = json.loads(Path("static/manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["name"], "Morrow")
        self.assertEqual(manifest["id"], "/")
        self.assertEqual({item["purpose"] for item in manifest["icons"]}, {"any", "maskable"})
        for icon in manifest["icons"]:
            name = urlsplit(icon["src"]).path.lstrip("/")
            blob = Path(name).read_bytes()
            self.assertEqual(blob[:8], b"\x89PNG\r\n\x1a\n")
            self.assertEqual(struct.unpack(">II", blob[16:24]), tuple(map(int, icon["sizes"].split("x"))))
            self.assertTrue(urlsplit(icon["src"]).query)
        for name, size in (("apple-touch-icon.png", 180), ("favicon-32.png", 32), ("badge-96.png", 96)):
            blob = (Path("static/icons") / name).read_bytes()
            self.assertEqual(struct.unpack(">II", blob[16:24]), (size, size))
        page = Path("static/index.html").read_text(encoding="utf-8")
        self.assertIn('rel="apple-touch-icon"', page)
        self.assertIn('/static/icons/favicon-32.png', page)
        worker = Path("static/sw.js").read_text(encoding="utf-8")
        self.assertIn('/static/icons/badge-96.png', worker)
        self.assertNotRegex(worker, r"(?m)^\s*icon\s*:")

    def test_data_page_only_exposes_github_cloud_backup(self):
        page = Path("static/index.html").read_text(encoding="utf-8")
        self.assertIn("GitHub 云端备份", page)
        self.assertNotIn("Supabase 云端镜像", page)
        self.assertNotIn("cloudSyncCard", page)
        self.assertNotIn("loadCloudSyncStatus", page)


if __name__ == "__main__":
    unittest.main()

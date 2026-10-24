import runpy
import unittest
from unittest import mock

import app


class WsgiEntrypointTest(unittest.TestCase):
    def test_production_entrypoint_starts_scheduler_and_exports_app(self):
        with mock.patch("app.start_night_scheduler") as start:
            namespace = runpy.run_path("wsgi.py")
        start.assert_called_once_with()
        self.assertIs(namespace["app"], app.app)


if __name__ == "__main__":
    unittest.main()

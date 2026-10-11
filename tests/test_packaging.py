"""Packaging: the `etrace` command is installable and the presets ship inside the package."""
import contextlib
import io
import os
import tomllib
import unittest

import etrace
from etrace import cli, presets

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class PackagingTest(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(REPO, "pyproject.toml"), "rb") as fh:
            self.project = tomllib.load(fh)

    def test_console_script_points_at_cli_main(self):
        self.assertEqual(self.project["project"]["scripts"]["etrace"], "etrace.cli:main")
        self.assertTrue(callable(cli.main))

    def test_standard_library_only(self):
        self.assertEqual(self.project["project"]["dependencies"], [])
        self.assertEqual(self.project["project"]["requires-python"], ">=3.11")

    def test_version_matches_package(self):
        self.assertEqual(self.project["project"]["version"], etrace.__version__)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(["--version"]), 0)
        self.assertEqual(out.getvalue().strip(), f"etrace {etrace.__version__}")

    def test_presets_live_inside_the_package(self):
        package_dir = os.path.dirname(os.path.abspath(etrace.__file__))
        self.assertEqual(os.path.dirname(presets.PRESETS_DIR), package_dir)
        for name in presets.NAMES:
            presets.load(name)
        self.assertIn("preset_data/*/*", self.project["tool"]["setuptools"]["package-data"]["etrace"])



class MethodologyPageTest(unittest.TestCase):
    def test_page_matches_default_preset(self):
        with open(os.path.join(REPO, "docs", "methodology.html"), encoding="utf-8") as fh:
            page = fh.read()
        p = presets.load("default")
        self.assertIn(f"g-{p['g']['version']}", page)
        for code, weight, label in presets.g_items(p):
            self.assertIn(f"<b>{code}</b>", page)
            self.assertIn(label["zh"], page)
            self.assertIn(f"{weight:.2f}", page)
        for d in p["xp"]["dimensions"]:
            self.assertIn(d["label"]["en"], page)
        self.assertIn("125", page)  # states that the PAD 125-word mapping is not written
        self.assertIn("methodology.html", open(os.path.join(REPO, "docs", "index.html"), encoding="utf-8").read())


if __name__ == "__main__":
    unittest.main()

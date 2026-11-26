import contextlib
import io
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

import perfkit
from perfkit import cli, collectors


class CliDiscoveryTest(unittest.TestCase):
    def test_help_lists_commands(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as cm:
            cli.main(["--help"])
        self.assertEqual(cm.exception.code, 0)
        self.assertIn("usage: perfkit", out.getvalue())

    def test_no_args_prints_help(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = cli.main([])
        self.assertEqual(rc, 0)
        self.assertIn("usage: perfkit", out.getvalue())

    def test_new_module_registers_without_touching_cli(self):
        # 새 패키지가 perfkit/<폴더>/만 더했을 때를 흉내 낸다.
        with tempfile.TemporaryDirectory() as tmp:
            pkg = Path(tmp) / "zz_demo"
            pkg.mkdir()
            (pkg / "__init__.py").write_text(textwrap.dedent("""
                def register_cli(sub):
                    p = sub.add_parser("zzdemo", help="demo")
                    p.set_defaults(func=lambda a: 7)
            """), encoding="utf-8")
            (Path(tmp) / "zz_broken.py").write_text("raise RuntimeError('boom')\n", encoding="utf-8")
            perfkit.__path__.append(tmp)
            try:
                found = cli.discover()
                names = [n for n, _ in found.registrars]
                self.assertIn("zz_demo", names)
                self.assertIn("zz_broken", [n for n, _ in found.failures])
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    rc = cli.main(["zzdemo"])
                self.assertEqual(rc, 7)
                self.assertIn("zz_broken", err.getvalue())
            finally:
                perfkit.__path__.remove(tmp)
                for m in [m for m in sys.modules if m.startswith("perfkit.zz_")]:
                    del sys.modules[m]


class CollectorRegistryTest(unittest.TestCase):
    def test_name_rule(self):
        self.assertTrue(collectors.NAME_RE.match("device.frame_timing"))
        self.assertFalse(collectors.NAME_RE.match("frame_timing"))
        self.assertFalse(collectors.NAME_RE.match("gpu.frame"))
        with self.assertRaises(ValueError):
            collectors.collector("Bad.Name")

    def test_register_and_get(self):
        @collectors.collector("run.zz_test_only")
        def fn(ctx):
            return {}

        self.assertIs(collectors.get("run.zz_test_only"), fn)
        self.assertEqual(collectors.owner_of("run.zz_test_only"), "perfkit/run")
        collectors._REGISTRY.pop("run.zz_test_only")


if __name__ == "__main__":
    unittest.main()

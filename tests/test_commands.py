import contextlib
import io
import unittest

from perfkit import cli
from perfkit.paths import REPO_ROOT

EX = REPO_ROOT / "examples"


def run(argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        try:
            rc = cli.main(argv)
        except SystemExit as exc:
            rc = exc.code
    return rc, out.getvalue()


class CommandsTest(unittest.TestCase):
    def test_help_lists_p1_commands(self):
        rc, text = run(["--help"])
        self.assertEqual(rc, 0)
        for cmd in ("manifest", "schema", "verdict"):
            self.assertIn(cmd, text)

    def test_manifest_check_exit_zero(self):
        rc, text = run(["manifest", "check"])
        self.assertEqual(rc, 0)
        self.assertIn("checks=123 areas=9", text)

    def test_schema_validate_examples(self):
        files = [str(EX / n) for n in ("run-meta.emulator.json", "run-meta.device.json",
                                       "result.l3e.json", "comparison.l3e.json")]
        rc, text = run(["schema", "validate", "--manifest", *files])
        self.assertEqual(rc, 0, text)

    def test_verdict_judge(self):
        rc, text = run(["verdict", "judge", "--id", "BLD-01", "--layer", "L1", "--metric", "apk_mb",
                        "--base", "100", "--cand", "106"])
        self.assertEqual(rc, 0)
        self.assertIn("=> fail", text)


if __name__ == "__main__":
    unittest.main()

"""모든 명령·하위 명령의 --help가 서식 오류 없이 나오는지(argparse는 help 문자열의 %를 서식으로 읽는다)."""

import argparse
import unittest

from perfkit import cli
from tests.test_commands import run


def _walk(parser, path=()):
    yield path, parser
    for act in parser._actions:
        if isinstance(act, argparse._SubParsersAction):
            for name, sub in act.choices.items():
                yield from _walk(sub, path + (name,))


class HelpTest(unittest.TestCase):
    def test_every_help_renders(self):
        found = cli.discover()
        self.assertEqual(found.failures, [])
        parser = cli.build_parser(found)
        paths = list(_walk(parser))
        self.assertGreater(len(paths), 20)
        for path, p in paths:
            with self.subTest(cmd=" ".join(path) or "(root)"):
                p.format_help()                        # KeyError·ValueError면 여기서 실패
                rc, _ = run([*path, "--help"])
                self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()

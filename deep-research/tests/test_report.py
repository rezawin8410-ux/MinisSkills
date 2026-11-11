#!/usr/bin/env python3
"""Tests for the generated report's own claims about itself.

The report is the deliverable a user reads and quotes, and its Method section
used to be hardcoded: it always said the pages came from "(Bing, Bing News,
Wikipedia)" over a "three-stage fallback", whatever `--group`, `--provider` or
the actual fetch chain did. A report that misdescribes how it was produced is
worse than one with no method section at all.

Run:  python3 deep-research/tests/test_report.py -v
"""
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESEARCH_PY = ROOT / "deep-research" / "scripts" / "research.py"


def _load_research():
    spec = importlib.util.spec_from_file_location("deepres_research", RESEARCH_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


R = _load_research()


def _res(**over):
    base = {
        "question": "does the vendor tier stay opt-in",
        "queries": ["q1", "q2"],
        "sources": [{"url": "https://example.com/a", "title": "A", "chars": 100,
                     "source": "direct", "hop": 1}],
        "summaries": {"https://example.com/a": ["It does."]},
        "numbers": [],
        "failures": [],
        "errors": {},
        "followed": [],
        "_json_path": "report.json",
        "group": "tech",
        "providers": ["wikipedia", "hn", "lobsters"],
    }
    base.update(over)
    return base


class TableCellTests(unittest.TestCase):
    """A page title with a newline or a pipe must not reshape the table.

    Titles come from the page and from search engines; `|` added a column and a
    newline split the row in two, losing the domain/chars/url of that source.
    """

    def test_cell_is_one_line_and_one_cell(self):
        self.assertEqual(R.cell("a | b\nc\td"), "a / b c d")
        self.assertEqual(R.cell(None), "")
        self.assertEqual(R.cell("x" * 200, 10), "x" * 10)
        self.assertEqual(R.cell("back\\slash"), "back/slash")

    def test_the_sources_table_keeps_one_row_per_source(self):
        res = _res(sources=[
            {"url": "https://a.example/1", "title": "Real | Title\n| injected | row |",
             "chars": 10, "source": "direct", "hop": 1},
            {"url": "https://b.example/2", "search_title": "line1\nline2",
             "chars": 5, "source": "direct", "hop": 1},
        ])
        md = R.to_markdown(res)
        rows = [l for l in md.splitlines() if l.startswith("| [")]
        self.assertEqual(len(rows), 2, rows)
        for row in rows:
            self.assertEqual(row.count("|"), 8, f"row changed shape: {row}")

    def test_a_failure_url_is_also_single_line(self):
        md = R.to_markdown(_res(failures=[{"url": "https://c.example/\nx",
                                           "errors": ["a\nb", "c"]}]))
        line = next(l for l in md.splitlines() if "c.example" in l)
        self.assertNotIn("\n", line)


class MethodTruthTests(unittest.TestCase):
    def test_the_method_names_the_providers_that_ran(self):
        md = R.to_markdown(_res())
        self.assertIn("hn", md)
        self.assertIn("lobsters", md)
        self.assertIn("`tech`", md)
        self.assertNotIn("Bing News", md, "must not describe a group that did not run")

    def test_the_method_describes_the_real_chain(self):
        md = R.to_markdown(_res())
        self.assertNotIn("three-stage", md)
        for stage in ("direct", "reader", "vendor", "Wayback"):
            self.assertIn(stage, md)

    def test_an_old_trail_without_a_provider_list_still_renders(self):
        r = _res()
        r.pop("providers")
        r.pop("group")
        md = R.to_markdown(r)
        self.assertIn("## Method", md)
        self.assertIn("`web`", md)

    def test_run_records_the_group_and_providers(self):
        """The recording has to happen where the searching happens."""
        import inspect
        src = inspect.getsource(R.run)
        self.assertIn('"group": group', src)
        self.assertIn('"providers": used', src)


if __name__ == "__main__":
    unittest.main(verbosity=2)

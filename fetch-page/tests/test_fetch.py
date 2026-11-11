#!/usr/bin/env python3
"""Strict, hermetic tests for the fetch-page deadline and the Firecrawl vendor stage.

No network: every test runs against a local HTTP server on 127.0.0.1, including a
server that accepts a connection and never answers (the bug this suite exists for).

Run:  python3 -m unittest discover -s fetch-page/tests -t .   (from the repo root)
      python3 fetch-page/tests/test_fetch.py -v
"""
from __future__ import annotations

import importlib.util
import json
import os
import socket
import subprocess
import sys
import threading
import time
import unittest
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FETCH_PY = ROOT / "fetch-page" / "scripts" / "fetch.py"


def _load_fetch_module():
    spec = importlib.util.spec_from_file_location("fetchpage_fetch", FETCH_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


fetch_mod = _load_fetch_module()

# Nothing listens on port 1, so an un-redirected vendor call fails instantly and
# offline. Tests that want a vendor to answer point these at their local _Site.
DEAD_VENDOR = "http://127.0.0.1:1"


def setUpModule():
    os.environ["FIRECRAWL_SCRAPE_URL"] = DEAD_VENDOR + "/v2/scrape"
    os.environ["EXA_MCP_URL"] = DEAD_VENDOR + "/mcp"


def tearDownModule():
    os.environ.pop("FIRECRAWL_SCRAPE_URL", None)
    os.environ.pop("EXA_MCP_URL", None)


# ---------------------------------------------------------------- test servers
class _Site:
    """Configurable origin server. Records what was requested and when."""

    def __init__(self):
        self.requests: list[str] = []
        self.vendor_requests = 0
        self.vendor_status = 200
        self.vendor_payload: dict = {}
        self.exa_requests = 0
        self.exa_bodies: list[dict] = []
        self.exa_status = 200
        self.exa_payload: dict = {"jsonrpc": "2.0", "id": 1,
                                  "error": {"code": -1, "message": "exa not configured here"}}
        self.exa_sse = False          # answer as text/event-stream instead of JSON
        self.exa_raw: str | None = None
        self.exa_headers: dict = {}
        self.json_routes: dict = {}
        self.raw_routes: dict = {}    # prefix -> (status, content_type, body, extra_headers)
        self.page_html = "<html><head><title>t</title></head><body><p>hi</p></body></html>"
        self.site = self

        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):        # silence
                pass

            def do_GET(self):
                outer.requests.append(self.path)
                if self.path.startswith("/media/") and not outer.raw_routes:
                    body = b"\xff\xd8\xff\xe0" + b"JPEGDATA" * 50 + b"\xff\xd9"
                    self.send_response(200)
                    self.send_header("Content-Type", "image/jpeg")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                for prefix, spec in outer.raw_routes.items():
                    if self.path.startswith(prefix):
                        status, ctype, body = spec[:3]
                        extra = spec[3] if len(spec) > 3 else {}
                        self.send_response(status)
                        self.send_header("Content-Type", ctype)
                        self.send_header("Content-Length", str(len(body)))
                        for k, v in (extra or {}).items():
                            self.send_header(k, v)
                        self.end_headers()
                        self.wfile.write(body)
                        return
                if outer.json_routes:
                    for prefix, payload in outer.json_routes.items():
                        if self.path.startswith(prefix):
                            body = json.dumps(payload).encode()
                            self.send_response(200)
                            self.send_header("Content-Type", "application/json")
                            self.send_header("Content-Length", str(len(body)))
                            self.end_headers()
                            self.wfile.write(body)
                            return
                if self.path.startswith("/json"):
                    body = json.dumps(outer.exa_payload).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                body = outer.page_html.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                outer.requests.append(self.path)
                if self.path.startswith("/v2/scrape"):
                    outer.vendor_requests += 1
                    self.rfile.read(length)
                    if outer.vendor_status != 200:
                        msg = b'{"error":"nope"}'
                        self.send_response(outer.vendor_status)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(len(msg)))
                        self.end_headers()
                        self.wfile.write(msg)
                        return
                    payload = json.dumps(outer.vendor_payload).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                elif self.path.startswith("/mcp"):
                    outer.exa_requests += 1
                    outer.exa_headers = dict(self.headers)
                    raw_body = self.rfile.read(length)
                    try:
                        outer.exa_bodies.append(json.loads(raw_body.decode() or "{}"))
                    except json.JSONDecodeError:
                        outer.exa_bodies.append({"__unparsed__": raw_body.decode()})
                    if outer.exa_status != 200:
                        msg = b'{"error":"nope"}'
                        self.send_response(outer.exa_status)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(len(msg)))
                        self.end_headers()
                        self.wfile.write(msg)
                        return
                    if outer.exa_raw is not None:
                        payload = outer.exa_raw.encode()
                    else:
                        blob = json.dumps(outer.exa_payload)
                        payload = (f"event: message\ndata: {blob}\n\n".encode()
                                   if outer.exa_sse else blob.encode())
                    self.send_response(200)
                    self.send_header("Content-Type",
                                     "text/event-stream" if outer.exa_sse else "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                else:
                    self.send_response(404)
                    self.end_headers()

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=lambda: self.httpd.serve_forever(poll_interval=0.05), daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.port}"

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class _HangingServer:
    """Accepts TCP connections and never writes a byte — a dead relay or DNS black hole."""

    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self._conns = []
        self._stop = threading.Event()
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        self.sock.settimeout(0.2)
        while not self._stop.is_set():
            try:
                conn, _ = self.sock.accept()
                self._conns.append(conn)          # hold it open, never respond
            except (socket.timeout, OSError):
                continue

    def close(self):
        self._stop.set()
        for c in self._conns:
            try:
                c.close()
            except OSError:
                pass
        try:
            self.sock.close()
        except OSError:
            pass


VENDOR_OK = {"success": True, "data": {
    "markdown": "# Vendor Page\n\n" + ("real content " * 40),
    "metadata": {"title": "Vendor Title", "statusCode": 200, "url": "https://example.test/final"},
}}


EXA_OK = {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text":
    "# Exa Page\n\nURL: https://example.test/paid\nPublished: 2026-10-01\n\n"
    + "page body from exa " * 40}], "isError": False}}


class DeadlineTests(unittest.TestCase):
    """A stage that ignores its socket timeouts must not be able to hang the caller."""

    def setUp(self):
        self.hang = _HangingServer()

    def tearDown(self):
        self.hang.close()

    def test_deadline_cuts_a_hanging_stage(self):
        t0 = time.monotonic()
        res = fetch_mod.fetch(f"http://127.0.0.1:{self.hang.port}/never",
                              timeout=30, deadline=2, ttl=0,
                              use_reader=False, use_archive=False, use_vendor=False, retries=0)
        elapsed = time.monotonic() - t0
        self.assertFalse(res["ok"])
        self.assertTrue(res.get("deadline_exceeded"), res)
        self.assertLess(elapsed, 2 + 3.0, f"deadline did not cut the stage: {elapsed:.1f}s")
        self.assertTrue(any("deadline" in e or "no answer" in e for e in res["errors"]), res["errors"])

    def test_deadline_is_wall_clock_not_per_stage(self):
        """Four stages must not add up to four deadlines."""
        t0 = time.monotonic()
        res = fetch_mod.fetch(f"http://127.0.0.1:{self.hang.port}/never",
                              timeout=30, deadline=2, ttl=0,
                              use_reader=True, use_archive=True, use_vendor=True, retries=0)
        elapsed = time.monotonic() - t0
        self.assertFalse(res["ok"])
        self.assertLess(elapsed, 2 + 3.0, f"chain outlived the global deadline: {elapsed:.1f}s")

    def test_deadline_does_not_fire_on_a_fast_page(self):
        """The deadline must not be a timeout: a fast stage still succeeds."""
        site = _Site()
        try:
            site.page_html = ("<html><head><title>fast</title></head><body>"
                              + "<p>" + ("fast page text " * 40) + "</p></body></html>")
            t0 = time.monotonic()
            res = fetch_mod.fetch(f"{site.base}/fast", timeout=5, deadline=10, ttl=0,
                                  use_reader=False, use_archive=False, use_vendor=False,
                                  retries=0)
            self.assertTrue(res["ok"], res)
            self.assertFalse(res.get("deadline_exceeded"))
            self.assertEqual(res["source"], "direct")
            self.assertLess(time.monotonic() - t0, 5)
        finally:
            site.close()


class VendorStageTests(unittest.TestCase):
    def setUp(self):
        self.site = _Site()
        os.environ["FIRECRAWL_SCRAPE_URL"] = f"{self.site.base}/v2/scrape"

    def tearDown(self):
        self.site.close()
        os.environ["FIRECRAWL_SCRAPE_URL"] = DEAD_VENDOR + "/v2/scrape"
        os.environ.pop("FETCH_VENDOR", None)

    def _thin_page(self):
        """Make the direct stage give up: <200 chars of body reads as JS-rendered."""
        self.site.page_html = "<html><head><title>x</title></head><body>too thin</body></html>"

    def test_vendor_is_used_when_direct_fails(self):
        self._thin_page()
        self.site.vendor_payload = VENDOR_OK
        res = fetch_mod.fetch(f"{self.site.base}/page", timeout=10, deadline=15, ttl=0,
                              use_reader=False, use_archive=False, retries=0)
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["source"], "vendor:firecrawl")
        self.assertEqual(res["title"], "Vendor Title")
        self.assertEqual(res["final_url"], "https://example.test/final")
        self.assertEqual(self.site.vendor_requests, 1)
        self.assertIn("real content", res["text"])

    def test_direct_wins_and_vendor_is_never_called(self):
        self.site.page_html = ("<html><head><title>Good</title></head><body>"
                               + "<p>" + ("plenty of real text " * 30) + "</p></body></html>")
        res = fetch_mod.fetch(f"{self.site.base}/page", timeout=10, deadline=15, ttl=0,
                              use_reader=False, use_archive=False, retries=0)
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["source"], "direct")
        self.assertEqual(self.site.vendor_requests, 0, "vendor was called although direct worked")

    def test_no_vendor_flag_disables_the_stage(self):
        self._thin_page()
        self.site.vendor_payload = VENDOR_OK
        res = fetch_mod.fetch(f"{self.site.base}/page", timeout=10, deadline=15, ttl=0,
                              use_reader=False, use_archive=False, use_vendor=False, retries=0)
        self.assertFalse(res["ok"])
        self.assertEqual(self.site.vendor_requests, 0)
        self.assertFalse(any(e.startswith("vendor:") for e in res["errors"]), res["errors"])

    def test_vendor_env_kill_switch(self):
        self._thin_page()
        self.site.vendor_payload = VENDOR_OK
        os.environ["FETCH_VENDOR"] = "0"
        res = fetch_mod.fetch(f"{self.site.base}/page", timeout=10, deadline=15, ttl=0,
                              use_reader=False, use_archive=False, retries=0)
        self.assertFalse(res["ok"])
        self.assertEqual(self.site.vendor_requests, 0)

    def test_vendor_output_is_capped_by_max_chars(self):
        self._thin_page()
        huge = "# Big\n\n" + ("x" * 422279)          # the real RFC-sized payload we measured
        self.site.vendor_payload = {"data": {"markdown": huge,
                                            "metadata": {"title": "Big", "statusCode": 200}}}
        res = fetch_mod.fetch(f"{self.site.base}/page", timeout=20, deadline=25, ttl=0,
                              use_reader=False, use_archive=False, max_chars=20000, retries=0)
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["chars"], 20000)
        self.assertEqual(len(res["text"]), 20000)
        self.assertLess(len(json.dumps(res)), 20000 * 2, "the caller must not receive 422k chars")

    def test_vendor_http_error_fails_over(self):
        self._thin_page()
        self.site.vendor_status = 500
        res = fetch_mod.fetch(f"{self.site.base}/page", timeout=10, deadline=15, ttl=0,
                              use_reader=False, use_archive=False, retries=0)
        self.assertFalse(res["ok"])
        self.assertTrue(any("vendor" in e and "500" in e for e in res["errors"]), res["errors"])
        self.assertEqual(self.site.vendor_requests, 1)

    def test_vendor_empty_markdown_is_an_error_not_a_result(self):
        self._thin_page()
        self.site.vendor_payload = {"data": {"markdown": "   ", "metadata": {}}}
        res = fetch_mod.fetch(f"{self.site.base}/page", timeout=10, deadline=15, ttl=0,
                              use_reader=False, use_archive=False, retries=0)
        self.assertFalse(res["ok"])
        self.assertTrue(any("empty markdown" in e for e in res["errors"]), res["errors"])

    def test_vendor_request_carries_our_own_identity(self):
        self._thin_page()
        self.site.vendor_payload = VENDOR_OK
        fetch_mod.fetch(f"{self.site.base}/page", timeout=10, deadline=15, ttl=0,
                        use_reader=False, use_archive=False, retries=0)
        ua = fetch_mod.VENDOR_UA
        self.assertIn("keyless-agent-skills", ua)
        self.assertNotIn("hermes", ua.lower(), "must not borrow another product's identity")

    def test_cache_round_trip_respects_the_cap(self):
        self._thin_page()
        self.site.vendor_payload = {"data": {"markdown": "# t\n" + ("y" * 5000),
                                             "metadata": {"title": "T", "statusCode": 200}}}
        first = fetch_mod.fetch(f"{self.site.base}/page", timeout=10, deadline=15, ttl=600,
                               use_reader=False, use_archive=False, max_chars=1000, retries=0)
        self.assertTrue(first["ok"])
        second = fetch_mod.fetch(f"{self.site.base}/page", timeout=10, deadline=15, ttl=600,
                                 use_reader=False, use_archive=False, max_chars=400, retries=0)
        self.assertTrue(second.get("cached"), "second call should be a cache hit")
        self.assertEqual(second["chars"], 400, "a cached page must still honour max_chars")


class StageShareTests(unittest.TestCase):
    """A hanging stage must not starve the stages behind it (the g2.com case)."""

    def test_hanging_reader_does_not_starve_the_vendor_stage(self):
        site = _Site()
        os.environ["FIRECRAWL_SCRAPE_URL"] = f"{site.base}/v2/scrape"
        site.page_html = "<html><body>too thin</body></html>"
        site.vendor_payload = VENDOR_OK
        original = fetch_mod.fetch_reader
        fetch_mod.fetch_reader = lambda url, t, keep_links=False: time.sleep(60)
        try:
            t0 = time.monotonic()
            res = fetch_mod.fetch(f"{site.base}/page", timeout=30, deadline=6, ttl=0,
                                  use_archive=False, retries=0)
            elapsed = time.monotonic() - t0
        finally:
            fetch_mod.fetch_reader = original
            site.close()
            os.environ["FIRECRAWL_SCRAPE_URL"] = DEAD_VENDOR + "/v2/scrape"
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["source"], "vendor:firecrawl")
        self.assertEqual(site.vendor_requests, 1, "the vendor stage never got its turn")
        self.assertLess(elapsed, 6 + 3, f"a 6s deadline took {elapsed:.1f}s")

    def test_reader_share_is_bounded_even_when_it_would_answer_late(self):
        """The reader cannot spend the whole deadline and leave nothing for vendor."""
        site = _Site()
        os.environ["FIRECRAWL_SCRAPE_URL"] = f"{site.base}/v2/scrape"
        site.page_html = "<html><body>too thin</body></html>"
        site.vendor_payload = VENDOR_OK
        original = fetch_mod.fetch_reader
        def slow_reader(url, t, keep_links=False):
            time.sleep(t)
            raise RuntimeError("late answer")
        fetch_mod.fetch_reader = slow_reader
        try:
            t0 = time.monotonic()
            res = fetch_mod.fetch(f"{site.base}/page", timeout=30, deadline=8, ttl=0,
                                  use_archive=False, retries=0)
            elapsed = time.monotonic() - t0
        finally:
            fetch_mod.fetch_reader = original
            site.close()
            os.environ["FIRECRAWL_SCRAPE_URL"] = DEAD_VENDOR + "/v2/scrape"
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["source"], "vendor:firecrawl")
        # reader share is 0.40 * 8 = 3.2s, so the vendor stage still had room
        self.assertLess(elapsed, 8)


class ExaStageTests(unittest.TestCase):
    """Stage 4: Exa's anonymous MCP reader, ahead of Wayback."""

    def setUp(self):
        self.site = _Site()
        os.environ["EXA_MCP_URL"] = f"{self.site.base}/mcp"
        os.environ["FIRECRAWL_SCRAPE_URL"] = f"{self.site.base}/v2/scrape"
        self.site.page_html = "<html><body>too thin</body></html>"
        self.site.vendor_status = 500           # Firecrawl fails, Exa must catch
        self.site.exa_payload = EXA_OK
        self.orig = (fetch_mod.fetch_direct, fetch_mod.fetch_reader, fetch_mod.fetch_archive)
        fetch_mod.fetch_direct = lambda url, t, keep_links=False: (_ for _ in ()).throw(RuntimeError("blocked"))
        fetch_mod.fetch_reader = lambda url, t, keep_links=False: (_ for _ in ()).throw(RuntimeError("blocked"))

    def tearDown(self):
        fetch_mod.fetch_direct, fetch_mod.fetch_reader, fetch_mod.fetch_archive = self.orig
        self.site.close()
        os.environ["EXA_MCP_URL"] = DEAD_VENDOR + "/mcp"
        os.environ["FIRECRAWL_SCRAPE_URL"] = DEAD_VENDOR + "/v2/scrape"

    def _fetch(self, **kw):
        kw.setdefault("timeout", 10)
        kw.setdefault("deadline", 20)
        return fetch_mod.fetch(f"{self.site.base}/page", ttl=0, retries=0, **kw)

    def test_exa_answers_when_the_other_vendors_fail(self):
        self.site.vendor_status = 503
        res = self._fetch()
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["source"], "vendor:exa")
        self.assertEqual(res["title"], "Exa Page")
        self.assertGreater(res["chars"], 400)
        self.assertEqual(self.site.exa_requests, 1)

    def test_request_is_a_jsonrpc_call_with_a_real_character_budget(self):
        self.site.vendor_status = 503
        self._fetch()
        body = self.site.exa_bodies[0]
        self.assertEqual(body["method"], "tools/call")
        self.assertEqual(body["params"]["name"], "web_fetch_exa")
        args = body["params"]["arguments"]
        self.assertEqual(args["urls"], [f"{self.site.base}/page"])
        # the default is ~3,000 chars; a fetch stage must ask for far more
        self.assertGreaterEqual(args["maxCharacters"], 20000)

    def test_server_sent_events_body_is_parsed(self):
        self.site.vendor_status = 503
        self.site.exa_sse = True
        res = self._fetch()
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["source"], "vendor:exa")
        self.assertIn("page body from exa", res["text"])

    def test_exa_runs_before_wayback(self):
        calls = []
        fetch_mod.fetch_archive = lambda url, t, keep_links=False: (
            calls.append("archive") or (_ for _ in ()).throw(RuntimeError("no snapshot")))
        self.site.vendor_status = 503
        res = self._fetch()
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["source"], "vendor:exa")
        self.assertEqual(calls, [], "Wayback was tried before Exa")

    def test_jsonrpc_error_fails_over_to_wayback(self):
        self.site.exa_payload = {"jsonrpc": "2.0", "id": 1,
                                 "error": {"code": -32000, "message": "rate limited"}}
        self.site.vendor_status = 503
        fetch_mod.fetch_archive = lambda url, t, keep_links=False: {
            "title": "archived", "text": "archive copy " * 40,
            "meta": {"content_type": "markdown", "vendor": "wayback", "status_code": 200},
            "links": [], "source": "archive:wayback", "final_url": url,
            "status": 200, "elapsed": 0.1}
        res = self._fetch()
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["source"], "archive:wayback")
        self.assertTrue(any("exa" in e for e in res["errors"]), res["errors"])

    def test_exa_carries_our_identity_and_no_credential(self):
        self.site.vendor_status = 503
        self._fetch()
        headers = {k.lower(): v for k, v in self.site.exa_headers.items()}
        self.assertNotIn("authorization", headers, "sent a credential Exa never asked for")
        self.assertIn("keyless-agent-skills", headers.get("user-agent", ""))
        self.assertIn("text/event-stream", headers.get("accept", ""))

    def test_no_vendor_disables_the_exa_stage_too(self):
        res = self._fetch(use_vendor=False, use_archive=False)
        self.assertFalse(res["ok"])
        self.assertEqual(self.site.exa_requests, 0)

    def test_exa_is_never_retried(self):
        """One attempt per borrowed endpoint, even when it errors."""
        self.site.vendor_status = 503
        self.site.exa_payload = {"jsonrpc": "2.0", "id": 1,
                                 "error": {"code": -1, "message": "slow down"}}
        res = self._fetch(use_archive=False)
        self.assertFalse(res["ok"])
        self.assertEqual(self.site.exa_requests, 1, res["errors"])

    def test_exa_preamble_is_stripped(self):
        self.site.vendor_status = 503
        res = self._fetch()
        self.assertNotIn("Published: 2026-10-01", res["text"])
        self.assertNotIn("URL: https://example.test/paid", res["text"])
        self.assertTrue(res["text"].startswith("# Exa Page"), res["text"][:80])


class StageReportingTests(unittest.TestCase):
    """`deadline_exceeded` means "the call was cut", not "a stage was cut".

    A call that skips a hung stage and still returns a page is a success; the
    abandoned stages belong in `stages_cut`, otherwise every g2.com fetch looks
    like a partial answer and agents retry work that already succeeded.
    """

    def setUp(self):
        self.site = _Site()
        os.environ["FIRECRAWL_SCRAPE_URL"] = f"{self.site.base}/v2/scrape"
        self.original_reader = fetch_mod.fetch_reader

    def tearDown(self):
        fetch_mod.fetch_reader = self.original_reader
        self.site.close()
        os.environ["FIRECRAWL_SCRAPE_URL"] = DEAD_VENDOR + "/v2/scrape"

    def test_success_after_a_cut_stage_is_not_a_deadline_trip(self):
        self.site.page_html = "<html><body>too thin</body></html>"
        self.site.vendor_payload = VENDOR_OK
        fetch_mod.fetch_reader = lambda url, t, keep_links=False: time.sleep(60)
        res = fetch_mod.fetch(f"{self.site.base}/page", timeout=30, deadline=6, ttl=0,
                              use_archive=False, retries=0)
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["source"], "vendor:firecrawl")
        self.assertFalse(res["deadline_exceeded"], res)
        self.assertIn("reader", res["stages_cut"], res)
        self.assertNotIn("vendor", res["stages_cut"], res)

    def test_clean_success_reports_no_cut_stages(self):
        self.site.page_html = ("<html><head><title>fast</title></head><body><p>"
                               + "fast page text " * 40 + "</p></body></html>")
        res = fetch_mod.fetch(f"{self.site.base}/fast", timeout=5, deadline=10, ttl=0,
                              use_reader=False, use_archive=False, retries=0)
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["deadline_exceeded"], False)
        self.assertEqual(res["stages_cut"], [])

    def test_total_miss_still_flags_the_deadline_and_names_the_stage(self):
        hang = _HangingServer()
        try:
            res = fetch_mod.fetch(f"http://127.0.0.1:{hang.port}/never",
                                  timeout=30, deadline=2, ttl=0,
                                  use_reader=False, use_archive=False, use_vendor=False,
                                  retries=0)
        finally:
            hang.close()
        self.assertFalse(res["ok"])
        self.assertTrue(res["deadline_exceeded"], res)
        self.assertIn("direct", res["stages_cut"], res)

    def test_cli_json_carries_both_fields(self):
        self.site.page_html = ("<html><head><title>fast</title></head><body><p>"
                               + "cli page text " * 40 + "</p></body></html>")
        out = subprocess.run([sys.executable, str(FETCH_PY), f"{self.site.base}/cli",
                              "--json", "--quiet", "--no-cache"],
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr[-800:])
        row = json.loads(out.stdout)[0]
        self.assertTrue(row["ok"], row)
        self.assertIn("stages_cut", row)
        self.assertFalse(row["deadline_exceeded"])


class RewriteMappingTests(unittest.TestCase):
    """rewrite_candidates is pure: URL in, machine-readable twin out."""

    def test_github_blob_becomes_raw(self):
        out = fetch_mod.rewrite_candidates("https://github.com/FFmpeg/FFmpeg/blob/master/README.md")
        self.assertEqual(out[0][0], "gh-raw")
        self.assertEqual(out[0][1], "https://raw.githubusercontent.com/FFmpeg/FFmpeg/master/README.md")
        self.assertEqual(out[0][2], "raw")

    def test_github_repo_page_is_not_rewritten(self):
        self.assertEqual(fetch_mod.rewrite_candidates("https://github.com/FFmpeg/FFmpeg"), [])

    def test_stackoverflow_question_becomes_the_api(self):
        out = fetch_mod.rewrite_candidates("https://stackoverflow.com/questions/1976007/what-chars")
        self.assertEqual(out[0][0], "stackexchange")
        self.assertEqual(out[0][1],
                         "https://api.stackexchange.com/2.3/questions/1976007"
                         "?site=stackoverflow&filter=withbody")

    def test_subdomain_stackexchange_keeps_its_site(self):
        out = fetch_mod.rewrite_candidates("https://math.stackexchange.com/questions/42/is-it-true")
        self.assertIn("site=math", out[0][1])

    def test_wikipedia_gets_the_rest_summary(self):
        out = fetch_mod.rewrite_candidates("https://en.wikipedia.org/wiki/FFmpeg")
        self.assertEqual(out[0][0], "wikipedia-summary")
        self.assertTrue(out[0][1].startswith("https://en.wikipedia.org/api/rest_v1/page/summary/"))

    def test_youtube_watch_and_short_forms(self):
        a = fetch_mod.rewrite_candidates("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        b = fetch_mod.rewrite_candidates("https://youtu.be/dQw4w9WgXcQ")
        self.assertEqual(a[0][0], b[0][0], "watch and youtu.be must map to the same rewrite")
        self.assertIn("v=dQw4w9WgXcQ", a[0][1])

    def test_x_tries_the_api_first_then_the_mirrors(self):
        out = fetch_mod.rewrite_candidates("https://x.com/rustlang/status/1")
        self.assertEqual([c[0] for c in out],
                         ["x-post", "nitter", "nitter-privacydev", "xcancel"])
        before, after = fetch_mod.order_rewrites(out)
        self.assertEqual([c[0] for c in before], ["x-post"], "the JSON API must run before direct")
        self.assertEqual(len(after), 3, "the mirrors stay as fallbacks")

    def test_arxiv_abs_gets_the_pdf(self):
        out = fetch_mod.rewrite_candidates("https://arxiv.org/abs/1706.03762")
        self.assertEqual(out[0][1], "https://arxiv.org/pdf/1706.03762")

    def test_ordinary_urls_have_no_twin(self):
        for u in ("https://example.com/a/b", "https://docs.python.org/3/library/asyncio.html",
                  "https://news.ycombinator.com/item?id=1"):
            self.assertEqual(fetch_mod.rewrite_candidates(u), [], u)

    def test_priority_split(self):
        before, after = fetch_mod.order_rewrites(
            fetch_mod.rewrite_candidates("https://github.com/a/b/blob/c/d.md"))
        self.assertEqual([c[0] for c in before], ["gh-raw"])
        before, after = fetch_mod.order_rewrites(
            fetch_mod.rewrite_candidates("https://en.wikipedia.org/wiki/X"))
        self.assertEqual(before, [])
        self.assertEqual([c[0] for c in after], ["wikipedia-summary"])


class RewriteFetchTests(unittest.TestCase):
    """The fetching half: renderers, priority, and running before the direct stage."""

    def setUp(self):
        self.site = _Site()

    def tearDown(self):
        self.site.close()

    def test_github_raw_is_served_before_direct(self):
        target = f"{self.site.base}/README.md"
        self.site.page_html = "# README\n\n" + "real file content " * 40
        orig = (fetch_mod.rewrite_candidates, fetch_mod.fetch_direct)
        fetch_mod.rewrite_candidates = lambda url: [("gh-raw", target, "raw")]
        fetch_mod.fetch_direct = lambda url, t, keep_links=False: (_ for _ in ()).throw(
            RuntimeError("github nav sandwich"))
        try:
            res = fetch_mod.fetch(f"{self.site.base}/blob/page", timeout=10, deadline=20, ttl=0,
                                  use_reader=False, use_vendor=False, use_archive=False, retries=0)
        finally:
            fetch_mod.rewrite_candidates, fetch_mod.fetch_direct = orig
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["source"], "rewrite:gh-raw")
        self.assertIn("real file content", res["text"])
        self.assertEqual(res["title"], "README.md")

    def test_stackexchange_json_is_rendered_as_a_qa_page(self):
        payload = {"items": [
            {"title": "What characters are forbidden?&lt;b&gt;", "link": "https://so.test/q/1",
             "score": 42, "answer_count": 3, "is_answered": True,
             "body": "<p>The forbidden characters are <code>&lt; &gt; : &quot;</code></p>"},
            {"answer_id": 9, "score": 17, "is_accepted": True,
             "owner": {"display_name": "someone"},
             "body": "<p>Use a whitelist.</p>"},
        ]}
        self.site.exa_payload = payload                     # reused generic JSON responder
        res = fetch_mod.fetch_rewrite("stackexchange", f"{self.site.base}/json", "json", 10)
        self.assertEqual(res["source"], "rewrite:stackexchange")
        self.assertIn("# What characters are forbidden?", res["text"])
        self.assertIn("42 votes", res["text"])
        self.assertIn("## Answer 1 (accepted)", res["text"])
        self.assertIn("Use a whitelist.", res["text"])
        self.assertNotIn("<p>", res["text"])

    def test_stackexchange_with_no_items_is_an_error(self):
        self.site.exa_payload = {"items": []}
        with self.assertRaises(RuntimeError):
            fetch_mod.fetch_rewrite("stackexchange", f"{self.site.base}/json", "json", 10)

    def test_wikipedia_summary_says_it_is_only_a_summary(self):
        self.site.exa_payload = {"title": "FFmpeg", "extract": "FFmpeg is a free software project."}
        res = fetch_mod.fetch_rewrite("wikipedia-summary", f"{self.site.base}/json", "json", 10)
        self.assertIn("# FFmpeg", res["text"])
        self.assertIn("lede only", res["text"])
        self.assertTrue(res["meta"].get("summary"))

    def test_youtube_oembed_does_not_pretend_to_be_the_video(self):
        self.site.exa_payload = {"title": "A video", "author_name": "A channel"}
        res = fetch_mod.fetch_rewrite("youtube-oembed", f"{self.site.base}/json", "json", 10)
        self.assertIn("by A channel", res["text"])
        self.assertIn("not the video", res["text"])


class WallTests(unittest.TestCase):
    """A relay answering 200 with a wall is not an answer."""

    def test_signup_walls_are_named(self):
        page = ("Final URL: https://stackoverflow.com/questions/1\n\n"
                "# Join Stack Overflow\n\nSign up with Google\nSign up with GitHub\n"
                + "filler " * 200)
        self.assertEqual(fetch_mod.wall_reason(page), "sign up with google")

    def test_challenge_pages_are_named(self):
        page = "<title>Just a moment...</title> Checking your browser before accessing" + " x" * 400
        self.assertIsNotNone(fetch_mod.wall_reason(page))

    def test_relay_error_wrappers_are_named(self):
        page = "Title: \n\nWarning: Target URL returned error 403: Forbidden\n"
        self.assertIsNotNone(fetch_mod.wall_reason(page))

    def test_real_pages_are_not_walls(self):
        for page in ("# Documentation\n\nInstall with pip install ffmpeg-python.\n" + "text " * 300,
                     "# Sign in with Google — OAuth guide\n\nThis page explains the flow. "
                     + "Long body. " * 300):
            self.assertIsNone(fetch_mod.wall_reason(page), page[:50])

    def test_a_wall_stage_is_rejected_and_the_chain_moves_on(self):
        site = _Site()
        site.page_html = ("<html><body><h1>Join Stack Overflow</h1>"
                          "<a>Sign up with Google</a> Sign up with GitHub"
                          + "<p>" + "navigation boilerplate " * 40 + "</p></body></html>")
        try:
            res = fetch_mod.fetch(f"{site.base}/walled", timeout=5, deadline=8, ttl=0,
                                  use_reader=False, use_vendor=False, use_archive=False,
                                  use_rewrite=False, retries=0)
        finally:
            site.close()
        self.assertFalse(res["ok"], res)
        self.assertTrue(any("wall page" in e for e in res["errors"]), res["errors"])
        self.assertEqual(res["text"], "")


class StdlibPdfTests(unittest.TestCase):
    """PDF text without poppler or pypdf — the Termux / slim container case."""

    @staticmethod
    def _mini_pdf(text=b"Hello PDF world"):
        content = b"BT /F1 12 Tf 72 720 Td (" + text + b") Tj ET"
        comp = zlib.compress(content)
        head = (b"%PDF-1.7\n1 0 obj\n<< /Length " + str(len(comp)).encode()
                + b" /Filter /FlateDecode >>\nstream\n")
        return head + comp + b"\nendstream\nendobj\n%%EOF\n"

    def test_flate_decode_stream_is_read(self):
        out = fetch_mod.pdf_extract_stdlib(self._mini_pdf())
        self.assertIn("Hello PDF world", out)

    def test_tex_ligature_codes_are_decoded(self):
        # \x0c is TeX's T1 slot for the "fi" ligature: "efficient" arrives as e\x0ccient
        out = fetch_mod.pdf_extract_stdlib(self._mini_pdf(b"e\x0ecient e\x0cne"))
        self.assertIn("efficient", out)
        self.assertIn("efine", out)

    def test_garbage_pdf_returns_empty_rather_than_raising(self):
        self.assertEqual(fetch_mod.pdf_extract_stdlib(b"not a pdf at all"), "")

    def test_pdf_to_text_falls_back_when_pdftotext_and_pypdf_are_missing(self):
        # a real page's worth of text, not one line: the fallback only accepts a
        # result long enough to be an answer rather than a stray caption
        orig_which = fetch_mod.shutil.which
        orig_pypdf = sys.modules.get("pypdf")
        fetch_mod.shutil.which = lambda name: None
        sys.modules["pypdf"] = None                 # makes `import pypdf` raise ImportError
        try:
            txt = fetch_mod.pdf_to_text(self._mini_pdf(b"Hello PDF world " * 8))
        finally:
            fetch_mod.shutil.which = orig_which
            if orig_pypdf is None:
                sys.modules.pop("pypdf", None)
            else:
                sys.modules["pypdf"] = orig_pypdf
        self.assertTrue(txt and "Hello PDF world" in txt, txt)

    def test_a_broken_pypdf_does_not_hide_the_builtin_parser(self):
        """CI installs pypdf; if it raises on a file, the next extractor must run."""
        import types

        class Boom:
            def __init__(self, *a, **k):
                raise RuntimeError("malformed xref")

        orig_which = fetch_mod.shutil.which
        orig_pypdf = sys.modules.get("pypdf")
        fetch_mod.shutil.which = lambda name: None
        sys.modules["pypdf"] = types.SimpleNamespace(PdfReader=Boom)
        try:
            txt = fetch_mod.pdf_to_text(self._mini_pdf(b"Hello PDF world " * 8))
        finally:
            fetch_mod.shutil.which = orig_which
            if orig_pypdf is None:
                sys.modules.pop("pypdf", None)
            else:
                sys.modules["pypdf"] = orig_pypdf
        self.assertTrue(txt and "Hello PDF world" in txt, txt)


REDDIT_POST = {"data": [{
    "id": "abc123", "subreddit": "pics", "author": "someone", "title": "A sunset &amp; a cat",
    "selftext": "", "score": 1234, "num_comments": 2, "created_utc": 1793634911,
    "url": "https://i.redd.it/aaa.jpeg", "is_self": False, "post_hint": "image",
    "permalink": "/r/pics/comments/abc123/a_sunset/", "is_gallery": False,
    "over_18": False,
}]}

REDDIT_GALLERY = {"data": [{
    "id": "gal123", "subreddit": "pics", "author": "someone", "title": "Four shots",
    "selftext": "some context", "score": 5, "num_comments": 0, "created_utc": 1793634911,
    "is_gallery": True, "url": "https://www.reddit.com/gallery/gal123",
    "gallery_data": {"items": [{"media_id": "m2"}, {"media_id": "m1"}]},
    "media_metadata": {
        "m1": {"s": {"u": "https://i.redd.it/one.jpeg", "x": 100, "y": 200}},
        "m2": {"s": {"u": "https://i.redd.it/two.jpeg?width=99", "x": 300, "y": 400}},
    },
}]}

REDDIT_COMMENTS = {"data": [
    {"body": "low score take", "score": 2, "author": "b"},
    {"body": "the best answer", "score": 90, "author": "a"},
    {"body": "[removed]", "score": 50, "author": "c"},
]}

REDDIT_LISTING = {"data": [
    {"id": "p1", "subreddit": "ffmpeg", "author": "u1", "title": "ffmpeg.org down?",
     "score": 3, "num_comments": 7, "created_utc": 1793634911, "permalink": "/r/ffmpeg/comments/p1/x/",
     "url": "https://i.redd.it/x.jpeg"},
    {"id": "p2", "subreddit": "ffmpeg", "author": "u2", "title": "how to zoompan",
     "score": 9, "num_comments": 1, "created_utc": 1793634800, "permalink": "/r/ffmpeg/comments/p2/y/",
     "url": "https://www.reddit.com/r/ffmpeg/"},
]}

X_TWEET = {"code": 200, "message": "OK", "tweet": {
    "url": "https://x.com/someone/status/2101", "id": "2101",
    "text": "look at this", "likes": 46979, "replies": 869, "reposts": 12, "views": 9559914,
    "created_at": "Sun Sep 20 00:21:08 +0000 2026",
    "author": {"name": "Someone", "screen_name": "someone"},
    "media": {
        "all": [{"type": "photo", "url": "https://pbs.twimg.com/media/A.jpg?name=orig"}],
        "photos": [{"type": "photo", "url": "https://pbs.twimg.com/media/A.jpg?name=orig",
                    "width": 1170, "height": 1463},
                   {"type": "photo", "url": "https://pbs.twimg.com/media/B.jpg?name=orig",
                    "width": 1638, "height": 2048}],
        "videos": [{"type": "video", "url": "https://video.twimg.com/v/clip.mp4",
                    "width": 1280, "height": 720, "duration": 65.1}],
    },
    "quote": {"text": "the original", "author": {"screen_name": "quoted"}},
}}


class RedditTargetTests(unittest.TestCase):
    """Pure URL mapping: every shape of reddit link, and the one that cannot work."""

    def test_post_urls(self):
        for u in ("https://www.reddit.com/r/pics/comments/1xm0xm3/oc_sunset/",
                  "https://old.reddit.com/r/pics/comments/1xm0xm3/",
                  "https://np.reddit.com/r/AskReddit/comments/17vdtx2/x/",
                  "https://redd.it/1xm0xm3",
                  "https://www.reddit.com/gallery/1xm0qvj"):
            tgt = fetch_mod.reddit_target(u)
            self.assertIsNotNone(tgt, u)
            self.assertEqual(tgt[0], "post", u)

    def test_id_is_extracted(self):
        self.assertEqual(fetch_mod.reddit_target(
            "https://www.reddit.com/r/pics/comments/1xm0xm3/oc_sunset/")[1], "1xm0xm3")
        self.assertEqual(fetch_mod.reddit_target("https://redd.it/1xm0xm3")[1], "1xm0xm3")

    def test_subreddit_and_user_listings(self):
        self.assertEqual(fetch_mod.reddit_target("https://www.reddit.com/r/pics/")[:2], ("listing", "pics"))
        self.assertEqual(fetch_mod.reddit_target("https://www.reddit.com/r/pics/new")[2], {"sort": "asc"})
        self.assertEqual(fetch_mod.reddit_target("https://www.reddit.com/user/someone/")[2], {"author": True})

    def test_share_links_are_refused_not_guessed(self):
        self.assertIsNone(fetch_mod.reddit_target("https://www.reddit.com/r/pics/s/AbCdEf"))

    def test_non_reddit_urls(self):
        self.assertIsNone(fetch_mod.reddit_target("https://example.com/r/pics/"))


class XTargetTests(unittest.TestCase):
    def test_status_urls(self):
        for u in ("https://x.com/Complex/status/2101466653175464354",
                  "https://twitter.com/NASA/status/1234567890123",
                  "https://mobile.twitter.com/a/status/999999999",
                  "https://x.com/i/status/2101466653175464354"):
            tgt = fetch_mod.x_target(u)
            self.assertIsNotNone(tgt, u)

    def test_id_is_extracted(self):
        self.assertEqual(fetch_mod.x_target("https://x.com/a/status/2101466653175464354")[1],
                         "2101466653175464354")

    def test_profiles_and_shorteners_are_refused(self):
        self.assertIsNone(fetch_mod.x_target("https://x.com/Complex"))
        self.assertIsNone(fetch_mod.x_target("https://t.co/abc"))


class RedditMediaTests(unittest.TestCase):
    def test_gallery_keeps_its_order_and_dimensions(self):
        media = fetch_mod._reddit_media(REDDIT_GALLERY["data"][0])
        self.assertEqual([m["url"] for m in media],
                         ["https://i.redd.it/two.jpeg?width=99", "https://i.redd.it/one.jpeg"])
        self.assertEqual((media[1]["width"], media[1]["height"]), (100, 200))

    def test_plain_image_post(self):
        media = fetch_mod._reddit_media(REDDIT_POST["data"][0])
        self.assertEqual([m["url"] for m in media], ["https://i.redd.it/aaa.jpeg"])

    def test_duplicates_are_collapsed(self):
        post = {"url": "https://i.redd.it/aaa.jpeg",
                "media_metadata": {"m": {"s": {"u": "https://i.redd.it/aaa.jpeg"}}}}
        self.assertEqual(len(fetch_mod._reddit_media(post)), 1)

    def test_video_fallback_url_is_media_too(self):
        post = {"is_video": True, "url": "https://v.redd.it/xyz",
                "media": {"reddit_video": {"fallback_url": "https://v.redd.it/xyz/DASH_720.mp4",
                                           "width": 1280, "height": 720}}}
        media = fetch_mod._reddit_media(post)
        self.assertEqual(media[0]["type"], "video")
        self.assertIn("DASH_720", media[0]["url"])

    def test_preview_is_the_last_resort(self):
        post = {"url": "https://example.com/article",
                "preview": {"images": [{"source": {"url": "https://preview.redd.it/x.jpg?s=sig",
                                                   "width": 10, "height": 20}}]}}
        media = fetch_mod._reddit_media(post)
        self.assertEqual(media[0]["url"], "https://preview.redd.it/x.jpg?s=sig")


class RedditFetchTests(unittest.TestCase):
    def setUp(self):
        self.site = _Site()
        self.site.json_routes = {
            "/api/posts/ids": REDDIT_POST,
            "/api/posts/search": REDDIT_LISTING,
            "/api/comments/search": REDDIT_COMMENTS,
        }
        self._arctic = os.environ.get("ARCTIC_SHIFT_URL")
        os.environ["ARCTIC_SHIFT_URL"] = self.site.base

    def tearDown(self):
        self.site.close()
        if self._arctic is None:
            os.environ.pop("ARCTIC_SHIFT_URL", None)
        else:
            os.environ["ARCTIC_SHIFT_URL"] = self._arctic

    def test_post_renders_title_comments_and_media(self):
        res = fetch_mod.fetch_reddit("https://www.reddit.com/r/pics/comments/abc123/x/", 10)
        self.assertEqual(res["source"], "reddit:post")
        self.assertIn("# A sunset & a cat", res["text"])          # entity decoded
        self.assertIn("1234 points", res["text"])
        self.assertIn("https://i.redd.it/aaa.jpeg", res["text"])
        self.assertEqual([m["url"] for m in res["media"]], ["https://i.redd.it/aaa.jpeg"])

    def test_comments_are_sorted_and_removed_ones_dropped(self):
        res = fetch_mod.fetch_reddit("https://www.reddit.com/r/pics/comments/abc123/x/", 10)
        body = res["text"]
        self.assertLess(body.index("the best answer"), body.index("low score take"))
        self.assertNotIn("[removed]", body)

    def test_gallery_post_exposes_every_image(self):
        self.site.json_routes["/api/posts/ids"] = REDDIT_GALLERY
        res = fetch_mod.fetch_reddit("https://www.reddit.com/gallery/gal123", 10)
        self.assertEqual(len(res["media"]), 2)
        self.assertIn("## Media", res["text"])

    def test_listing_renders_a_list(self):
        res = fetch_mod.fetch_reddit("https://www.reddit.com/r/ffmpeg/", 10)
        self.assertEqual(res["source"], "reddit:listing")
        self.assertIn("# r/ffmpeg", res["text"])
        self.assertIn("ffmpeg.org down?", res["text"])
        self.assertTrue(res["media"], "listing images should be collected too")

    def test_empty_archive_is_an_error_so_the_chain_moves_on(self):
        self.site.json_routes["/api/posts/ids"] = {"data": []}
        with self.assertRaises(RuntimeError):
            fetch_mod.fetch_reddit("https://www.reddit.com/r/pics/comments/dead00/x/", 10)

    def test_a_reddit_url_takes_the_reddit_stage_first(self):
        res = fetch_mod.fetch("https://www.reddit.com/r/pics/comments/abc123/x/", timeout=10,
                              deadline=20, ttl=0, use_reader=False, use_vendor=False,
                              use_archive=False, retries=0)
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["source"], "reddit:post")
        self.assertEqual(len(res["media"]), 1)


class XFetchTests(unittest.TestCase):
    def setUp(self):
        self.site = _Site()
        self.site.json_routes = {"/status/": X_TWEET}
        self._fx = os.environ.get("FXTWITTER_URL")
        os.environ["FXTWITTER_URL"] = self.site.base

    def tearDown(self):
        self.site.close()
        if self._fx is None:
            os.environ.pop("FXTWITTER_URL", None)
        else:
            os.environ["FXTWITTER_URL"] = self._fx

    def test_tweet_renders_text_photos_and_video(self):
        res = fetch_mod.fetch_x("https://x.com/someone/status/2101", 10)
        self.assertEqual(res["source"], "x:post")
        self.assertIn("Someone (@someone)", res["text"])
        self.assertIn("look at this", res["text"])
        kinds = [m["type"] for m in res["media"]]
        self.assertEqual(kinds, ["image", "image", "video"])
        self.assertIn("name=orig", res["media"][0]["url"])

    def test_video_duration_is_reported(self):
        res = fetch_mod.fetch_x("https://x.com/someone/status/2101", 10)
        self.assertIn("65s", res["text"])

    def test_quoted_tweet_is_summarised(self):
        res = fetch_mod.fetch_x("https://x.com/someone/status/2101", 10)
        self.assertIn("@quoted", res["text"])
        self.assertIn("the original", res["text"])

    def test_api_error_code_becomes_a_stage_error(self):
        self.site.json_routes["/status/"] = {"code": 404, "message": "NOT_FOUND"}
        with self.assertRaises(RuntimeError):
            fetch_mod.fetch_x("https://x.com/someone/status/404", 10)

    def test_an_x_url_takes_the_x_stage_first(self):
        res = fetch_mod.fetch("https://x.com/someone/status/2101", timeout=10, deadline=20,
                              ttl=0, use_reader=False, use_vendor=False, use_archive=False, retries=0)
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["source"], "x:post")
        self.assertEqual(len(res["media"]), 3)


class MediaDownloadTests(unittest.TestCase):
    def setUp(self):
        self.site = _Site()

    def tearDown(self):
        self.site.close()

    def test_files_are_written_with_sane_names(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            items = [{"url": f"{self.site.base}/media/A.jpg?name=orig", "type": "image"},
                     {"url": f"{self.site.base}/media/B.mp4", "type": "video"}]
            files = fetch_mod.download_media(items, d)
            self.assertEqual(len(files), 2)
            names = sorted(os.path.basename(f) for f in files)
            self.assertEqual(names[0][:2], "01")
            self.assertTrue(names[0].endswith(".jpg"))
            for f in files:
                self.assertTrue(os.path.getsize(f) > 100)

    def test_a_second_call_does_not_overwrite_the_first_pages_media(self):
        """`fetch.py a b --media DIR` used to write both pages into 01-…."""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            first = fetch_mod.download_media(
                [{"url": f"{self.site.base}/media/A.jpg", "type": "image"},
                 {"url": f"{self.site.base}/media/B.jpg", "type": "image"}], d)
            second = fetch_mod.download_media(
                [{"url": f"{self.site.base}/media/C.jpg", "type": "image"}], d)
            self.assertEqual(len(first), 2)
            self.assertEqual(len(second), 1)
            self.assertEqual(len(set(first + second)), 3, "no file may be reused")
            self.assertTrue(os.path.basename(second[0]).startswith("03"))
            self.assertEqual(len(os.listdir(d)), 3)

    def test_limit_and_bad_urls_are_respected(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            items = [{"url": "not-a-url"}] + [{"url": f"{self.site.base}/media/A.jpg"}] * 3
            files = fetch_mod.download_media(items, d, limit=2)
            self.assertEqual(len(files), 2)


class CacheIntegrityTests(unittest.TestCase):
    """The cache stores results; the caller's --max-chars must not be stored with them."""

    def setUp(self):
        import tempfile
        self.site = _Site()
        self.site.page_html = ("<html><head><title>long</title></head><body><p>"
                               + ("alpha beta gamma delta " * 200) + "</p></body></html>")
        self.tmp = tempfile.mkdtemp(prefix="fetchcache-")
        self._old_cache = fetch_mod.CACHE
        fetch_mod.CACHE = self.tmp

    def tearDown(self):
        import shutil
        fetch_mod.CACHE = self._old_cache
        shutil.rmtree(self.tmp, ignore_errors=True)
        self.site.close()

    def _fetch(self, **kw):
        return fetch_mod.fetch(f"{self.site.base}/page", timeout=10, deadline=15,
                               use_reader=False, use_archive=False, use_vendor=False,
                               retries=0, **kw)

    def test_a_capped_fetch_does_not_shrink_the_cached_page(self):
        """A small --max-chars used to poison the entry for every later caller."""
        small = self._fetch(ttl=600, max_chars=300)
        self.assertEqual(small["chars"], 300)
        self.assertGreater(small["full_chars"], 300, "caller must be told it got a slice")

        big = self._fetch(ttl=600, max_chars=5000)
        self.assertTrue(big.get("cached"), "second call should hit the cache")
        self.assertEqual(
            small["full_chars"], big["chars"],
            "a later call must see the whole cached text, not the earlier slice")

    def test_the_cached_entry_keeps_the_full_text(self):
        self._fetch(ttl=600, max_chars=100)
        entry = json.loads(open(fetch_mod.cache_path(f"{self.site.base}/page")).read())
        self.assertGreater(len(entry["text"]), 1000)
        self.assertEqual(entry["_schema"], fetch_mod.CACHE_SCHEMA)

    def test_an_entry_from_an_older_schema_is_ignored(self):
        """1.x entries hold truncated text; serving them would re-introduce the bug."""
        url = f"{self.site.base}/page"
        os.makedirs(fetch_mod.CACHE, exist_ok=True)
        legacy = {"url": url, "ok": True, "text": "truncated", "source": "direct", "_ts": time.time()}
        with open(fetch_mod.cache_path(url), "w") as f:
            json.dump(legacy, f)
        res = self._fetch(ttl=600, max_chars=5000)
        self.assertNotEqual(res["text"], "truncated", "a legacy entry must not be served")
        self.assertGreater(res["chars"], 1000)

    def test_a_write_that_dies_midway_leaves_the_old_entry_and_no_debris(self):
        """The torn-file case: rename is atomic, so the previous entry survives."""
        url = f"{self.site.base}/page"
        self._fetch(ttl=600)
        path = fetch_mod.cache_path(url)
        with open(path) as f:
            before = f.read()

        real_dump = json.dump

        def dying_dump(obj, fp, **kw):
            fp.write('{"half": ')       # a plausible half-written entry
            raise OSError("disk full")

        json.dump = dying_dump
        try:
            fetch_mod.cache_put(url, {"text": "new text", "ok": True})   # must not raise
        finally:
            json.dump = real_dump

        with open(path) as f:
            self.assertEqual(f.read(), before, "a failed write must not damage the old entry")
        self.assertEqual([n for n in os.listdir(fetch_mod.CACHE) if n.startswith(".tmp-")], [],
                         "a failed write must not leave a temp file behind")
        json.loads(before)              # and the survivor is still valid JSON


class BodyLimitTests(unittest.TestCase):
    """One hostile URL must not be able to exhaust the agent's memory."""

    def setUp(self):
        self.site = _Site()
        self._caps = (fetch_mod.MAX_BODY, fetch_mod.MAX_PDF, fetch_mod.MAX_INFLATED)

    def tearDown(self):
        fetch_mod.MAX_BODY, fetch_mod.MAX_PDF, fetch_mod.MAX_INFLATED = self._caps
        self.site.close()

    def _fetch(self, path):
        return fetch_mod.fetch(f"{self.site.base}{path}", timeout=10, deadline=15, ttl=0,
                               use_reader=False, use_archive=False, use_vendor=False,
                               retries=0)

    def test_an_oversized_body_is_refused(self):
        fetch_mod.MAX_BODY = fetch_mod.MAX_PDF = 4096
        self.site.raw_routes["/huge"] = (200, "text/html", b"<p>" + b"x" * 200000 + b"</p>")
        res = self._fetch("/huge")
        self.assertFalse(res["ok"], res)
        self.assertTrue(any("refused" in e for e in res["errors"]), res["errors"])

    def test_a_gzip_bomb_is_refused(self):
        fetch_mod.MAX_INFLATED = 8192
        bomb = zlib.compressobj(9, zlib.DEFLATED, 16 + zlib.MAX_WBITS)
        body = b"".join(bomb.compress(b"a" * 65536) for _ in range(64)) + bomb.flush()
        self.site.raw_routes["/bomb"] = (200, "text/html", body, {"Content-Encoding": "gzip"})
        self.assertGreater(len(body), 0)
        res = self._fetch("/bomb")
        self.assertFalse(res["ok"], res)
        self.assertTrue(any("gunzipped" in e for e in res["errors"]), res["errors"])

    def test_a_body_under_the_cap_still_works(self):
        fetch_mod.MAX_BODY = fetch_mod.MAX_PDF = 65536
        self.site.page_html = "<html><body><p>" + ("fine page text " * 20) + "</p></body></html>"
        res = self._fetch("/page")
        self.assertTrue(res["ok"], res)


class MediaSniffTests(unittest.TestCase):
    """An error page is not a picture, however the CDN labelled it."""

    def setUp(self):
        self.site = _Site()

    def tearDown(self):
        self.site.close()

    def test_media_kind_goes_by_bytes_first(self):
        self.assertEqual(fetch_mod.media_kind(b"\xff\xd8\xff\xe0", "text/html"), "jpeg")
        self.assertEqual(fetch_mod.media_kind(b"\x89PNG\r\n", ""), "png")
        self.assertEqual(fetch_mod.media_kind(b"RIFF\x00\x00\x00\x00WEBPVP8 ", ""), "webp")
        self.assertEqual(fetch_mod.media_kind(b"\x00\x00\x00\x18ftypisom", ""), "mp4")
        self.assertEqual(fetch_mod.media_kind(b"", "image/png"), "png")
        self.assertEqual(fetch_mod.media_kind(b"<html>nope", "text/html"), "")

    def test_an_html_error_page_is_not_saved_as_media(self):
        import tempfile
        self.site.raw_routes["/media/blocked.mp4"] = (
            200, "text/html", b"<html><body>rate limited</body></html>")
        with tempfile.TemporaryDirectory() as d:
            files = fetch_mod.download_media(
                [{"url": f"{self.site.base}/media/blocked.mp4", "type": "video"}], d)
            self.assertEqual(files, [], "an HTML body must not be written as a video")
            self.assertEqual(os.listdir(d), [])


class UrlSchemeTests(unittest.TestCase):
    """A URL fetcher must not be a local-file reader.

    `fetch.py file:///etc/passwd` used to return the file as clean markdown with
    ok=True. That is a one-line instruction away from handing an agent an SSH key
    that a web page told it to read.
    """

    def test_only_http_and_https_are_fetched(self):
        for url in ("file:///etc/passwd", "ftp://example.com/x", "data:text/html,hi",
                    "javascript:alert(1)", "//evil.example/x", "gopher://x/1"):
            with self.assertRaises(ValueError, msg=url):
                fetch_mod.check_url(url)

    def test_valid_urls_pass_and_are_stripped(self):
        self.assertEqual(fetch_mod.check_url("  https://example.com/a  "),
                         "https://example.com/a")
        self.assertEqual(fetch_mod.check_url("HTTP://example.com"), "HTTP://example.com")

    def test_empty_input_is_refused(self):
        for url in ("", "   ", None, 42):
            with self.assertRaises(ValueError):
                fetch_mod.check_url(url)

    def test_fetch_refuses_before_touching_the_cache(self):
        import tempfile
        d = tempfile.mkdtemp(prefix="schemecache-")
        old = fetch_mod.CACHE
        fetch_mod.CACHE = d
        try:
            with self.assertRaises(ValueError):
                fetch_mod.fetch("file:///etc/passwd", ttl=600)
            self.assertEqual(os.listdir(d), [], "a refused URL must not write a cache entry")
        finally:
            fetch_mod.CACHE = old
            import shutil
            shutil.rmtree(d, ignore_errors=True)

    def test_the_cli_reports_a_refusal_instead_of_crashing(self):
        out = subprocess.run([sys.executable, str(FETCH_PY), "file:///etc/passwd",
                              "--deadline", "5", "--ttl", "0"],
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 2, out.stderr)
        self.assertIn("only http and https", out.stdout + out.stderr)
        self.assertNotIn("Traceback", out.stderr)
        self.assertNotIn("root:", out.stdout, "must not leak /etc/passwd")


class FrontmatterTests(unittest.TestCase):
    """Page titles are attacker-controlled; they must not break the YAML block.

    A title as ordinary as "Python: The Basics" made the frontmatter unparseable
    ("mapping values are not allowed here"), which breaks any agent that reads
    the metadata of a file this tool wrote.
    """

    def _frontmatter(self, res):
        md = fetch_mod.to_markdown(res)
        self.assertTrue(md.startswith("---\n"))
        block = md.split("---\n")[1]
        return block, md

    def test_a_colon_in_the_title_stays_quoted(self):
        block, md = self._frontmatter({"url": "https://e.example/a", "title": "Python: The Basics",
                                       "text": "body", "chars": 4, "source": "direct"})
        line = next(l for l in block.splitlines() if l.startswith("title:"))
        self.assertEqual(json.loads(line[len("title: "):]), "Python: The Basics")

    def test_a_newline_in_the_title_cannot_close_the_block(self):
        block, md = self._frontmatter({"url": "https://e.example/a",
                                       "title": "evil\n---\ngenerated: 1999",
                                       "text": "body", "chars": 4, "source": "direct"})
        self.assertNotIn("---", [l.strip() for l in block.splitlines()],
                         "no line may look like a frontmatter close")
        line = next(l for l in block.splitlines() if l.startswith("title:"))
        self.assertEqual(json.loads(line[len("title: "):]), "evil\n---\ngenerated: 1999")
        self.assertEqual(md.count("\n---\n"), 1, "the block must still close exactly once")

    def test_credentials_in_a_url_are_not_written_out(self):
        block, md = self._frontmatter({"url": "https://user:hunter2@e.example/a",
                                       "text": "body", "chars": 4, "source": "direct"})
        self.assertNotIn("hunter2", block)
        self.assertIn("user:***@", block)

    def test_a_link_with_a_newline_or_a_bracket_cannot_break_the_list(self):
        md = fetch_mod.to_markdown({"url": "https://e.example/a", "text": "body", "chars": 4,
                                    "source": "direct", "title": "t",
                                    "links": [("has\nnewline] and [brackets", "https://e.example/a b(c)")]})
        item = [l for l in md.splitlines() if l.startswith("- [")][0]
        self.assertNotIn("\n", item)
        self.assertEqual(item.count("]"), 1, f"a stray ] breaks the link: {item}")
        self.assertEqual(item.index("]"), item.index("]("), "the only ] must close the text")
        self.assertNotIn(" ", item.split("](")[1].rstrip(")"))

    def test_redact_leaves_ordinary_urls_alone(self):
        self.assertEqual(fetch_mod.redact("https://e.example/a?b=1"),
                         "https://e.example/a?b=1")
        self.assertEqual(fetch_mod.redact("https://u:p@e.example:8443/x"),
                         "https://u:***@e.example:8443/x")


class CliContractTests(unittest.TestCase):
    """Flags that must be refused, and combinations that must stay parseable."""

    def setUp(self):
        self.site = _Site()
        self.site.page_html = ("<html><head><title>t</title></head><body><p>"
                               + "cli contract text " * 30 + "</p></body></html>")

    def tearDown(self):
        self.site.close()

    def _cli(self, *args):
        return subprocess.run([sys.executable, str(FETCH_PY), *args],
                              capture_output=True, text=True)

    def test_impossible_numbers_are_refused_not_silently_obeyed(self):
        for flag, value in (("--parallel", "0"), ("--parallel", "-3"),
                            ("--max-chars", "0"), ("--max-chars", "-5"),
                            ("--timeout", "0"), ("--deadline", "0"), ("--ttl", "-1")):
            out = self._cli(f"{self.site.base}/page", flag, value)
            self.assertEqual(out.returncode, 2, f"{flag} {value} was accepted")
            self.assertNotIn("Traceback", out.stderr)
            self.assertIn("must be", out.stderr + out.stdout)

    def test_json_with_text_is_still_json(self):
        out = self._cli(f"{self.site.base}/page", "--json", "--text", "--ttl", "0",
                        "--deadline", "10", "--no-reader", "--no-archive", "--no-vendor")
        payload = json.loads(out.stdout)          # the whole point: it must parse
        self.assertIn("text", payload[0])
        self.assertNotIn("=====", out.stdout)

    def test_out_pointing_at_a_file_is_refused_before_the_fetch(self):
        import tempfile
        with tempfile.NamedTemporaryFile() as fh:
            out = self._cli(f"{self.site.base}/page", "--out", fh.name)
            self.assertNotEqual(out.returncode, 0)
            self.assertNotIn("Traceback", out.stderr)
            self.assertIn("not a directory", out.stderr)


class UnitTests(unittest.TestCase):
    def test_stage_timeout_is_a_runtime_error(self):
        self.assertTrue(issubclass(fetch_mod.StageTimeout, RuntimeError))

    def test_run_with_budget_returns_the_value(self):
        self.assertEqual(fetch_mod._run_with_budget(lambda: 42, 5), 42)

    def test_run_with_budget_propagates_exceptions(self):
        with self.assertRaises(ValueError):
            fetch_mod._run_with_budget(lambda: (_ for _ in ()).throw(ValueError("boom")), 5)

    def test_run_with_budget_abandons_a_hung_callable(self):
        t0 = time.monotonic()
        with self.assertRaises(fetch_mod.StageTimeout):
            fetch_mod._run_with_budget(lambda: time.sleep(30), 1, "hang")
        self.assertLess(time.monotonic() - t0, 2.5)


if __name__ == "__main__":
    unittest.main(verbosity=2)

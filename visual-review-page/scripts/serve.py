#!/usr/bin/env python3
"""Serve a review page folder on 127.0.0.1 and store its answers in answers.json.

Usage: python3 serve.py PAGE_DIR [--port N]

GET  /answers  -> the current answers.json ({} when absent)
POST /answers  -> body {"id": str, "text": str}; merged into answers.json, returns {"ok": true}
Everything else is served as static files from PAGE_DIR.
"""

import argparse
import json
import os
import re
import tempfile
import threading
from datetime import datetime, timezone
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

MAX_BODY = 1_000_000
RANGE = re.compile(r"bytes=(\d*)-(\d*)")
lock = threading.Lock()


def read_answers(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def write_answers(path, answers):
    # Atomic replace so the agent never reads a half-written file.
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(answers, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, answers_path, **kwargs):
        self.answers_path = answers_path
        super().__init__(*args, **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("Accept-Ranges", "bytes")
        super().end_headers()

    def send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # Only the page itself may talk to the server: a foreign Host means DNS rebinding, and a foreign
    # Origin means another site in the browser trying to read answers or plant a fake one.
    def trusted(self):
        port = self.server.server_address[1]
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        origin = self.headers.get("Origin")
        return self.headers.get("Host") in hosts and (origin is None or origin in {f"http://{h}" for h in hosts})

    def do_GET(self):
        if not self.trusted():
            return self.send_json(403, {"error": "forbidden"})
        if self.path.split("?")[0] == "/answers":
            with lock:
                return self.send_json(200, read_answers(self.answers_path))
        path = self.translate_path(self.path)
        match = RANGE.fullmatch(self.headers.get("Range", "").strip())
        if match and any(match.groups()) and os.path.isfile(path):
            return self.send_range(path, *match.groups())
        return super().do_GET()

    # Browsers need byte ranges to seek audio and video; without them the A/B switch restarts at 0.
    def send_range(self, path, first, last):
        size = os.path.getsize(path)
        if first:
            start, end = int(first), min(int(last) if last else size - 1, size - 1)
        else:
            start, end = max(0, size - int(last)), size - 1
        if start > end:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.send_header("Content-Length", "0")
            return self.end_headers()
        self.send_response(206)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        try:
            with open(path, "rb") as f:
                f.seek(start)
                remaining = end - start + 1
                while remaining:
                    chunk = f.read(min(65536, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass  # the browser dropped a range it no longer needs

    def do_HEAD(self):
        if not self.trusted():
            return self.send_json(403, {"error": "forbidden"})
        return super().do_HEAD()

    def do_POST(self):
        if not self.trusted():
            return self.send_json(403, {"error": "forbidden"})
        if self.path.split("?")[0] != "/answers":
            return self.send_json(404, {"error": "not found"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > MAX_BODY:
                return self.send_json(413, {"error": "too large"})
            data = json.loads(self.rfile.read(length) or b"{}")
            item_id, text = data["id"], data["text"]
            if not isinstance(item_id, str) or not item_id or not isinstance(text, str):
                raise ValueError
        except (ValueError, KeyError, json.JSONDecodeError):
            return self.send_json(400, {"error": "expected {id, text}"})
        with lock:
            answers = read_answers(self.answers_path)
            answers[item_id] = {"text": text, "at": datetime.now(timezone.utc).isoformat()}
            write_answers(self.answers_path, answers)
        return self.send_json(200, {"ok": True})

    def log_message(self, *args):
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("page_dir")
    parser.add_argument("--port", type=int, default=0, help="0 picks a free port")
    args = parser.parse_args()

    page_dir = os.path.abspath(args.page_dir)
    if not os.path.isfile(os.path.join(page_dir, "index.html")):
        parser.error(f"no index.html in {page_dir}")

    handler = partial(Handler, directory=page_dir, answers_path=os.path.join(page_dir, "answers.json"))
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    print(f"http://127.0.0.1:{server.server_address[1]}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

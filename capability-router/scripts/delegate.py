#!/usr/bin/env python3
"""Delegate work to an external agent runtime and get structured output back.

aswath-capability does not use the host's native orchestrator. It delegates to
headless-capable agent CLIs (cmdc, agy) that are configured in
registry/backends.json. This script is the single seam between the skill and
those runtimes.

Every call returns the same normalized envelope, whatever the backend:

    {
      "backend": "cmdc",
      "ok": true,
      "text": "<the agent's final message>",
      "parsed": {...},          # JSON extracted from text, if any
      "usage": {"input": 0, "output": 0, "cache_read": 0},
      "duration_s": 4.6,
      "session_id": "...",
      "stderr_tail": "",
      "error": null
    }

Usage:
    python delegate.py --backend cmdc --prompt "..." --json
    python delegate.py --backend agy  --prompt-file p.md --schema-file s.json
    python delegate.py --backends --list
    python delegate.py --backend cmdc --prompt "..." --raw   # show raw output
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

# Windows consoles default to cp1252; agent output routinely contains
# characters (arrows, em dashes, emoji) that would blow up a print().
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def skill_root(home=None):
    home = Path(home) if home else Path.home()
    for base in (home / ".agents" / "skills", home / ".claude" / "skills"):
        p = base / "aswath-capability"
        if p.is_dir():
            return p
    raise SystemExit("aswath-capability skill not found")


def load_backends(root):
    p = root / "registry" / "backends.json"
    if not p.is_file():
        raise SystemExit("registry/backends.json not found")
    return json.loads(p.read_text(encoding="utf-8"))


def resolve_cmd(cmd):
    """Expand a configured command, resolving the executable to an absolute path."""
    if not cmd:
        raise SystemExit("empty command")
    exe = cmd[0]
    if os.path.isabs(exe) or ("/" in exe or "\\" in exe):
        if not Path(exe).is_file():
            raise SystemExit(f"backend executable not found: {exe}")
        return [exe] + list(cmd[1:])
    found = shutil.which(exe)
    if not found:
        raise SystemExit(f"backend executable not on PATH: {exe}")
    return [found] + list(cmd[1:])


def extract_ndjson_result(raw):
    """cmdc: NDJSON stream, final line carries type=result and finalText."""
    text, usage, session = "", {}, None
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if obj.get("type") == "result":
            text = obj.get("finalText") or text
            usage = obj.get("usage") or usage
            session = obj.get("sessionId") or session
        elif obj.get("type") == "event":
            ev = obj.get("event") or {}
            if ev.get("type") == "run_end":
                res = ev.get("result") or {}
                text = res.get("finalText") or text
                usage = res.get("usage") or usage
                session = res.get("sessionId") or session
    return text, usage, session


def extract_json_response(raw):
    """agy: a single JSON object with a 'response' field.

    When --json-schema was passed, agy also returns 'structured_output' with
    the schema already enforced. Prefer that over parsing the prose.
    """
    text, usage, session, structured = "", {}, None, None
    obj = None
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            candidate = json.loads(line)
        except ValueError:
            continue
        obj = candidate
    if obj:
        # An agent whose tools were denied returns an EMPTY response. Do not
        # fall back to the CLI envelope - that would masquerade as a result.
        text = obj.get("response")
        if text is None:
            text = obj.get("finalText") or obj.get("text") or ""
        usage = obj.get("usage") or {}
        session = obj.get("conversation_id") or obj.get("session_id")
        structured = obj.get("structured_output")
    else:
        text = raw.strip()
    return text, usage, session, structured


def normalize_usage(usage, backend):
    if not isinstance(usage, dict):
        return {}
    if backend == "agy":
        return {
            "input": usage.get("input_tokens", 0),
            "output": usage.get("output_tokens", 0),
            "cache_read": usage.get("cache_read_tokens", 0),
            "thinking": usage.get("thinking_tokens", 0),
        }
    return {
        "input": usage.get("inputTokens", 0),
        "output": usage.get("outputTokens", 0),
        "cache_read": usage.get("cacheReadTokens", 0),
        "cache_write": usage.get("cacheWriteTokens", 0),
    }


def parse_json_from_text(text):
    """Pull a JSON object out of an agent's reply, tolerating fences and prose."""
    if not text:
        return None
    stripped = text.strip()
    try:
        return json.loads(stripped)
    except ValueError:
        pass
    m = FENCE_RE.search(text)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except ValueError:
            pass
    # First balanced object in the text.
    start = text.find("{")
    while start != -1:
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(text)):
            c = text[i]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
                continue
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except ValueError:
                        break
        start = text.find("{", start + 1)
    return None


def run_once(cfg, prompt, schema, cwd, allow_edits, timeout, raw=False, model=None):
    cmd = resolve_cmd(cfg["cmd"])
    # The prompt has to be attached to its flag for some CLIs: agy treats a
    # detached --print as taking the NEXT argument as its prompt, which would
    # silently swallow --output-format.
    style = cfg.get("prompt_style", "next")
    flag = cfg.get("prompt_flag")
    if style == "attached" and flag and flag in cmd:
        # Replace the flag in place with flag=value, keeping the executable.
        cmd = [(flag + "=" + prompt) if a == flag else a for a in cmd]
    else:
        cmd += [prompt]
    cmd += list(cfg.get("output_args") or [])
    cmd += list(cfg.get("extra_args") or [])
    if allow_edits:
        cmd += list(cfg.get("edits_args") or [])
    else:
        cmd += list(cfg.get("readonly_args") or [])
    if schema and cfg.get("supports_schema") and cfg.get("schema_arg"):
        cmd += [cfg["schema_arg"], json.dumps(schema)]
    if model and cfg.get("model_arg"):
        cmd += [cfg["model_arg"], model]

    started = time.time()
    try:
        proc = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True,
            timeout=timeout, encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False, "text": "", "parsed": None, "usage": {},
            "duration_s": round(time.time() - started, 2), "session_id": None,
            "stderr_tail": f"timeout after {timeout}s", "error": "timeout",
        }

    raw_out = proc.stdout or ""
    if raw:
        print(raw_out)

    if cfg.get("extract") == "ndjson_result":
        text, usage, session = extract_ndjson_result(raw_out)
        structured = None
    else:
        text, usage, session, structured = extract_json_response(raw_out)

    # Schema-enforced output wins over anything we have to parse ourselves.
    parsed = structured if structured is not None else parse_json_from_text(text)

    ok = proc.returncode == 0 and bool(text.strip())
    if ok and not text.strip() and "auto-denied" in (proc.stderr or ""):
        ok = False
    return {
        "ok": ok,
        "text": text,
        "parsed": parsed,
        "usage": normalize_usage(usage, cfg.get("_id")),
        "duration_s": round(time.time() - started, 2),
        "session_id": session,
        "stderr_tail": (proc.stderr or "")[-800:],
        "error": None if ok else f"exit {proc.returncode}",
        "returncode": proc.returncode,
    }


def cmd_run(args, root):
    cfg_all = load_backends(root)
    backends = cfg_all["backends"]
    defaults = cfg_all.get("defaults", {})
    name = args.backend or defaults.get("backend", "cmdc")
    if name not in backends:
        raise SystemExit(f"unknown backend '{name}'. known: {', '.join(backends)}")
    cfg = dict(backends[name])
    cfg["_id"] = name
    tiers = cfg_all.get("model_tiers", {})
    model = args.model
    if model in tiers:
        model = tiers[model]

    if args.prompt_file:
        prompt = Path(args.prompt_file).read_text(encoding="utf-8")
    else:
        prompt = args.prompt or ""

    schema = None
    if args.schema_file:
        schema = json.loads(Path(args.schema_file).read_text(encoding="utf-8"))
    if schema and cfg.get("supports_schema"):
        pass  # enforced by the CLI itself
    elif schema:
        prompt += "\n\n" + cfg.get("json_hint", "") + "\n" + json.dumps(schema)

    cwd = args.cwd or os.getcwd()
    timeout = args.timeout or defaults.get("timeout_s", 900)
    retries = args.retries if args.retries is not None else defaults.get("retries", 1)

    result = None
    for attempt in range(retries + 1):
        result = run_once(cfg, prompt, schema, cwd, args.allow_edits, timeout, args.raw, model)
        if result["ok"]:
            break
        if attempt < retries:
            time.sleep(2)

    result["backend"] = name
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["ok"] else 1


def cmd_list(args, root):
    cfg_all = load_backends(root)
    backends = cfg_all["backends"]
    if args.backends:
        for name, cfg in backends.items():
            exe = resolve_cmd(cfg["cmd"])[0]
            print(f"  {name:<10} {cfg.get('label','')}")
            print(f"      exe    : {exe}")
            print(f"      extract: {cfg.get('extract')}")
            print(f"      schema : {'yes' if cfg.get('supports_schema') else 'no'}")
            print(f"      {cfg.get('description','')}")
        return 0
    print(json.dumps({"backends": list(backends), "defaults": cfg_all.get("defaults", {})}, indent=2))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--home", default=None)
    ap.add_argument("--backend")
    ap.add_argument("--prompt")
    ap.add_argument("--prompt-file")
    ap.add_argument("--schema-file")
    ap.add_argument("--cwd")
    ap.add_argument("--timeout", type=int)
    ap.add_argument("--retries", type=int)
    ap.add_argument("--allow-edits", action="store_true")
    ap.add_argument("--raw", action="store_true")
    ap.add_argument("--model", default=None, help="model tier: fast | strong | a backend model name")
    ap.add_argument("--backends", action="store_true", help="list configured backends")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    root = skill_root(args.home)
    if args.backends or args.list:
        return cmd_list(args, root)
    if not args.prompt and not args.prompt_file:
        ap.print_help()
        return 1
    return cmd_run(args, root)


if __name__ == "__main__":
    sys.exit(main() or 0)

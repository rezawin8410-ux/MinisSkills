"""Deterministic A/B eval harness for agent skills."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

__version__ = "0.1.1"


def load_eval(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"eval file must be JSON: {exc}") from exc
    if "cases" not in data or not isinstance(data["cases"], list):
        raise ValueError("eval file needs a cases array")
    return data


def run_solver(solver: list[str], work: Path, prompt: str, skill: Path | None, timeout: int) -> dict[str, Any]:
    env = os.environ.copy()
    env["SKILLEVAL_PROMPT"] = prompt
    env["SKILLEVAL_SKILL"] = str(skill) if skill else ""
    out_file = work / "skilleval.out"
    env["SKILLEVAL_OUT"] = str(out_file)
    result = subprocess.run(
        solver,
        cwd=str(work),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if out_file.is_file():
        output = out_file.read_text(encoding="utf-8", errors="replace")
    else:
        output = result.stdout
    return {"output": output, "stderr": result.stderr, "exit_code": result.returncode}


def check_one(check: dict[str, Any], result: dict[str, Any], work: Path) -> tuple[bool, str]:
    kind = check.get("type")
    output = result["output"]
    if kind == "contains":
        ok = str(check["text"]) in output
        return ok, "contains " + json.dumps(check["text"])
    if kind == "not_contains":
        ok = str(check["text"]) not in output
        return ok, "not_contains " + json.dumps(check["text"])
    if kind == "regex":
        ok = re.search(check["pattern"], output, re.M) is not None
        return ok, f"regex {check['pattern']}"
    if kind == "exit_code":
        ok = result["exit_code"] == int(check["value"])
        return ok, f"exit_code {check['value']}"
    if kind == "file_exists":
        ok = (work / check["path"]).exists()
        return ok, f"file_exists {check['path']}"
    if kind == "file_contains":
        path = work / check["path"]
        ok = path.is_file() and str(check["text"]) in path.read_text(encoding="utf-8", errors="replace")
        return ok, f"file_contains {check['path']}"
    raise ValueError(f"unknown check type: {kind}")


def resolve_skill(spec: Any, base: Path) -> Path | None:
    if not spec:
        return None
    path = Path(str(spec))
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def run_case(case: dict[str, Any], solver: list[str], base: Path, timeout: int) -> dict[str, Any]:
    variants = case.get("variants")
    if not variants:
        variants = {
            "baseline": {"skill": case.get("baseline_skill")},
            "with_skill": {"skill": case.get("skill")},
        }
    checks = case.get("checks") or []
    files = case.get("files") or {}
    prompt = case.get("prompt") or ""
    results = {}
    for variant_name, variant in variants.items():
        work = Path(tempfile.mkdtemp(prefix="skilleval-"))
        try:
            for rel, content in files.items():
                dest = work / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(content, encoding="utf-8")
            skill = resolve_skill(variant.get("skill"), base)
            ran = run_solver(solver, work, prompt, skill, timeout)
            check_rows = []
            passed = 0
            for check in checks:
                ok, label = check_one(check, ran, work)
                check_rows.append({"check": label, "ok": ok})
                if ok:
                    passed += 1
            results[variant_name] = {
                "exit_code": ran["exit_code"],
                "output": ran["output"],
                "passed": passed,
                "total": len(checks),
                "checks": check_rows,
            }
        finally:
            shutil.rmtree(work, ignore_errors=True)
    return {"id": case.get("id") or "case", "prompt": prompt, "variants": results}


def summarize(case_results: list[dict[str, Any]]) -> dict[str, Any]:
    variant_names: list[str] = []
    for case in case_results:
        for name in case["variants"]:
            if name not in variant_names:
                variant_names.append(name)
    totals = {name: {"passed": 0, "total": 0, "cases": 0} for name in variant_names}
    for case in case_results:
        for name, row in case["variants"].items():
            totals[name]["passed"] += row["passed"]
            totals[name]["total"] += row["total"]
            totals[name]["cases"] += 1
    return {"variants": totals, "cases": case_results}


def render(summary: dict[str, Any]) -> str:
    lines = ["variant\tchecks passed\tcases"]
    for name, row in summary["variants"].items():
        lines.append(f"{name}\t{row['passed']}/{row['total']}\t{row['cases']}")
    for case in summary["cases"]:
        lines.append(f"\n[{case['id']}]")
        for name, row in case["variants"].items():
            lines.append(f"  {name}: {row['passed']}/{row['total']}")
            for check in row["checks"]:
                mark = "ok" if check["ok"] else "FAIL"
                lines.append(f"    {mark}  {check['check']}")
    return "\n".join(lines)

def bind_path(part: str, base: Path) -> str:
    candidate = base / part
    if part and not Path(part).is_absolute() and candidate.exists():
        return str(candidate.resolve())
    return part


def bind_command(command: str, base: Path) -> str:
    try:
        parts = shlex.split(command)
    except ValueError:
        return command
    return shlex.join(bind_path(part, base) for part in parts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="skilleval",
        description="Deterministic A/B eval harness for agent skills. No API key required.",
    )
    parser.add_argument("eval_file", help="JSON eval file")
    parser.add_argument("--solver", help="solver command; default comes from the eval file")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--version", action="version", version=f"skilleval {__version__}")
    args = parser.parse_args(argv)
    path = Path(args.eval_file).resolve()
    if not path.is_file():
        print(f"error: file not found: {path}", file=sys.stderr)
        return 1
    if args.timeout <= 0:
        print("error: --timeout must be positive", file=sys.stderr)
        return 1
    try:
        data = load_eval(path)
        solver_cmd = args.solver or data.get("solver")
        if not solver_cmd:
            raise ValueError("pass --solver or set solver in the eval file")
        if isinstance(solver_cmd, list):
            solver = [bind_path(part, path.parent) for part in solver_cmd]
        else:
            solver = ["bash", "-lc", bind_command(str(solver_cmd), path.parent)]
        results = [run_case(case, solver, path.parent, args.timeout) for case in data["cases"]]
        summary = summarize(results)
    except (ValueError, json.JSONDecodeError, OSError, subprocess.TimeoutExpired) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print(render(summary))
    preferred = None
    for name in ("with_skill", "skill", "variant"):
        if name in summary["variants"]:
            preferred = name
            break
    if preferred is None and summary["variants"]:
        preferred = list(summary["variants"])[-1]
    if preferred:
        row = summary["variants"][preferred]
        if row["total"] and row["passed"] < row["total"]:
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Record skill outcomes and propose what should become permanent.

The ledger is the memory of this system. Every accept/reject writes to it,
and the quality score it computes feeds straight back into registry ranking.
A skill you reject stops being selected, even if its description matches
perfectly.

Usage:
    python record-outcome.py --skill impeccable --outcome accept --note "found the a11y lens"
    python record-outcome.py --skill some-skill --outcome reject --note "too generic"
    python record-outcome.py --authored <name> --skill <name> --outcome accept
    python record-outcome.py --propose
    python record-outcome.py --ledger

Score model
    score = (accepts - rejects) / (accepts + rejects + 1) * 100
    Range -100..100. Unseen skills score 0 and rank by cost and popularity.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

OUTCOMES = ("accept", "reject", "partial")


# --- shared router state -------------------------------------------------
# The router is installed in more than one skills root (mcode reads its own
# root, other harnesses read theirs). The mutable state - the registry index
# and the outcome ledger - must have exactly one copy, or the two installs
# drift and the router ranks differently depending on who is asking.
# Shared location: ~/.minimax/local-runtime/skill-learning/
def shared_state_dir():
    """Where the mutable router state lives.

    Overridable so the router can run from a checkout, a container or a
    CI job instead of one machine's home directory.
    """
    env = os.environ.get("SKILL_ROUTER_STATE_DIR")
    if env:
        return Path(env)
    return Path.home() / ".minimax" / "local-runtime" / "skill-learning"


def shared_registry_path():
    return shared_state_dir() / "skills.json"


def shared_ledger_path():
    return shared_state_dir() / "ledger.json"


def skill_root(home=None):
    home = Path(home) if home else Path.home()
    for base in (home / ".agents" / "skills", home / ".claude" / "skills"):
        p = base / "aswath-capability"
        if p.is_dir():
            return p
    raise SystemExit("aswath-capability skill not found")


def ledger_path(root):
    return shared_ledger_path()


def load_ledger(root):
    p = ledger_path(root)
    if not p.is_file():
        return {"skills": {}, "authored": {}, "history": []}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"skills": {}, "authored": {}, "history": []}


def save_ledger(root, data):
    p = ledger_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    return p


def score_of(rec):
    a = rec.get("accepts", 0)
    r = rec.get("rejects", 0)
    p = rec.get("partials", 0)
    total = a + r + p
    if total == 0:
        return 0
    return round((a - r) / (total + 1) * 100)


def cmd_record(args, root):
    led = load_ledger(root)
    name = args.skill
    if not name:
        raise SystemExit("--skill is required")
    rec = led["skills"].setdefault(name, {"accepts": 0, "rejects": 0, "partials": 0, "notes": []})
    if args.outcome == "accept":
        rec["accepts"] += 1
    elif args.outcome == "reject":
        rec["rejects"] += 1
    else:
        rec["partials"] += 1
    if args.note:
        rec["notes"].append({"note": args.note, "at": time.strftime("%Y-%m-%dT%H:%M:%S")})
    rec["score"] = score_of(rec)
    rec["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    if args.authored:
        led.setdefault("authored", {})[name] = {
            "status": "proposed",
            "first_seen": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "outcome": args.outcome,
        }
    led.setdefault("history", []).append({
        "skill": name, "outcome": args.outcome, "note": args.note,
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    })
    p = save_ledger(root, led)
    print(f"recorded {args.outcome} for {name}  score={rec['score']:+d}  -> {p}")
    return 0


def cmd_propose(args, root):
    led = load_ledger(root)
    print("=" * 68)
    print("PROMOTION PROPOSAL - review with the user before anything is written")
    print("=" * 68)

    authored = led.get("authored", {})
    promote, retire = [], []
    for name, meta in authored.items():
        rec = led["skills"].get(name, {})
        if rec.get("accepts", 0) > 0:
            promote.append((name, rec))
        elif rec.get("rejects", 0) >= 2:
            retire.append((name, rec))

    print(f"\n[PROMOTE] authored this session, used and accepted ({len(promote)}):")
    if not promote:
        print("   (none)")
    for name, rec in promote:
        print(f"   {name}")
        print(f"      score {score_of(rec):+d} | accepts {rec.get('accepts',0)} | "
              f"cost on invoke: see registry")
        for n in rec.get("notes", [])[-2:]:
            print(f"      - {n['note']}")

    print(f"\n[RETIRE] failed twice or more ({len(retire)}):")
    if not retire:
        print("   (none)")
    for name, rec in retire:
        print(f"   {name}  rejects={rec.get('rejects',0)} score={score_of(rec):+d}")
        for n in rec.get("notes", [])[-2:]:
            print(f"      - {n['note']}")

    failing = [(n, r) for n, r in led["skills"].items() if r.get("rejects", 0) >= 2]
    if failing:
        print(f"\n[FLAG] established skills that failed repeatedly ({len(failing)}):")
        for name, rec in failing:
            print(f"   {name}  score={score_of(rec):+d}")
            print("      -> ranked below alternatives automatically; consider rewriting its description")

    print("\n" + "-" * 68)
    print("Nothing above has been written. To apply a promotion:")
    print("   1. move/copy the skill into ~/.agents/skills/<name>/")
    print("   2. python scripts/build-registry.py")
    print("   3. python scripts/record-outcome.py --skill <name> --outcome accept")
    return 0


def cmd_ledger(args, root):
    led = load_ledger(root)
    skills = led.get("skills", {})
    if not skills:
        print("ledger empty")
        return 0
    rows = sorted(skills.items(), key=lambda kv: -score_of(kv[1]))
    print(f"{'skill':<40} {'score':>6} {'a':>3} {'r':>3} {'p':>3}")
    for name, rec in rows:
        print(f"{name:<40} {score_of(rec):>+6} {rec.get('accepts',0):>3} "
              f"{rec.get('rejects',0):>3} {rec.get('partials',0):>3}")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--home", default=None)
    ap.add_argument("--skill")
    ap.add_argument("--outcome", choices=list(OUTCOMES))
    ap.add_argument("--note")
    ap.add_argument("--authored", action="store_true",
                    help="mark this skill as one you authored this session")
    ap.add_argument("--propose", action="store_true")
    ap.add_argument("--ledger", action="store_true")
    args = ap.parse_args()

    root = skill_root(args.home)
    if args.propose:
        return cmd_propose(args, root)
    if args.ledger:
        return cmd_ledger(args, root)
    if not args.skill or not args.outcome:
        ap.print_help()
        return 1
    return cmd_record(args, root)


if __name__ == "__main__":
    sys.exit(main() or 0)

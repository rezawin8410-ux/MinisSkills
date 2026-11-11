#!/usr/bin/env python3
"""Learn which skill a request actually needs, from what really happened.

The live hook records events as they occur. This reads the whole session -
the ask, the skills that were loaded, the edits that followed, the corrections
the user made - and turns that sequence into accept / reject verdicts per skill.

Why the sequence and not the moment: a verdict needs to know what came after.
A skill that was loaded and then followed by edits that survived is an accept.
A skill that was loaded and then followed by a correction is a reject.
A skill that was loaded and then followed by a delete of its own output is a
reject too - the work it produced was thrown away.

Output goes to the router's ledger in the native shape, so build-registry.py
picks it up as a quality score and query-registry.py ranks with it. The loop
closes with no extra machinery.

Usage:
    python learn.py                    # learn over recent sessions, update ledger
    python learn.py --dry-run          # show what would be learned, change nothing
    python learn.py --days 30          # how far back to look
    python learn.py --sessions 50      # how many sessions to scan
    python learn.py --report           # show the current ledger
"""

import argparse
import ast
import glob
import json
import os
import re
import sys
import time
from pathlib import Path

ISSUE_KINDS = ('missing-skill', 'missing-step', 'bad-default', 'wrong-trigger', 'manual-trigger')

# The user's own words that mean "that was wrong".
REJECT_RE = [
    re.compile(p, re.I) for p in [
        r"\bno[,.]?\s+(?:that|not|don'?t|do not|stop|use)\b",
        r"\bwrong\b", r"\bundo\b", r"\brevert\b", r"\broll ?back\b",
        r"\bdon'?t use\b", r"\bnot what i (?:asked|wanted)\b",
        r"\bthat'?s not\b", r"\binstead of\b", r"\bnever mind\b",
        r"\bscrap that\b", r"\bwrong skill\b", r"\bwrong tool\b",
        r"\bshould have\b",
    ]
]

# The user's own words that mean "that was right".
ACCEPT_RE = [
    re.compile(p, re.I) for p in [
        r"\bperfect\b", r"\bthat'?s (?:right|it|correct|what i wanted)\b",
        r"\bexactly\b", r"\bworks?\b", r"\bnice\b", r"\bthanks?\b",
        r"\bgood job\b", r"\bship it\b", r"\blgtm\b",
    ]
]

# Harness plumbing that is never a real user ask.
BOILERPLATE = (
    '<system-reminder>', '<agent-context>', '<background-task-finished>',
    '<questionnaire-response>', '<plugin-hook-context>', 'PTC is enabled',
    '<mavis-context-appendix', '<host-', 'Recent user queries',
)


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
    here = Path(__file__).resolve().parent.parent
    if (here / 'SKILL.md').is_file():
        return here
    base = Path(home) if home else Path.home()
    for b in (base / '.minimax' / 'skills', base / '.agents' / 'skills',
              base / '.claude' / 'skills', base / '.codex' / 'skills'):
        p = b / 'aswath-capability'
        if (p / 'SKILL.md').is_file():
            return p
    raise SystemExit('aswath-capability skill not found')


def sessions_root(home=None):
    base = Path(home) if home else Path.home()
    return base / '.minimax' / 'v2' / 'sessions'


def ledger_path(root):
    return shared_ledger_path()


def load_ledger(root):
    p = ledger_path(root)
    if not p.is_file():
        return {'skills': {}, 'authored': {}, 'history': []}
    try:
        data = json.loads(p.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {'skills': {}, 'authored': {}, 'history': []}
    data.setdefault('skills', {})
    data.setdefault('authored', {})
    data.setdefault('history', [])
    return data


def score_of(rec):
    a = rec.get('accepts', 0)
    r = rec.get('rejects', 0)
    p = rec.get('partials', 0)
    total = a + r + p
    if total == 0:
        return 0
    return round((a - r) / (total + 1) * 100)


def is_boilerplate(text):
    if not text:
        return True
    head = text[:600]
    return any(marker in head for marker in BOILERPLATE)


def classify(text):
    if not text:
        return None
    for rx in REJECT_RE:
        if rx.search(text):
            return 'reject'
    for rx in ACCEPT_RE:
        if rx.search(text):
            return 'accept'
    return None


def parse_message(raw):
    """A transcript line's message is a Python repr, not JSON. Parse it."""
    if isinstance(raw, str):
        try:
            return ast.literal_eval(raw)
        except (ValueError, SyntaxError):
            try:
                return json.loads(raw)
            except ValueError:
                return None
    return raw


def read_session(path):
    """Return the ordered events of one session.

    Events are:
      ('ask', text)      - a real user prompt
      ('skill', name)    - a skill was loaded
      ('edit', path)     - a file was edited or written
      ('delete', path)   - a file was deleted
    """
    events = []
    try:
        raw = path.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return events
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        msg = parse_message(obj.get('message'))
        if not isinstance(msg, dict):
            continue
        role = msg.get('role')
        content = msg.get('content')
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get('type')
            if btype == 'toolCall':
                name = block.get('name')
                args = block.get('arguments')
                args = args if isinstance(args, dict) else {}
                if name == 'skill':
                    sname = args.get('name')
                    if sname:
                        events.append(('skill', str(sname)))
                elif name in ('edit', 'write', 'notebook_edit'):
                    p = args.get('file_path') or args.get('path')
                    if p:
                        events.append(('edit', str(p)))
                elif name == 'bash':
                    cmd = args.get('command')
                    if isinstance(cmd, str) and re.search(
                            r'\b(rm|del|rmdir|remove-item|unlink)\b', cmd, re.I):
                        events.append(('delete', cmd[:200]))
            elif role == 'user' and btype == 'text':
                text = block.get('text') or ''
                if not is_boilerplate(text):
                    events.append(('ask', text))
    return events


def judge_session(events):
    """Turn one session's events into per-skill verdicts.

    The rule, in order of strength:
      - a correction after a skill load is a reject
      - a delete of a file the skill produced is a reject
      - an affirmation after a skill load is an accept
      - the skill produced edits that survived, with no correction, is an accept

    A verdict is attributed to the most recent skill load before the evidence,
    because that is the skill the evidence is about. A skill load opens a
    window; the window closes when the next skill is loaded.
    """
    verdicts = []
    last_skill = None
    edited = set()
    edits_since = 0
    judged = False

    def flush():
        """Close the window on the current skill and record what happened."""
        nonlocal edits_since, judged
        if last_skill is not None and not judged and edits_since:
            verdicts.append((last_skill, 'accept',
                             'this skill produced %d edit(s) that stood' % edits_since))
        edits_since = 0
        judged = False

    def basenames(paths):
        out = set()
        for path in paths:
            name = path.replace('\\', '/').rsplit('/', 1)[-1]
            if name:
                out.add(name.lower())
        return out

    for kind, value in events:
        if kind == 'skill':
            flush()
            last_skill = value
            edited = set()
        elif kind == 'ask':
            verdict = classify(value)
            if verdict and last_skill and not judged:
                verdicts.append((last_skill, verdict, value[:200]))
                judged = True
        elif kind == 'edit':
            if last_skill:
                edits_since += 1
                edited.add(value)
        elif kind == 'delete':
            # Only a delete that targets a file this skill actually produced
            # counts against the skill. An unrelated rm in the same window is
            # not evidence about the skill.
            if not last_skill or judged:
                continue
            targets = basenames(edited)
            low = value.lower()
            hit = any(name and name in low for name in targets)
            if hit and edited:
                verdicts.append((last_skill, 'reject',
                                 'the file this skill produced was deleted'))
                judged = True
    flush()
    return verdicts


def collect(sessions_dir, days, max_sessions):
    cutoff = time.time() - days * 86400
    files = glob.glob(str(sessions_dir / '**' / 'messages.jsonl'), recursive=True)
    files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    picked = []
    for p in files:
        try:
            if os.path.getmtime(p) < cutoff:
                continue
        except OSError:
            continue
        picked.append(p)
        if len(picked) >= max_sessions:
            break
    return picked


def learn(sessions, dry_run):
    tally = {}
    evidence = {}
    for path in sessions:
        events = read_session(Path(path))
        if not events:
            continue
        for skill, verdict, snippet in judge_session(events):
            rec = tally.setdefault(skill, {'accepts': 0, 'rejects': 0, 'partials': 0})
            rec['accepts' if verdict == 'accept' else 'rejects'] += 1
            evidence.setdefault((skill, verdict), []).append(snippet)
    return tally, evidence


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--home', default=None)
    ap.add_argument('--days', type=int, default=60)
    ap.add_argument('--sessions', type=int, default=200)
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--report', action='store_true')
    args = ap.parse_args()

    root = skill_root(args.home)
    led = load_ledger(root)

    if args.report:
        skills = led.get('skills', {})
        if not skills:
            print('ledger empty')
            return 0
        print('%-42s %7s %4s %4s %4s' % ('skill', 'score', 'acc', 'rej', 'par'))
        for name, rec in sorted(skills.items(), key=lambda kv: -score_of(kv[1])):
            print('%-42s %+7d %4d %4d %4d' % (
                name, score_of(rec), rec.get('accepts', 0),
                rec.get('rejects', 0), rec.get('partials', 0)))
        return 0

    sessions = collect(sessions_root(args.home), args.days, args.sessions)
    if not sessions:
        print('no sessions found to learn from')
        return 0

    tally, evidence = learn(sessions, args.dry_run)
    if not tally:
        print('scanned %d sessions - no verdicts yet' % len(sessions))
        print('a verdict needs a skill load followed by a correction, an')
        print('affirmation, or an edit. Nothing in these sessions had that shape.')
        return 0

    print('scanned %d sessions' % len(sessions))
    print('%-42s %5s %5s %7s' % ('skill', 'acc', 'rej', 'score'))
    for name, rec in sorted(tally.items(), key=lambda kv: -(kv[1]['accepts'] - kv[1]['rejects'])):
        print('%-42s %5d %5d %+7d' % (name, rec['accepts'], rec['rejects'],
                                      rec['accepts'] - rec['rejects']))

    if args.dry_run:
        print('\nDRY RUN - ledger not written')
        return 0

    for name, rec in tally.items():
        target = led['skills'].setdefault(
            name, {'accepts': 0, 'rejects': 0, 'partials': 0, 'notes': []})
        target['accepts'] += rec['accepts']
        target['rejects'] += rec['rejects']
        target['score'] = score_of(target)
        target['updated'] = time.strftime('%Y-%m-%dT%H:%M:%S')
        for (skill, verdict), snippets in evidence.items():
            if skill != name:
                continue
            for snippet in snippets[:2]:
                note = ('learned from session history: %s' % verdict)
                target.setdefault('notes', []).append({
                    'note': note,
                    'excerpt': snippet,
                    'at': target['updated'],
                })
                break
        led['history'].append({
            'skill': name,
            'outcome': 'batch',
            'accepts': rec['accepts'],
            'rejects': rec['rejects'],
            'at': target['updated'],
        })

    p = ledger_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(led, indent=1, ensure_ascii=False), encoding='utf-8')
    print('\nledger updated: %s (%d skills)' % (p, len(led['skills'])))
    print('run scripts/build-registry.py to feed these scores into ranking')
    return 0


if __name__ == '__main__':
    sys.exit(main() or 0)

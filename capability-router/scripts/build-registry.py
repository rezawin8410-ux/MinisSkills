#!/usr/bin/env python3
"""Build the aswath-capability skill registry.

Scans three sources and writes one index the orchestrator can query cheaply:

  1. Local skills        ~/.agents/skills/*/SKILL.md   (frontmatter + description)
  2. Marketplace skills  ~/.claude/plugins/plugin-catalog-cache.json
                         (per-skill token cost on invoke + plugin install count)
  3. Installable plugins ~/.claude/plugins/plugin-directory-cache-v2.json
                         (what can be installed, from where)

Every skill gets capability tags by keyword-matching its description against
registry/capabilities.json. Cost is estimated from character counts (chars/4),
which matches how Claude Code bills a skill's on-invoke payload.

Usage:
    python build-registry.py [--home <dir>] [--out <file>] [--quiet]

Exit codes: 0 ok, 1 failed.
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

SKILL_DIR_NAME = "skills"
FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
DESC_RE = re.compile(r"^description:\s*(.*)$", re.MULTILINE | re.DOTALL)
NAME_RE = re.compile(r"^name:\s*(.+)$", re.MULTILINE)
VERSION_RE = re.compile(r"^version:\s*(.+)$", re.MULTILINE)
TAGLINE_RE = re.compile(r"^\s*-\s+(.+)$", re.MULTILINE)


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


def chars_to_tokens(chars):
    if not chars:
        return 0
    return max(1, round(chars / 4))


def read_frontmatter(path):
    """Return (name, description, version, raw_meta) for a SKILL.md."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None, None, None, ""
    m = FRONTMATTER_RE.match(text)
    if not m:
        return path.parent.name, "", None, ""
    block = m.group(1)

    name = None
    nm = NAME_RE.search(block)
    if nm:
        name = nm.group(1).strip().strip("\"'")

    # description: may be inline, a folded block (>-), or a |- block.
    desc = ""
    lines = block.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("description:"):
            rest = line[len("description:"):].strip()
            if rest in (">", ">-", "|", "|-", ">-"):
                acc = []
                for nxt in lines[i + 1:]:
                    if nxt.startswith("  ") or nxt.startswith("\t"):
                        acc.append(nxt.strip())
                    else:
                        break
                desc = " ".join(x for x in acc if x)
            else:
                desc = rest.strip().strip("\"'")
            break

    ver = None
    vm = VERSION_RE.search(block)
    if vm:
        ver = vm.group(1).strip().strip("\"'")

    return name or path.parent.name, desc, ver, block


def skill_file_evidence(skill_dir, limit=600):
    """Collect the stems of files a skill ships.

    The reference files are the strongest signal of what a skill actually
    does: a skill shipping reference/critique.md critiques, whatever its
    description says. Matching is on exact filename stems, not substrings -
    'type' must not match 'typescript-best-practices' or every skill's
    SKILL.md. Stems only, never file contents.
    """
    stems = set()
    skip_dirs = {"node_modules", ".git", "__pycache__", ".venv", "venv", "dist", "build", ".next"}
    generic = {"skill", "readme", "license", "changelog", "index", "package"}
    try:
        for p in skill_dir.rglob("*"):
            if len(stems) >= limit:
                break
            if not p.is_file():
                continue
            try:
                rel = p.relative_to(skill_dir)
            except ValueError:
                continue
            if any(part in skip_dirs for part in rel.parts):
                continue
            if p.suffix.lower() not in (".md", ".json", ".toml", ".yaml", ".yml", ".txt"):
                continue
            stem = p.stem.lower()
            if stem in generic:
                continue
            stems.add(stem)
    except OSError:
        pass
    return stems


def name_token_match(name_low, keyword):
    """Does a weak keyword match a skill NAME?

    Raw substring matching is what let 'form' match 'transform', 'test'
    match 'latest', 'git' match 'github' and 'lock' match 'block'. A weak
    keyword matches a name token by token-prefix instead: 'brainstorm'
    still matches 'brainstorming' and 'cli' still matches 'cli-for-agents',
    but 'test' no longer matches 'latest'.
    """
    kw = (keyword or "").lower().strip()
    if not kw:
        return False
    if " " in kw or "-" in kw:
        return kw in name_low
    if len(kw) < 3:
        return False
    for tok in re.split(r"[^a-z0-9]+", name_low):
        if tok.startswith(kw):
            return True
    return False


def name_token_exact(name_low, keyword):
    """A weak keyword that must match a whole name token.

    'cli' must match 'cli-for-agents' and 'hyperframes-cli' but not
    'clickmeeting'. Token-prefix matching cannot tell those apart, so a
    capability can list exact keywords separately.
    """
    kw = (keyword or "").lower().strip()
    if not kw:
        return False
    return kw in re.split(r"[^a-z0-9]+", name_low)


def tag_capabilities(text, name, capability_map, files=""):
    """Tag a skill with the capabilities it actually provides.

    Four evidence channels, strongest last:
      files        - the reference files the skill ships (proof of what it does)
      strong       - a keyword in the description or the frontmatter
      weak         - a keyword in the skill's NAME only, matched per token
      require_name - the capability only applies to skills whose name
                     contains one of these strings

    A skill that merely mentions 'product page' is not a provider of
    ecommerce capability; a skill shipping reference/cart.md is.
    """
    low = (text or "").lower()
    lname = (name or "").lower()
    stems = files if isinstance(files, (set, frozenset)) else set()
    tags = []
    for cap, spec in capability_map.items():
        if isinstance(spec, dict):
            strong = spec.get("strong") or []
            weak = spec.get("weak") or []
            weak_exact = spec.get("weak_exact") or []
            files_kw = spec.get("files") or []
            require_name = spec.get("require_name") or []
        else:
            strong, weak, weak_exact, files_kw = spec or [], [], [], []
            require_name = []
        if require_name and not any(r.lower() in lname for r in require_name):
            continue
        hit = False
        for kw in files_kw:
            if kw.lower() in stems:
                hit = True
                break
        if not hit:
            for kw in strong:
                if kw.lower() in low:
                    hit = True
                    break
        if not hit:
            for kw in weak:
                if name_token_match(lname, kw):
                    hit = True
                    break
        if not hit:
            for kw in weak_exact:
                if name_token_exact(lname, kw):
                    hit = True
                    break
        if hit:
            tags.append(cap)
    return sorted(tags)


def skill_roots(home):
    """Every root that holds installed skills, most authoritative first.

    The data dir root is always scanned by the host, so its skills are
    always installed and must never be missing from the registry. The
    external roots come next. A skill name found in an earlier root wins,
    so the always-installed copy is the one that gets recorded.
    """
    roots = [
        home / ".minimax" / "skills",
        home / ".minimax" / ".builtin-skills",
        home / ".agents" / SKILL_DIR_NAME,
        home / ".claude" / SKILL_DIR_NAME,
        home / ".codex" / SKILL_DIR_NAME,
    ]
    out = []
    for r in roots:
        if r.is_dir() and r not in out:
            out.append(r)
    return out


def scan_local_skills(home, capability_map, excluded=None):
    excluded = excluded or set()
    out = []
    seen = set()
    for base in skill_roots(home):
        try:
            entries = sorted(base.iterdir())
        except OSError:
            continue
        for entry in entries:
            md = entry / "SKILL.md"
            if not md.is_file():
                continue
            if entry.name in seen:
                continue
            name, desc, ver, block = read_frontmatter(md)
            if not name or name in seen:
                continue
            seen.add(entry.name)
            seen.add(name)
            size = md.stat().st_size
            text = f"{desc} {block}"
            files = skill_file_evidence(entry)
            out.append({
                "name": name,
                "slug": slugify(name),
                "description": (desc or "")[:600],
                "version": ver,
                "source": "local",
                "path": str(md),
                "tokens_est": chars_to_tokens(size),
                "auto_invocable": "disable-model-invocation: true" not in block,
                "tags": [] if name in excluded else tag_capabilities(text, name, capability_map, files),
                "router": name in excluded,
                "file_evidence": len(files),
                "evidence_stems": sorted(files)[:12],
            })
    return sorted(out, key=lambda s: s["name"])


def scan_marketplace(home):
    """Marketplace skills: token cost on invoke + plugin popularity."""
    path = home / ".claude" / "plugins" / "plugin-catalog-cache.json"
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    plugins = (data.get("catalog") or {}).get("plugins") or {}
    out = []
    for key, p in plugins.items():
        installs = p.get("unique_installs") or 0
        comps = p.get("components") or {}
        for s in comps.get("skills") or []:
            chars = (s.get("chars") or {})
            on_invoke = chars.get("on_invoke") or 0
            out.append({
                "name": s.get("name"),
                "slug": slugify(s.get("name")),
                "description": "",
                "version": (p.get("release") or {}).get("version"),
                "source": "marketplace",
                "plugin": key,
                "tokens_est": chars_to_tokens(on_invoke),
                "always_on_tokens": chars_to_tokens(chars.get("always_on") or 0),
                "installs": installs,
                "auto_invocable": True,
                "tags": [],
            })
        for a in comps.get("agents") or []:
            if not isinstance(a, dict):
                a = {"name": str(a)}
            out.append({
                "name": a.get("name"),
                "description": "agent",
                "version": None,
                "source": "marketplace",
                "plugin": key,
                "kind": "agent",
                "tokens_est": chars_to_tokens((a.get("chars") or {}).get("on_invoke") or 0),
                "installs": installs,
                "auto_invocable": True,
                "tags": [],
            })
        for m in comps.get("mcpServers") or []:
            if not isinstance(m, dict):
                m = {"name": str(m)}
            out.append({
                "name": m.get("name") or m.get("command"),
                "description": "mcp server",
                "version": None,
                "source": "marketplace",
                "plugin": key,
                "kind": "mcp",
                "tokens_est": 0,
                "installs": installs,
                "auto_invocable": True,
                "tags": [],
            })
    return out


def plugin_roots(home):
    """Directories that hold plugin-provided skills."""
    out = []
    for r in (home / ".claude" / "plugins", home / ".minimax" / "plugins"):
        if r.is_dir() and r not in out:
            out.append(r)
    return out


def scan_plugin_skills(home, capability_map, excluded=None):
    """Skills shipped by installed plugins.

    They live under <plugins>/<cache|marketplaces>/.../skills/<name>/ and
    under the host's own plugin directory. They are invokable exactly like
    local skills, so the registry must see them - otherwise a skillset
    referencing one resolves to nothing.
    """
    excluded = excluded or set()
    seen = {}
    for plugins_root in plugin_roots(home):
        try:
            candidates = list(plugins_root.rglob("SKILL.md"))
        except OSError:
            continue
        for md in candidates:
            try:
                rel = md.relative_to(plugins_root)
            except ValueError:
                continue
            parts = rel.parts
            if "skills" not in parts:
                continue
            idx = parts.index("skills")
            if idx + 1 >= len(parts) - 1:
                continue
            plugin = parts[idx - 1] if idx > 0 else "plugin"
            name, desc, ver, block = read_frontmatter(md)
            if not name or name in excluded or name in seen:
                continue
            files = skill_file_evidence(md.parent)
            seen[name] = {
                "name": name,
                "slug": slugify(name),
                "description": (desc or "")[:600],
                "version": ver,
                "source": "plugin",
                "plugin": plugin,
                "path": str(md),
                "tokens_est": chars_to_tokens(md.stat().st_size),
                "auto_invocable": "disable-model-invocation: true" not in block,
                "tags": tag_capabilities(f"{desc} {block}", name, capability_map, files),
                "file_evidence": len(files),
            }
    return sorted(seen.values(), key=lambda s: s["name"])


def slugify(name):
    """Marketplace catalog keys are slugs ('Awesome Ecom Skills' -> 'awesome-ecom-skills')."""
    s = re.sub(r"[^a-zA-Z0-9]+", "-", (name or "").lower())
    return s.strip("-")


def scan_installable(home):
    """Plugins that exist upstream and can be staged for install."""
    path = home / ".claude" / "plugins" / "plugin-directory-cache-v2.json"
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out = []
    for l in (data.get("listings") or []):
        repo = ((l.get("source") or {}).get("repository") or {})
        out.append({
            "plugin": l.get("name"),
            "slug": slugify(l.get("name")),
            "id": l.get("id"),
            "summary": (l.get("summary") or "")[:300],
            "version": (l.get("release") or {}).get("version"),
            "repo": repo.get("url"),
            "repo_path": repo.get("path"),
            "surfaces": l.get("surfaces") or [],
            "reviewed_by": ((l.get("checks") or {}).get("review") or {}).get("by"),
        })
    return out


def load_capability_map(home):
    """Return (capability_map, excluded_skill_names)."""
    p = shared_state_dir() / "capabilities.json"
    if not p.is_file():
        p = home / ".claude" / SKILL_DIR_NAME / "aswath-capability" / "registry" / "capabilities.json"
    if not p.is_file():
        return {}, set()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}, set()
    return data.get("capabilities", {}), set(data.get("_exclude") or [])


def load_ledger(home):
    """Quality scores recorded from past outcomes; overrides cost-blind ranking."""
    p = shared_ledger_path()
    if not p.is_file():
        p = home / ".claude" / SKILL_DIR_NAME / "aswath-capability" / "registry" / "ledger.json"
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("skills", {})
    except (OSError, ValueError):
        return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--home", default=str(Path.home()))
    ap.add_argument("--out", default=None)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    home = Path(args.home)
    cap_map, excluded = load_capability_map(home)
    ledger = load_ledger(home)

    local = scan_local_skills(home, cap_map, excluded)
    plugin_raw = scan_plugin_skills(home, cap_map, excluded)
    market = scan_marketplace(home)

    # Dedup in precedence order: local > plugin > marketplace. Many plugin
    # skills are also installed locally, and the marketplace ships copies of
    # both. The same skill listed three times is one option, not three.
    local_slugs = {slugify(s["name"]) for s in local if s.get("name")}
    plugin_skills = [p for p in plugin_raw if slugify(p.get("name", "")) not in local_slugs]
    plugin_slugs = {slugify(p["name"]) for p in plugin_skills if p.get("name")}
    have = local_slugs | plugin_slugs
    installable = scan_installable(home)

    # A marketplace copy of a skill you already have is not a second option -
    # it is the same skill. Local and plugin copies win; drop the marketplace
    # duplicate so the registry does not double-count it.
    before_count = len(market)
    market = [m for m in market if slugify(m.get("name")) not in have]
    duplicates = before_count - len(market)

    # Tag marketplace entries using the plugin summary; local entries already tagged.
    summaries = {p["plugin"]: p.get("summary", "") for p in installable}
    for m in market:
        blob = f"{m.get('name','')} {summaries.get(m.get('plugin'),'')}"
        m["tags"] = tag_capabilities(blob, m.get("name", ""), cap_map)

    # Merge quality scores from the ledger.
    for entry in local + market:
        score = ledger.get(entry["name"])
        if score:
            entry["quality"] = score

    out_path = Path(args.out) if args.out else (
        shared_registry_path()
    )
    registry = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "counts": {
            "local": len(local),
            "plugin": len(plugin_skills),
            "marketplace": len(market),
            "installable_plugins": len(installable),
            "marketplace_duplicates_suppressed": duplicates,
            "plugin_duplicates_suppressed": len(plugin_raw) - len(plugin_skills),
        },
        "local": sorted(local, key=lambda s: s["name"]),
        "plugin": plugin_skills,
        "marketplace": sorted(market, key=lambda s: (s.get("plugin", ""), s.get("name", ""))),
        "installable": sorted(installable, key=lambda p: p.get("plugin") or ""),
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(registry, indent=1, ensure_ascii=False), encoding="utf-8")

    if not args.quiet:
        c = registry["counts"]
        print(f"registry -> {out_path}")
        print(f"  local skills      : {c['local']}")
        print(f"  plugin skills     : {c['plugin']}")
        print(f"  marketplace skills: {c['marketplace']}")
        print(f"  installable plugs : {c['installable_plugins']}")
        print(f"  mkt duplicates    : {c.get('marketplace_duplicates_suppressed', 0)} (already installed)")
        print(f"  plugin duplicates : {c.get('plugin_duplicates_suppressed', 0)} (already local)")
        untagged = [s["name"] for s in local if not s["tags"]]
        print(f"  local untagged    : {len(untagged)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

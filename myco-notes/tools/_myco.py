"""Shared helpers for the Myco Notes tools. Standard library only; the same code runs on Linux, macOS and Windows."""
import os
import re
import subprocess
import sys

VERSION = "0.13.2"   # the kit version these tools come from; moves with plugin.json and SKILL.md (tests check it)

IS_WINDOWS = os.name == "nt"


def utf8_console():
    """Make printing safe on consoles that are not UTF-8 (Windows)."""
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def git(*args, cwd=None, input=None):
    """Run git, capture UTF-8 output. Returns the CompletedProcess (never raises on a non-zero exit)."""
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", input=input)


def toplevel(start):
    """Root of the git repository that contains `start`, or None."""
    r = git("-C", start, "rev-parse", "--show-toplevel")
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None


def _real(p):
    return os.path.normcase(os.path.realpath(os.path.abspath(p)))


def overlap(a, b):
    """How folder b relates to folder a: 'same', 'inside' (b is inside a), 'contains' (b contains a) or None.
    Compares resolved paths (symbolic links, case on Windows), so two spellings of one folder are the same folder."""
    ra, rb = _real(a), _real(b)
    if ra == rb:
        return "same"
    try:
        common = os.path.commonpath([ra, rb])
    except ValueError:  # different drives on Windows
        return None
    if common == ra:
        return "inside"
    if common == rb:
        return "contains"
    return None


def owned_or_empty(path, marker, looks_like=None):
    """True when `path` does not exist, is an empty folder, holds our `marker` file, or (for folders made before markers
    existed) passes `looks_like(path)`. Tools that empty or overwrite a folder call this first."""
    if not os.path.exists(path):
        return True
    if not os.path.isdir(path):
        return False
    if not os.listdir(path) or os.path.isfile(os.path.join(path, marker)):
        return True
    return bool(looks_like and looks_like(path))


def load_conf(root):
    """Read .myco-notes.conf (shell-style KEY=value, quoted values may span lines). No shell is executed."""
    p = os.path.join(root, ".myco-notes.conf")
    conf = {}
    if not os.path.isfile(p):
        return conf
    with open(p, encoding="utf-8") as fh:
        text = fh.read()
    pat = re.compile(r"^[ \t]*([A-Za-z_][A-Za-z0-9_]*)=", re.M)
    n, pos = len(text), 0
    while True:
        m = pat.search(text, pos)
        if not m:
            break
        j = m.end()
        if j < n and text[j] in "\"'":
            q, k, buf = text[j], j + 1, []
            while k < n and text[k] != q:
                if q == '"' and text[k] == "\\" and k + 1 < n and text[k + 1] in '"\\$`':
                    buf.append(text[k + 1])
                    k += 2
                    continue
                buf.append(text[k])
                k += 1
            val, pos = "".join(buf), k + 1
        else:
            e = text.find("\n", j)
            e = n if e < 0 else e
            val, pos = re.split(r"\s+#", text[j:e])[0].strip(), e
        conf[m.group(1)] = val
    return conf


def frontmatter(text):
    """Optional header at the top of a note (method 10.1): a '---' line, 'key: value' lines, a closing '---' line. No YAML library.

    Returns ({key: (value, line_number)}, number of lines the header occupies); ({}, 0) when the note has none. Keys are lower-cased
    and use underscores; a trailing '# comment' and surrounding quotes are dropped; anything that is not 'key: value' is ignored.
    """
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return {}, 0
    fm = {}
    for i in range(1, min(len(lines), 60)):
        raw = lines[i].rstrip("\r")
        if raw.strip() in ("---", "..."):
            return fm, i + 1
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_-]*)\s*:\s*(.*)$", raw)
        if m:
            fm[m.group(1).lower().replace("-", "_")] = (re.sub(r"\s+#.*$", "", m.group(2)).strip().strip("\"'"), i + 1)
    return {}, 0


def _rel(p):
    """A repository-relative folder name written with forward slashes and no leading or trailing slash."""
    return (p or "").strip().replace("\\", "/").strip("/")


def layout(root, conf=None):
    """Folder layout from .myco-notes.conf, as repository-relative paths with forward slashes.

    No MYCO_DIR (trees made before 0.6.0): notes/, tools/, docs/ and notes-map/ at the repository root.
    MYCO_DIR=myco-notes: that one folder holds the notes themselves plus tools/, docs/ and map/.
    NOTES_DIR, TOOLS_DIR, DOCS_DIR and MAP_DIR each override one folder; DATA_DIRS lists project folders that are not notes;
    HISTORY_DIRS (inside the notes folder) holds records that are not re-checked for dates and references.
    STATE_FILE is the master record's file name inside the notes folder (default state.md; a project may call it homelab.md).
    """
    conf = load_conf(root) if conf is None else conf
    my = _rel(conf.get("MYCO_DIR"))

    def pick(key, legacy, nested):
        return _rel(conf.get(key)) or (nested if my else legacy)

    return {
        "myco": my,
        "notes": pick("NOTES_DIR", "notes", my),
        "tools": pick("TOOLS_DIR", "tools", my + "/tools"),
        "docs": pick("DOCS_DIR", "docs", my + "/docs"),
        "map": pick("MAP_DIR", "notes-map", my + "/map"),
        "state": _rel(conf.get("STATE_FILE")) or "state.md",
        "data": [d for d in (_rel(x) for x in conf.get("DATA_DIRS", "").split()) if d],
        "history": [d for d in (_rel(x) for x in conf.get("HISTORY_DIRS", "archive logs").split()) if d],
    }


def record_kit_version(root):
    """Write KIT_VERSION=<these tools' version> into the tree's .myco-notes.conf (bootstrap and relocate call it)."""
    cp = os.path.join(root, ".myco-notes.conf")
    if not os.path.isfile(cp):
        return
    with open(cp, encoding="utf-8", newline="") as fh:
        text = fh.read()
    with open(cp, "w", encoding="utf-8", newline="") as fh:
        fh.write(set_conf_key(text, "KIT_VERSION", VERSION))


def set_conf_key(text, key, value):
    """Return the conf text with KEY set to value: the first uncommented KEY= line is replaced, otherwise the line is appended."""
    new = f'{key}="{value}"' if (" " in value) else f"{key}={value}"
    pat = re.compile(rf"^[ \t]*{re.escape(key)}=.*$", re.M)
    if pat.search(text):
        return pat.sub(lambda _m: new, text, count=1)
    return text + ("" if text.endswith("\n") or not text else "\n") + new + "\n"


def run_shell(cmd, cwd, timeout=None):
    """Run a command line from the configuration: bash on Linux and macOS, the platform shell (cmd.exe) on Windows."""
    args = cmd if IS_WINDOWS else ["bash", "-c", cmd]
    return subprocess.run(args, cwd=cwd, shell=IS_WINDOWS, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)


def human_size(nbytes):
    for unit in ("B", "K", "M", "G", "T"):
        if nbytes < 1024 or unit == "T":
            return f"{nbytes:.0f}{unit}" if unit == "B" else f"{nbytes:.1f}{unit}"
        nbytes /= 1024

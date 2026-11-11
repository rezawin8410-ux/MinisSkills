---
name: dynamic-ui
description: Generate interactive UI for a task without writing an app. Use when the user wants a dashboard, a form, a small tool with buttons, or any visual interface rather than plain text output.
version: 2.0.0
---
# Dynamic UI

Build a single self-contained HTML file, host it in the workspace, and open
it in the in-app browser. No build step, no server.

## Layout

```
/var/minis/workspace/myapp/
  index.html       # entry — references the others with relative paths
  style.css
  app.js
```

Then: `browser_use navigate minis://workspace/myapp/index.html`

## What works

- Relative paths resolve against the minis:// base automatically; absolute
  `minis://attachments/x.png` cross-references also work.
- Vanilla JS, CSS, web fonts, canvas.
- A Python step can pre-compute data into a .json the page fetches.

## When you need a backend

Run a tiny local server and point the page at `http://127.0.0.1:PORT`:

```sh
python3 -m http.server 8765 > /dev/null 2>&1 &
```
Redirect stdout/stderr — without it the server dies with SIGPIPE when the
shell exits. And remember the process dies when the app is suspended.

## Rules

1. One task = one folder; keep it self-contained and deletable.
2. Set the viewport with `set_viewport` before screenshotting a wide layout.
3. RTL/Persian UI: `<html lang="fa" dir="rtl">`.
4. This is a UI *for the current task*, not an app you ship — say so.

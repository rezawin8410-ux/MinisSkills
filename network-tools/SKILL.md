---
name: network-tools
description: Network diagnostics and access from the sandbox. Use when checking connectivity, downloading files, testing an API, or when the network "just doesn't work". Knows which tools are blocked and what to use instead.
version: 2.0.0
---

# Network Tools

## What is blocked here

- **ICMP** — `ping`, `traceroute` hang forever. Do not use them.
- **Raw sockets below TCP** — unavailable in PRoot.

Use this instead of ping:

```sh
curl -sS -o /dev/null -w '%{http_code}\n' https://example.com/
```

## Connectivity troubleshooting

```sh
# 1. DNS (resets on every sandbox restart — the #1 cause of "no network")
printf 'nameserver 8.8.8.8\nnameserver 1.1.1.1\n' > /etc/resolv.conf

# 2. Verify resolution
nslookup api.github.com

# 3. Verify reachability with HTTP code
curl -sS -o /dev/null -w '%{http_code}\n' https://github.com/

# 4. TLS failing on a known-good host? Update CA store
apk add ca-certificates && update-ca-certificates
```

## Downloading

```sh
curl -sSL -m 600 -o file.tar.gz <url>         # follow redirects, 10m timeout
curl -sSL -C - -o file <url>                  # resume a partial download
wget --tries=3 --timeout=60 <url>             # retries
```

For large files (100MB+), resume support matters — a dropped connection mid
-download otherwise restarts from zero.

## Reading a webpage

Prefer the lightweight fetcher over the full browser when no interaction is
needed — it costs no UI resources:

```
python3 tools/webtool.py fetch "url"
python3 tools/webtool.py search "query"
```

Only use `browser_use` when clicking, forms, login, or screenshots are
required.

## Rules

1. Never hang on `ping` — it blocks the whole turn with no output.
2. Always set a timeout (`-m`) — a silent hang is worse than a fast failure.
3. For GitHub API, add `Accept: application/vnd.github+json` and a UA header.
4. Rate limits: unauthenticated GitHub search is 10 req/min. Batch queries,
   dedupe results locally, sleep between calls.

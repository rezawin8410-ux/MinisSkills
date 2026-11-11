import { execFile } from 'node:child_process';
import { promises as fs } from 'node:fs';
import os from 'node:os';
import path from 'node:path';

export interface RemoteTarget {
  cloneUrl: string;
  ref?: string;
  subdir?: string;
  label: string;
}

/**
 * Recognises `github:owner/repo[/sub/dir][#ref]`, `gitlab:owner/repo` and
 * `https://github.com/owner/repo[/tree/<ref>/<sub/dir>]` style arguments.
 */
export function parseRemote(arg: string): RemoteTarget | undefined {
  const target = parseRemoteUnchecked(arg);
  // Defence in depth: nothing that reaches `git` may look like an option.
  if (target && [target.ref, target.subdir].some((v) => v !== undefined && (v.startsWith('-') || v.split('/').includes('..')))) {
    return undefined;
  }
  return target;
}

function parseRemoteUnchecked(arg: string): RemoteTarget | undefined {
  let m = /^(github|gitlab):([\w.-]+)\/([\w.-]+?)(?:\.git)?((?:\/[^#\s]+)?)(?:#([\w./-]+))?$/.exec(arg);
  if (m) {
    const host = m[1] === 'github' ? 'github.com' : 'gitlab.com';
    const subdir = m[4] ? m[4].replace(/^\//, '') : undefined;
    return { cloneUrl: `https://${host}/${m[2]}/${m[3]}.git`, ref: m[5], subdir, label: `${m[1]}:${m[2]}/${m[3]}${m[4] ?? ''}` };
  }
  m =
    /^https:\/\/(github\.com|gitlab\.com)\/([\w.-]+)\/([\w.-]+?)(?:\.git)?(?:\/(?:-\/)?(?:tree|blob)\/([\w.-]+)(\/[^\s?#]*)?)?\/?$/.exec(
      arg,
    );
  if (m) {
    const subdir = m[5] ? m[5].replace(/^\//, '').replace(/\/SKILL\.md$/, '') : undefined;
    return {
      cloneUrl: `https://${m[1]}/${m[2]}/${m[3]}.git`,
      ref: m[4],
      subdir: subdir || undefined,
      label: `${m[1]}/${m[2]}/${m[3]}${m[5] ?? ''}`,
    };
  }
  if (/^https:\/\/\S+\.git$/.test(arg)) return { cloneUrl: arg, label: arg };
  return undefined;
}

/** Large monorepos can take minutes to shallow-clone on a slow link. */
const CLONE_TIMEOUT_MS = 10 * 60_000;

function git(args: string[], cwd?: string): Promise<void> {
  return new Promise((resolve, reject) => {
    execFile(
      'git',
      args,
      {
        cwd,
        timeout: CLONE_TIMEOUT_MS,
        // Ignore the user's git config: global filter/smudge drivers or hooks must never run
        // on attacker-controlled content.
        env: {
          ...process.env,
          GIT_TERMINAL_PROMPT: '0',
          GIT_LFS_SKIP_SMUDGE: '1',
          GIT_CONFIG_NOSYSTEM: '1',
          GIT_CONFIG_GLOBAL: os.devNull,
        },
        maxBuffer: 10 * 1024 * 1024,
      },
      (err, _stdout, stderr) => {
        if (!err) return resolve();
        if (err.killed) {
          return reject(
            new Error(
              `git clone timed out after ${CLONE_TIMEOUT_MS / 60_000} minutes (very large repository?); clone it yourself and scan the directory`,
            ),
          );
        }
        reject(new Error(stderr.trim() || err.message));
      },
    );
  });
}

/** Shallow-clones the target into a temporary directory. Call `cleanup` when done. */
export async function fetchRemote(target: RemoteTarget): Promise<{ dir: string; cleanup: () => Promise<void> }> {
  const tmp = await fs.mkdtemp(path.join(os.tmpdir(), 'bekci-'));
  const cleanup = () => fs.rm(tmp, { recursive: true, force: true });
  try {
    const repo = path.join(tmp, 'repo');
    const args = [
      '-c',
      'core.symlinks=false',
      '-c',
      `core.hooksPath=${os.devNull}`,
      '-c',
      'core.fsmonitor=false',
      '-c',
      'protocol.file.allow=never',
      'clone',
      '--depth',
      '1',
      '--quiet',
      '--no-tags',
    ];
    if (target.ref) args.push('--branch', target.ref);
    args.push('--', target.cloneUrl, repo);
    await git(args);
    const dir = target.subdir ? path.join(repo, target.subdir) : repo;
    const resolved = path.resolve(dir);
    if (resolved !== path.resolve(repo) && !resolved.startsWith(path.resolve(repo) + path.sep))
      throw new Error('Invalid sub-directory');
    await fs.stat(dir);
    return { dir, cleanup };
  } catch (e) {
    await cleanup();
    throw new Error(`Could not fetch ${target.label}: ${(e as Error).message}`);
  }
}

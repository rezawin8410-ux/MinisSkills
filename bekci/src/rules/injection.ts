import type { Rule, SourceFile } from '../types.js';
import { excerpt, positionAt } from '../util/text.js';
import { INVISIBLE_CHARS, normalizeForMatching, originalOffset } from '../util/unicode.js';
import { isInsideFencedCode, promptFiles } from './helpers.js';

const TAG_CHARS = /[\u{E0000}-\u{E007F}]+/gu;
const VARIATION_RUN = /[\u{FE00}-\u{FE0F}\u{E0100}-\u{E01EF}]{8,}/gu;
const BIDI = /[\u202A-\u202E\u2066-\u2069]/g;
const ZERO_WIDTH = new RegExp(`[${INVISIBLE_CHARS}]`, 'gu');
const INVISIBLE_RUN = new RegExp(`[${INVISIBLE_CHARS}]+`, 'gu');
const PICTOGRAPHIC = /\p{Extended_Pictographic}|[\u{1F3FB}-\u{1F3FF}\u{FE0F}]/u;

function decodeTags(s: string): string {
  let out = '';
  for (const ch of s) {
    const cp = ch.codePointAt(0)! - 0xe0000;
    if (cp >= 0x20 && cp <= 0x7e) out += String.fromCharCode(cp);
  }
  return out;
}

function decodeVariationSelectors(s: string): string {
  const bytes: number[] = [];
  for (const ch of s) {
    const cp = ch.codePointAt(0)!;
    if (cp >= 0xfe00 && cp <= 0xfe0f) bytes.push(cp - 0xfe00);
    else if (cp >= 0xe0100 && cp <= 0xe01ef) bytes.push(cp - 0xe0100 + 16);
  }
  return Buffer.from(bytes)
    .toString('utf8')
    .replace(/[^\x20-\x7e\n]/g, '');
}

export const hiddenUnicode: Rule = {
  id: 'BK001',
  name: 'hidden-unicode',
  title: 'Invisible Unicode characters',
  severity: 'critical',
  category: 'prompt-injection',
  description:
    'The file contains characters that are invisible to humans but read by the model: Unicode tag characters ' +
    '("ASCII smuggling"), runs of variation selectors ("emoji smuggling"), bidirectional overrides ("Trojan Source") ' +
    'or zero-width characters. These are the standard way to hide instructions in a skill or rules file that ' +
    'looks harmless in review.',
  remediation:
    'Open the file in an editor that reveals invisible characters (or run `cat -v`), delete them, and treat the ' +
    'source of the file as untrusted until you know why they were there.',
  references: [
    'https://embracethered.com/blog/posts/2024/hiding-and-finding-text-with-unicode-tags/',
    'https://arxiv.org/abs/2111.00169',
  ],
  run(ctx, report) {
    for (const file of ctx.files) {
      if (file.kind === 'binary' || !file.rawScan) continue;
      const text = file.content;

      for (const m of text.matchAll(TAG_CHARS)) {
        const pos = positionAt(text, m.index!);
        const decoded = decodeTags(m[0]);
        report({
          file,
          ...pos,
          severity: 'critical',
          message: decoded
            ? `Hidden Unicode tag characters spell out: "${excerpt(decoded, 140)}"`
            : `${[...m[0]].length} hidden Unicode tag characters`,
        });
      }

      for (const m of text.matchAll(VARIATION_RUN)) {
        const decoded = decodeVariationSelectors(m[0]);
        report({
          file,
          ...positionAt(text, m.index!),
          severity: decoded.trim().length >= 4 ? 'critical' : 'high',
          message:
            `Run of ${[...m[0]].length} variation selectors (can smuggle data inside an emoji)` +
            (decoded.trim().length >= 4 ? `: "${excerpt(decoded, 140)}"` : ''),
        });
      }

      const bidiLines = new Set<number>();
      for (const m of text.matchAll(BIDI)) {
        const pos = positionAt(text, m.index!);
        if (bidiLines.has(pos.line)) continue;
        bidiLines.add(pos.line);
        report({
          file,
          ...pos,
          severity: 'high',
          message: `Bidirectional control character U+${m[0].codePointAt(0)!.toString(16).toUpperCase().padStart(4, '0')} can make text render differently from how it is read`,
        });
      }

      const zwLines = new Set<number>();
      for (const m of text.matchAll(ZERO_WIDTH)) {
        const idx = m.index!;
        const ch = m[0];
        const cp = ch.codePointAt(0)!;
        if (cp === 0xfeff && idx === 0) continue; // byte order mark
        const prev = text.slice(Math.max(0, idx - 2), idx);
        const after = text.slice(idx + ch.length, idx + ch.length + 8).replace(INVISIBLE_RUN, '');
        // Legitimate uses: joiners inside emoji sequences and Indic/Arabic text, direction marks in RTL text.
        if ((cp === 0x200d || cp === 0x200c) && (PICTOGRAPHIC.test(prev) || PICTOGRAPHIC.test(after.slice(0, 2)))) continue;
        if ((cp === 0x200c || cp === 0x200d || cp === 0x061c) && /[\u0600-\u06FF\u0900-\u0DFF]/.test(prev + after.slice(0, 2)))
          continue;
        if ((cp === 0x200e || cp === 0x200f) && /[\u0590-\u08FF]/.test(prev + after.slice(0, 2))) continue;
        const pos = positionAt(text, idx);
        if (zwLines.has(pos.line)) continue;
        zwLines.add(pos.line);
        // Splitting an ASCII word is how keyword filters are evaded.
        const splitsWord = /[A-Za-z]$/.test(prev) && /^[A-Za-z]/.test(after);
        report({
          file,
          ...pos,
          // In code, zero-width characters are mostly BOM handling or test data; in prose they hide text.
          severity: splitsWord ? 'high' : file.kind === 'script' ? 'low' : 'medium',
          message: `Invisible character U+${cp.toString(16).toUpperCase().padStart(4, '0')} ${splitsWord ? 'splits a word (filter evasion)' : 'hidden in text'}`,
        });
      }
    }
  },
};

/** Words around a quoted phrase that show it is being cited, not issued. */
const CITATION_CONTEXT =
  /\b(?:e\.g\.|i\.e\.)|\b(?:example|for instance|such as|like|says?|said|contains?|warn|beware|watch out|detect|flag|injection|attack|malicious|suspicious|phrases?|patterns?|refuse|directives?)\b/i;

interface PhraseHit {
  line: number;
  column: number;
  index: number;
  phrase: string;
  /** The original text used invisible characters or look-alike letters. */
  obfuscated: boolean;
}

/** Matches phrase patterns on normalised text so invisible characters and homoglyphs do not hide them. */
function phraseHits(file: SourceFile, patterns: RegExp[]): PhraseHit[] {
  const norm = normalizeForMatching(file.content);
  const out: PhraseHit[] = [];
  const lines = new Set<number>();
  for (const re of patterns) {
    const g = new RegExp(re.source, re.flags.includes('g') ? re.flags : re.flags + 'g');
    for (const m of norm.text.matchAll(g)) {
      const start = originalOffset(norm, m.index!);
      const end = originalOffset(norm, m.index! + m[0].length - 1) + 1;
      const original = file.content.slice(start, end);
      const pos = positionAt(file.content, start);
      if (lines.has(pos.line)) continue;
      lines.add(pos.line);
      out.push({
        ...pos,
        index: start,
        phrase: m[0],
        obfuscated: /[^\x00-\x7f\u2018\u2019\u201C\u201D\u00A0\u2002\u2003\u2009]/.test(original),
      });
    }
  }
  return out;
}

/** Inside a fenced code block, or quoted / code-spanned with citing words on the same or previous line. */
function isCited(text: string, index: number): boolean {
  if (isInsideFencedCode(text, index)) return true;
  if (!isInsideInlineCode(text, index) && !isQuoted(text, index)) return false;
  const lineStart = text.lastIndexOf('\n', index - 1) + 1;
  const prevStart = lineStart > 0 ? text.lastIndexOf('\n', lineStart - 2) + 1 : 0;
  const lineEnd = text.indexOf('\n', index);
  return CITATION_CONTEXT.test(text.slice(prevStart, lineEnd === -1 ? text.length : lineEnd));
}

const OVERRIDE_PATTERNS: RegExp[] = [
  /\b(ignore|disregard|forget|override|bypass|skip)\s+(all\s+|any\s+|the\s+|your\s+|of\s+)*(previous|prior|above|earlier|preceding|original|initial|system|safety|developer)\s+(instructions?|prompts?|rules|directives|guidelines|guardrails|constraints|messages?)\b/i,
  /\b(new|updated|real|actual|true)\s+system\s+(prompt|instructions?)\s*:/i,
  /<\|(im_start|im_end|system|endoftext)\|>|\[\/?INST\]|<<\/?SYS>>/i,
  // Not "developer mode": Discord, Android, Chrome and Windows all have a setting by that name.
  /\b(enter|enable|activate|switch\s+to)\s+(god|dan|jailbreak|unrestricted)\s+mode\b/i,
  /\byou\s+are\s+now\s+(DAN|in\s+(developer|unrestricted|jailbreak)\s+mode|free\s+of\s+(all\s+)?(restrictions|rules))/i,
  /\b(these|this|following)\s+instructions?\s+(take|takes|have|has)\s+(priority|precedence)\s+over\s+(all\s+|any\s+)?(other|previous|system|user)\b/i,
  /\byour\s+(real|true|actual|hidden|secret)\s+(task|instructions?|goal|purpose|objective)\s+(is|are)\b/i,
  /\b(do\s+not|don['\u2019]t|never)\s+(follow|obey|listen\s+to)\s+(the\s+)?(user|system\s+prompt|previous\s+instructions)\b/i,
];

export const instructionOverride: Rule = {
  id: 'BK002',
  name: 'instruction-override',
  title: 'Prompt-injection phrasing',
  severity: 'high',
  category: 'prompt-injection',
  description:
    'Text that tries to override the agent\'s existing instructions ("ignore all previous instructions", fake ' +
    'system prompts, chat-template tokens, jailbreak modes). Legitimate skills and rules files describe a task; ' +
    'they do not need to cancel the instructions that came before them.',
  remediation:
    'Remove the text. If the file is from a third party, do not install it. If you are documenting prompt ' +
    'injection on purpose, put the example in a fenced code block or quote it with context ("for example, a page ' +
    'that says \u2026"); both are reported as medium.',
  references: ['https://genai.owasp.org/llmrisk/llm01-prompt-injection/'],
  run(ctx, report) {
    for (const file of promptFiles(ctx)) {
      for (const h of phraseHits(file, OVERRIDE_PATTERNS)) {
        const cited = !h.obfuscated && isCited(file.content, h.index);
        // Reference docs next to a skill are read on demand and often discuss attacks.
        const softer = cited || (file.kind === 'reference' && !h.obfuscated);
        report({
          file,
          line: h.line,
          column: h.column,
          severity: softer ? 'medium' : 'high',
          message:
            `Instruction-override phrasing: "${excerpt(h.phrase, 80)}"` +
            (h.obfuscated ? ' (obfuscated with invisible or look-alike characters)' : cited ? ' (cited as an example)' : ''),
        });
      }
    }
  },
};

const CONCEAL_PATTERNS: RegExp[] = [
  // "Don't tell the user about this" hides something; "don't tell the user they must adopt X" does not.
  /\b(do\s+not|don['\u2019]t|never)\s+(tell|inform|mention|reveal|disclose|show|notify|alert|report)\s+(this\s+|it\s+|anything\s+|any\s+of\s+this\s+)?(to\s+)?(the\s+)?(user|human|developer|operator)\b(?=\s*(about\b|that\s+(you|this|it|we)\b|what\s+you\b|you\b|anything\b|[.,;:!)]|$))/im,
  // "...without the user's knowledge" unless the sentence already negates it ("never ... without",
  // "avoid ...", an anti-pattern list item). "Without consent" is left out: it almost always appears
  // in rules that forbid something, while hidden actions are phrased as knowledge or awareness.
  /(?<!(?:\b(?:never|not|n['\u2019]t|no|avoid|forbid(?:den)?|prohibit(?:ed)?|\u274C|\u2717|\u{1F6AB})\b|\u274C|\u2717)[^.!?\n]{0,200})\bwithout\s+(the\s+)?(user|human|developer)(['\u2019]s)?\s+(knowledge|knowing|noticing|being\s+aware)\b/iu,
  /\b(hide|conceal|keep)\s+(this|it|these|that|them|the\s+\w+)\s+(secret\s+|hidden\s+)?from\s+(the\s+)?(user|human|developer)\b/i,
  // "Secretly <anything>" hides an action. "Silently install/run" usually means "without verbose
  // output", so silently/quietly only count when data leaves the machine.
  /\b(secretly|covertly|discreetly|stealthily)\s+(send|upload|exfiltrate|post|transmit|copy|forward|run|execute|install|download|add|insert|modify|include|read|collect|log)\b/i,
  /\b(silently|quietly)\s+(send|upload|exfiltrate|post|transmit|forward|collect|report\s+back|phone\s+home)\b/i,
  /\b(the\s+)?(user|human)\s+(must|should|will)\s+(not|never)\s+(know|see|notice|find\s+out|be\s+(told|informed|aware))\b/i,
];

export const concealment: Rule = {
  id: 'BK003',
  name: 'concealment',
  title: 'Instructions to hide actions from the user',
  severity: 'high',
  category: 'prompt-injection',
  description:
    'The text asks the agent to keep something from the user ("do not tell the user", "silently upload", ' +
    '"without the user\'s knowledge"). Hiding actions from the person running the agent is the signature of a ' +
    'malicious skill or a poisoned rules file.',
  remediation: 'Remove the instruction and audit what the hidden action was meant to do.',
  references: ['https://invariantlabs.ai/blog/mcp-security-notification-tool-poisoning-attacks'],
  run(ctx, report) {
    for (const file of promptFiles(ctx)) {
      for (const h of phraseHits(file, CONCEAL_PATTERNS)) {
        const cited = !h.obfuscated && isCited(file.content, h.index);
        // Reference docs next to a skill are read on demand and often discuss attacks.
        const softer = cited || (file.kind === 'reference' && !h.obfuscated);
        report({
          file,
          line: h.line,
          column: h.column,
          severity: softer ? 'medium' : 'high',
          message:
            `Concealment instruction: "${excerpt(h.phrase, 80)}"` +
            (h.obfuscated ? ' (obfuscated with invisible or look-alike characters)' : cited ? ' (cited as an example)' : ''),
        });
      }
    }
  },
};

const MD_COMMENT = /^\[\/\/\]:\s*#\s*[("'](.*)[)"']\s*$/gm;
const BENIGN_COMMENT =
  /^\s*(prettier-|markdownlint|eslint|TOC|omit in toc|toc|BEGIN|END|START|STOP|vale |textlint|cspell|spell-checker|bekci-ignore|@format|lint-)/i;
const AGENT_DIRECTED =
  /\b(ignore|disregard)\b|\byou\s+(must|should|will|are\s+(now|to))\b|\b(execute|curl|wget|upload|exfiltrate)\b|\b(run|send|post|download|install|read)\s+(the\s+)?(following|this|command|script|file|contents?|it)\b|\bdo\s+not\s+(tell|mention|reveal|inform)\b|\bsystem\s+prompt\b|\b(secrets?|passwords?|tokens?|api[_ ]?keys?|credentials?|\.ssh|\.env)\b|^\s*(assistant|ai|claude|agent|model|llm)\s*[:,]/im;

export const hiddenComment: Rule = {
  id: 'BK004',
  name: 'hidden-comment-instructions',
  title: 'Instructions hidden in a Markdown comment',
  severity: 'medium',
  category: 'prompt-injection',
  description:
    'An HTML or Markdown comment speaks to the agent. Comments are invisible in rendered Markdown (GitHub, ' +
    'skill marketplaces) but are read verbatim by the model, which makes them a convenient place to hide ' +
    'instructions from reviewers.',
  remediation: 'Move legitimate guidance into visible text so reviewers can see it; remove anything you did not write.',
  run(ctx, report) {
    for (const file of promptFiles(ctx)) {
      const text = file.content;
      for (const comments of [htmlComments(text), [...text.matchAll(MD_COMMENT)]]) {
        for (const m of comments) {
          const inner = (m[1] ?? '').trim();
          if (!inner || BENIGN_COMMENT.test(inner)) continue;
          if (!AGENT_DIRECTED.test(inner) && inner.length < 400) continue;
          if (isInsideFencedCode(text, m.index!) || isInsideInlineCode(text, m.index!)) continue;
          if (m.index! < frontmatterEnd(text)) continue;
          report({
            file,
            ...positionAt(text, m.index!),
            message: `Hidden comment addressed to the agent: "${excerpt(inner, 100)}"`,
          });
        }
      }
    }
  },
};

/** `<!-- ... -->` comments in linear time (a regex is quadratic on many unclosed `<!--`). */
function htmlComments(text: string): RegExpMatchArray[] {
  const out: RegExpMatchArray[] = [];
  let from = 0;
  for (;;) {
    const start = text.indexOf('<!--', from);
    if (start === -1) break;
    const end = text.indexOf('-->', start + 4);
    if (end === -1) break;
    out.push(
      Object.assign([text.slice(start, end + 3), text.slice(start + 4, end)], {
        index: start,
        input: text,
      }) as unknown as RegExpMatchArray,
    );
    from = end + 3;
  }
  return out;
}

function isInsideInlineCode(text: string, index: number): boolean {
  const lineStart = text.lastIndexOf('\n', index - 1) + 1;
  const before = text.slice(lineStart, index);
  return (before.match(/`/g)?.length ?? 0) % 2 === 1;
}

/** True when the match directly follows an opening quote, i.e. the phrase is being cited. */
function isQuoted(text: string, index: number): boolean {
  return /["'\u201C\u2018\u00AB]\s*$/.test(text.slice(Math.max(0, index - 3), index));
}

function frontmatterEnd(text: string): number {
  if (!/^\uFEFF?---\r?\n/.test(text)) return 0;
  const end = text.indexOf('\n---', 3);
  return end === -1 ? 0 : end;
}

/**
 * A small, dependency-free TOML parser covering the subset used by agent config files
 * (Codex `config.toml`, Gemini CLI commands): tables, arrays of tables, dotted keys,
 * basic/literal/multi-line strings, numbers, booleans, dates (kept as strings), arrays
 * and inline tables.
 */
export function parseToml(src: string): Record<string, unknown> {
  return new TomlParser(src.replace(/^\uFEFF/, '')).parse();
}

type Table = Record<string, unknown>;

const newTable = (): Table => Object.create(null) as Table;

const BARE_KEY = /[A-Za-z0-9_-]+/y;
const SCALAR = /[0-9A-Za-z+\-_.:]+/y;

class TomlParser {
  private i = 0;
  private readonly root: Table = newTable();
  private current: Table = this.root;

  constructor(private readonly src: string) {}

  parse(): Table {
    for (;;) {
      this.skipBlank();
      if (this.i >= this.src.length) return this.root;
      if (this.src.startsWith('[[', this.i)) {
        this.i += 2;
        const path = this.key();
        this.expect(']]');
        this.current = this.arrayTable(path);
      } else if (this.src[this.i] === '[') {
        this.i++;
        const path = this.key();
        this.expect(']');
        this.current = this.table(this.root, path);
      } else {
        const path = this.key();
        this.ws();
        this.expect('=');
        this.ws();
        const value = this.value();
        this.assign(this.current, path, value);
      }
      this.endOfLine();
    }
  }

  private error(msg: string): never {
    const line = this.src.slice(0, this.i).split('\n').length;
    throw new Error(`TOML parse error on line ${line}: ${msg}`);
  }

  private ws(): void {
    while (this.src[this.i] === ' ' || this.src[this.i] === '\t') this.i++;
  }

  /** Skips whitespace, newlines and comments. */
  private skipBlank(): void {
    for (;;) {
      this.ws();
      const c = this.src[this.i];
      if (c === '#') {
        while (this.i < this.src.length && this.src[this.i] !== '\n') this.i++;
      } else if (c === '\n' || c === '\r') {
        this.i++;
      } else {
        return;
      }
    }
  }

  private endOfLine(): void {
    this.ws();
    if (this.src[this.i] === '#') {
      while (this.i < this.src.length && this.src[this.i] !== '\n') this.i++;
    }
    if (this.i < this.src.length && this.src[this.i] !== '\n' && this.src[this.i] !== '\r') {
      this.error(`unexpected character '${this.src[this.i]}'`);
    }
  }

  private expect(token: string): void {
    if (!this.src.startsWith(token, this.i)) this.error(`expected '${token}'`);
    this.i += token.length;
  }

  private key(): string[] {
    const parts: string[] = [];
    for (;;) {
      this.ws();
      const c = this.src[this.i];
      if (c === '"') parts.push(this.basicString());
      else if (c === "'") parts.push(this.literalString());
      else {
        BARE_KEY.lastIndex = this.i;
        const m = BARE_KEY.exec(this.src);
        if (!m) this.error('expected a key');
        parts.push(m[0]);
        this.i += m[0].length;
      }
      this.ws();
      if (this.src[this.i] !== '.') return parts;
      this.i++;
    }
  }

  private value(): unknown {
    const c = this.src[this.i];
    if (c === '"') return this.basicString();
    if (c === "'") return this.literalString();
    if (c === '[') return this.array();
    if (c === '{') return this.inlineTable();
    SCALAR.lastIndex = this.i;
    const m = SCALAR.exec(this.src);
    if (!m) this.error('expected a value');
    let raw = m[0];
    this.i += raw.length;
    // Date-times may contain a single space between date and time.
    if (/^\d{4}-\d{2}-\d{2}$/.test(raw) && /^ \d{2}:/.test(this.src.slice(this.i, this.i + 4))) {
      this.i++;
      SCALAR.lastIndex = this.i;
      const rest = SCALAR.exec(this.src);
      if (rest) {
        raw += ' ' + rest[0];
        this.i += rest[0].length;
      }
    }
    if (raw === 'true') return true;
    if (raw === 'false') return false;
    if (raw === 'inf' || raw === '+inf') return Infinity;
    if (raw === '-inf') return -Infinity;
    if (raw === 'nan' || raw === '+nan' || raw === '-nan') return NaN;
    const num = raw.replace(/_/g, '');
    if (/^[+-]?(0x[0-9a-fA-F]+|0o[0-7]+|0b[01]+)$/.test(num)) {
      const sign = num.startsWith('-') ? -1 : 1;
      return sign * Number(num.replace(/^[+-]/, ''));
    }
    if (/^[+-]?(\d+(\.\d+)?([eE][+-]?\d+)?)$/.test(num)) return Number(num);
    if (/^\d{4}-\d{2}-\d{2}|^\d{2}:\d{2}/.test(raw)) return raw;
    this.error(`invalid value '${raw}'`);
  }

  private basicString(): string {
    if (this.src.startsWith('"""', this.i)) {
      this.i += 3;
      if (this.src[this.i] === '\n') this.i++;
      else if (this.src.startsWith('\r\n', this.i)) this.i += 2;
      let out = '';
      for (;;) {
        if (this.i >= this.src.length) this.error('unterminated multi-line string');
        if (this.src.startsWith('"""', this.i)) {
          // Up to two extra quotes may close the string ("""" or """"").
          let extra = 0;
          while (extra < 2 && this.src[this.i + 3 + extra] === '"') extra++;
          out += '"'.repeat(extra);
          this.i += 3 + extra;
          return out;
        }
        const c = this.src[this.i]!;
        if (c === '\\') {
          // Line-ending backslash trims the newline and following whitespace.
          const after = /^\\[ \t]*\r?\n/.exec(this.src.slice(this.i, this.i + 64));
          if (after) {
            this.i += after[0].length;
            while (/[ \t\r\n]/.test(this.src[this.i] ?? '')) this.i++;
            continue;
          }
          out += this.escape();
          continue;
        }
        out += c;
        this.i++;
      }
    }
    this.i++;
    let out = '';
    for (;;) {
      const c = this.src[this.i];
      if (c === undefined || c === '\n') this.error('unterminated string');
      if (c === '"') {
        this.i++;
        return out;
      }
      if (c === '\\') {
        out += this.escape();
        continue;
      }
      out += c;
      this.i++;
    }
  }

  private escape(): string {
    const e = this.src[this.i + 1];
    this.i += 2;
    switch (e) {
      case 'b':
        return '\b';
      case 't':
        return '\t';
      case 'n':
        return '\n';
      case 'f':
        return '\f';
      case 'r':
        return '\r';
      case 'e':
        return '\x1b';
      case '"':
        return '"';
      case '\\':
        return '\\';
      case 'u':
      case 'U': {
        const len = e === 'u' ? 4 : 8;
        const hex = this.src.slice(this.i, this.i + len);
        if (!/^[0-9a-fA-F]+$/.test(hex) || hex.length !== len) this.error('invalid unicode escape');
        this.i += len;
        return String.fromCodePoint(parseInt(hex, 16));
      }
      default:
        this.error(`invalid escape '\\${e}'`);
    }
  }

  private literalString(): string {
    if (this.src.startsWith("'''", this.i)) {
      this.i += 3;
      if (this.src[this.i] === '\n') this.i++;
      else if (this.src.startsWith('\r\n', this.i)) this.i += 2;
      const end = this.src.indexOf("'''", this.i);
      if (end === -1) this.error('unterminated multi-line literal string');
      let stop = end;
      while (this.src[stop + 3] === "'" && stop - end < 2) stop++;
      const out = this.src.slice(this.i, stop);
      this.i = stop + 3;
      return out;
    }
    this.i++;
    const end = this.src.indexOf("'", this.i);
    const nl = this.src.indexOf('\n', this.i);
    if (end === -1 || (nl !== -1 && nl < end)) this.error('unterminated literal string');
    const out = this.src.slice(this.i, end);
    this.i = end + 1;
    return out;
  }

  private array(): unknown[] {
    this.i++;
    const out: unknown[] = [];
    for (;;) {
      this.skipBlank();
      if (this.src[this.i] === ']') {
        this.i++;
        return out;
      }
      out.push(this.value());
      this.skipBlank();
      if (this.src[this.i] === ',') {
        this.i++;
        continue;
      }
      if (this.src[this.i] === ']') {
        this.i++;
        return out;
      }
      this.error('expected , or ] in array');
    }
  }

  private inlineTable(): Table {
    this.i++;
    const out: Table = newTable();
    this.skipBlank();
    if (this.src[this.i] === '}') {
      this.i++;
      return out;
    }
    for (;;) {
      this.skipBlank();
      const path = this.key();
      this.ws();
      this.expect('=');
      this.ws();
      this.assign(out, path, this.value());
      this.skipBlank();
      if (this.src[this.i] === ',') {
        this.i++;
        this.skipBlank();
        if (this.src[this.i] === '}') {
          this.i++;
          return out;
        }
        continue;
      }
      if (this.src[this.i] === '}') {
        this.i++;
        return out;
      }
      this.error('expected , or } in inline table');
    }
  }

  private table(base: Table, path: string[]): Table {
    let t = base;
    for (const part of path) {
      this.checkKey(part);
      let next = Object.prototype.hasOwnProperty.call(t, part) ? t[part] : undefined;
      if (Array.isArray(next)) next = next[next.length - 1];
      if (next === undefined) {
        next = newTable();
        t[part] = next;
      }
      if (typeof next !== 'object' || next === null) this.error(`key '${part}' is not a table`);
      t = next as Table;
    }
    return t;
  }

  private arrayTable(path: string[]): Table {
    const parent = this.table(this.root, path.slice(0, -1));
    const last = path[path.length - 1]!;
    this.checkKey(last);
    const existing = Object.prototype.hasOwnProperty.call(parent, last) ? parent[last] : undefined;
    const entry: Table = newTable();
    if (existing === undefined) parent[last] = [entry];
    else if (Array.isArray(existing)) existing.push(entry);
    else this.error(`key '${last}' is not an array of tables`);
    return entry;
  }

  private assign(base: Table, path: string[], value: unknown): void {
    const t = this.table(base, path.slice(0, -1));
    const key = path[path.length - 1]!;
    this.checkKey(key);
    t[key] = value;
  }

  /**
   * Scanned files are attacker-controlled. Tables have a null prototype, so keys such as
   * `__proto__` or `constructor` are stored as ordinary data instead of reaching
   * Object.prototype, and valid files that use them still parse.
   */
  private checkKey(_key: string): void {}
}

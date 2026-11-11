import type { Severity } from '../types.js';

let enabled = true;

export function setColor(on: boolean): void {
  enabled = on;
}

export function colorEnabled(): boolean {
  return enabled;
}

export function colorSupported(stream: NodeJS.WriteStream = process.stdout): boolean {
  if ('NO_COLOR' in process.env) return false;
  if ('FORCE_COLOR' in process.env) return process.env.FORCE_COLOR !== '0';
  return Boolean(stream.isTTY) && process.env.TERM !== 'dumb';
}

const wrap = (open: string, close: string) => (s: string) => (enabled ? `\x1b[${open}m${s}\x1b[${close}m` : s);

export const c = {
  bold: wrap('1', '22'),
  dim: wrap('2', '22'),
  italic: wrap('3', '23'),
  underline: wrap('4', '24'),
  red: wrap('31', '39'),
  green: wrap('32', '39'),
  yellow: wrap('33', '39'),
  blue: wrap('34', '39'),
  magenta: wrap('35', '39'),
  cyan: wrap('36', '39'),
  gray: wrap('90', '39'),
  bgRed: wrap('41', '49'),
  bgYellow: wrap('43', '49'),
  bgMagenta: wrap('45', '49'),
  bgBlue: wrap('44', '49'),
  bgGreen: wrap('42', '49'),
  bgGray: wrap('100', '49'),
  black: wrap('30', '39'),
  white: wrap('97', '39'),
};

export function severityBadge(s: Severity): string {
  const label = ` ${s.toUpperCase()} `.padEnd(10);
  switch (s) {
    case 'critical':
      return c.bold(c.white(c.bgMagenta(label)));
    case 'high':
      return c.bold(c.white(c.bgRed(label)));
    case 'medium':
      return c.bold(c.black(c.bgYellow(label)));
    case 'low':
      return c.bold(c.white(c.bgBlue(label)));
    default:
      return c.bold(c.white(c.bgGray(label)));
  }
}

export function severityColor(s: Severity): (t: string) => string {
  switch (s) {
    case 'critical':
      return c.magenta;
    case 'high':
      return c.red;
    case 'medium':
      return c.yellow;
    case 'low':
      return c.blue;
    default:
      return c.gray;
  }
}

/** Visible length of a string that may contain ANSI escapes. */
export function visibleLength(s: string): number {
  // eslint-disable-next-line no-control-regex
  return s.replace(/\x1b\[[0-9;]*m/g, '').length;
}

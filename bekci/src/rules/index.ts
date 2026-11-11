import type { Rule } from '../types.js';
import { destructiveCommand, obfuscatedPayload, remoteCodeExecution } from './execution.js';
import { exfiltrationChain, exfiltrationEndpoint, sensitiveFileAccess } from './exfiltration.js';
import { concealment, hiddenComment, hiddenUnicode, instructionOverride } from './injection.js';
import {
  autoApprovedMcp,
  greedySkillTrigger,
  permissionBypass,
  projectHooks,
  riskyAllowRule,
  skillUnrestrictedTools,
  unrestrictedShell,
} from './permissions.js';
import { hardcodedSecret, plaintextCredential } from './secrets.js';
import {
  broadFilesystem,
  insecureRemote,
  opaqueBinary,
  privilegedContainer,
  typosquat,
  unpinnedPackage,
  unparseableConfig,
  unscannableFile,
} from './supply-chain.js';
import { lethalTrifecta } from './toxic-flow.js';

export const RULES: readonly Rule[] = [
  hiddenUnicode,
  instructionOverride,
  concealment,
  hiddenComment,
  remoteCodeExecution,
  obfuscatedPayload,
  destructiveCommand,
  sensitiveFileAccess,
  exfiltrationEndpoint,
  exfiltrationChain,
  hardcodedSecret,
  plaintextCredential,
  permissionBypass,
  unrestrictedShell,
  riskyAllowRule,
  autoApprovedMcp,
  projectHooks,
  skillUnrestrictedTools,
  greedySkillTrigger,
  unpinnedPackage,
  insecureRemote,
  privilegedContainer,
  broadFilesystem,
  typosquat,
  opaqueBinary,
  unscannableFile,
  unparseableConfig,
  lethalTrifecta,
];

export function findRule(idOrName: string): Rule | undefined {
  const q = idOrName.trim().toLowerCase();
  return RULES.find((r) => r.id.toLowerCase() === q || r.name === q);
}

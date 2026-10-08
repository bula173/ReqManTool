import type { ProjectLock } from '../api/types';

/** "Locked by Jane Doe on 2026-10-08: Released as v1.0 (baseline v1.0)". */
export function describeLock(lock: ProjectLock): string {
  const parts = ['Locked'];
  if (lock.by) parts.push(`by ${lock.by}`);
  if (lock.at) parts.push(`on ${lock.at.slice(0, 10)}`);
  let text = parts.join(' ');
  if (lock.reason) text += `: ${lock.reason}`;
  if (lock.baseline && !lock.reason.includes(lock.baseline)) text += ` (baseline ${lock.baseline})`;
  return `${text}. Changes are refused until the project is unlocked.`;
}

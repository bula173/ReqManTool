import type { CompareChanges } from '../api/types';

/** Attribute names for diff lines: `known` (e.g. the live project's) plus those named in `result`. */
export function compareAttributeNames(
  known: { id: string; name: string }[] | undefined,
  result: CompareChanges | undefined,
): (id: string) => string {
  const names = new Map<string, string>((known ?? []).map((attr) => [attr.id, attr.name]));
  if (result) {
    for (const group of [result.attributes.added, result.attributes.removed, result.attributes.changed]) {
      for (const attr of group) names.set(attr.id, attr.name);
    }
  }
  return (id: string) => names.get(id) ?? id;
}

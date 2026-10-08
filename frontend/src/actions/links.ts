import type { AttributeDefinition, Requirement } from '../api/types';
import type { LinkTarget } from './types';

/** One entry of a link attribute value: `> TARGET` (outgoing) or `< SOURCE` (incoming). */
export interface LinkEntry {
  direction: 'out' | 'in';
  otherId: string;
}

/**
 * Parses a link attribute value as the server writes it (`"> A; < B"`, see
 * `ProjectService._refresh_link_attribute`). Entries without a direction
 * marker are ignored.
 */
export function parseLinkValue(value: unknown): LinkEntry[] {
  if (typeof value !== 'string') return [];
  return value
    .split(';')
    .map((entry) => entry.trim())
    .flatMap((entry): LinkEntry[] => {
      const otherId = entry.slice(1).trim();
      if (!otherId) return [];
      if (entry.startsWith('>')) return [{ direction: 'out', otherId }];
      if (entry.startsWith('<')) return [{ direction: 'in', otherId }];
      return [];
    });
}

/** Every link of a requirement, from the project's link attributes (system attributes, id = link configuration id). */
export function linksOf(requirement: Requirement, attributes: AttributeDefinition[]): (LinkTarget & { configName: string })[] {
  return attributes
    .filter((attribute) => attribute.system)
    .flatMap((attribute) =>
      parseLinkValue(requirement.attributes[attribute.id]).map((entry) => ({
        requirement,
        configId: attribute.id,
        configName: attribute.name,
        ...entry,
      })),
    );
}

/**
 * A link end as the server writes it: `SYRS-0001` (same project) or
 * `rbc-gp:SYRS-0001` (a requirement of another project: cross-project links,
 * whose ids are only unique per project). Requirement ids never contain `:`.
 */
export function splitLinkEnd(otherId: string): { projectId: string | null; requirementId: string } {
  const index = otherId.indexOf(':');
  if (index <= 0) return { projectId: null, requirementId: otherId };
  return { projectId: otherId.slice(0, index), requirementId: otherId.slice(index + 1) };
}

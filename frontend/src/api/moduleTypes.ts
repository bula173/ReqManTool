import type { Module, ModuleType } from './types';

/** Module types in their canonical order (mirrors `models.MODULE_TYPES`). */
export const MODULE_TYPES: ModuleType[] = ['requirements', 'test_cases', 'test_execution', 'operational_scenarios'];

export const MODULE_TYPE_LABELS: Record<ModuleType, string> = {
  requirements: 'Requirements',
  test_cases: 'Test cases',
  test_execution: 'Test execution',
  operational_scenarios: 'Operational Scenarios',
};

/** A module's type; the backend omits the default `requirements`. */
export function moduleTypeOf(module: Pick<Module, 'type'> | null | undefined): ModuleType {
  return module?.type ?? 'requirements';
}

/**
 * The (source type, target type) pairs a link configuration may join. Mirrors
 * `ProjectService.LINK_TYPE_PAIRS`; the server enforces it when a link
 * configuration is created, the UI only uses it to offer valid targets.
 */
export const LINK_TYPE_PAIRS: [ModuleType, ModuleType][] = [
  ['requirements', 'requirements'],
  ['test_cases', 'requirements'],
  ['test_execution', 'test_cases'],
  ['requirements', 'operational_scenarios'],
  ['test_cases', 'operational_scenarios'],
  ['operational_scenarios', 'operational_scenarios'],
];

export function linkTypesAllowed(source: ModuleType, target: ModuleType): boolean {
  return LINK_TYPE_PAIRS.some(([s, t]) => s === source && t === target);
}

import type { AttributeDefinition } from './types';

/**
 * Whether an attribute applies to requirements of `moduleId`. Mirrors
 * `ProjectService._attribute_applies`: an attribute without a module list
 * (absent or empty) applies to every module. The server enforces the rule;
 * the UI only uses this to show the module's applicable attributes.
 */
export function attributeAppliesTo(attr: AttributeDefinition, moduleId: string): boolean {
  return !attr.modules || attr.modules.length === 0 || attr.modules.includes(moduleId);
}

/** The attributes that apply to `moduleId`, in project order; all of them when no module is given. */
export function applicableAttributes(
  attributes: AttributeDefinition[],
  moduleId: string | null | undefined,
): AttributeDefinition[] {
  if (!moduleId) return attributes;
  return attributes.filter((attr) => attributeAppliesTo(attr, moduleId));
}

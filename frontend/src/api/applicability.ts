import type { AttributeDefinition } from './types';

/**
 * Whether an attribute applies to requirements of `moduleId`. Mirrors
 * `ProjectService._attribute_applies`: an attribute without a module list
 * (absent or empty) applies to every module. The server enforces the rule;
 * the UI only uses this to show the module's applicable attributes.
 */
export function attributeAppliesTo(attr: AttributeDefinition, moduleId: string): boolean {
  return attr.required || !attr.modules || attr.modules.length === 0 || attr.modules.includes(moduleId);
}

/**
 * Why a required attribute cannot be limited to modules. Mirrors
 * `ProjectService._check_required_scope`: required attributes always apply
 * to every module (and `attributeAppliesTo` treats them so, even if a
 * hand-edited project lists modules for one).
 */
export const REQUIRED_SCOPE_HINT = 'Required attributes apply to every module';

/** The attributes that apply to `moduleId`, in project order; all of them when no module is given. */
export function applicableAttributes(
  attributes: AttributeDefinition[],
  moduleId: string | null | undefined,
): AttributeDefinition[] {
  if (!moduleId) return attributes;
  return attributes.filter((attr) => attributeAppliesTo(attr, moduleId));
}

import { MODULE_TYPE_LABELS, moduleTypeOf } from './moduleTypes';
import type { AttributeDefinition, Module } from './types';

/** What applicability needs to know about a module: its id and (optional) type. */
export type ModuleRef = Pick<Module, 'id' | 'type'>;

/**
 * Whether an attribute applies to requirements of `module`. Mirrors
 * `ProjectService._attribute_applies`: an attribute without a module list and
 * without a module type list applies to every module; otherwise it applies to
 * the modules it lists and to every module of a type it lists. The server
 * enforces the rule; the UI only uses this to show the module's applicable
 * attributes.
 */
export function attributeAppliesTo(attr: AttributeDefinition, module: ModuleRef): boolean {
  const modules = attr.modules ?? [];
  const types = attr.module_types ?? [];
  if (attr.required || (modules.length === 0 && types.length === 0)) return true;
  return modules.includes(module.id) || types.includes(moduleTypeOf(module));
}

/**
 * Why a required attribute cannot be limited to modules. Mirrors
 * `ProjectService._check_required_scope`: required attributes always apply
 * to every module (and `attributeAppliesTo` treats them so, even if a
 * hand-edited project lists modules for one).
 */
export const REQUIRED_SCOPE_HINT = 'Required attributes apply to every module';

/** The attributes that apply to `module`, in project order; all of them when no module is given. */
export function applicableAttributes(
  attributes: AttributeDefinition[],
  module: ModuleRef | null | undefined,
): AttributeDefinition[] {
  if (!module) return attributes;
  return attributes.filter((attr) => attributeAppliesTo(attr, module));
}

/**
 * A short description of where an attribute applies: `All`, or its modules
 * (named by `moduleLabel`) followed by its module types, e.g.
 * `SyRS, all test execution modules`.
 */
export function attributeScopeLabel(attr: AttributeDefinition, moduleLabel: (moduleId: string) => string): string {
  const modules = attr.modules ?? [];
  const types = attr.module_types ?? [];
  if (attr.required || (modules.length === 0 && types.length === 0)) return 'All';
  return [
    ...modules.map(moduleLabel),
    ...types.map((t) => `all ${MODULE_TYPE_LABELS[t].toLowerCase()} modules`),
  ].join(', ');
}

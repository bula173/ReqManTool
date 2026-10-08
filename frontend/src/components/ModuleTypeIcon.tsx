import { IconChecklist, IconFileText, IconFlask, type IconProps } from '@tabler/icons-react';
import { MODULE_TYPE_LABELS, moduleTypeOf } from '../api/moduleTypes';
import type { Module, ModuleType } from '../api/types';

const ICONS: Record<ModuleType, typeof IconFileText> = {
  requirements: IconFileText,
  test_cases: IconFlask,
  test_execution: IconChecklist,
};

/**
 * The icon of a module type (requirements: document, test cases: flask, test
 * execution: checklist), titled with the type's name for hover and screen
 * readers. Pass `module` (its `type` may be omitted: requirements) or `type`.
 */
export function ModuleTypeIcon({
  module,
  type,
  ...props
}: { module?: Pick<Module, 'type'> | null; type?: ModuleType } & IconProps) {
  const moduleType = type ?? moduleTypeOf(module);
  const Icon = ICONS[moduleType];
  const label = `${MODULE_TYPE_LABELS[moduleType]} module`;
  return <Icon aria-label={label} role="img" title={label} {...props} />;
}

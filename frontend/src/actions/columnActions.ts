import { IconAdjustments, IconArrowsHorizontal } from '@tabler/icons-react';
import type { Action, ActionContext } from './types';

/**
 * Actions on one requirements table column header. Sorting, filtering,
 * hiding a column and "add to view" are not offered: the table has no
 * sort/filter yet and views cannot be edited (only created/removed); they
 * join here with those backlog items.
 */

type ColumnContext = ActionContext<'column'>;

export const columnActions: Action<'column'>[] = [
  {
    id: 'column.wrapText',
    element: 'column',
    label: 'Wrap text',
    isAvailable: (ctx) => ctx.target !== null,
    isChecked: (ctx) => ctx.target !== null && !ctx.target.columns.isSingleLine(ctx.target.columnKey),
    run: (ctx) => ctx.target?.columns.toggleSingleLine(ctx.target.columnKey),
  },
  {
    id: 'column.autoFit',
    element: 'column',
    label: 'Auto-fit width',
    icon: IconArrowsHorizontal,
    isAvailable: (ctx) => ctx.target !== null,
    run: (ctx) => ctx.target?.columns.autoFit(ctx.target.columnKey),
  },
  {
    // "Attribute properties": which modules the attribute applies to (the
    // only attribute property editable after creation). Not for the Id
    // column, link (system) attributes or Object Text; disabled for required
    // attributes, which apply to every module.
    id: 'column.attributeModules',
    element: 'column',
    edits: true,
    label: 'Attribute modules',
    icon: IconAdjustments,
    opensDialog: true,
    isSupported: (ctx: ColumnContext) => {
      const attribute = ctx.target?.attribute;
      return attribute !== null && attribute !== undefined && !attribute.system && attribute.id !== 'object_text';
    },
    isAvailable: (ctx) => ctx.projectId !== null && ctx.target?.attribute?.required === false,
    run: (ctx) => {
      const attribute = ctx.target?.attribute;
      if (ctx.projectId && attribute) ctx.commands.openDialog({ kind: 'attributeModules', projectId: ctx.projectId, attribute });
    },
  },
];

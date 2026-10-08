import { IconAdjustments, IconArrowLeft, IconArrowRight, IconArrowsHorizontal } from '@tabler/icons-react';
import type { Action, ActionContext } from './types';

/**
 * Actions on one requirements table column header. Sorting, filtering,
 * hiding a column and "add to view" are not offered: the table has no
 * sort/filter yet; they join here with those backlog items. In a named view,
 * wrap, width and order changes are unsaved until "Save layout to view".
 */

type ColumnContext = ActionContext<'column'>;

/** The column's position among the table's reorderable columns; -1 when it cannot move (Id, Verification). */
function orderIndex(ctx: ColumnContext): number {
  const target = ctx.target;
  return target?.order ? target.order.keys.indexOf(target.columnKey) : -1;
}

function moveColumn(ctx: ColumnContext, delta: number): void {
  const target = ctx.target;
  const index = orderIndex(ctx);
  if (target?.order && index >= 0) target.order.move(target.columnKey, index + delta);
}

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
    // The keyboard alternative to dragging the header (per-browser order, like the widths).
    id: 'column.moveLeft',
    element: 'column',
    label: 'Move column left',
    icon: IconArrowLeft,
    isSupported: (ctx) => orderIndex(ctx) >= 0,
    isAvailable: (ctx) => orderIndex(ctx) > 0,
    run: (ctx) => moveColumn(ctx, -1),
  },
  {
    id: 'column.moveRight',
    element: 'column',
    label: 'Move column right',
    icon: IconArrowRight,
    isSupported: (ctx) => orderIndex(ctx) >= 0,
    isAvailable: (ctx) => {
      const index = orderIndex(ctx);
      return index >= 0 && index < (ctx.target?.order?.keys.length ?? 0) - 1;
    },
    run: (ctx) => moveColumn(ctx, 1),
  },
  {
    // "Attribute properties": which modules the attribute applies to (the
    // only attribute property editable after creation). Not for the Id
    // column, link (system) attributes or Object Text; disabled for required
    // attributes, which apply to every module.
    id: 'column.attributeModules',
    element: 'column',
    edits: true,
    requires: ['projectAdmin'],
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

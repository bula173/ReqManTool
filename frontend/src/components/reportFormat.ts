/** Labels and colors of report statuses (see `ReportParts.tsx`). */

export const STATUS_LABELS: Record<string, string> = {
  verified: 'Verified',
  failed: 'Failed',
  not_run: 'Not run',
  not_covered: 'Not covered',
  passed: 'Passed',
  blocked: 'Blocked',
  not_applicable: 'Not applicable',
  not_planned: 'Not planned',
};

export const STATUS_COLORS: Record<string, string> = {
  verified: 'green',
  passed: 'green',
  failed: 'red',
  blocked: 'orange',
  not_run: 'gray',
  not_applicable: 'gray',
  not_covered: 'yellow',
  not_planned: 'yellow',
};

/** A value of a report cell: empty values as a dimmed dash. */
export function cellText(value: unknown): string {
  if (value === null || value === undefined || value === '') return '–';
  return typeof value === 'string' ? value : JSON.stringify(value);
}

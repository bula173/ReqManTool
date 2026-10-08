const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

/**
 * Whether `value` is an existing calendar date written `YYYY-MM-DD`, the
 * stored form of `date` attribute values. Mirrors
 * `ProjectService._is_iso_date`; the server enforces it.
 */
export function isIsoDate(value: unknown): value is string {
  if (typeof value !== 'string' || !ISO_DATE.test(value)) return false;
  const [y, m, d] = value.split('-').map(Number);
  if (y < 1) return false; // Python's date starts at year 1
  const date = new Date(0);
  date.setUTCFullYear(y, m - 1, d); // unlike Date.UTC, keeps years 1-99 as given
  return date.getUTCFullYear() === y && date.getUTCMonth() === m - 1 && date.getUTCDate() === d;
}

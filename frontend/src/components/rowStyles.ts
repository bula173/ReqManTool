import { useComputedColorScheme } from '@mantine/core';
import { useMemo, type CSSProperties } from 'react';
import type { AttributeDefinition, RowStyle } from '../api/types';

/**
 * Row styling (`Project.row_styles`): which rule a requirement row takes and
 * the CSS it turns into. The server validates rules on write; these helpers
 * mirror `ProjectService._effective_row_styles` so hand-edited rules that no
 * longer resolve (missing attribute or choice) are ignored the same way.
 *
 * Theme handling: in light mode the colors are applied as given. In dark mode
 * a background is darkened (mixed into the dark body color, keeping its hue)
 * so the light default text stays readable on it, and a font color is
 * lightened until it has a contrast ratio of at least 4.5:1 against the row's
 * background. The stored colors (and the DOCX export) are never changed.
 */

export type ColorScheme = 'light' | 'dark';

/** Mantine's dark-mode body color (`--mantine-color-dark-7`). */
const DARK_BODY = '#242424';
/** How much of a background color is kept when it is mixed into the dark body. */
const DARK_BACKGROUND_SHARE = 0.35;
const MIN_CONTRAST = 4.5;

export const HEX_COLOR = /^#[0-9a-fA-F]{6}$/;

/** Rules that still resolve: enum attribute with the value among its choices, first rule per pair. */
export function effectiveRowStyles(rowStyles: RowStyle[] | undefined, attributes: AttributeDefinition[]): RowStyle[] {
  const byId = new Map(attributes.map((a) => [a.id, a]));
  const seen = new Set<string>();
  const result: RowStyle[] = [];
  for (const style of rowStyles ?? []) {
    const attr = byId.get(style.attribute_id);
    const key = `${style.attribute_id}\u0000${style.value}`;
    if (!attr || attr.type !== 'enum' || attr.system || seen.has(key)) continue;
    if (!(attr.choices ?? []).includes(style.value)) continue;
    if (!style.font_color && !style.background_color && !style.bold) continue;
    seen.add(key);
    result.push(style);
  }
  return result;
}

/** The first rule whose attribute value the requirement has, or null. */
export function rowStyleFor(styles: RowStyle[], values: Record<string, unknown> | undefined): RowStyle | null {
  if (!values) return null;
  return styles.find((s) => values[s.attribute_id] === s.value) ?? null;
}

type Rgb = [number, number, number];

function parse(hex: string): Rgb {
  return [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16)) as Rgb;
}

function format(rgb: Rgb): string {
  return `#${rgb.map((c) => Math.round(c).toString(16).padStart(2, '0')).join('')}`;
}

function mix(a: Rgb, b: Rgb, shareOfA: number): Rgb {
  return a.map((c, i) => c * shareOfA + b[i] * (1 - shareOfA)) as Rgb;
}

function luminance(rgb: Rgb): number {
  const [r, g, b] = rgb.map((c) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

export function contrastRatio(a: string, b: string): number {
  const la = luminance(parse(a));
  const lb = luminance(parse(b));
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}

/** The background color a rule shows with in `scheme`, or null. */
export function displayedBackground(style: RowStyle, scheme: ColorScheme): string | null {
  if (!style.background_color || !HEX_COLOR.test(style.background_color)) return null;
  if (scheme === 'light') return style.background_color;
  return format(mix(parse(style.background_color), parse(DARK_BODY), DARK_BACKGROUND_SHARE));
}

/** The font color a rule shows with in `scheme`, or null. */
export function displayedFontColor(style: RowStyle, scheme: ColorScheme): string | null {
  if (!style.font_color || !HEX_COLOR.test(style.font_color)) return null;
  if (scheme === 'light') return style.font_color;
  const background = displayedBackground(style, scheme) ?? DARK_BODY;
  let color = parse(style.font_color);
  // Lighten towards white in small steps until the text is readable.
  for (let step = 0; step < 20 && contrastRatio(format(color), background) < MIN_CONTRAST; step += 1) {
    color = mix(color, [255, 255, 255], 0.85);
  }
  return format(color);
}

/** Inline CSS for a styled row (or one of its cells); `{}` when there is no rule. */
export function rowStyleCss(style: RowStyle | null, scheme: ColorScheme): CSSProperties {
  if (!style) return {};
  const css: CSSProperties = {};
  const color = displayedFontColor(style, scheme);
  const background = displayedBackground(style, scheme);
  if (color) css.color = color;
  if (background) css.backgroundColor = background;
  if (style.bold) css.fontWeight = 700;
  return css;
}

/** What a row needs to show its rule: inline CSS (colors) and the bold flag (`data-row-bold`). */
export interface RowAppearance {
  style: CSSProperties;
  bold: boolean;
}

const NO_APPEARANCE: RowAppearance = { style: {}, bold: false };

/**
 * A function from a requirement's attribute values to its row appearance,
 * for the given (possibly still loading) project rules and attributes, in the
 * current color scheme.
 */
export function useRowAppearance(
  rowStyles: RowStyle[] | undefined,
  attributes: AttributeDefinition[] | undefined,
): (values: Record<string, unknown> | undefined) => RowAppearance {
  const scheme = useComputedColorScheme('light');
  const styles = useMemo(() => effectiveRowStyles(rowStyles, attributes ?? []), [rowStyles, attributes]);
  return (values) => {
    const style = rowStyleFor(styles, values);
    if (!style) return NO_APPEARANCE;
    const css: CSSProperties = {};
    const color = displayedFontColor(style, scheme);
    const background = displayedBackground(style, scheme);
    if (color) css.color = color;
    if (background) css.backgroundColor = background;
    return { style: css, bold: style.bold };
  };
}

/** `rowStyleCss` (colors only; bold is the caller's) in the current color scheme. */
export function useRowStyleCss(): (style: RowStyle | null) => CSSProperties {
  const scheme = useComputedColorScheme('light');
  return (style) => {
    if (!style) return {};
    const { color, backgroundColor } = rowStyleCss(style, scheme);
    return { color, backgroundColor, padding: backgroundColor ? '0 4px' : undefined, borderRadius: 4 };
  };
}

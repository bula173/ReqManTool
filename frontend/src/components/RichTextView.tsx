import { Text } from '@mantine/core';
import { richTextInnerHtml, richTextToPlain } from './richText';

/**
 * A rich text value, read-only. `wrap` false: one line of its plain
 * projection (truncated); true: the full formatted text; omitted: the
 * formatted text clamped to two lines (the compact detail-panel layout).
 * The HTML is the client-side sanitised form (`richText.ts`), never the raw value.
 */
export function RichTextView({ value, wrap }: { value: string; wrap?: boolean }) {
  if (wrap === false) {
    const plain = richTextToPlain(value);
    return (
      <Text size="sm" truncate="end" title={plain}>
        {plain.replace(/\s*\n\s*/g, ' ')}
      </Text>
    );
  }
  return (
    <Text
      size="sm"
      component="div"
      className="reqman-rich-text"
      lineClamp={wrap === undefined ? 2 : undefined}
      maw={wrap === undefined ? 280 : undefined}
      title={wrap === undefined ? richTextToPlain(value) : undefined}
      // Sanitised with the server's allow-list: p, br, strong, em, u, s, lists, span color/font-size.
      dangerouslySetInnerHTML={{ __html: richTextInnerHtml(value) }}
    />
  );
}

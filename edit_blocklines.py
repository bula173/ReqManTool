from pathlib import Path

path = Path('frontend/src/components/richText.ts')
content = path.read_text()

old = """function blockLines(list: Block[], depth: number): string[] {
  const indent = '  '.repeat(depth);
  const lines: string[] = [];
  for (const block of list) {
    if (block.kind === 'p') {
      lines.push(...inlineText(block.children).split('\n').map((l) => indent + l));
      continue;
    }
    block.items.forEach((entry, index) => {
      const marker = block.tag === 'ul' ? '- ' : `${index + 1}. `;
      const [first, ...rest] = inlineText((entry[0] as Para).children).split('\n');
      lines.push(indent + marker + first);
      lines.push(...rest.map((l) => indent + ' '.repeat(marker.length) + l));
      lines.push(...blockLines(entry.slice(1), depth + 1));
    });
  }
  return lines;
}"""

new = """function blockLines(list: Block[], depth: number): string[] {
  const indent = '  '.repeat(depth);
  const lines: string[] = [];
  for (const block of list) {
    if (block.kind === 'p') {
      lines.push(...inlineText(block.children).split('\n').map((l) => indent + l));
      continue;
    }
    if (block.kind === 'table') {
      for (const row of block.rows) {
        lines.push(indent + row.map((cell) => cell.blocks.map(blockLines).join(' / ')).join(' | '));
      }
      continue;
    }
    block.items.forEach((entry, index) => {
      const marker = block.tag === 'ul' ? '- ' : `${index + 1}. `;
      const [first, ...rest] = inlineText((entry[0] as Para).children).split('\n');
      lines.push(indent + marker + first);
      lines.push(...rest.map((l) => indent + ' '.repeat(marker.length) + l));
      lines.push(...blockLines(entry.slice(1), depth + 1));
    });
  }
  return lines;
}"""

assert content.count(old) == 1, f"count={content.count(old)}"
content = content.replace(old, new)
path.write_text(content)
print("ok")
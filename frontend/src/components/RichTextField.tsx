import '@mantine/tiptap/styles.css';
import { Menu } from '@mantine/core';
import { RichTextEditor, useRichTextEditorContext } from '@mantine/tiptap';
import { IconTextSize } from '@tabler/icons-react';
import { Color, FontSize, TextStyle } from '@tiptap/extension-text-style';
import { useEditor, useEditorState } from '@tiptap/react';
import StarterKit from '@tiptap/starter-kit';
import { useEffect, useRef } from 'react';
import type { KeyboardEvent } from 'react';
import { FONT_SIZES, editorHtmlToValue, valueToEditorHtml } from './richText';

/** Swatches of the font color control (`#rrggbb`, as the server stores colors). */
const SWATCHES = [
  '#000000', '#495057', '#868e96', '#c92a2a', '#e8590c', '#f08c00', '#2f9e44',
  '#0c8599', '#1971c2', '#3b5bdb', '#6741d9', '#9c36b5', '#c2255c', '#ffffff',
];

// Dropdowns stay inside the editor's DOM (so a click in them is not "outside"
// the inline editor) and float with a fixed strategy (so a table cell does not clip them).
const DROPDOWN_PROPS = { withinPortal: false, floatingStrategy: 'fixed' as const };

export interface RichTextFieldProps {
  /** The stored value (plain or rich text). Read once, when the editor is created. */
  value: unknown;
  /** Called with the value to store (canonical rich text, or plain text when unformatted) after every edit. */
  onChange: (value: string) => void;
  autoFocus?: boolean;
  /** Inline editing: called on Ctrl/Cmd+Enter and when the user clicks or tabs outside the editor. */
  onCommit?: () => void;
  /** Inline editing: called on Esc (unless a toolbar dropdown is open). */
  onCancel?: () => void;
  minHeight?: number;
  'aria-label'?: string;
}

function FontSizeControl() {
  const { editor } = useRichTextEditorContext();
  const current = useEditorState({
    editor,
    selector: ({ editor: e }) => (e?.getAttributes('textStyle').fontSize as string | undefined) ?? null,
  });
  return (
    <Menu {...DROPDOWN_PROPS} position="bottom-start" shadow="md">
      <Menu.Target>
        <RichTextEditor.Control aria-label="Font size" title="Font size" active={Boolean(current)}>
          <IconTextSize size={16} stroke={1.5} />
        </RichTextEditor.Control>
      </Menu.Target>
      <Menu.Dropdown>
        <Menu.Item onClick={() => editor?.chain().focus().unsetFontSize().run()} fw={current ? undefined : 600}>
          Default
        </Menu.Item>
        {FONT_SIZES.map((size) => (
          <Menu.Item
            key={size}
            onClick={() => editor?.chain().focus().setFontSize(size).run()}
            fw={current === size ? 600 : undefined}
            style={{ fontSize: size }}
          >
            {size}
          </Menu.Item>
        ))}
      </Menu.Dropdown>
    </Menu>
  );
}

/**
 * The rich text editor of `text` attributes (TipTap via @mantine/tiptap):
 * bold, italic, underline, strikethrough, bullet/numbered lists, font color
 * and font size. Loaded lazily (see `LazyRichTextField`), so the editor code
 * is only downloaded when a text value is first edited.
 */
export default function RichTextField({
  value,
  onChange,
  autoFocus,
  onCommit,
  onCancel,
  minHeight = 60,
  'aria-label': ariaLabel,
}: RichTextFieldProps) {
  const wrapperRef = useRef<HTMLDivElement | null>(null);
  // Latest callbacks; `finish` calls onCommit/onCancel at most once (a click
  // outside is followed by a blur, both of which would commit).
  const handlers = useRef({ onChange, onCommit, onCancel });
  const finished = useRef(false);
  useEffect(() => {
    handlers.current = { onChange, onCommit, onCancel };
  });
  const finish = (how: 'onCommit' | 'onCancel') => {
    const handler = handlers.current[how];
    if (!handler || finished.current) return;
    finished.current = true;
    handler();
  };
  const inline = Boolean(onCommit);

  const editor = useEditor({
    extensions: [
      StarterKit.configure({
        heading: false,
        blockquote: false,
        code: false,
        codeBlock: false,
        horizontalRule: false,
        link: false,
        trailingNode: false,
      }),
      TextStyle,
      Color,
      FontSize,
    ],
    content: valueToEditorHtml(value),
    // Keep the value's whitespace exactly (runs of spaces, leading/trailing
    // spaces, tabs): the default parse collapses it, which would silently
    // rewrite a plain value on its first edit.
    parseOptions: { preserveWhitespace: 'full' },
    autofocus: autoFocus ? 'end' : false,
    editorProps: { attributes: { 'aria-label': ariaLabel ?? 'Rich text', 'aria-multiline': 'true', role: 'textbox' } },
    onUpdate: ({ editor: e }) => handlers.current.onChange(editorHtmlToValue(e.getHTML())),
  });

  // Inline editing commits when the user clicks anywhere outside the editor
  // (its toolbar dropdowns render inside it).
  useEffect(() => {
    if (!inline) return undefined;
    const onPointerDown = (event: PointerEvent) => {
      const wrapper = wrapperRef.current;
      if (wrapper && event.target instanceof Node && !wrapper.contains(event.target)) finish('onCommit');
    };
    document.addEventListener('pointerdown', onPointerDown, true);
    return () => document.removeEventListener('pointerdown', onPointerDown, true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [inline]);

  const onKeyDownCapture = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'Enter' && (event.metaKey || event.ctrlKey) && handlers.current.onCommit) {
      // Before ProseMirror, whose Mod-Enter would insert a line break.
      event.preventDefault();
      event.stopPropagation();
      finish('onCommit');
      return;
    }
    if (event.key === 'Escape' && handlers.current.onCancel) {
      const target = event.target as Element;
      // Esc in an open toolbar dropdown only closes the dropdown.
      if (target.closest('.mantine-Popover-dropdown, .mantine-Menu-dropdown, [role="menu"]')) return;
      event.preventDefault();
      event.stopPropagation();
      finish('onCancel');
    }
  };

  return (
    <div
      ref={wrapperRef}
      onKeyDownCapture={onKeyDownCapture}
      onBlur={(event) => {
        // Tabbing out of the editor commits too.
        const next = event.relatedTarget;
        if (inline && next instanceof Node && !event.currentTarget.contains(next)) finish('onCommit');
      }}
    >
      <RichTextEditor editor={editor} withCodeHighlightStyles={false} variant="subtle">
        <RichTextEditor.Toolbar>
          <RichTextEditor.ControlsGroup>
            <RichTextEditor.Bold />
            <RichTextEditor.Italic />
            <RichTextEditor.Underline />
            <RichTextEditor.Strikethrough />
          </RichTextEditor.ControlsGroup>
          <RichTextEditor.ControlsGroup>
            <RichTextEditor.BulletList />
            <RichTextEditor.OrderedList />
          </RichTextEditor.ControlsGroup>
          <RichTextEditor.ControlsGroup>
            <RichTextEditor.ColorPicker
              colors={SWATCHES}
              popoverProps={DROPDOWN_PROPS}
              colorPickerProps={{ format: 'hex' }}
            />
            <FontSizeControl />
          </RichTextEditor.ControlsGroup>
        </RichTextEditor.Toolbar>
        <RichTextEditor.Content className="reqman-rich-text" style={{ minHeight }} />
      </RichTextEditor>
    </div>
  );
}

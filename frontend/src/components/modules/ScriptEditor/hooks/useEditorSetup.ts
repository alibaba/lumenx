'use client';

import { useEditor } from '@tiptap/react';
import StarterKit from '@tiptap/starter-kit';
import Placeholder from '@tiptap/extension-placeholder';
import CharacterCount from '@tiptap/extension-character-count';
import { useTranslations } from 'next-intl';
import { useEditorStore } from '@/store/editorStore';
import { scriptExtensions } from '../extensions';

interface UseEditorSetupOptions {
  /** Initial document content (Tiptap JSON or HTML string) */
  content?: string | Record<string, unknown> | null;
  /** Whether the editor should be immediately editable */
  editable?: boolean;
}

/**
 * Editor lifecycle management hook.
 *
 * Responsibilities:
 * - Creates and configures the Tiptap Editor instance
 * - Registers all script extensions (SceneHeading, Action, CharacterCue, Dialogue, Transition)
 * - Integrates StarterKit (with custom heading/paragraph disabled)
 * - Adds Placeholder, CharacterCount, History extensions
 * - Binds onUpdate → marks isDirty in editorStore
 * - Binds onSelectionUpdate → reserved for right panel switching
 * - Cleans up editor on unmount
 */
export function useEditorSetup(options: UseEditorSetupOptions = {}) {
  const { content = '', editable = true } = options;
  const t = useTranslations('scriptEditor');

  const editor = useEditor({
    extensions: [
      StarterKit.configure({
        // Disable defaults that conflict with our custom nodes
        heading: false,
        paragraph: false,
        // The list extensions depend on the paragraph node
        // (listItem content = 'paragraph block*'). Since paragraph is
        // disabled above, they must be disabled too, otherwise the
        // ProseMirror schema fails to build at runtime:
        //   "No node type or group 'paragraph' found".
        // The script editor does not use bullet/ordered lists.
        bulletList: false,
        orderedList: false,
        listItem: false,
        listKeymap: false,
        // Disable unused block nodes. These are NOT used by the screenplay
        // editor, and blockquote (content 'block+') is registered before our
        // custom nodes: with paragraph disabled it becomes the FIRST fillable
        // block-group node, so ProseMirror's createAndFill picks it to fill
        // the empty doc and recurses on its own `block+` content forever
        // (RangeError: Maximum call stack size exceeded). Removing them makes
        // Action (inline*) the first, empty-completable block-group node.
        blockquote: false,
        codeBlock: false,
        horizontalRule: false,
        // Configure the built-in undo/redo here. StarterKit (v3) already
        // bundles the `undoRedo` extension, so adding a separate History
        // extension caused: Duplicate extension names ['undoRedo'].
        undoRedo: {
          depth: 200,
        },
      }),
      ...scriptExtensions,
      Placeholder.configure({
        placeholder: ({ node }) => {
          if (node.type.name === 'sceneHeading') {
            return t('placeholders.sceneHeading');
          }
          return t('placeholders.startTyping');
        },
      }),
      CharacterCount,
    ],
    content: content || '',
    editable,
    onUpdate: ({ editor }) => {
      const store = useEditorStore.getState();
      store.setDirty(true);
      // Update word count derivation
      const text = editor.state.doc.textContent;
      store.updateDerivation({ wordCount: text.length });
    },
    onSelectionUpdate: ({ editor: _editor }) => {
      // Reserved for right panel context switching
      // Will inspect current node type and update activeRightPanel
    },
  });

  const isReady = !!editor && !editor.isDestroyed;

  return { editor, isReady };
}

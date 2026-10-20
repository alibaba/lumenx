import { Extension } from '@tiptap/core'

/**
 * Tab cycle order for screenplay node types.
 * Tab advances forward; Shift+Tab goes backward.
 */
const TAB_CYCLE = ['action', 'characterCue', 'dialogue', 'parenthetical'] as const

/**
 * Maps a node type to the node created when Enter is pressed.
 */
const ENTER_NEXT: Record<string, string> = {
  characterCue: 'dialogue',
  dialogue: 'action',
  parenthetical: 'dialogue',
  action: 'action',
  sceneHeading: 'action',
}

function getNodeNameAtCursor(editor: { state: { selection: { $from: { parent: { type: { name: string } } } } } }): string {
  return editor.state.selection.$from.parent.type.name
}

export const Keymap = Extension.create({
  name: 'scriptKeymap',

  addKeyboardShortcuts() {
    return {
      Tab: ({ editor }) => {
        const current = getNodeNameAtCursor(editor)
        const idx = TAB_CYCLE.indexOf(current as typeof TAB_CYCLE[number])
        if (idx === -1) {
          // Not in cycle — default to action
          return editor.commands.setNode('action')
        }
        const nextIdx = (idx + 1) % TAB_CYCLE.length
        const nextType = TAB_CYCLE[nextIdx]
        return editor.commands.setNode(nextType)
      },

      'Shift-Tab': ({ editor }) => {
        const current = getNodeNameAtCursor(editor)
        const idx = TAB_CYCLE.indexOf(current as typeof TAB_CYCLE[number])
        if (idx === -1) {
          return editor.commands.setNode('action')
        }
        const prevIdx = (idx - 1 + TAB_CYCLE.length) % TAB_CYCLE.length
        const prevType = TAB_CYCLE[prevIdx]
        return editor.commands.setNode(prevType)
      },

      Enter: ({ editor }) => {
        const { $from } = editor.state.selection
        const currentName = $from.parent.type.name

        // Determine next node type based on current
        const nextType = ENTER_NEXT[currentName]

        if (!nextType) {
          // For unknown types or paragraph, default behavior
          return false
        }

        if (nextType === currentName) {
          // Same-type split (e.g. action -> action): splitBlock() already
          // produces a correct same-type block. Running setNode afterwards
          // would re-run clearNodes/nodesBetween against post-split positions
          // that can be out of range in a real browser, throwing
          // "Position out of range" and discarding the whole transaction.
          // Short-circuit setNode entirely for the same-type case.
          return editor.commands.splitBlock()
        }

        // Cross-type: split at the cursor via the high-level command so
        // ProseMirror maps positions and moves the selection into the newly
        // created block. Then convert that new block: setNode re-derives its
        // target range from the post-split selection, so no stale (pre-split)
        // position is reused.
        return editor
          .chain()
          .splitBlock()
          .command(({ commands }) => commands.setNode(nextType))
          .run()
      },

      'Mod-Enter': ({ editor }) => {
        const { $from } = editor.state.selection
        const currentName = $from.parent.type.name

        if (currentName === 'sceneHeading') {
          // Already a scene heading: splitBlock() yields another scene heading.
          // Skip setNode to avoid the same post-split out-of-range hazard.
          return editor.commands.splitBlock()
        }

        // Same position-safe pattern: split first, then convert the new block
        // to a scene heading using positions derived from the updated state.
        return editor
          .chain()
          .splitBlock()
          .command(({ commands }) => commands.setNode('sceneHeading'))
          .run()
      },
    }
  },
})

export default Keymap

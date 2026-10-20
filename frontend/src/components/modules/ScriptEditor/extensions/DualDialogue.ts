import { Node, mergeAttributes } from '@tiptap/core'

export interface DualDialogueOptions {
  HTMLAttributes: Record<string, unknown>
}

declare module '@tiptap/core' {
  interface Commands<ReturnType> {
    dualDialogue: {
      setDualDialogue: () => ReturnType
    }
  }
}

/**
 * DialogueColumn — child node of DualDialogue.
 * Each column holds one character's dialogue block.
 */
export const DialogueColumn = Node.create({
  name: 'dialogueColumn',

  group: '',

  content: 'block+',

  defining: true,

  parseHTML() {
    return [{ tag: 'div.dialogue-column' }]
  },

  renderHTML({ HTMLAttributes }) {
    return [
      'div',
      mergeAttributes(HTMLAttributes, { class: 'dialogue-column' }),
      0,
    ]
  },
})

/**
 * DualDialogue — two-column simultaneous dialogue container.
 */
export const DualDialogue = Node.create<DualDialogueOptions>({
  name: 'dualDialogue',

  group: 'block',

  content: 'dialogueColumn dialogueColumn',

  defining: true,

  addOptions() {
    return {
      HTMLAttributes: {},
    }
  },

  parseHTML() {
    return [{ tag: 'div.dual-dialogue' }]
  },

  renderHTML({ HTMLAttributes }) {
    return [
      'div',
      mergeAttributes(this.options.HTMLAttributes, HTMLAttributes, {
        class: 'dual-dialogue',
      }),
      0,
    ]
  },

  addCommands() {
    return {
      setDualDialogue:
        () =>
        ({ commands }) => {
          return commands.setNode(this.name)
        },
    }
  },
})

export default DualDialogue

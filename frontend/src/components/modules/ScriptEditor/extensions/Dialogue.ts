import { Node, mergeAttributes } from '@tiptap/core'

export interface DialogueOptions {
  HTMLAttributes: Record<string, unknown>
}

declare module '@tiptap/core' {
  interface Commands<ReturnType> {
    dialogue: {
      setDialogue: () => ReturnType
    }
  }
}

export const Dialogue = Node.create<DialogueOptions>({
  name: 'dialogue',

  group: 'block',

  content: 'inline*',

  defining: true,

  addOptions() {
    return {
      HTMLAttributes: {},
    }
  },

  parseHTML() {
    return [{ tag: 'p[data-type="dialogue"]' }]
  },

  renderHTML({ HTMLAttributes }) {
    return [
      'p',
      mergeAttributes(this.options.HTMLAttributes, HTMLAttributes, {
        'data-type': 'dialogue',
        class: 'dialogue',
      }),
      0,
    ]
  },

  addCommands() {
    return {
      setDialogue:
        () =>
        ({ commands }) => {
          return commands.setNode(this.name)
        },
    }
  },
})

export default Dialogue

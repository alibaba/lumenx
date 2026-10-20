import { Node, mergeAttributes } from '@tiptap/core'

export interface NoteOptions {
  HTMLAttributes: Record<string, unknown>
}

declare module '@tiptap/core' {
  interface Commands<ReturnType> {
    note: {
      setNote: () => ReturnType
    }
  }
}

export const Note = Node.create<NoteOptions>({
  name: 'note',

  group: 'block',

  content: 'inline*',

  defining: true,

  addOptions() {
    return {
      HTMLAttributes: {},
    }
  },

  addAttributes() {
    return {
      author: {
        default: null,
        parseHTML: (element) => element.getAttribute('data-author'),
        renderHTML: (attributes) => {
          if (!attributes.author) return {}
          return { 'data-author': attributes.author }
        },
      },
      timestamp: {
        default: null,
        parseHTML: (element) => element.getAttribute('data-timestamp'),
        renderHTML: (attributes) => {
          if (!attributes.timestamp) return {}
          return { 'data-timestamp': attributes.timestamp }
        },
      },
      resolved: {
        default: false,
        parseHTML: (element) => element.getAttribute('data-resolved') === 'true',
        renderHTML: (attributes) => {
          if (!attributes.resolved) return {}
          return { 'data-resolved': 'true' }
        },
      },
    }
  },

  parseHTML() {
    return [{ tag: 'div.note' }]
  },

  renderHTML({ HTMLAttributes }) {
    return [
      'div',
      mergeAttributes(this.options.HTMLAttributes, HTMLAttributes, {
        class: 'note',
      }),
      0,
    ]
  },

  addCommands() {
    return {
      setNote:
        () =>
        ({ commands }) => {
          return commands.setNode(this.name)
        },
    }
  },
})

export default Note

import { Node, mergeAttributes } from '@tiptap/core'

export interface ActionOptions {
  HTMLAttributes: Record<string, unknown>
}

declare module '@tiptap/core' {
  interface Commands<ReturnType> {
    action: {
      setAction: () => ReturnType
    }
  }
}

export const Action = Node.create<ActionOptions>({
  name: 'action',

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
      centered: {
        default: false,
        parseHTML: (element) => element.getAttribute('data-centered') === 'true',
        renderHTML: (attributes) => {
          if (!attributes.centered) return {}
          return { 'data-centered': 'true' }
        },
      },
    }
  },

  parseHTML() {
    return [{ tag: 'p[data-type="action"]' }]
  },

  renderHTML({ HTMLAttributes }) {
    return [
      'p',
      mergeAttributes(this.options.HTMLAttributes, HTMLAttributes, {
        'data-type': 'action',
        class: 'action',
      }),
      0,
    ]
  },

  addCommands() {
    return {
      setAction:
        () =>
        ({ commands }) => {
          return commands.setNode(this.name)
        },
    }
  },
})

export default Action

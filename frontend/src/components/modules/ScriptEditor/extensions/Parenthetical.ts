import { Node, mergeAttributes } from '@tiptap/core'

export interface ParentheticalOptions {
  HTMLAttributes: Record<string, unknown>
}

declare module '@tiptap/core' {
  interface Commands<ReturnType> {
    parenthetical: {
      setParenthetical: () => ReturnType
    }
  }
}

export const Parenthetical = Node.create<ParentheticalOptions>({
  name: 'parenthetical',

  group: 'block',

  content: 'inline*',

  defining: true,

  addOptions() {
    return {
      HTMLAttributes: {},
    }
  },

  parseHTML() {
    return [{ tag: 'p.parenthetical' }]
  },

  renderHTML({ HTMLAttributes }) {
    return [
      'p',
      mergeAttributes(this.options.HTMLAttributes, HTMLAttributes, {
        class: 'parenthetical',
      }),
      0,
    ]
  },

  addCommands() {
    return {
      setParenthetical:
        () =>
        ({ commands }) => {
          return commands.setNode(this.name)
        },
    }
  },
})

export default Parenthetical

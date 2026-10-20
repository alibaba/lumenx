import { Node, mergeAttributes } from '@tiptap/core'
import { InputRule } from '@tiptap/core'

export interface TransitionOptions {
  HTMLAttributes: Record<string, unknown>
}

declare module '@tiptap/core' {
  interface Commands<ReturnType> {
    transition: {
      setTransition: () => ReturnType
    }
  }
}

export const Transition = Node.create<TransitionOptions>({
  name: 'transition',

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
      type: {
        default: 'custom',
        parseHTML: (element) => element.getAttribute('data-transition-type') || 'custom',
        renderHTML: (attributes) => {
          return { 'data-transition-type': attributes.type }
        },
      },
    }
  },

  parseHTML() {
    return [{ tag: 'p[data-type="transition"]' }]
  },

  renderHTML({ HTMLAttributes }) {
    return [
      'p',
      mergeAttributes(this.options.HTMLAttributes, HTMLAttributes, {
        'data-type': 'transition',
        class: 'transition',
      }),
      0,
    ]
  },

  addCommands() {
    return {
      setTransition:
        () =>
        ({ commands }) => {
          return commands.setNode(this.name)
        },
    }
  },

  addInputRules() {
    const transitionMap: Record<string, string> = {
      'CUT TO': 'CUT_TO',
      'FADE IN': 'FADE_IN',
      'FADE OUT': 'FADE_OUT',
      'DISSOLVE TO': 'DISSOLVE',
      'SMASH CUT TO': 'SMASH_CUT',
    }

    return [
      new InputRule({
        find: /^(CUT TO|FADE IN|FADE OUT|DISSOLVE TO|SMASH CUT TO):\s$/,
        handler: ({ state, range, match }) => {
          const { tr } = state
          const text = match[1] as string
          const transitionType = transitionMap[text] || 'custom'
          const start = range.from
          const end = range.to

          tr.delete(start, end)
          const nodeType = state.schema.nodes[this.name]
          tr.setBlockType(start, start, nodeType, { type: transitionType })
          tr.insertText(`${text}: `, start)
        },
      }),
    ]
  },
})

export default Transition

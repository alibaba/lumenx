import { Node, mergeAttributes, InputRule } from '@tiptap/core'

export interface SectionOptions {
  HTMLAttributes: Record<string, unknown>
}

declare module '@tiptap/core' {
  interface Commands<ReturnType> {
    section: {
      setSection: (attrs?: { title?: string; level?: number }) => ReturnType
    }
  }
}

export const Section = Node.create<SectionOptions>({
  name: 'section',

  group: 'block',

  content: 'block+',

  defining: true,

  addOptions() {
    return {
      HTMLAttributes: {},
    }
  },

  addAttributes() {
    return {
      title: {
        default: '',
        parseHTML: (element) => element.getAttribute('data-title') ?? '',
        renderHTML: (attributes) => {
          return { 'data-title': attributes.title }
        },
      },
      level: {
        default: 1,
        parseHTML: (element) => {
          const val = element.getAttribute('data-level')
          return val ? parseInt(val, 10) : 1
        },
        renderHTML: (attributes) => {
          return { 'data-level': attributes.level }
        },
      },
    }
  },

  parseHTML() {
    return [{ tag: 'div.section' }]
  },

  renderHTML({ HTMLAttributes }) {
    return [
      'div',
      mergeAttributes(this.options.HTMLAttributes, HTMLAttributes, {
        class: 'section',
      }),
      0,
    ]
  },

  addCommands() {
    return {
      setSection:
        (attrs) =>
        ({ commands }) => {
          return commands.setNode(this.name, attrs)
        },
    }
  },

  addInputRules() {
    return [
      new InputRule({
        find: /^(#{1,3})\s(.+)$/,
        handler: ({ state, range, match }) => {
          const { tr } = state
          const level = match[1].length as 1 | 2 | 3
          const title = match[2]
          const start = range.from
          const end = range.to

          tr.delete(start, end)
          const nodeType = state.schema.nodes[this.name]
          tr.setBlockType(start, start, nodeType, { title, level })
          tr.insertText(title, start)
        },
      }),
    ]
  },
})

export default Section

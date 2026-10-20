import { Node, mergeAttributes } from '@tiptap/core'
import { InputRule } from '@tiptap/core'

export interface SceneHeadingOptions {
  HTMLAttributes: Record<string, unknown>
}

declare module '@tiptap/core' {
  interface Commands<ReturnType> {
    sceneHeading: {
      setSceneHeading: () => ReturnType
    }
  }
}

export const SceneHeading = Node.create<SceneHeadingOptions>({
  name: 'sceneHeading',

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
      id: {
        default: null,
        parseHTML: (element) => element.getAttribute('data-id'),
        renderHTML: (attributes) => {
          if (!attributes.id) return {}
          return { 'data-id': attributes.id }
        },
      },
      intExt: {
        default: null,
        parseHTML: (element) => element.getAttribute('data-int-ext'),
        renderHTML: (attributes) => {
          if (!attributes.intExt) return {}
          return { 'data-int-ext': attributes.intExt }
        },
      },
      location: {
        default: null,
        parseHTML: (element) => element.getAttribute('data-location'),
        renderHTML: (attributes) => {
          if (!attributes.location) return {}
          return { 'data-location': attributes.location }
        },
      },
      timeOfDay: {
        default: null,
        parseHTML: (element) => element.getAttribute('data-time-of-day'),
        renderHTML: (attributes) => {
          if (!attributes.timeOfDay) return {}
          return { 'data-time-of-day': attributes.timeOfDay }
        },
      },
    }
  },

  parseHTML() {
    return [{ tag: 'h3[data-type="scene-heading"]' }]
  },

  renderHTML({ HTMLAttributes }) {
    return [
      'h3',
      mergeAttributes(this.options.HTMLAttributes, HTMLAttributes, {
        'data-type': 'scene-heading',
        class: 'scene-heading',
      }),
      0,
    ]
  },

  addCommands() {
    return {
      setSceneHeading:
        () =>
        ({ commands }) => {
          return commands.setNode(this.name)
        },
    }
  },

  addInputRules() {
    return [
      new InputRule({
        find: /^(INT|EXT|INT\/EXT)\.\s$/,
        handler: ({ state, range, match }) => {
          const { tr } = state
          const intExt = match[1] as string
          const start = range.from
          const end = range.to

          tr.delete(start, end)
          const nodeType = state.schema.nodes[this.name]
          tr.setBlockType(start, start, nodeType, {
            id: crypto.randomUUID(),
            intExt,
          })
          tr.insertText(`${intExt}. `, start)
        },
      }),
    ]
  },
})

export default SceneHeading

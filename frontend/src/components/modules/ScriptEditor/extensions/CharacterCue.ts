import { Node, mergeAttributes } from '@tiptap/core'
import { InputRule } from '@tiptap/core'

export interface CharacterCueOptions {
  HTMLAttributes: Record<string, unknown>
}

declare module '@tiptap/core' {
  interface Commands<ReturnType> {
    characterCue: {
      setCharacterCue: () => ReturnType
    }
  }
}

export const CharacterCue = Node.create<CharacterCueOptions>({
  name: 'characterCue',

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
      characterId: {
        default: null,
        parseHTML: (element) => element.getAttribute('data-character-id'),
        renderHTML: (attributes) => {
          if (!attributes.characterId) return {}
          return { 'data-character-id': attributes.characterId }
        },
      },
      extension: {
        default: null,
        parseHTML: (element) => element.getAttribute('data-extension'),
        renderHTML: (attributes) => {
          if (!attributes.extension) return {}
          return { 'data-extension': attributes.extension }
        },
      },
    }
  },

  parseHTML() {
    return [{ tag: 'p[data-type="character-cue"]' }]
  },

  renderHTML({ HTMLAttributes }) {
    return [
      'p',
      mergeAttributes(this.options.HTMLAttributes, HTMLAttributes, {
        'data-type': 'character-cue',
        class: 'character-cue',
      }),
      0,
    ]
  },

  addCommands() {
    return {
      setCharacterCue:
        () =>
        ({ commands }) => {
          return commands.setNode(this.name)
        },
    }
  },

  addInputRules() {
    return [
      new InputRule({
        find: /^([A-Z][A-Z\s]+)$/,
        handler: ({ state, range, match }) => {
          const { tr } = state
          const start = range.from
          const end = range.to

          tr.delete(start, end)
          const nodeType = state.schema.nodes[this.name]
          tr.setBlockType(start, start, nodeType)
          tr.insertText(match[1], start)
        },
      }),
    ]
  },
})

export default CharacterCue

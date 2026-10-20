import { Node, mergeAttributes } from '@tiptap/core'

export type ShotType = 'WS' | 'MS' | 'CU' | 'ECU' | 'OTS' | 'POV' | 'custom'

export type PipelineStatus =
  | 'suggested'
  | 'reviewing'
  | 'confirmed'
  | 'queued'
  | 'generating'
  | 'done'
  | 'failed'

export interface ShotBlockOptions {
  HTMLAttributes: Record<string, unknown>
}

declare module '@tiptap/core' {
  interface Commands<ReturnType> {
    shotBlock: {
      setShotBlock: (attributes?: Partial<ShotBlockAttributes>) => ReturnType
    }
  }
}

export interface ShotBlockAttributes {
  id: string | null
  shotNumber: number | null
  shotType: ShotType | null
  cameraMovement: string | null
  duration: number | null
  description: string | null
  characters: string[]
  pipelineStatus: PipelineStatus
  generatedAssetUrl: string | null
  thumbnailUrl: string | null
}

export const ShotBlock = Node.create<ShotBlockOptions>({
  name: 'shotBlock',

  group: 'block',

  content: 'text*',

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
      shotNumber: {
        default: null,
        parseHTML: (element) => {
          const val = element.getAttribute('data-shot-number')
          return val ? Number(val) : null
        },
        renderHTML: (attributes) => {
          if (attributes.shotNumber == null) return {}
          return { 'data-shot-number': String(attributes.shotNumber) }
        },
      },
      shotType: {
        default: null,
        parseHTML: (element) => element.getAttribute('data-shot-type'),
        renderHTML: (attributes) => {
          if (!attributes.shotType) return {}
          return { 'data-shot-type': attributes.shotType }
        },
      },
      cameraMovement: {
        default: null,
        parseHTML: (element) => element.getAttribute('data-camera-movement'),
        renderHTML: (attributes) => {
          if (!attributes.cameraMovement) return {}
          return { 'data-camera-movement': attributes.cameraMovement }
        },
      },
      duration: {
        default: null,
        parseHTML: (element) => {
          const val = element.getAttribute('data-duration')
          return val ? Number(val) : null
        },
        renderHTML: (attributes) => {
          if (attributes.duration == null) return {}
          return { 'data-duration': String(attributes.duration) }
        },
      },
      description: {
        default: null,
        parseHTML: (element) => element.getAttribute('data-description'),
        renderHTML: (attributes) => {
          if (!attributes.description) return {}
          return { 'data-description': attributes.description }
        },
      },
      characters: {
        default: [],
        parseHTML: (element) => {
          const raw = element.getAttribute('data-characters')
          if (!raw) return []
          try {
            return JSON.parse(raw) as string[]
          } catch {
            return []
          }
        },
        renderHTML: (attributes) => {
          if (!attributes.characters || attributes.characters.length === 0) return {}
          return { 'data-characters': JSON.stringify(attributes.characters) }
        },
      },
      pipelineStatus: {
        default: 'suggested' as PipelineStatus,
        parseHTML: (element) => element.getAttribute('data-status') || 'suggested',
        renderHTML: (attributes) => {
          return { 'data-status': attributes.pipelineStatus || 'suggested' }
        },
      },
      generatedAssetUrl: {
        default: null,
        parseHTML: (element) => element.getAttribute('data-asset-url'),
        renderHTML: (attributes) => {
          if (!attributes.generatedAssetUrl) return {}
          return { 'data-asset-url': attributes.generatedAssetUrl }
        },
      },
      thumbnailUrl: {
        default: null,
        parseHTML: (element) => element.getAttribute('data-thumbnail-url'),
        renderHTML: (attributes) => {
          if (!attributes.thumbnailUrl) return {}
          return { 'data-thumbnail-url': attributes.thumbnailUrl }
        },
      },
    }
  },

  parseHTML() {
    return [{ tag: 'div[data-type="shot-block"]' }]
  },

  renderHTML({ HTMLAttributes }) {
    return [
      'div',
      mergeAttributes(this.options.HTMLAttributes, HTMLAttributes, {
        'data-type': 'shot-block',
        class: 'shot-block',
      }),
      0,
    ]
  },

  addCommands() {
    return {
      setShotBlock:
        (attributes) =>
        ({ commands }) => {
          return commands.setNode(this.name, {
            id: crypto.randomUUID(),
            ...attributes,
          })
        },
    }
  },
})

export default ShotBlock

import Mention from '@tiptap/extension-mention'
import type { SuggestionOptions } from '@tiptap/suggestion'
import type { DerivedCharacter } from '@/store/editorStore'

export type CharacterMentionItem = DerivedCharacter

/**
 * CharacterMention – @触发角色选择的 Mention Extension 配置。
 *
 * suggestion.items 会从外部通过 render 函数传入的 store 获取角色列表，
 * 但这里提供一个默认的空数组占位。实际渲染由 MentionList 组件完成。
 */
export const CharacterMention = Mention.configure({
  HTMLAttributes: {
    class: 'character-mention',
  },
  suggestion: {
    char: '@',
    allowSpaces: true,
    items: ({ query }) => {
      // 默认空实现 – 会在 editor 初始化时被 suggestionConfig 覆盖
      // 实际使用时通过 getSuggestionConfig() 提供带 store 绑定的 items
      return [] as CharacterMentionItem[]
    },
  } as Partial<SuggestionOptions<CharacterMentionItem>>,
})

/**
 * 创建带 store 绑定的 suggestion 配置。
 * 在编辑器初始化时使用，将 editorStore 的 derivedCharacters 注入到 items 函数中。
 */
export function createCharacterSuggestion(
  getCharacters: () => DerivedCharacter[],
): Partial<SuggestionOptions<CharacterMentionItem>> {
  return {
    char: '@',
    allowSpaces: true,
    items: ({ query }) => {
      const characters = getCharacters()
      if (!query) return characters
      const lower = query.toLowerCase()
      return characters.filter((char) =>
        char.name.toLowerCase().includes(lower),
      )
    },
  }
}

export default CharacterMention

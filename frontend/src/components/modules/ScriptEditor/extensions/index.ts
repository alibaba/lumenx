import { SceneHeading } from './SceneHeading'
import { Action } from './Action'
import { CharacterCue } from './CharacterCue'
import { Dialogue } from './Dialogue'
import { Transition } from './Transition'
import { Parenthetical } from './Parenthetical'
import { DualDialogue, DialogueColumn } from './DualDialogue'
import { Note } from './Note'
import { Section } from './Section'
import { Keymap } from './Keymap'
import { ShotBlock } from './ShotBlock'
import { CharacterMention } from './CharacterMention'

export { SceneHeading } from './SceneHeading'
export { Action } from './Action'
export { CharacterCue } from './CharacterCue'
export { Dialogue } from './Dialogue'
export { Transition } from './Transition'
export { Parenthetical } from './Parenthetical'
export { DualDialogue, DialogueColumn } from './DualDialogue'
export { Note } from './Note'
export { Section } from './Section'
export { Keymap } from './Keymap'
export { ShotBlock } from './ShotBlock'
export type { ShotBlockAttributes, ShotBlockOptions, ShotType, PipelineStatus } from './ShotBlock'
export { CharacterMention, createCharacterSuggestion } from './CharacterMention'
export type { CharacterMentionItem } from './CharacterMention'

/**
 * All script editor node extensions bundled for one-shot registration.
 * Usage: useEditorSetup({ extensions: [...scriptExtensions, ...otherExtensions] })
 */
export const scriptExtensions = [
  // Action must be registered FIRST so it becomes the first member of the
  // 'block' group. ProseMirror's ContentMatch.fillBefore picks the first
  // block-group node to fill any `block+` requirement (including the empty
  // top doc). Action's content is `inline*` (empty-completable), so it
  // terminates the fill recursion. If a `block+` node (e.g. blockquote,
  // section) were first, createAndFill would recurse infinitely → stack
  // overflow on empty-doc init.
  Action,
  SceneHeading,
  CharacterCue,
  Dialogue,
  Transition,
  Parenthetical,
  DualDialogue,
  DialogueColumn,
  Note,
  Section,
  Keymap,
  ShotBlock,
  CharacterMention,
]

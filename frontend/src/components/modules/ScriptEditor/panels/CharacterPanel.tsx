'use client';

import { useState } from 'react';
import { useTranslations } from 'next-intl';
import { motion, AnimatePresence } from 'framer-motion';
import { User, Users, ChevronDown } from 'lucide-react';
import type { Editor } from '@tiptap/react';
import { useEditorStore, type DerivedCharacter } from '@/store/editorStore';

export interface CharacterPanelProps {
  editor: Editor | null;
}

function CharacterCard({ character, scenes }: { character: DerivedCharacter; scenes: string[] }) {
  const t = useTranslations('scriptEditor');
  const [expanded, setExpanded] = useState(false);

  return (
    <motion.div
      layout
      className="rounded-lg border border-white/10 bg-zinc-800/80 p-3 cursor-pointer hover:border-white/20 hover:bg-zinc-800 transition-colors"
      onClick={() => setExpanded(!expanded)}
    >
      <div className="flex items-start gap-2">
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-zinc-700">
          <User size={14} className="text-zinc-300" />
        </div>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-foreground truncate">{character.name}</p>
          <div className="flex items-center gap-2 mt-0.5">
            <span className="text-xs text-text-muted">{t('panels.characterOccurrences', { count: character.occurrences })}</span>
            <span className="text-white/20">·</span>
            <span className="text-xs text-text-muted">{t('panels.characterScenes', { count: scenes.length })}</span>
          </div>
        </div>
        <motion.div
          animate={{ rotate: expanded ? 180 : 0 }}
          transition={{ duration: 0.2 }}
        >
          <ChevronDown size={14} className="text-text-muted" />
        </motion.div>
      </div>

      <AnimatePresence>
        {expanded && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="overflow-hidden"
          >
            <div className="mt-3 pt-2 border-t border-white/5">
              <p className="text-xs text-text-muted mb-1.5">{t('panels.appearsInScenes')}</p>
              {scenes.length > 0 ? (
                <ul className="space-y-1">
                  {scenes.map((scene, i) => (
                    <li key={i} className="text-xs text-text-secondary px-2 py-1 rounded bg-zinc-900/50">
                      {scene}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-xs text-text-muted italic">{t('panels.noRelatedScenes')}</p>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
}

export default function CharacterPanel({ editor }: CharacterPanelProps) {
  const t = useTranslations('scriptEditor');
  const derivedCharacters = useEditorStore((s) => s.derivedCharacters);
  const derivedScenes = useEditorStore((s) => s.derivedScenes);

  // Map characters to their related scenes
  const getCharacterScenes = (character: DerivedCharacter): string[] => {
    // Use firstAppearance to show which scene the character first appeared in
    const firstScene = derivedScenes.find((s) => s.number === character.firstAppearance);
    if (firstScene) {
      return [firstScene.title || t('panels.sceneLabel', { number: firstScene.number ?? '?' })];
    }
    return [];
  };

  if (derivedCharacters.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-12 px-4 text-center">
        <div className="flex h-12 w-12 items-center justify-center rounded-full bg-zinc-800 mb-3">
          <Users size={20} className="text-zinc-500" />
        </div>
        <p className="text-sm text-text-muted">{t('panels.characterEmpty')}</p>
        <p className="text-xs text-text-muted/60 mt-1">{t('panels.characterEmptyHint')}</p>
      </div>
    );
  }

  return (
    <div className="p-3">
      <div className="flex items-center gap-2 mb-3">
        <Users size={14} className="text-text-muted" />
        <span className="text-xs font-medium text-text-muted uppercase tracking-wider">
          {t('panels.characterCount', { count: derivedCharacters.length })}
        </span>
      </div>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        {derivedCharacters.map((character) => (
          <CharacterCard
            key={character.id}
            character={character}
            scenes={getCharacterScenes(character)}
          />
        ))}
      </div>
    </div>
  );
}

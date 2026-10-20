'use client';

import { useState } from 'react';
import { useTranslations } from 'next-intl';
import { motion } from 'framer-motion';
import { Film, TreePine, Search } from 'lucide-react';
import type { Editor } from '@tiptap/react';
import SceneNavigator from './SceneNavigator';
import OutlineView from './OutlineView';
import SearchPanel from './SearchPanel';

export interface LeftSidebarProps {
  editor: Editor | null;
  collapsed?: boolean;
}

type SidebarTab = 'scenes' | 'outline' | 'search';

export default function LeftSidebar({ editor, collapsed = false }: LeftSidebarProps) {
  const t = useTranslations('scriptEditor');
  const [activeTab, setActiveTab] = useState<SidebarTab>('scenes');

  const TABS: { id: SidebarTab; label: string; icon: React.ReactNode }[] = [
    { id: 'scenes', label: t('sidebar.scenes'), icon: <Film size={16} /> },
    { id: 'outline', label: t('sidebar.outline'), icon: <TreePine size={16} /> },
    { id: 'search', label: t('sidebar.search'), icon: <Search size={16} /> },
  ];

  // Collapsed mode: only show icon bar
  if (collapsed) {
    return (
      <div className="flex flex-col items-center py-2 gap-2 w-[40px]">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            type="button"
            onClick={() => setActiveTab(tab.id)}
            className={`flex h-8 w-8 items-center justify-center rounded-lg transition-colors ${
              activeTab === tab.id
                ? 'bg-indigo-500/20 text-indigo-400'
                : 'text-text-muted hover:text-foreground hover:bg-white/5'
            }`}
            title={tab.label}
          >
            {tab.icon}
          </button>
        ))}
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      {/* Tab bar */}
      <div className="flex shrink-0 border-b border-white/10">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            type="button"
            onClick={() => setActiveTab(tab.id)}
            className={`relative flex flex-1 items-center justify-center gap-1.5 px-2 py-2.5 text-xs font-medium transition-colors ${
              activeTab === tab.id
                ? 'text-foreground'
                : 'text-text-muted hover:text-text-secondary'
            }`}
          >
            {tab.icon}
            {tab.label}
            {activeTab === tab.id && (
              <motion.div
                layoutId="sidebar-tab-indicator"
                className="absolute bottom-0 left-2 right-2 h-0.5 rounded-full bg-indigo-500"
                transition={{ type: 'spring', stiffness: 400, damping: 30 }}
              />
            )}
          </button>
        ))}
      </div>

      {/* Tab content */}
      <div className="flex-1 overflow-y-auto">
        {activeTab === 'scenes' && <SceneNavigator editor={editor} />}
        {activeTab === 'outline' && <OutlineView editor={editor} />}
        {activeTab === 'search' && <SearchPanel editor={editor} />}
      </div>
    </div>
  );
}

"use client";

import { FolderOpen, Library, Settings } from "lucide-react";
import clsx from "clsx";
import { messages } from "@/lib/i18n";
import LumenXBranding from "./LumenXBranding";

export type GlobalTab = "workspace" | "library" | "settings";

interface GlobalSidebarProps {
  activeTab: GlobalTab;
  onTabChange: (tab: GlobalTab) => void;
}

const NAV_ITEMS: { id: GlobalTab; label: string; icon: typeof FolderOpen; hash: string }[] = [
  { id: "workspace", label: messages.navigation.workspace, icon: FolderOpen, hash: "#/" },
  { id: "library", label: messages.navigation.library, icon: Library, hash: "#/library" },
  { id: "settings", label: messages.navigation.settings, icon: Settings, hash: "#/settings" },
];

export default function GlobalSidebar({ activeTab, onTabChange }: GlobalSidebarProps) {
  const handleNav = (item: (typeof NAV_ITEMS)[number]) => {
    onTabChange(item.id);
    window.location.hash = item.hash;
  };

  return (
    <aside className="w-56 flex-shrink-0 h-full border-r border-glass-border bg-black/40 backdrop-blur-xl flex flex-col">
      {/* Branding */}
      <div className="p-5 border-b border-glass-border">
        <LumenXBranding size="sm" />
      </div>

      {/* Navigation */}
      <nav className="flex-1 p-4 space-y-1">
        {NAV_ITEMS.map((item) => {
          const isActive = activeTab === item.id;
          const Icon = item.icon;
          return (
            <button
              key={item.id}
              onClick={() => handleNav(item)}
              className={clsx(
                "w-full flex items-center gap-3 px-4 py-3 rounded-lg transition-all duration-200 relative overflow-hidden",
                isActive
                  ? "bg-primary/10 text-white"
                  : "text-gray-400 hover:text-white hover:bg-white/5"
              )}
            >
              {isActive && (
                <div className="absolute left-0 w-1 h-full bg-primary rounded-r" />
              )}
              <Icon size={18} className={isActive ? "text-primary" : ""} />
              <span className="text-sm font-medium">{item.label}</span>
            </button>
          );
        })}
      </nav>

      {/* Footer */}
      <div className="p-4 border-t border-glass-border">
        <span className="text-xs text-gray-600 px-4">v0.1.0</span>
      </div>
    </aside>
  );
}

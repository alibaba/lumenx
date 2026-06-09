"use client";

import { useEffect, useState } from "react";
import {
  ChevronLeft,
  ChevronRight,
  FolderOpen,
  Library,
  Sparkles,
  Settings,
} from "lucide-react";
import { useTranslations } from "next-intl";
import clsx from "clsx";
import LumenXBranding from "./LumenXBranding";

export type GlobalTab = "workspace" | "library" | "playground" | "settings";

interface GlobalSidebarProps {
  activeTab: GlobalTab;
  onTabChange: (tab: GlobalTab) => void;
}

const NAV_ITEMS: { id: GlobalTab; icon: typeof FolderOpen; hash: string }[] = [
  { id: "workspace", icon: FolderOpen, hash: "#/" },
  { id: "library", icon: Library, hash: "#/library" },
  { id: "playground", icon: Sparkles, hash: "#/playground" },
  { id: "settings", icon: Settings, hash: "#/settings" },
];

const SIDEBAR_COLLAPSED_STORAGE_KEY = "lumenx-global-sidebar-collapsed";

export default function GlobalSidebar({ activeTab, onTabChange }: GlobalSidebarProps) {
  const t = useTranslations("nav");
  const [isCollapsed, setIsCollapsed] = useState(false);

  useEffect(() => {
    try {
      setIsCollapsed(window.localStorage.getItem(SIDEBAR_COLLAPSED_STORAGE_KEY) === "true");
    } catch {
      // Keep the expanded default when storage is unavailable.
    }
  }, []);

  const handleNav = (item: (typeof NAV_ITEMS)[number]) => {
    onTabChange(item.id);
    window.location.hash = item.hash;
  };

  const toggleCollapsed = () => {
    setIsCollapsed((current) => {
      const next = !current;
      try {
        window.localStorage.setItem(SIDEBAR_COLLAPSED_STORAGE_KEY, String(next));
      } catch {
        // The current session can still use the toggle without persistence.
      }
      return next;
    });
  };

  const toggleLabel = isCollapsed ? t("expandSidebar") : t("collapseSidebar");

  return (
    <aside
      className={clsx(
        "flex-shrink-0 h-full border-r border-glass-border bg-surface backdrop-blur-xl flex flex-col",
        "transition-[width] duration-base ease-out-quart",
        isCollapsed ? "w-[72px]" : "w-56"
      )}
    >
      {/* Branding */}
      <div
        className={clsx(
          "relative border-b border-glass-border",
          isCollapsed ? "flex h-[96px] items-center justify-center px-2" : "p-5 pr-12"
        )}
      >
        <div className="min-w-0">
          <LumenXBranding
            size="sm"
            showSlogan={!isCollapsed}
            showWordmark={!isCollapsed}
          />
        </div>
        <button
          type="button"
          onClick={toggleCollapsed}
          aria-label={toggleLabel}
          title={toggleLabel}
          className={clsx(
            "absolute z-10 flex h-7 w-7 items-center justify-center rounded-md",
            "border border-glass-border bg-surface text-text-muted shadow-sm",
            "transition-colors duration-fast hover:border-primary/40 hover:bg-hover-bg hover:text-foreground",
            isCollapsed ? "-right-3.5 top-[34px]" : "right-3 top-5"
          )}
        >
          {isCollapsed ? <ChevronRight size={16} /> : <ChevronLeft size={16} />}
        </button>
      </div>

      {/* Navigation */}
      <nav className={clsx("flex-1 space-y-1", isCollapsed ? "p-2" : "p-4")}>
        {NAV_ITEMS.map((item) => {
          const isActive = activeTab === item.id;
          const Icon = item.icon;
          return (
            <button
              key={item.id}
              onClick={() => handleNav(item)}
              aria-label={t(item.id)}
              title={isCollapsed ? t(item.id) : undefined}
              className={clsx(
                "w-full flex h-12 items-center rounded-lg transition-all duration-200 relative overflow-hidden",
                isCollapsed ? "justify-center px-0" : "gap-3 px-4",
                isActive
                  ? "bg-primary/10 text-foreground"
                  : "text-text-secondary hover:text-foreground hover:bg-hover-bg"
              )}
            >
              {isActive && (
                <div className="absolute left-0 w-1 h-full bg-primary rounded-r" />
              )}
              <Icon
                size={isCollapsed ? 20 : 18}
                className={clsx("flex-shrink-0", isActive && "text-primary")}
              />
              {!isCollapsed && <span className="text-sm font-medium">{t(item.id)}</span>}
            </button>
          );
        })}
      </nav>

      {/* Footer */}
      {!isCollapsed && (
        <div className="p-4 border-t border-glass-border">
          <span className="text-xs text-text-muted px-4">v0.1.0</span>
        </div>
      )}
    </aside>
  );
}

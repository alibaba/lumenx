"use client";

import { LayoutGrid, Layers, Wand2, Settings, FileText } from "lucide-react";
import { useTranslations } from "next-intl";
import clsx from "clsx";
import LumenXBranding from "./LumenXBranding";
import { isTauri } from "@/lib/transport";

export type GlobalTab = "workspace" | "library" | "editor" | "playground" | "settings";

interface GlobalSidebarProps {
  activeTab: GlobalTab;
  onTabChange: (tab: GlobalTab) => void;
}

// Shared global nav model (workspace/library/playground + settings). Reused by
// the desktop GlobalSidebar (below) and the mobile BottomTabBar (md:hidden).
export const GLOBAL_NAV_ITEMS: { id: GlobalTab; icon: typeof LayoutGrid; hash: string }[] = [
  { id: "workspace", icon: LayoutGrid, hash: "#/" },
  { id: "library", icon: Layers, hash: "#/library" },
  { id: "editor", icon: FileText, hash: "#/studio/editor" },
  { id: "playground", icon: Wand2, hash: "#/playground" },
  { id: "settings", icon: Settings, hash: "#/settings" },
];

const APP_VERSION = "v0.2.0";

function NavButton({
  active,
  label,
  icon: Icon,
  onClick,
}: {
  active: boolean;
  label: string;
  icon: typeof LayoutGrid;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-current={active ? "page" : undefined}
      className={clsx(
        "group relative flex items-center gap-3 w-full px-3 py-2.5 rounded-lg text-left transition-colors",
        active
          ? "bg-primary/10 text-foreground font-semibold"
          : "text-text-secondary hover:bg-hover-bg hover:text-foreground font-medium"
      )}
    >
      {/* Active accent bar */}
      {active && (
        <span className="absolute left-0 top-1/2 -translate-y-1/2 h-[18px] w-[3px] rounded-r bg-primary" />
      )}
      <Icon
        size={18}
        strokeWidth={1.8}
        className={clsx(
          "flex-shrink-0 transition-colors",
          active ? "text-primary" : "text-text-muted group-hover:text-foreground"
        )}
      />
      <span className="text-base">{label}</span>
    </button>
  );
}

/**
 * 全局导航 —— 带文字标签的品牌侧栏（Line B "Luminous Atelier"）。
 *
 * 顶部常驻 Logo + LUMENX 字标 + Slogan；主导航图标+文字（无 hover、无歧义）；
 * 设置固定底部；底部版本号。早先为给二级筛选栏腾地的 60px 图标轨已废弃——
 * 资产库/设置改走横向筛选后，竖向只剩这一条栏，故恢复完整品牌呈现。
 * 结构对所有主题统一，视觉身份由语义 token 切换（zero-leak）。
 */
export default function GlobalSidebar({ activeTab, onTabChange }: GlobalSidebarProps) {
  const t = useTranslations("nav");

  const handleNav = (id: GlobalTab, hash: string) => {
    onTabChange(id);
    window.location.hash = hash;
  };

  return (
    <aside
      className="w-52 flex-shrink-0 h-full hidden md:flex flex-col border-r border-glass-border bg-surface/60 backdrop-blur-xl"
      data-tauri-drag-region
    >
      {/* Traffic Light inset for Tauri desktop */}
      {isTauri() && <div className="tauri-titlebar-inset" />}
      {/* Brand lockup — Logo + LUMENX + Slogan, click → workspace */}
      <button
        type="button"
        onClick={() => handleNav("workspace", "#/")}
        aria-label={t("workspaceAria")}
        className="text-left px-4 pt-5 pb-4 border-b border-glass-border hover:opacity-90 transition-opacity"
      >
        <LumenXBranding size="md" showSlogan={false} />
        <p className="font-display atelier-display text-[0.75rem] italic text-text-muted tracking-wide leading-snug mt-2.5">
          Render Noise into Narrative
        </p>
      </button>

      {/* Primary navigation */}
      <nav className="flex-1 flex flex-col gap-0.5 p-2.5" aria-label={t("mainNavAria")}>
        {GLOBAL_NAV_ITEMS.slice(0, 4).map((item) => (
          <NavButton
            key={item.id}
            active={activeTab === item.id}
            label={t(item.id)}
            icon={item.icon}
            onClick={() => handleNav(item.id, item.hash)}
          />
        ))}
      </nav>

      {/* Settings pinned bottom + version */}
      <div className="p-2.5 border-t border-glass-border">
        <NavButton
          active={activeTab === "settings"}
          label={t("settings")}
          icon={Settings}
          onClick={() => handleNav("settings", "#/settings")}
        />
        <div className="px-3 pt-2.5 font-mono text-[0.6875rem] tracking-wide text-text-muted">
          {APP_VERSION}
        </div>
      </div>
    </aside>
  );
}

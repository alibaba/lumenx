"use client";

import { useState, useEffect, useMemo } from "react";
import { Search, Users, MapPin, Package, Image as ImageIcon, ChevronDown, ChevronRight } from "lucide-react";
import { api } from "@/lib/api";
import AssetCard from "@/components/common/AssetCard";
import { assetTypeTerms, messages } from "@/lib/i18n";
import type { Series, Project, Character, Scene, Prop } from "@/store/projectStore";

type AssetTab = "characters" | "scenes" | "props";

interface AssetSource {
  id: string;
  name: string;
  type: "series" | "project";
  characters: Character[];
  scenes: Scene[];
  props: Prop[];
}

export default function AssetLibraryPage() {
  const copy = messages.libraryPage;
  const [sources, setSources] = useState<AssetSource[]>([]);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState<AssetTab>("characters");
  const [searchQuery, setSearchQuery] = useState("");
  const [collapsedSources, setCollapsedSources] = useState<Set<string>>(new Set());

  useEffect(() => {
    loadAssets();
  }, []);

  const loadAssets = async () => {
    setLoading(true);
    try {
      const [seriesList, projects] = await Promise.all([
        api.listSeries(),
        api.getProjects(),
      ]);

      const result: AssetSource[] = [];

      for (const s of seriesList as Series[]) {
        if ((s.characters?.length || 0) + (s.scenes?.length || 0) + (s.props?.length || 0) > 0) {
          result.push({
            id: `series-${s.id}`,
            name: s.title,
            type: "series",
            characters: s.characters || [],
            scenes: s.scenes || [],
            props: s.props || [],
          });
        }
      }

      const standaloneProjects = (projects as Project[]).filter((p) => !p.series_id);
      for (const p of standaloneProjects) {
        if ((p.characters?.length || 0) + (p.scenes?.length || 0) + (p.props?.length || 0) > 0) {
          result.push({
            id: `project-${p.id}`,
            name: p.title,
            type: "project",
            characters: p.characters || [],
            scenes: p.scenes || [],
            props: p.props || [],
          });
        }
      }

      setSources(result);
    } catch (error) {
      console.error("Failed to load asset library:", error);
    } finally {
      setLoading(false);
    }
  };

  const tabs: { id: AssetTab; label: string; icon: typeof Users }[] = [
    { id: "characters", label: assetTypeTerms.character.pluralLabel, icon: Users },
    { id: "scenes", label: assetTypeTerms.scene.pluralLabel, icon: MapPin },
    { id: "props", label: assetTypeTerms.prop.pluralLabel, icon: Package },
  ];

  const filteredSources = useMemo(() => {
    if (!searchQuery.trim()) return sources;
    const q = searchQuery.toLowerCase();
    return sources
      .map((source) => ({
        ...source,
        characters: source.characters.filter((a) => a.name.toLowerCase().includes(q) || a.description?.toLowerCase().includes(q)),
        scenes: source.scenes.filter((a) => a.name.toLowerCase().includes(q) || a.description?.toLowerCase().includes(q)),
        props: source.props.filter((a) => a.name.toLowerCase().includes(q) || a.description?.toLowerCase().includes(q)),
      }))
      .filter((s) => {
        const count = activeTab === "characters" ? s.characters.length : activeTab === "scenes" ? s.scenes.length : s.props.length;
        return count > 0;
      });
  }, [sources, searchQuery, activeTab]);

  const totalCount = filteredSources.reduce((acc, s) => {
    return acc + (activeTab === "characters" ? s.characters.length : activeTab === "scenes" ? s.scenes.length : s.props.length);
  }, 0);

  const toggleCollapse = (sourceId: string) => {
    setCollapsedSources((prev) => {
      const next = new Set(prev);
      if (next.has(sourceId)) next.delete(sourceId);
      else next.add(sourceId);
      return next;
    });
  };

  return (
    <div className="container mx-auto px-6 py-8 max-w-6xl">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-display font-bold text-white">{copy.title}</h1>
        <div className="relative w-72">
          <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-500" />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder={copy.searchPlaceholder}
            className="w-full bg-white/5 border border-white/10 rounded-lg pl-9 pr-4 py-2 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-primary/50 transition-colors"
          />
        </div>
      </div>

      {/* Tabs */}
      <div className="flex border-b border-gray-700/50 mb-6">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={`flex items-center gap-2 px-5 py-3 text-sm font-medium transition-colors border-b-2 ${
              activeTab === tab.id
                ? "border-primary text-white"
                : "border-transparent text-gray-400 hover:text-gray-200"
            }`}
          >
            <tab.icon size={16} />
            {tab.label}
          </button>
        ))}
        <div className="flex-1" />
        <span className="self-center text-xs text-gray-500">{copy.totalAssets(totalCount)}</span>
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-20">
          <div className="text-gray-400">{copy.loading}</div>
        </div>
      ) : filteredSources.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-20 text-gray-500">
          <ImageIcon size={48} className="mb-3 text-gray-600" />
          <p className="text-sm">{copy.emptyTitle}</p>
          <p className="text-xs text-gray-600 mt-1">{copy.emptyHint}</p>
        </div>
      ) : (
        <div className="space-y-6">
          {filteredSources.map((source) => {
            const assets: (Character | Scene | Prop)[] =
              activeTab === "characters" ? source.characters : activeTab === "scenes" ? source.scenes : source.props;
            if (assets.length === 0) return null;
            const isCollapsed = collapsedSources.has(source.id);

            return (
              <div key={source.id}>
                <button
                  onClick={() => toggleCollapse(source.id)}
                  className="flex items-center gap-2 mb-3 text-sm text-gray-300 hover:text-white transition-colors"
                >
                  {isCollapsed ? <ChevronRight size={14} /> : <ChevronDown size={14} />}
                  <span className="font-medium">{source.name}</span>
                  <span className="text-xs px-2 py-0.5 rounded bg-white/5 text-gray-500">
                    {source.type === "series" ? copy.sourceType.series : copy.sourceType.project} · {assets.length}
                  </span>
                </button>
                {!isCollapsed && (
                  <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-4 pl-6">
                    {assets.map((asset) => (
                      <AssetCard key={asset.id} asset={asset} type={activeTab} />
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

"use client";

import { useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import type { AtelierNode } from "@/lib/atelierTypes";

interface Props {
  node: AtelierNode;
  nodes: AtelierNode[];
  disabled: boolean;
  onSave: (regionId: string | null) => Promise<unknown>;
}

/** The shell keys this editor by environment, project and selected node. */
export function RegionMembershipControls({ node, nodes, disabled, onSave }: Props) {
  const t = useTranslations("atelier.foundation");
  const savedRegionId = typeof node.data.region_id === "string" ? node.data.region_id : "";
  const [draft, setDraft] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const active = useRef(true);
  const inFlight = useRef(false);
  const regions = nodes.filter(item => item.type === "region");
  const selectedRegionId = draft ?? savedRegionId;
  const missing = !!selectedRegionId && !regions.some(region => region.id === selectedRegionId);
  const changed = selectedRegionId !== savedRegionId;
  const blocked = disabled || saving;
  const savedRegion = regions.find(region => region.id === savedRegionId);
  const savedLabel = !savedRegionId ? t("regionNone") : savedRegion ? savedRegion.title || t("createRegion") : t("regionUnavailable");

  useEffect(() => {
    active.current = true;
    return () => { active.current = false; };
  }, []);

  useEffect(() => {
    if (draft !== null && draft === savedRegionId) {
      setDraft(null);
      setError(null);
    }
  }, [draft, savedRegionId]);

  const save = async () => {
    if (blocked || !changed || missing || inFlight.current) return;
    inFlight.current = true;
    setSaving(true);
    setError(null);
    try {
      await onSave(selectedRegionId || null);
    } catch (cause) {
      if (active.current) setError(cause instanceof Error ? `${t("regionSaveError")}: ${cause.message}` : t("regionSaveError"));
    } finally {
      inFlight.current = false;
      if (active.current) setSaving(false);
    }
  };

  return (
    <div className="space-y-2 border-t border-white/[0.08] pt-3">
      <label className="block text-xs text-white/45">
        {t("regionMembership")}
        <select
          aria-label={t("regionMembership")}
          className="mt-1 w-full rounded-xl border border-white/[0.1] bg-black/35 px-3 py-2 text-sm text-white/80 outline-none focus:border-white/25 disabled:opacity-40"
          disabled={blocked}
          value={selectedRegionId}
          onChange={event => { setDraft(event.target.value); setError(null); }}
        >
          <option value="">{t("regionNone")}</option>
          {regions.map(region => <option key={region.id} value={region.id}>{region.title || t("createRegion")}</option>)}
          {missing ? <option value={selectedRegionId} disabled>{t("regionUnavailable")}</option> : null}
        </select>
      </label>
      <p className="text-xs text-white/45">{t("regionSaved", { region: savedLabel })}</p>
      {changed ? <p role="status" className="text-xs text-amber-200/80">{t("regionUnsaved")}</p> : null}
      {error ? <p role="alert" className="text-xs text-rose-300">{error}</p> : null}
      <button
        type="button"
        className="rounded-xl border border-white/[0.1] bg-white/[0.06] px-3 py-2 text-sm text-white/75 transition hover:bg-white/[0.1] disabled:cursor-not-allowed disabled:opacity-40"
        disabled={blocked || !changed || missing}
        onClick={() => { void save(); }}
      >
        {saving ? t("commandBusy") : t("saveRegionMembership")}
      </button>
    </div>
  );
}

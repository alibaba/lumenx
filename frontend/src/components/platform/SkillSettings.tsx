"use client";

import { useEffect, useState } from "react";
import { platformApi, type PlatformSkillVersion } from "@/lib/platformApi";

async function hashManifest(manifest: Omit<Parameters<typeof platformApi.createSkillVersion>[0]["manifest"], "content_hash">): Promise<string> {
  const canonical = JSON.stringify(Object.fromEntries(Object.entries(manifest).sort(([a], [b]) => a.localeCompare(b))));
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(canonical));
  return Array.from(new Uint8Array(digest), value => value.toString(16).padStart(2, "0")).join("");
}

export default function SkillSettings() {
  const [items, setItems] = useState<PlatformSkillVersion[]>([]);
  const [name, setName] = useState("story-scout");
  const [version, setVersion] = useState("1.0.0-user");
  const [title, setTitle] = useState("我的 Story Scout");
  const [tools, setTools] = useState("canvas.readProject");
  const [projectId, setProjectId] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const refresh = () => platformApi.skills().then(setItems).catch(() => setMessage("Skill 列表读取失败"));
  useEffect(() => { void refresh(); }, []);
  const create = async () => {
    setMessage(null);
    try {
      const manifestBase = { name, version, title, expected_tools: tools.split(",").map(item => item.trim()).filter(Boolean) };
      const contentHash = await hashManifest(manifestBase);
      await platformApi.createSkillVersion({ manifest: { ...manifestBase, content_hash: contentHash }, scope: projectId ? "project" : "user", ...(projectId ? { project_id: projectId } : {}) });
      await refresh(); setMessage("Skill 草稿已创建");
    } catch (error) { setMessage(error instanceof Error ? error.message : "Skill 创建失败"); }
  };
  const bind = async (id: string) => { try { await platformApi.bindSkillVersion(id); await refresh(); setMessage("Skill 已激活"); } catch (error) { setMessage(error instanceof Error ? error.message : "Skill 激活失败"); } };
  return <div className="glass-panel p-6 space-y-5"><div><h2 className="text-xl font-semibold text-foreground">我的视频 Skill</h2><p className="text-sm text-text-secondary mt-2">当前只展示受控 manifest 和版本绑定；不允许上传脚本或执行任意代码。</p></div><div className="grid md:grid-cols-2 gap-3"><input className="glass-input" value={name} onChange={e => setName(e.target.value)} placeholder="Skill 名称" /><input className="glass-input" value={version} onChange={e => setVersion(e.target.value)} placeholder="版本" /><input className="glass-input md:col-span-2" value={title} onChange={e => setTitle(e.target.value)} placeholder="显示名称" /><input className="glass-input md:col-span-2" value={tools} onChange={e => setTools(e.target.value)} placeholder="允许工具，逗号分隔" /><input className="glass-input md:col-span-2" value={projectId} onChange={e => setProjectId(e.target.value)} placeholder="项目 ID（留空表示账户级）" /></div><button type="button" className="bg-primary text-foreground rounded-lg px-4 py-2" onClick={() => void create()}>创建 Skill 草稿</button>{message && <p role="status" className="text-sm text-text-secondary">{message}</p>}<div className="space-y-2">{items.map(item => <div key={item.id} className="flex items-center justify-between gap-3 border-b border-glass-border py-3"><div><p className="text-foreground">{item.name} · {item.version}</p><p className="text-xs text-text-muted">{item.scope}{item.project_id ? ` · ${item.project_id}` : ""} · {item.status}</p></div><button type="button" className="glass-button" onClick={() => void bind(item.id)} disabled={item.status === "active"}>激活</button></div>)}</div></div>;
}

"use client";

import { useEffect, useState } from "react";
import { platformApi } from "@/lib/platformApi";
import { getBillingMode, getCredentialRef, saveComputePreference, type BillingMode } from "@/lib/computePreference";

type Credential = { id: string; provider: string; label: string; masked_key: string; status: string; last_verified_at?: string | null };

export default function ComputeSettings() {
  const [mode, setMode] = useState<BillingMode>(getBillingMode());
  const [credentialRef, setCredentialRef] = useState(getCredentialRef() || "");
  const [credentials, setCredentials] = useState<Credential[]>([]);
  const [balance, setBalance] = useState<number | null>(null);
  const [provider, setProvider] = useState("dashscope");
  const [label, setLabel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [message, setMessage] = useState<string | null>(null);

  const refresh = () => Promise.all([platformApi.credentials(), platformApi.creditBalance()]).then(([items, wallet]) => { setCredentials(items); setBalance(wallet.available_credits); }).catch(() => setMessage("算力配置读取失败"));
  useEffect(() => { void refresh(); }, []);

  const save = () => { saveComputePreference(mode, credentialRef || null); setMessage("算力来源已保存；下一次生成请求开始生效"); };
  const addCredential = async () => {
    if (!apiKey || !label) return;
    try { const created = await platformApi.createCredential({ provider, label, api_key: apiKey }) as Credential; setApiKey(""); setLabel(""); setCredentialRef(created.id); setMode("byok"); await refresh(); setMessage("API-Key 已加密保存"); } catch (error) { setMessage(error instanceof Error ? error.message : "API-Key 保存失败"); }
  };

  return <div className="space-y-5">
    <div className="glass-panel p-6"><h2 className="text-xl font-semibold text-foreground">算力来源</h2><p className="text-sm text-text-secondary mt-2">每次 Studio / Atelier 请求都会读取这里的选择。</p><div className="grid md:grid-cols-2 gap-3 mt-5"><button type="button" className={`rounded-lg border p-4 text-left ${mode === "platform_credits" ? "border-primary bg-primary/10" : "border-glass-border"}`} onClick={() => setMode("platform_credits")}><strong>平台积分</strong><p className="text-xs text-text-secondary mt-1">平台承担 Provider 费用，当前余额：{balance ?? "—"} 积分</p></button><button type="button" className={`rounded-lg border p-4 text-left ${mode === "byok" ? "border-primary bg-primary/10" : "border-glass-border"}`} onClick={() => setMode("byok")}><strong>我的 API-Key</strong><p className="text-xs text-text-secondary mt-1">使用自己的 Provider 凭证，不扣平台积分</p></button></div>{mode === "byok" && <select className="glass-input w-full mt-4" value={credentialRef} onChange={event => setCredentialRef(event.target.value)}><option value="">选择已保存的 API-Key</option>{credentials.filter(item => item.status === "active").map(item => <option key={item.id} value={item.id}>{item.label} · {item.provider} · {item.masked_key}</option>)}</select>}<button type="button" className="bg-primary text-foreground rounded-lg px-4 py-2 mt-4" onClick={save} disabled={mode === "byok" && !credentialRef}>保存算力来源</button>{message && <p role="status" className="text-sm text-text-secondary mt-3">{message}</p>}</div>
    <div className="glass-panel p-6"><h2 className="text-xl font-semibold text-foreground">添加 API-Key</h2><p className="text-sm text-text-secondary mt-2">明文只提交到后端一次，平台只返回脱敏指纹。</p><div className="grid md:grid-cols-3 gap-3 mt-4"><input className="glass-input" placeholder="Provider，如 dashscope" value={provider} onChange={event => setProvider(event.target.value)} /><input className="glass-input" placeholder="凭证名称" value={label} onChange={event => setLabel(event.target.value)} /><input className="glass-input" type="password" placeholder="API-Key" value={apiKey} onChange={event => setApiKey(event.target.value)} /></div><button type="button" className="glass-button mt-4" onClick={() => void addCredential()} disabled={!label || !apiKey}>加密保存</button><div className="mt-5 space-y-2">{credentials.map(item => <div key={item.id} className="flex items-center justify-between border-b border-glass-border py-2 text-sm"><span>{item.label} · {item.provider} · {item.masked_key}</span><button type="button" className="text-status-failed-fg" onClick={() => void platformApi.revokeCredential(item.id).then(refresh)}>撤销</button></div>)}</div></div>
  </div>;
}

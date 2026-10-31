"use client";

import { useEffect, useState, type FormEvent } from "react";
import { BriefcaseBusiness, GraduationCap, KeyRound, LogIn, ShieldCheck, Sparkles, Trophy, UserRound, Users } from "lucide-react";
import { platformApi } from "@/lib/platformApi";
import { usePlatformStore } from "@/store/platformStore";
import ComputeSettings from "@/components/platform/ComputeSettings";
import SkillSettings from "@/components/platform/SkillSettings";

type Section = "users" | "compute" | "skills" | "competitions" | "certification" | "tasks" | "academy";

const sections: Array<{ id: Section; label: string; icon: typeof Users }> = [
  { id: "users", label: "用户管理", icon: Users },
  { id: "compute", label: "算力与凭证", icon: KeyRound },
  { id: "skills", label: "我的视频 Skill", icon: Sparkles },
  { id: "competitions", label: "赛事活动", icon: Trophy },
  { id: "certification", label: "创作者认证", icon: ShieldCheck },
  { id: "tasks", label: "商业任务", icon: BriefcaseBusiness },
  { id: "academy", label: "培训学院", icon: GraduationCap },
];

export default function PlatformPortal() {
  const [section, setSection] = useState<Section>("users");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [email, setEmail] = useState("");
  const [registerMode, setRegisterMode] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [summary, setSummary] = useState<{ competitions: number; tasks: number; courses: number } | null>(null);
  const [tokenUsage, setTokenUsage] = useState<{ prompt_tokens: number; completion_tokens: number; total_tokens: number } | null>(null);
  const { user, walletCents, loading, error, initialize, login, register, logout } = usePlatformStore();

  useEffect(() => { void initialize(); }, [initialize]);

  useEffect(() => {
    if (!user) return;
    Promise.all([platformApi.competitions(), platformApi.tasks(), platformApi.courses(), platformApi.tokenUsage()])
      .then(([competitions, tasks, courses, usage]) => { setSummary({ competitions: competitions.length, tasks: tasks.length, courses: courses.length }); setTokenUsage(usage); })
      .catch(() => setSummary(null));
  }, [user]);

  const submitAuth = async (event: FormEvent) => {
    event.preventDefault(); setMessage(null);
    try { if (registerMode) await register(username, password, email || undefined); else await login(username, password); setPassword(""); setMessage(registerMode ? "账户已创建并登录" : "登录成功"); }
    catch { /* store exposes the error */ }
  };

  return (
    <main className="min-h-full p-6 md:p-10 bg-background">
      <header className="max-w-6xl mx-auto mb-8 flex items-start justify-between gap-6">
        <div><p className="font-mono text-xs tracking-[0.28em] text-primary uppercase">Creator Platform · Account & Operations</p><h1 className="text-4xl font-display text-foreground mt-3">创作者平台</h1><p className="text-text-secondary mt-2">用户、认证、赛事、培训与商业任务统一工作台</p></div>
        {user && <button type="button" onClick={() => void logout()} className="glass-button">退出登录</button>}
      </header>
      <div className="max-w-6xl mx-auto grid lg:grid-cols-[220px_1fr] gap-6">
        <nav className="glass-panel p-3 h-fit space-y-1" aria-label="平台导航">
          {sections.map(({ id, label, icon: Icon }) => <button key={id} type="button" onClick={() => setSection(id)} className={`w-full flex items-center gap-3 rounded-lg px-3 py-3 text-left transition-colors ${section === id ? "bg-primary/15 text-foreground" : "text-text-secondary hover:bg-hover-bg"}`}><Icon size={17} /><span>{label}</span></button>)}
        </nav>
        <section className="space-y-6">
          {!user ? <div className="glass-panel p-6 max-w-xl"><div className="flex items-center gap-3 mb-5"><div className="rounded-xl bg-primary/15 p-3 text-primary"><LogIn size={22} /></div><div><h2 className="text-xl font-semibold text-foreground">{registerMode ? "创建平台账户" : "登录创作者平台"}</h2><p className="text-sm text-text-secondary">登录后进入 Studio / Atelier 和平台业务</p></div></div><form onSubmit={submitAuth} className="space-y-4"><input className="glass-input w-full" placeholder="用户名" value={username} onChange={e => setUsername(e.target.value)} required /><input className="glass-input w-full" type="password" placeholder="密码（至少 12 位）" value={password} onChange={e => setPassword(e.target.value)} minLength={12} required />{registerMode && <input className="glass-input w-full" type="email" placeholder="邮箱（可选）" value={email} onChange={e => setEmail(e.target.value)} />}<button className="bg-primary text-foreground rounded-lg px-4 py-3 w-full font-semibold disabled:opacity-50" disabled={loading}>{loading ? "处理中…" : registerMode ? "注册并登录" : "登录"}</button></form><button type="button" className="text-sm text-primary mt-4" onClick={() => setRegisterMode(v => !v)}>{registerMode ? "已有账户？返回登录" : "还没有账户？创建账户"}</button>{(error || message) && <p role="status" className={`mt-4 text-sm ${error ? "text-status-failed-fg" : "text-status-success-fg"}`}>{error || message}</p>}</div> : <><div className="glass-panel p-6"><div className="flex items-center justify-between"><div className="flex items-center gap-3"><div className="rounded-full bg-primary/15 p-3 text-primary"><UserRound size={22} /></div><div><h2 className="text-xl font-semibold text-foreground">{user.username}</h2><p className="text-sm text-text-secondary">角色：{user.role} · 平台账户</p></div></div><div className="text-right"><p className="text-xs text-text-muted">模拟账本余额</p><p className="text-2xl font-mono text-primary">¥{(walletCents / 100).toFixed(2)}</p></div></div></div><div className="grid md:grid-cols-3 gap-4">{[["赛事活动", summary?.competitions ?? "—"], ["开放任务", summary?.tasks ?? "—"], ["培训课程", summary?.courses ?? "—"]].map(([label, value]) => <div key={label} className="glass-panel p-5"><p className="text-sm text-text-secondary">{label}</p><p className="text-3xl font-display text-foreground mt-2">{value}</p></div>)}</div><div className="glass-panel p-6"><h2 className="text-xl font-semibold text-foreground">{sections.find(item => item.id === section)?.label}</h2><p className="text-text-secondary mt-2">当前账户已登录。此区域承载对应平台业务入口和状态。</p><div className="mt-5 rounded-xl border border-glass-border bg-surface/50 p-5"><p className="text-sm text-text-secondary">算力账本</p><p className="text-foreground mt-1">输入 {tokenUsage?.prompt_tokens ?? 0} · 输出 {tokenUsage?.completion_tokens ?? 0} · 总计 {tokenUsage?.total_tokens ?? 0} tokens</p></div></div></>}
          {user && section === "compute" && <ComputeSettings />}
          {user && section === "skills" && <SkillSettings />}
          {user && <div className="glass-panel p-6"><p className="text-sm text-text-secondary">LumenX 算力账本</p><p className="text-2xl font-mono text-primary mt-2">{tokenUsage?.total_tokens ?? 0} tokens</p><p className="text-xs text-text-muted mt-2">输入 {tokenUsage?.prompt_tokens ?? 0} · 输出 {tokenUsage?.completion_tokens ?? 0}</p><button type="button" className="glass-button mt-4" onClick={() => { window.location.hash = "#/"; }}>进入 LumenX 工作区</button></div>}
        </section>
      </div>
    </main>
  );
}


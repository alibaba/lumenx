import { getApiUrl } from "./apiUrl";

const TOKEN_KEY = "lumenx_platform_token";

export type PlatformUser = { id: string; username: string; email?: string | null; role: string };
export type PlatformSession = { access_token: string; expires_at: string; user: PlatformUser };

export function getPlatformToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_KEY);
}

export function clearPlatformToken() {
  if (typeof window !== "undefined") window.localStorage.removeItem(TOKEN_KEY);
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Content-Type", "application/json");
  const token = getPlatformToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(`${getApiUrl()}/api/platform${path}`, { ...init, headers });
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    const message = body?.detail?.message || body?.detail || `Platform request failed (${response.status})`;
    throw new Error(typeof message === "string" ? message : JSON.stringify(message));
  }
  return body as T;
}

export const platformApi = {
  register: (payload: { username: string; password: string; email?: string }) => request<PlatformUser>("/auth/register", { method: "POST", body: JSON.stringify(payload) }),
  login: async (payload: { username: string; password: string }) => {
    const session = await request<PlatformSession>("/auth/login", { method: "POST", body: JSON.stringify(payload) });
    if (typeof window !== "undefined") window.localStorage.setItem(TOKEN_KEY, session.access_token);
    return session;
  },
  me: () => request<PlatformUser & { wallet?: { simulated_balance_cents: number } }>("/auth/me"),
  logout: async () => { await request("/auth/logout", { method: "POST" }); clearPlatformToken(); },
  competitions: () => request<Array<{ id: string; title: string; status: string; description: string }>>("/competitions"),
  courses: () => request<Array<{ id: string; title: string; description: string }>>("/academy/courses"),
  tasks: () => request<Array<{ id: string; title: string; category: string; bounty_cents: number; deadline: string }>>("/tasks/square"),
  certification: () => request<{ status: string; level: string } | null>("/certifications/status"),
  tokenUsage: (limit = 20) => request<{ prompt_tokens: number; completion_tokens: number; total_tokens: number; records: Array<Record<string, unknown>> }>(`/usage/tokens?limit=${limit}`),
};


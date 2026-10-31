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
  creditPricing: () => request<Array<{ provider: string; model: string; pricing_version: string; input_credits_per_1k: number; output_credits_per_1k: number }>>("/credits/pricing"),
  creditBalance: () => request<{ user_id: string; available_credits: number; credited_credits: number; debited_credits: number }>("/credits/balance"),
  creditLedger: (limit = 50) => request<Array<Record<string, unknown>>>(`/credits/ledger?limit=${limit}`),
  estimateCredits: (payload: { provider: string; model: string; prompt_tokens: number; completion_tokens: number }) => request<{ estimated_credits: number; pricing_version: string }>("/credits/estimate", { method: "POST", body: JSON.stringify(payload) }),
  mediaPricing: () => request<Array<{ provider: string; model: string; media_kind: string; pricing_version: string; credits_per_unit: number }>>("/credits/media-pricing"),
  estimateMediaCredits: (payload: { provider: string; model: string; media_kind: string; units: number }) => request<{ estimated_credits: number; pricing_version: string; credits_per_unit: number }>("/credits/media-estimate", { method: "POST", body: JSON.stringify(payload) }),
  reserveCredits: (payload: { estimated_credits: number; generation_id: string; pricing_version: string; idempotency_key: string }) => request<{ reservation_id: string; estimated_credits: number; status: string }>("/credits/reservations", { method: "POST", body: JSON.stringify(payload) }),
  settleCredits: (reservationId: string, actual_credits: number) => request(`/credits/reservations/${reservationId}/settle`, { method: "POST", body: JSON.stringify({ actual_credits }) }),
  releaseCredits: (reservationId: string) => request(`/credits/reservations/${reservationId}/release`, { method: "POST" }),
  credentials: () => request<Array<{ id: string; provider: string; label: string; masked_key: string; status: string; last_verified_at?: string | null }>>("/credentials"),
  createCredential: (payload: { provider: string; label: string; api_key: string }) => request("/credentials", { method: "POST", body: JSON.stringify(payload) }),
  verifyCredential: (credentialId: string) => request(`/credentials/${credentialId}/verify`, { method: "POST" }),
  rotateCredential: (credentialId: string, api_key: string) => request(`/credentials/${credentialId}/rotate`, { method: "POST", body: JSON.stringify({ api_key }) }),
  revokeCredential: (credentialId: string) => request(`/credentials/${credentialId}/revoke`, { method: "POST" }),
};


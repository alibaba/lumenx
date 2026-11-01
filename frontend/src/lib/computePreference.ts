export type BillingMode = "platform_credits" | "byok";

const MODE_KEY = "lumenx_billing_mode";
const CREDENTIAL_KEY = "lumenx_credential_ref";

export function getBillingMode(): BillingMode {
  if (typeof window === "undefined") return "platform_credits";
  return window.localStorage.getItem(MODE_KEY) === "byok" ? "byok" : "platform_credits";
}

export function getCredentialRef(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(CREDENTIAL_KEY);
}

export function saveComputePreference(mode: BillingMode, credentialRef?: string | null): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(MODE_KEY, mode);
  if (mode === "byok" && credentialRef) window.localStorage.setItem(CREDENTIAL_KEY, credentialRef);
  else window.localStorage.removeItem(CREDENTIAL_KEY);
}

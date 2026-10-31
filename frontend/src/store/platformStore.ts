import { create } from "zustand";
import { clearPlatformToken, getPlatformToken, platformApi, type PlatformUser } from "@/lib/platformApi";

type PlatformStore = {
  user: PlatformUser | null;
  walletCents: number;
  loading: boolean;
  error: string | null;
  initialize: () => Promise<void>;
  login: (username: string, password: string) => Promise<void>;
  register: (username: string, password: string, email?: string) => Promise<void>;
  logout: () => Promise<void>;
  clearError: () => void;
};

export const usePlatformStore = create<PlatformStore>((set) => ({
  user: null,
  walletCents: 0,
  loading: false,
  error: null,
  initialize: async () => {
    if (!getPlatformToken()) return;
    set({ loading: true, error: null });
    try {
      const me = await platformApi.me();
      set({ user: me, walletCents: me.wallet?.simulated_balance_cents ?? 0, loading: false });
    } catch (error) {
      clearPlatformToken();
      set({ user: null, loading: false, error: error instanceof Error ? error.message : String(error) });
    }
  },
  login: async (username, password) => {
    set({ loading: true, error: null });
    try { const session = await platformApi.login({ username, password }); set({ user: session.user, loading: false }); }
    catch (error) { set({ loading: false, error: error instanceof Error ? error.message : String(error) }); throw error; }
  },
  register: async (username, password, email) => {
    set({ loading: true, error: null });
    try { await platformApi.register({ username, password, email }); const session = await platformApi.login({ username, password }); set({ user: session.user, loading: false }); }
    catch (error) { set({ loading: false, error: error instanceof Error ? error.message : String(error) }); throw error; }
  },
  logout: async () => { await platformApi.logout(); set({ user: null, walletCents: 0 }); },
  clearError: () => set({ error: null }),
}));


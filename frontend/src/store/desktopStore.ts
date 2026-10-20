/**
 * Desktop App Store - manages Tauri desktop-specific state
 * Handles: backend readiness, native capabilities, window state
 */
import { create } from 'zustand';
import { isTauri, checkBackendReady } from '@/lib/transport';

interface DesktopState {
    /** Whether running in Tauri desktop environment */
    isDesktop: boolean;
    /** Whether the Python backend sidecar is ready */
    backendReady: boolean;
    /** Whether the backend health check is in progress */
    backendChecking: boolean;
    /** Error message if backend failed to start */
    backendError: string | null;

    /** Initialize desktop state detection */
    init: () => void;
    /** Start polling for backend readiness */
    startHealthCheck: () => void;
    /** Set backend ready state */
    setBackendReady: (ready: boolean) => void;
}

export const useDesktopStore = create<DesktopState>((set, get) => ({
    isDesktop: false,
    backendReady: false,
    backendChecking: false,
    backendError: null,

    init: () => {
        const isDesktop = isTauri();
        set({ isDesktop });

        if (isDesktop) {
            // In desktop mode, start checking backend health
            get().startHealthCheck();
        } else {
            // In web mode, assume backend is managed externally
            set({ backendReady: true });
        }
    },

    startHealthCheck: () => {
        set({ backendChecking: true, backendError: null });

        let attempts = 0;
        const maxAttempts = 150; // 30 seconds at 200ms intervals

        const poll = async () => {
            attempts++;
            const ready = await checkBackendReady();

            if (ready) {
                set({ backendReady: true, backendChecking: false });
                return;
            }

            if (attempts >= maxAttempts) {
                set({
                    backendChecking: false,
                    backendError: 'Backend failed to start within 30 seconds',
                });
                return;
            }

            // Continue polling
            setTimeout(poll, 200);
        };

        poll();
    },

    setBackendReady: (ready: boolean) => set({ backendReady: ready }),
}));

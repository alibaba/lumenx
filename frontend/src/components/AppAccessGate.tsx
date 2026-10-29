"use client";

import { FormEvent, ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import {
    APP_ACCESS_SESSION_EXPIRED_EVENT,
    installAppAccessAxiosInterceptors,
    loginAppAccess,
    readAppAccessSession,
    readAppServiceIdentity,
    type AppAccessSession,
} from "@/lib/appAccess";
import {
    activateRuntimeEnvironment,
    clearRuntimeEnvironment,
    readRuntimeEnvironment,
    type RuntimeEnvironment,
} from "@/lib/runtimeEnvironment";
import { activateProjectEnvironment, deactivateProjectEnvironment } from "@/store/projectStore";

type GateState = "checking" | "ready" | "login" | "error";

type AppAccessGateProps = {
    children: ReactNode;
};

const isUnlocked = (session: AppAccessSession): boolean => !session.auth_required || session.authenticated;

export default function AppAccessGate({ children }: AppAccessGateProps) {
    const t = useTranslations("appAccess");
    const [state, setState] = useState<GateState>("checking");
    const [hasMountedChildren, setHasMountedChildren] = useState(false);
    const [token, setToken] = useState("");
    const [messageKey, setMessageKey] = useState<string | null>(null);
    const [loginPending, setLoginPending] = useState(false);
    const loginPendingRef = useRef(false);
    const mountedRef = useRef(false);
    const operationEpochRef = useRef(0);
    const sessionAbortRef = useRef<AbortController | null>(null);
    const loginAbortRef = useRef<AbortController | null>(null);
    const dialogRef = useRef<HTMLDialogElement | null>(null);
    const activeEnvironmentKeyRef = useRef<string | null>(null);
    const [childrenKey, setChildrenKey] = useState(0);

    const applySession = useCallback(async (session: AppAccessSession, environment: RuntimeEnvironment | null, isCurrent?: () => boolean) => {
        if (isUnlocked(session)) {
            if (!environment) {
                throw new Error("Unlocked session is missing runtime environment");
            }
            await activateProjectEnvironment(environment.key, environment.generation);
            if (isCurrent && !isCurrent()) return;
            if (activeEnvironmentKeyRef.current && activeEnvironmentKeyRef.current !== environment.key) {
                setChildrenKey(value => value + 1);
            }
            activeEnvironmentKeyRef.current = environment.key;
            setState("ready");
            setHasMountedChildren(true);
            setMessageKey(null);
            setToken("");
            return;
        }
        clearRuntimeEnvironment();
        deactivateProjectEnvironment();
        setState("login");
        setMessageKey(null);
    }, []);

    const checkSession = useCallback(async () => {
        sessionAbortRef.current?.abort();
        const controller = new AbortController();
        sessionAbortRef.current = controller;
        const epoch = ++operationEpochRef.current;
        setState("checking");
        setMessageKey(null);
        try {
            await readAppServiceIdentity({ signal: controller.signal });
            if (!mountedRef.current || operationEpochRef.current !== epoch || controller.signal.aborted) return;
            const session = await readAppAccessSession({ signal: controller.signal });
            if (!mountedRef.current || operationEpochRef.current !== epoch || controller.signal.aborted) return;
            const identity = isUnlocked(session) ? await readRuntimeEnvironment({ signal: controller.signal }) : null;
            if (!mountedRef.current || operationEpochRef.current !== epoch || controller.signal.aborted) return;
            const environment = identity ? activateRuntimeEnvironment(identity) : null;
            await applySession(session, environment, () => mountedRef.current && operationEpochRef.current === epoch && !controller.signal.aborted);
        } catch {
            if (!mountedRef.current || operationEpochRef.current !== epoch || controller.signal.aborted) return;
            clearRuntimeEnvironment();
            deactivateProjectEnvironment();
            setState("error");
            setMessageKey("sessionError");
        }
    }, [applySession]);

    useEffect(() => {
        mountedRef.current = true;
        const cleanupInterceptors = installAppAccessAxiosInterceptors();
        const handleExpired = (event: Event) => {
            const reason = event instanceof CustomEvent ? event.detail?.reason : "access_required";
            if (reason === "environment_changed") {
                operationEpochRef.current += 1;
                sessionAbortRef.current?.abort();
                clearRuntimeEnvironment();
                deactivateProjectEnvironment();
                setMessageKey(null);
                setState("checking");
                void checkSession();
                return;
            }
            setState(current => {
                if (current === "login") {
                    return current;
                }
                operationEpochRef.current += 1;
                sessionAbortRef.current?.abort();
                clearRuntimeEnvironment();
                deactivateProjectEnvironment();
                setMessageKey("expired");
                return "login";
            });
        };
        window.addEventListener(APP_ACCESS_SESSION_EXPIRED_EVENT, handleExpired);
        void checkSession();
        return () => {
            mountedRef.current = false;
            operationEpochRef.current += 1;
            sessionAbortRef.current?.abort();
            loginAbortRef.current?.abort();
            cleanupInterceptors();
            window.removeEventListener(APP_ACCESS_SESSION_EXPIRED_EVENT, handleExpired);
        };
    }, [checkSession]);


    useEffect(() => {
        const dialog = dialogRef.current;
        if (state === "ready" || !dialog) return;
        if (!dialog.open) {
            if (typeof dialog.showModal === "function") {
                dialog.showModal();
            } else {
                dialog.setAttribute("open", "");
            }
        }
        return () => {
            if (!dialog.open) return;
            if (typeof dialog.close === "function") {
                dialog.close();
            } else {
                dialog.removeAttribute("open");
            }
        };
    }, [state]);

    const handleLogin = async (event: FormEvent<HTMLFormElement>) => {
        event.preventDefault();
        if (loginPendingRef.current) return;
        loginPendingRef.current = true;
        setLoginPending(true);
        setMessageKey(null);
        sessionAbortRef.current?.abort();
        loginAbortRef.current?.abort();
        const controller = new AbortController();
        loginAbortRef.current = controller;
        const epoch = ++operationEpochRef.current;
        try {
            await readAppServiceIdentity({ signal: controller.signal });
            if (!mountedRef.current || operationEpochRef.current !== epoch || controller.signal.aborted) return;
            const session = await loginAppAccess(token, { signal: controller.signal });
            if (!mountedRef.current || operationEpochRef.current !== epoch || controller.signal.aborted) return;
            if (!isUnlocked(session)) {
                throw new Error("Session is still locked");
            }
            const identity = await readRuntimeEnvironment({ signal: controller.signal });
            if (!mountedRef.current || operationEpochRef.current !== epoch || controller.signal.aborted) return;
            const environment = activateRuntimeEnvironment(identity);
            await applySession(session, environment, () => mountedRef.current && operationEpochRef.current === epoch && !controller.signal.aborted);
        } catch {
            if (!mountedRef.current || operationEpochRef.current !== epoch || controller.signal.aborted) return;
            clearRuntimeEnvironment();
            deactivateProjectEnvironment();
            setState("login");
            setMessageKey("loginFailed");
        } finally {
            if (loginAbortRef.current === controller) {
                loginAbortRef.current = null;
            }
            if (mountedRef.current && (operationEpochRef.current === epoch || controller.signal.aborted)) {
                setLoginPending(false);
                loginPendingRef.current = false;
            }
        }
    };

    const childContent = hasMountedChildren ? (
        <div hidden={state !== "ready"} aria-hidden={state !== "ready"} className={state === "ready" ? "contents" : undefined}>
            <div key={childrenKey} className="contents">{children}</div>
        </div>
    ) : null;

    if (state === "ready") {
        return <>{childContent}</>;
    }

    const showLogin = state === "login";
    const showRetry = state === "error";

    return (
        <>
            {childContent}
            <dialog
                ref={dialogRef}
                role="dialog"
                aria-modal="true"
                aria-labelledby="app-access-title"
                onCancel={event => event.preventDefault()}
                className="m-0 h-dvh max-h-none w-screen max-w-none border-0 bg-background p-0 text-foreground backdrop:bg-background"
            >
                <div className="min-h-screen flex items-center justify-center px-4 py-12">
                    <section className="w-full max-w-md rounded-3xl border border-white/10 bg-white/[0.06] p-6 shadow-2xl backdrop-blur-xl">
                    <div className="space-y-2">
                        <p className="text-xs uppercase tracking-[0.35em] text-primary/80">LumenX</p>
                        <h1 id="app-access-title" className="text-2xl font-semibold text-white">
                            {showLogin ? t("unlockTitle") : t("checkingTitle")}
                        </h1>
                        <p className="text-sm leading-6 text-white/65">
                            {showLogin ? t("unlockBody") : t("checkingBody")}
                        </p>
                    </div>

                    {messageKey ? (
                        <div role="alert" className="mt-5 rounded-2xl border border-amber-400/30 bg-amber-400/10 px-4 py-3 text-sm text-amber-100">
                            {t(messageKey)}
                        </div>
                    ) : null}

                    {showLogin ? (
                        <form className="mt-6 space-y-4" onSubmit={handleLogin}>
                            <label className="block text-sm font-medium text-white/80" htmlFor="app-access-token">
                                {t("tokenLabel")}
                            </label>
                            <input
                                id="app-access-token"
                                type="password"
                                value={token}
                                onChange={event => setToken(event.target.value)}
                                placeholder={t("tokenPlaceholder")}
                                autoComplete="off"
                                className="w-full rounded-2xl border border-white/10 bg-black/30 px-4 py-3 text-white outline-none transition focus:border-primary/70"
                            />
                            <button
                                type="submit"
                                disabled={loginPending}
                                className="w-full rounded-2xl bg-primary px-4 py-3 text-sm font-semibold text-white transition hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-60"
                            >
                                {loginPending ? t("unlocking") : t("unlock")}
                            </button>
                        </form>
                    ) : null}

                    {state === "checking" ? (
                        <div className="mt-6 text-sm text-white/55">{t("checkingStatus")}</div>
                    ) : null}

                    {showRetry ? (
                        <button
                            type="button"
                            onClick={() => void checkSession()}
                            className="mt-6 w-full rounded-2xl border border-white/15 px-4 py-3 text-sm font-semibold text-white transition hover:bg-white/10"
                        >
                            {t("retry")}
                        </button>
                    ) : null}
                    </section>
                </div>
            </dialog>
        </>
    );
}

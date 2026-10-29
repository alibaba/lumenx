import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { describe, expect, it, vi, afterEach } from "vitest";
import AppAccessGate from "@/components/AppAccessGate";
import {
    APP_ACCESS_SESSION_EXPIRED_EVENT,
    appAccessFetch,
} from "@/lib/appAccess";
import { API_URL } from "@/lib/api";
import enMessages from "../../messages/en.json";
import { render } from "@testing-library/react";
import { createPortal } from "react-dom";
import { StrictMode } from "react";

function jsonResponse(body: unknown, init: ResponseInit = {}) {
    return new Response(JSON.stringify(body), {
        status: 200,
        headers: { "Content-Type": "application/json" },
        ...init,
    });
}

function healthResponse() {
    return jsonResponse({ ok: true, service: "lumenx-core", protocol_version: 1 });
}

function environmentResponse(storageId = "11111111-1111-4111-8111-111111111111") {
    return jsonResponse({ schema_version: 1, storage_id: storageId });
}

function renderGate(children = <div data-testid="product">Product</div>) {
    return render(
        <NextIntlClientProvider locale="en" messages={enMessages} timeZone="Asia/Shanghai">
            <AppAccessGate>{children}</AppAccessGate>
        </NextIntlClientProvider>
    );
}

afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
});

describe("AppAccessGate", () => {

    it("validates service identity before reading session and mounting product", async () => {
        const fetchMock = vi.fn()
            .mockResolvedValueOnce(healthResponse())
            .mockResolvedValueOnce(jsonResponse({ auth_required: false, authenticated: false }))
            .mockResolvedValueOnce(environmentResponse());
        vi.stubGlobal("fetch", fetchMock);

        renderGate();
        await screen.findByTestId("product");

        expect(fetchMock).toHaveBeenNthCalledWith(1, expect.stringMatching(/\/health$/), expect.anything());
        expect(fetchMock).toHaveBeenNthCalledWith(2, expect.stringMatching(/\/auth\/session$/), expect.anything());
        expect(fetchMock).toHaveBeenNthCalledWith(3, expect.stringMatching(/\/runtime\/environment$/), expect.anything());
    });

    it("fails closed on wrong service identity before session read", async () => {
        const fetchMock = vi.fn()
            .mockResolvedValueOnce(jsonResponse({ ok: true, service: "not-lumenx", protocol_version: 1 }))
            .mockResolvedValueOnce(jsonResponse({ auth_required: false, authenticated: false }));
        vi.stubGlobal("fetch", fetchMock);

        renderGate();
        await screen.findByRole("alert");

        expect(screen.queryByTestId("product")).not.toBeInTheDocument();
        expect(fetchMock).toHaveBeenCalledTimes(1);
        expect(fetchMock).toHaveBeenCalledWith(expect.stringMatching(/\/health$/), expect.anything());
    });

    it("retry revalidates service identity before mounting", async () => {
        const fetchMock = vi.fn()
            .mockResolvedValueOnce(jsonResponse({ ok: true, service: "not-lumenx", protocol_version: 1 }))
            .mockResolvedValueOnce(healthResponse())
            .mockResolvedValueOnce(jsonResponse({ auth_required: false, authenticated: false }))
            .mockResolvedValueOnce(environmentResponse());
        vi.stubGlobal("fetch", fetchMock);

        renderGate();
        await screen.findByRole("alert");
        fireEvent.click(screen.getByRole("button", { name: /retry/i }));
        await screen.findByTestId("product");

        expect(fetchMock.mock.calls.map(call => String(call[0]).replace(/^.*(?=\/(?:health|auth|runtime))/, ""))).toEqual([
            "/health",
            "/health",
            "/auth/session",
            "/runtime/environment",
        ]);
    });


    it("times out a hung service identity check without mounting product", async () => {
        vi.useFakeTimers();
        const fetchMock = vi.fn((_url: string, init?: RequestInit) => new Promise<Response>((_resolve, reject) => {
            init?.signal?.addEventListener("abort", () => reject(new Error("health timed out")), { once: true });
        }));
        vi.stubGlobal("fetch", fetchMock);

        renderGate();
        expect(screen.queryByTestId("product")).not.toBeInTheDocument();

        await act(async () => {
            await vi.advanceTimersByTimeAsync(5_000);
        });
        vi.useRealTimers();

        await screen.findByRole("alert");
        expect(screen.getByRole("alert")).toHaveTextContent(/could not check app access/i);
        expect(screen.queryByTestId("product")).not.toBeInTheDocument();
        expect(fetchMock).toHaveBeenCalledTimes(1);
    });

    it("mounts product for local no-auth session", async () => {
        vi.stubGlobal("fetch", vi.fn()
            .mockResolvedValueOnce(healthResponse())
            .mockResolvedValueOnce(jsonResponse({ auth_required: false, authenticated: false }))
            .mockResolvedValueOnce(environmentResponse()));
        renderGate();
        expect(screen.queryByTestId("product")).not.toBeInTheDocument();
        await screen.findByTestId("product");
    });

    it("gates anonymous auth-required sessions without mounting product", async () => {
        vi.stubGlobal("fetch", vi.fn()
            .mockResolvedValueOnce(healthResponse())
            .mockResolvedValueOnce(jsonResponse({ auth_required: true, authenticated: false })));
        renderGate();
        await screen.findByRole("heading", { name: /unlock/i });
        expect(screen.queryByTestId("product")).not.toBeInTheDocument();
        expect(screen.getByLabelText(/access token/i)).toHaveAttribute("type", "password");
    });

    it("locks duplicate login submits and shows generic failure", async () => {
        let resolveLogin!: (value: Response) => void;
        const fetchMock = vi.fn()
            .mockResolvedValueOnce(healthResponse())
            .mockResolvedValueOnce(jsonResponse({ auth_required: true, authenticated: false }))
            .mockResolvedValueOnce(healthResponse())
            .mockImplementationOnce(() => new Promise<Response>(resolve => { resolveLogin = resolve; }));
        vi.stubGlobal("fetch", fetchMock);
        renderGate();
        await screen.findByLabelText(/access token/i);
        fireEvent.change(screen.getByLabelText(/access token/i), { target: { value: "bad-token" } });
        fireEvent.click(screen.getByRole("button", { name: /unlock/i }));
        fireEvent.click(screen.getByRole("button", { name: /unlocking/i }));
        await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(4));
        await act(async () => resolveLogin(jsonResponse({ code: "access_required", detail: "bad-token" }, { status: 401 })));
        await screen.findByRole("alert");
        expect(screen.getByRole("alert")).toHaveTextContent(/could not unlock/i);
        expect(screen.getByRole("alert")).not.toHaveTextContent("bad-token");
    });

    it("shows retry on session read failure and retry revalidates service identity", async () => {
        const fetchMock = vi.fn()
            .mockResolvedValueOnce(healthResponse())
            .mockRejectedValueOnce(new Error("offline"))
            .mockResolvedValueOnce(healthResponse())
            .mockResolvedValueOnce(jsonResponse({ auth_required: false, authenticated: false }))
            .mockResolvedValueOnce(environmentResponse());
        vi.stubGlobal("fetch", fetchMock);
        renderGate();
        await screen.findByRole("alert");
        fireEvent.click(screen.getByRole("button", { name: /retry/i }));
        await screen.findByTestId("product");
        expect(fetchMock).toHaveBeenCalledTimes(5);
        expect(fetchMock.mock.calls.map(call => String(call[0]).replace(/^.*(?=\/(?:health|auth|runtime))/, ""))).toEqual([
            "/health",
            "/auth/session",
            "/health",
            "/auth/session",
            "/runtime/environment",
        ]);
    });

    it("treats malformed session responses as app access read failures", async () => {
        vi.stubGlobal("fetch", vi.fn()
            .mockResolvedValueOnce(healthResponse())
            .mockResolvedValueOnce(jsonResponse({ auth_required: "yes", authenticated: false })));
        renderGate();
        await screen.findByRole("alert");
        expect(screen.getByRole("alert")).toHaveTextContent(/could not check app access/i);
        expect(screen.queryByTestId("product")).not.toBeInTheDocument();
    });

    it("keeps mounted draft hidden on session expiration and restores after login without replay", async () => {
        const paidAction = vi.fn();
        const fetchMock = vi.fn()
            .mockResolvedValueOnce(healthResponse())
            .mockResolvedValueOnce(jsonResponse({ auth_required: true, authenticated: true }))
            .mockResolvedValueOnce(environmentResponse())
            .mockResolvedValueOnce(healthResponse())
            .mockResolvedValueOnce(jsonResponse({ auth_required: true, authenticated: true }))
            .mockResolvedValueOnce(jsonResponse({ auth_required: true, authenticated: true }))
            .mockResolvedValueOnce(environmentResponse());
        vi.stubGlobal("fetch", fetchMock);
        renderGate(<button data-testid="paid" onClick={paidAction}>Paid command</button>);
        await screen.findByTestId("paid");
        fireEvent.click(screen.getByTestId("paid"));
        expect(paidAction).toHaveBeenCalledTimes(1);

        act(() => window.dispatchEvent(new Event(APP_ACCESS_SESSION_EXPIRED_EVENT)));
        await screen.findByRole("heading", { name: /unlock/i });
        expect(screen.getByTestId("paid")).toBeInTheDocument();
        expect(screen.getByTestId("paid").closest("div[hidden]")).not.toBeNull();

        fireEvent.change(screen.getByLabelText(/access token/i), { target: { value: "fresh" } });
        fireEvent.click(screen.getByRole("button", { name: /unlock/i }));
        await waitFor(() => expect(screen.getByTestId("paid").closest("div[hidden]")).toBeNull());
        expect(paidAction).toHaveBeenCalledTimes(1);
    });




    it("revalidates local access on environment mismatch without showing token login or replaying the mutation", async () => {
        function RootSwitchProbe() {
            return (
                <div>
                    <label>Draft <input aria-label="draft" defaultValue="" /></label>
                    <button
                        type="button"
                        onClick={() => {
                            void appAccessFetch(`${API_URL}/projects/p`, { method: "PUT" }).catch(() => undefined);
                        }}
                    >
                        Save project
                    </button>
                </div>
            );
        }
        const fetchMock = vi.fn()
            .mockResolvedValueOnce(healthResponse())
            .mockResolvedValueOnce(jsonResponse({ auth_required: false, authenticated: false }))
            .mockResolvedValueOnce(environmentResponse("11111111-1111-4111-8111-111111111111"))
            .mockResolvedValueOnce(jsonResponse({ code: "environment_changed" }, { status: 409 }))
            .mockResolvedValueOnce(healthResponse())
            .mockResolvedValueOnce(jsonResponse({ auth_required: false, authenticated: false }))
            .mockResolvedValueOnce(environmentResponse("22222222-2222-4222-8222-222222222222"));
        vi.stubGlobal("fetch", fetchMock);

        renderGate(<RootSwitchProbe />);
        await screen.findByRole("button", { name: /save project/i });
        fireEvent.change(screen.getByLabelText("draft"), { target: { value: "old unsaved draft" } });
        fireEvent.click(screen.getByRole("button", { name: /save project/i }));

        await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(7));
        await waitFor(() => expect(screen.getByLabelText("draft")).toHaveValue(""));
        expect(screen.queryByRole("heading", { name: /unlock/i })).not.toBeInTheDocument();
        expect(fetchMock.mock.calls.map(call => String(call[0]).replace(/^.*(?=\/(?:health|auth|runtime|projects))/, ""))).toEqual([
            "/health",
            "/auth/session",
            "/runtime/environment",
            "/projects/p",
            "/health",
            "/auth/session",
            "/runtime/environment",
        ]);
    });


    it("deactivates runtime caches when revalidation reads a malformed environment", async () => {
        function HiddenWriter() {
            return (
                <button
                    data-testid="writer"
                    type="button"
                    onClick={() => {
                        void appAccessFetch(`${API_URL}/projects/p`, { method: "PUT" }).catch(() => undefined);
                    }}
                >
                    Writer
                </button>
            );
        }
        const fetchMock = vi.fn()
            .mockResolvedValueOnce(healthResponse())
            .mockResolvedValueOnce(jsonResponse({ auth_required: false, authenticated: false }))
            .mockResolvedValueOnce(environmentResponse("11111111-1111-4111-8111-111111111111"))
            .mockResolvedValueOnce(healthResponse())
            .mockResolvedValueOnce(jsonResponse({ auth_required: false, authenticated: false }))
            .mockResolvedValueOnce(jsonResponse({ schema_version: 1, storage_id: "not-a-uuid" }));
        vi.stubGlobal("fetch", fetchMock);

        renderGate(<HiddenWriter />);
        await screen.findByTestId("writer");
        act(() => window.dispatchEvent(new CustomEvent(APP_ACCESS_SESSION_EXPIRED_EVENT, { detail: { reason: "environment_changed" } })));
        await screen.findByRole("alert");
        expect(fetchMock).toHaveBeenCalledTimes(6);

        fireEvent.click(screen.getByTestId("writer"));
        await new Promise(resolve => setTimeout(resolve, 0));
        expect(fetchMock).toHaveBeenCalledTimes(6);
    });

    it("uses a modal dialog barrier so body portals cannot cover the expired-login screen", async () => {
        const showModal = vi.fn(function (this: HTMLDialogElement) {
            this.setAttribute("open", "");
        });
        const close = vi.fn(function (this: HTMLDialogElement) {
            this.removeAttribute("open");
        });
        const originalShowModal = HTMLDialogElement.prototype.showModal;
        const originalClose = HTMLDialogElement.prototype.close;
        HTMLDialogElement.prototype.showModal = showModal;
        HTMLDialogElement.prototype.close = close;

        function PortalModal() {
            return createPortal(<button data-testid="portal-action">Portal action</button>, document.body);
        }

        vi.stubGlobal("fetch", vi.fn()
            .mockResolvedValueOnce(healthResponse())
            .mockResolvedValueOnce(jsonResponse({ auth_required: true, authenticated: true }))
            .mockResolvedValueOnce(environmentResponse()));
        try {
            renderGate(<PortalModal />);
            await screen.findByTestId("portal-action");
            act(() => window.dispatchEvent(new Event(APP_ACCESS_SESSION_EXPIRED_EVENT)));
            const dialog = await screen.findByRole("dialog");
            expect(showModal).toHaveBeenCalled();
            expect(dialog).toHaveAttribute("open");
            const cancelEvent = new Event("cancel", { cancelable: true });
            dialog.dispatchEvent(cancelEvent);
            expect(cancelEvent.defaultPrevented).toBe(true);
            expect(screen.getByTestId("portal-action")).toBeInTheDocument();
        } finally {
            HTMLDialogElement.prototype.showModal = originalShowModal;
            HTMLDialogElement.prototype.close = originalClose;
        }
    });


    it("blocks login POST when service identity revalidation fails after expiration", async () => {
        function DraftInput() {
            return <label>Draft <input aria-label="draft" defaultValue="" /></label>;
        }
        const fetchMock = vi.fn()
            .mockResolvedValueOnce(healthResponse())
            .mockResolvedValueOnce(jsonResponse({ auth_required: true, authenticated: true }))
            .mockResolvedValueOnce(environmentResponse())
            .mockRejectedValueOnce(new TypeError("redirected health"));
        vi.stubGlobal("fetch", fetchMock);

        renderGate(<DraftInput />);
        await screen.findByLabelText("draft");
        fireEvent.change(screen.getByLabelText("draft"), { target: { value: "kept while locked" } });
        act(() => window.dispatchEvent(new Event(APP_ACCESS_SESSION_EXPIRED_EVENT)));
        await screen.findByRole("heading", { name: /unlock/i });
        fireEvent.change(screen.getByLabelText(/access token/i), { target: { value: "token" } });
        fireEvent.click(screen.getByRole("button", { name: /unlock/i }));

        await screen.findByRole("alert");
        expect(screen.getByRole("alert")).toHaveTextContent(/could not unlock/i);
        expect(fetchMock).toHaveBeenCalledTimes(4);
        expect(fetchMock.mock.calls.map(call => String(call[0]).replace(/^.*(?=\/(?:health|auth|runtime))/, ""))).toEqual([
            "/health",
            "/auth/session",
            "/runtime/environment",
            "/health",
        ]);
        expect(screen.getByLabelText("draft")).toHaveValue("kept while locked");
        expect(screen.getByLabelText("draft").closest("div[hidden]")).not.toBeNull();
    });

    it("preserves stateful drafts across expiration and relogin", async () => {
        function DraftInput() {
            return <label>Draft <input aria-label="draft" defaultValue="" /></label>;
        }
        vi.stubGlobal("fetch", vi.fn()
            .mockResolvedValueOnce(healthResponse())
            .mockResolvedValueOnce(jsonResponse({ auth_required: true, authenticated: true }))
            .mockResolvedValueOnce(environmentResponse())
            .mockResolvedValueOnce(healthResponse())
            .mockResolvedValueOnce(jsonResponse({ auth_required: true, authenticated: true }))
            .mockResolvedValueOnce(jsonResponse({ auth_required: true, authenticated: true }))
            .mockResolvedValueOnce(environmentResponse()));
        renderGate(<DraftInput />);
        await screen.findByLabelText("draft");
        fireEvent.change(screen.getByLabelText("draft"), { target: { value: "unsaved line" } });
        act(() => window.dispatchEvent(new Event(APP_ACCESS_SESSION_EXPIRED_EVENT)));
        await screen.findByRole("heading", { name: /unlock/i });
        expect(screen.getByLabelText("draft")).toHaveValue("unsaved line");

        fireEvent.change(screen.getByLabelText(/access token/i), { target: { value: "fresh" } });
        fireEvent.click(screen.getByRole("button", { name: /unlock/i }));
        await waitFor(() => expect(screen.getByLabelText("draft").closest("div[hidden]")).toBeNull());
        expect(screen.getByLabelText("draft")).toHaveValue("unsaved line");
    });

    it("ignores the first StrictMode-style identity response when a later check wins", async () => {
        let resolveFirstHealth!: (value: Response) => void;
        let resolveSecondHealth!: (value: Response) => void;
        const fetchMock = vi.fn()
            .mockImplementationOnce(() => new Promise<Response>(resolve => { resolveFirstHealth = resolve; }))
            .mockImplementationOnce(() => new Promise<Response>(resolve => { resolveSecondHealth = resolve; }))
            .mockResolvedValueOnce(jsonResponse({ auth_required: true, authenticated: false }));
        vi.stubGlobal("fetch", fetchMock);
        render(
            <StrictMode>
                <NextIntlClientProvider locale="en" messages={enMessages} timeZone="Asia/Shanghai">
                    <AppAccessGate><div data-testid="product">Product</div></AppAccessGate>
                </NextIntlClientProvider>
            </StrictMode>
        );
        await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
        await act(async () => resolveSecondHealth(healthResponse()));
        await screen.findByRole("heading", { name: /unlock/i });
        await act(async () => resolveFirstHealth(healthResponse()));
        expect(screen.queryByTestId("product")).not.toBeInTheDocument();
    });

    it("requires a readback-confirmed cookie before mounting after login", async () => {
        const fetchMock = vi.fn()
            .mockResolvedValueOnce(healthResponse())
            .mockResolvedValueOnce(jsonResponse({ auth_required: true, authenticated: false }))
            .mockResolvedValueOnce(healthResponse())
            .mockResolvedValueOnce(jsonResponse({ auth_required: true, authenticated: true }))
            .mockResolvedValueOnce(jsonResponse({ auth_required: true, authenticated: false }));
        vi.stubGlobal("fetch", fetchMock);
        renderGate();
        await screen.findByLabelText(/access token/i);
        fireEvent.change(screen.getByLabelText(/access token/i), { target: { value: "token" } });
        fireEvent.click(screen.getByRole("button", { name: /unlock/i }));
        await screen.findByRole("alert");
        expect(screen.getByRole("alert")).toHaveTextContent(/could not unlock/i);
        expect(screen.queryByTestId("product")).not.toBeInTheDocument();
        expect(fetchMock).toHaveBeenCalledTimes(5);
    });

    it("ignores stale identity checks invalidated by expiration", async () => {
        let resolveHealth!: (value: Response) => void;
        vi.stubGlobal("fetch", vi.fn().mockImplementation(() => new Promise<Response>(resolve => { resolveHealth = resolve; })));
        renderGate();
        act(() => window.dispatchEvent(new Event(APP_ACCESS_SESSION_EXPIRED_EVENT)));
        await screen.findByRole("heading", { name: /unlock/i });
        await act(async () => resolveHealth(healthResponse()));
        expect(screen.queryByTestId("product")).not.toBeInTheDocument();
        expect(screen.getByRole("heading", { name: /unlock/i })).toBeInTheDocument();
    });

    it("coalesces repeated expiration events while login is pending", async () => {
        let resolveLoginHealth!: (value: Response) => void;
        let resolvePost!: (value: Response) => void;
        let resolveReadback!: (value: Response) => void;
        const paidAction = vi.fn();
        const fetchMock = vi.fn()
            .mockResolvedValueOnce(healthResponse())
            .mockResolvedValueOnce(jsonResponse({ auth_required: true, authenticated: true }))
            .mockResolvedValueOnce(environmentResponse())
            .mockImplementationOnce(() => new Promise<Response>(resolve => { resolveLoginHealth = resolve; }))
            .mockImplementationOnce(() => new Promise<Response>(resolve => { resolvePost = resolve; }))
            .mockImplementationOnce(() => new Promise<Response>(resolve => { resolveReadback = resolve; }))
            .mockResolvedValueOnce(environmentResponse());
        vi.stubGlobal("fetch", fetchMock);
        renderGate(<button data-testid="paid" onClick={paidAction}>Paid command</button>);
        await screen.findByTestId("paid");
        fireEvent.click(screen.getByTestId("paid"));
        expect(paidAction).toHaveBeenCalledTimes(1);

        act(() => window.dispatchEvent(new Event(APP_ACCESS_SESSION_EXPIRED_EVENT)));
        await screen.findByRole("heading", { name: /unlock/i });
        fireEvent.change(screen.getByLabelText(/access token/i), { target: { value: "token" } });
        fireEvent.click(screen.getByRole("button", { name: /unlock/i }));
        act(() => {
            window.dispatchEvent(new Event(APP_ACCESS_SESSION_EXPIRED_EVENT));
            window.dispatchEvent(new Event(APP_ACCESS_SESSION_EXPIRED_EVENT));
        });
        fireEvent.click(screen.getByRole("button", { name: /unlocking/i }));
        expect(fetchMock).toHaveBeenCalledTimes(4);

        await act(async () => resolveLoginHealth(healthResponse()));
        await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(5));
        await act(async () => resolvePost(jsonResponse({ auth_required: true, authenticated: true })));
        await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(6));
        await act(async () => resolveReadback(jsonResponse({ auth_required: true, authenticated: true })));
        await waitFor(() => expect(screen.getByTestId("paid").closest("div[hidden]")).toBeNull());
        expect(paidAction).toHaveBeenCalledTimes(1);
    });

    it("ignores late service identity result after unmount", async () => {
        let resolveHealth!: (value: Response) => void;
        vi.stubGlobal("fetch", vi.fn().mockImplementation(() => new Promise<Response>(resolve => { resolveHealth = resolve; })));
        const errors = vi.spyOn(console, "error").mockImplementation(() => undefined);
        const view = renderGate();
        view.unmount();
        await act(async () => resolveHealth(healthResponse()));
        expect(errors).not.toHaveBeenCalled();
        errors.mockRestore();
    });
});

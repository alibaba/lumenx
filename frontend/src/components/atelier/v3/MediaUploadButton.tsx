"use client";

import { useEffect, useRef, useState } from "react";
import { Upload } from "lucide-react";

interface Props {
    ownerKey: string;
    label: string;
    busyLabel: string;
    errorLabel?: string;
    accept: string;
    disabled?: boolean;
    onUpload: (file: File) => Promise<unknown>;
}

export function MediaUploadButton({ ownerKey, label, busyLabel, errorLabel = "Upload failed", accept, disabled, onUpload }: Props) {
    const input = useRef<HTMLInputElement>(null);
    const pickerOwner = useRef<string | null>(null);
    const active = useRef<{ owner: string; token: number } | null>(null);
    const serial = useRef(0);
    const currentOwner = useRef(ownerKey);
    const mounted = useRef(true);
    const [state, setState] = useState({ owner: ownerKey, busy: false, error: "" });
    if (currentOwner.current !== ownerKey) {
        currentOwner.current = ownerKey;
        pickerOwner.current = null;
    }
    useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
    const busy = state.owner === ownerKey && state.busy;
    const error = state.owner === ownerKey ? state.error : "";

    const upload = async (file: File) => {
        const operation = { owner: ownerKey, token: ++serial.current };
        active.current = operation;
        setState({ owner: ownerKey, busy: true, error: "" });
        try {
            await onUpload(file);
            if (mounted.current && currentOwner.current === ownerKey && active.current === operation) setState({ owner: ownerKey, busy: false, error: "" });
        } catch (cause) {
            if (mounted.current && currentOwner.current === ownerKey && active.current === operation) {
                setState({ owner: ownerKey, busy: false, error: cause instanceof Error ? cause.message : errorLabel });
            }
        } finally {
            if (active.current === operation) active.current = null;
        }
    };

    return <div>
        <input ref={input} type="file" className="hidden" aria-label={label} accept={accept} disabled={disabled || busy}
            onChange={event => {
                const file = event.target.files?.[0];
                event.target.value = "";
                const owner = pickerOwner.current;
                pickerOwner.current = null;
                if (!file || disabled || !mounted.current || owner !== ownerKey || currentOwner.current !== ownerKey
                    || active.current?.owner === ownerKey) return;
                void upload(file);
            }} />
        <button type="button" disabled={disabled || busy}
            className="inline-flex items-center justify-center gap-1.5 rounded-xl border border-white/10 bg-white/[0.06] px-3 py-2 text-sm text-white/75 hover:bg-white/10 disabled:cursor-not-allowed disabled:opacity-40"
            onClick={() => { if (!disabled && !busy) { pickerOwner.current = ownerKey; input.current?.click(); } }}>
            <Upload size={14} aria-hidden="true" />{busy ? busyLabel : label}
        </button>
        {error && <p role="alert" className="mt-2 max-w-xs text-xs text-amber-200">{error}</p>}
    </div>;
}

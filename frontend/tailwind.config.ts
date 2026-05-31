import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: 'class',
  content: [
    "./src/pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        background: "var(--color-bg-base)",
        foreground: "var(--color-text-primary)",
        surface: "var(--color-bg-surface)",
        elevated: "var(--color-bg-elevated)",
        "input-bg": "var(--color-bg-input)",
        "hover-bg": "var(--color-bg-hover)",
        glass: "var(--color-glass)",
        "glass-border": "var(--color-border-default)",
        "border-subtle": "var(--color-border-subtle)",
        "text-secondary": "var(--color-text-secondary)",
        "text-muted": "var(--color-text-muted)",
        overlay: "var(--color-overlay)",
        "surface-inset": "var(--color-bg-inset)",
        primary: "#646cff",
        secondary: "#535bf2",
        accent: "#ff0080",

        // ── v0.4.5 atelier design system (DESIGN-v0.4.md §13) ──
        // Brand cobalt (RESERVED for primary CTAs only).
        "atelier-brand-400": "#3b6bff",
        "atelier-brand-300": "#6e8fff",
        "atelier-brand-500": "#2548d8",

        // Muted artistic palette — chrome / category / decorative.
        // Always prefer these over brand-cobalt outside CTAs.
        "atelier-brand-soft": "#8a9cc4",
        "atelier-sky-soft":   "#b0bdc8",
        "atelier-sage":       "#95b89e",
        "atelier-ochre":      "#c9a87e",
        "atelier-mauve":      "#b59abe",
        "atelier-teal-soft":  "#88aaa6",
        "atelier-coral-soft": "#c98a7e",
        "atelier-slate-warm": "#98a3b0",

        // Atmospheric tier (bloom recipe + soft fills).
        "atelier-sky-300":   "#9cc4e8",
        "atelier-sky-100":   "#dde9f4",
        "atelier-peach-200": "#e8b89c",

        // Status — saturated, hard signaling only.
        "atelier-completed":  "#34d399",
        "atelier-processing": "#60a5fa",
        "atelier-failed":     "#f87171",

        // ── v0.5 Flova-grade target (see docs/design/atelier-flova-target-spec.md) ──
        // Node I/O port color code (multi-color allowed ON PORTS only).
        "atelier-port-model":    "#e0b94e", // amber — model input
        "atelier-port-positive": "#3ddc84", // green — positive input + Generate CTA
        "atelier-port-negative": "#f0616d", // red — negative input
        "atelier-port-output":   "#5b9dff", // blue — output
        // Frosted node + black vitrine canvas.
        "atelier-canvas-flova":  "#08080a",
      },
      fontFamily: {
        sans: ["var(--font-inter)", "sans-serif"],
        mono: ["var(--font-jetbrains-mono)", "monospace"],
        display: ["var(--font-space-grotesk)", "sans-serif"],
      },
      backgroundImage: {
        "gradient-radial": "radial-gradient(var(--tw-gradient-stops))",
        "gradient-conic":
          "conic-gradient(from 180deg at 50% 50%, var(--tw-gradient-stops))",
      },
      keyframes: {
        // Subtle entry for canvas nodes — soft fade with a tiny pop. Anchors
        // to the leaf's own coordinate system so it doesn't fight the
        // absolute translate(x, y) the node already applies.
        "atelier-node-in": {
          "0%": { opacity: "0", filter: "blur(2px)" },
          "100%": { opacity: "1", filter: "blur(0)" },
        },
        // Workbench expand — the compact draft card returns null and a 520px
        // bloomed workbench mounts at the same top-left anchor. Without a grow,
        // it pops in at full size ("嘎一下变成大框"). Uses the standalone `scale`
        // property (NOT transform) so it composes with the inline
        // transform: translate(x,y) the node already carries instead of
        // clobbering its position. origin-top-left (set on the element) pivots the
        // grow from the corner the compact card occupied → reads as a morph.
        "atelier-workbench-in": {
          "0%":   { opacity: "0", scale: "0.85", filter: "blur(3px)" },
          "100%": { opacity: "1", scale: "1", filter: "blur(0)" },
        },
        // Staggered operating-area body: the shell frame grows first (workbench-in),
        // then the dense inner card (meta + Composer + take strip) settles ~140ms
        // later — reads as "a drawer opens, then contents drop in" instead of the
        // whole thing exploding at full size at once. translateY on the child
        // composes fine with the parent's standalone `scale`. The 140ms delay +
        // `both` fill keeps it invisible during the frame's first ~40% (no flash).
        "atelier-workbench-content-in": {
          "0%":   { opacity: "0", transform: "translateY(4px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        // Marching-ants: scrolls the dash-array on processing/pending edges
        // so connections look "alive" while a generation is in flight.
        "atelier-dash": {
          from: { strokeDashoffset: "20" },
          to:   { strokeDashoffset: "0" },
        },
        // Popover entrance — chip dropdowns, context menus, project picker.
        // Translate-up + fade with cubic-bezier 'expo out' for a settled
        // landing instead of a bounce.
        "atelier-popover-in": {
          "0%":   { opacity: "0", transform: "translateY(-4px) scale(0.97)" },
          "100%": { opacity: "1", transform: "translateY(0) scale(1)" },
        },
        // Modal entrance — overlay fade + content scale-up. Used by Help
        // overlay, useAsRef picker, Preview modal.
        "atelier-modal-overlay-in": {
          "0%":   { opacity: "0" },
          "100%": { opacity: "1" },
        },
        "atelier-modal-content-in": {
          "0%":   { opacity: "0", transform: "translateY(8px) scale(0.96)" },
          "100%": { opacity: "1", transform: "translateY(0) scale(1)" },
        },
        // Toast entrance — slide from above. Quick (180ms) so the user sees
        // the message land instead of watching it choreograph.
        "atelier-toast-in": {
          "0%":   { opacity: "0", transform: "translateY(-12px) scale(0.96)" },
          "100%": { opacity: "1", transform: "translateY(0) scale(1)" },
        },
        // Composer entrance — anchored slide-up + fade. Reads as 'editor
        // attached itself to the selected node'.
        "atelier-composer-in": {
          "0%":   { opacity: "0", transform: "translateY(-6px) scale(0.985)" },
          "100%": { opacity: "1", transform: "translateY(0) scale(1)" },
        },
        // Soft pulse — primary action cue (e.g. submit button when prompt
        // is non-empty + no mismatch). Halo breathes 2px at 1.5s; not a
        // bounce, no scale change on the button itself.
        "atelier-pulse-soft": {
          "0%, 100%": { boxShadow: "0 0 0 0 rgba(100,108,255,0.0)" },
          "50%":      { boxShadow: "0 0 0 6px rgba(100,108,255,0.12)" },
        },
        // Shimmer — used on skeleton loaders and saved-just-now flash on
        // chips. Background-position sweep across the gradient.
        "atelier-shimmer": {
          "0%":   { backgroundPosition: "-200% 0" },
          "100%": { backgroundPosition: "200% 0" },
        },
      },
      animation: {
        "atelier-node-in": "atelier-node-in 220ms cubic-bezier(0.22, 1, 0.36, 1) both",
        "atelier-workbench-in": "atelier-workbench-in 360ms cubic-bezier(0.22, 1, 0.36, 1) both",
        "atelier-workbench-content-in": "atelier-workbench-content-in 240ms cubic-bezier(0.22, 1, 0.36, 1) 140ms both",
        "atelier-dash": "atelier-dash 1.6s linear infinite",
        "atelier-popover-in": "atelier-popover-in 160ms cubic-bezier(0.22, 1, 0.36, 1) both",
        "atelier-modal-overlay-in": "atelier-modal-overlay-in 200ms ease-out both",
        "atelier-modal-content-in": "atelier-modal-content-in 240ms cubic-bezier(0.22, 1, 0.36, 1) both",
        "atelier-toast-in": "atelier-toast-in 180ms cubic-bezier(0.22, 1, 0.36, 1) both",
        "atelier-composer-in": "atelier-composer-in 200ms cubic-bezier(0.22, 1, 0.36, 1) both",
        "atelier-pulse-soft": "atelier-pulse-soft 1.6s ease-in-out infinite",
        "atelier-shimmer": "atelier-shimmer 2.4s linear infinite",
      },
    },
  },
  plugins: [],
};
export default config;

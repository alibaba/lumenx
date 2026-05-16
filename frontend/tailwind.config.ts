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
        // Marching-ants: scrolls the dash-array on processing/pending edges
        // so connections look "alive" while a generation is in flight.
        "atelier-dash": {
          from: { strokeDashoffset: "20" },
          to:   { strokeDashoffset: "0" },
        },
      },
      animation: {
        "atelier-node-in": "atelier-node-in 220ms ease-out both",
        "atelier-dash": "atelier-dash 1.2s linear infinite",
      },
    },
  },
  plugins: [],
};
export default config;

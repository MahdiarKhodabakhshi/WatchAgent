import type { Config } from "tailwindcss";

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "var(--bg)",
        surface: "var(--surface)",
        "surface-2": "var(--surface-2)",
        border: "var(--border)",
        text: "var(--text)",
        "text-muted": "var(--text-muted)",
        "text-faint": "var(--text-faint)",
        "sev-info": "var(--sev-info)",
        "sev-warning": "var(--sev-warning)",
        "sev-severe": "var(--sev-severe)",
        "line-temp": "var(--line-temp)",
        "line-apparent": "var(--line-apparent)",
        "line-wind": "var(--line-wind)",
        "line-precip": "var(--line-precip)",
        "line-forecast": "var(--line-forecast)",
      },
      fontFamily: {
        sans: ["Inter", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      letterSpacing: {
        label: "0.08em",
      },
      borderRadius: {
        panel: "8px",
      },
    },
  },
  plugins: [],
} satisfies Config;

import typography from "@tailwindcss/typography";

/** @type {import('tailwindcss').Config} */
export default {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["Figtree", "Segoe UI", "system-ui", "sans-serif"],
        display: ["Poppins", "Figtree", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "Consolas", "ui-monospace", "monospace"],
      },
      colors: {
        // Semantic tokens: values live in src/styles/index.css and switch with the theme.
        ground: "var(--ground)",
        surface: { DEFAULT: "var(--surface)", 2: "var(--surface-2)" },
        line: { DEFAULT: "var(--line)", strong: "var(--line-strong)" },
        ink: { DEFAULT: "var(--ink)", 2: "var(--ink-2)" },
        muted: "var(--muted)",
        accent: {
          DEFAULT: "var(--accent)",
          on: "var(--accent-on)",
          ink: "var(--accent-ink)",
          tint: "var(--accent-tint)",
        },
        code: { bg: "var(--code-bg)", ink: "var(--code-ink)" },
        ok: "var(--ok)",
        graph: {
          ink: "var(--graph-ink)",
          line: "var(--graph-line)",
          bg: "var(--graph-bg)",
          chip: "var(--graph-chip)",
        },
        danger: { ink: "var(--danger-ink)", line: "var(--danger-line)", bg: "var(--danger-bg)" },
      },
    },
  },
  plugins: [typography],
};

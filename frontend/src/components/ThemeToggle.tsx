import { useEffect, useState } from "react";
import { useLanguage } from "./LanguageSelector";

const STORAGE_KEY = "beacon:theme";

type Theme = "light" | "dark";

function getInitialTheme(): Theme {
  const stored = localStorage.getItem(STORAGE_KEY);
  if (stored === "light" || stored === "dark") return stored;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function applyTheme(theme: Theme) {
  document.documentElement.classList.toggle("dark", theme === "dark");
}

export default function ThemeToggle() {
  const { t } = useLanguage();
  const [theme, setTheme] = useState<Theme>(getInitialTheme);

  useEffect(() => {
    applyTheme(theme);
    localStorage.setItem(STORAGE_KEY, theme);
  }, [theme]);

  const options: { value: Theme; label: string }[] = [
    { value: "light", label: t("theme_light") },
    { value: "dark", label: t("theme_dark") },
  ];

  return (
    <div
      role="group"
      aria-label={t("theme_label")}
      className="flex gap-1 rounded-[10px] border border-line bg-ground p-[3px]"
    >
      {options.map(({ value, label }) => (
        <button
          key={value}
          type="button"
          onClick={() => setTheme(value)}
          aria-pressed={theme === value}
          className={`h-[30px] flex-1 rounded-[7px] text-[13px] transition-colors ${
            theme === value ? "bg-surface font-semibold text-ink shadow-sm" : "text-muted hover:text-ink"
          }`}
        >
          {label}
        </button>
      ))}
    </div>
  );
}

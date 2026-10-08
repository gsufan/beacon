import { createContext, useContext, useState } from "react";
import { translations, type TranslationKey } from "../lib/i18n";

export type Language = "es" | "en";

interface LanguageContextValue {
  language: Language;
  setLanguage: (lang: Language) => void;
  t: (key: TranslationKey, vars?: Record<string, string | number>) => string;
}

const LanguageContext = createContext<LanguageContextValue | null>(null);

export function LanguageProvider({ children }: { children: React.ReactNode }) {
  const [language, setLanguageState] = useState<Language>(
    () => (localStorage.getItem("beacon:language") as Language) ?? "es"
  );

  const setLanguage = (lang: Language) => {
    localStorage.setItem("beacon:language", lang);
    setLanguageState(lang);
  };

  const t = (key: TranslationKey, vars?: Record<string, string | number>) => {
    let text: string = translations[language][key];
    for (const [name, value] of Object.entries(vars ?? {})) text = text.replace(`{${name}}`, String(value));
    return text;
  };

  return (
    <LanguageContext.Provider value={{ language, setLanguage, t }}>{children}</LanguageContext.Provider>
  );
}

export function useLanguage() {
  const ctx = useContext(LanguageContext);
  if (!ctx) throw new Error("useLanguage debe usarse dentro de LanguageProvider");
  return ctx;
}

export function LanguageSelector({ id }: { id?: string }) {
  const { language, setLanguage } = useLanguage();
  return (
    <select
      id={id}
      value={language}
      onChange={(e) => setLanguage(e.target.value as Language)}
      className="h-[30px] rounded-lg border border-line-strong bg-surface px-1.5 text-[13px] text-ink"
    >
      <option value="es">Español</option>
      <option value="en">English</option>
    </select>
  );
}

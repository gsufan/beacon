import { createContext, useContext, useState } from "react";
import { translations, type TranslationKey } from "../lib/i18n";

export type Language = "es" | "en";

interface LanguageContextValue {
  language: Language;
  setLanguage: (lang: Language) => void;
  t: (key: TranslationKey) => string;
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

  const t = (key: TranslationKey) => translations[language][key];

  return (
    <LanguageContext.Provider value={{ language, setLanguage, t }}>{children}</LanguageContext.Provider>
  );
}

export function useLanguage() {
  const ctx = useContext(LanguageContext);
  if (!ctx) throw new Error("useLanguage debe usarse dentro de LanguageProvider");
  return ctx;
}

export function LanguageSelector() {
  const { language, setLanguage } = useLanguage();
  return (
    <select
      value={language}
      onChange={(e) => setLanguage(e.target.value as Language)}
      className="w-full rounded-lg border border-beacon-11 bg-white px-3 py-2 text-sm text-beacon-4 dark:border-beacon-6 dark:bg-beacon-4 dark:text-beacon-11"
    >
      <option value="es">Español</option>
      <option value="en">English</option>
    </select>
  );
}

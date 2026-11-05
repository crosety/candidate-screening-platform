import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { useLocation, useNavigate } from "react-router";
import { browserLanguages, dictionaries, languageFromSearch, readStoredLanguage, resolveLanguage, storeLanguage, supportedLanguages, type Language, type Localized, type Messages } from "./i18n";

type LanguageState = {
  language: Language;
  explicitLanguage: Language | undefined;
  fallbackLanguage: Language;
  t: Messages;
  choose: (language: Language) => void;
  setSessionLanguage: (language: string) => void;
};

const LanguageContext = createContext<LanguageState | null>(null);

export function LanguageProvider({ children }: { children: ReactNode }) {
  const location = useLocation();
  const navigate = useNavigate();
  const [stored, setStored] = useState(() => readStoredLanguage());
  const [session, setSession] = useState("");
  const [preferred] = useState(browserLanguages);
  const fromQuery = languageFromSearch(location.search);
  const language = resolveLanguage({ search: location.search, stored, session, preferred });
  useEffect(() => {
    // An explicit link language is remembered like a choice made with the switch.
    if (fromQuery) { storeLanguage(fromQuery); setStored(fromQuery); }
  }, [fromQuery]);
  useEffect(() => { document.documentElement.lang = language; }, [language]);
  const choose = useCallback((next: Language) => {
    storeLanguage(next); setStored(next);
    const params = new URLSearchParams(location.search);
    if (!params.has("lang")) return;
    // Drop the link parameter so it does not override the new choice on reload.
    params.delete("lang");
    const search = params.toString();
    navigate({ pathname: location.pathname, search: search ? `?${search}` : "", hash: location.hash }, { replace: true });
  }, [location.hash, location.pathname, location.search, navigate]);
  const explicitLanguage = fromQuery ?? stored ?? undefined;
  const fallbackLanguage = resolveLanguage({ preferred });
  const value = useMemo<LanguageState>(() => ({ language, explicitLanguage, fallbackLanguage, t: dictionaries[language], choose, setSessionLanguage: setSession }), [language, explicitLanguage, fallbackLanguage, choose]);
  return <LanguageContext.Provider value={value}>{children}</LanguageContext.Provider>;
}

export function useI18n(): LanguageState {
  const value = useContext(LanguageContext);
  if (!value) throw new Error("useI18n must be used inside LanguageProvider");
  return value;
}

/** State for a notice that re-renders in the current language. */
export function useNotice(): [Localized | null, (text: Localized | null) => void] {
  // Wrapped in an object: a bare function would be taken as a state updater.
  const [state, setState] = useState<{ text: Localized } | null>(null);
  const set = useCallback((text: Localized | null) => setState(text ? { text } : null), []);
  return [state?.text ?? null, set];
}

export function LanguageSwitch({ className = "" }: { className?: string }) {
  const { language, t, choose } = useI18n();
  return <div className={`language-switch ${className}`} role="group" aria-label={t.switcher.label}>
    {supportedLanguages.map(code => <button key={code} type="button" lang={code} aria-label={t.switcher.names[code]} aria-pressed={language === code} onClick={() => choose(code)}>{code.toUpperCase()}</button>)}
  </div>;
}

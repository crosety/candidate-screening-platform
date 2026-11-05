import { StrictMode, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Route, Routes } from "react-router";
import { ChatPage } from "./pages/ChatPage";
import { ApplyPage } from "./pages/ApplyPage";
import { LanguageProvider, LanguageSwitch, useI18n } from "./language";
import "./index.css";

const client = new QueryClient({ defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } } });

function OpenLinkNotice() {
  const { t } = useI18n();
  useEffect(() => { document.title = t.app.title; }, [t]);
  return <main className="mx-auto max-w-md p-8"><p>{t.app.openLink}</p><LanguageSwitch className="mt-4" /></main>;
}

const root: Root = import.meta.hot?.data.root ?? createRoot(document.getElementById("root")!);
if (import.meta.hot) import.meta.hot.data.root = root;
root.render(<StrictMode><QueryClientProvider client={client}><BrowserRouter><LanguageProvider><Routes><Route path="/apply/:tenantSlug/:offerKey" element={<ApplyPage />} /><Route path="/c/:token" element={<ChatPage />} /><Route path="*" element={<OpenLinkNotice />} /></Routes></LanguageProvider></BrowserRouter></QueryClientProvider></StrictMode>);

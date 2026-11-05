import { useState } from "react";
import { ChannelSheet } from "./ChannelSheet";
import { useI18n } from "../language";

export function PrivacyNotice() {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  return <><button type="button" className="chat-meta-action" onClick={() => setOpen(true)}>{t.privacy.title}</button><ChannelSheet open={open} onClose={() => setOpen(false)} title={t.privacy.title}>
    <p>{t.privacy.assistant}</p>
    <p>{t.privacy.voice}</p>
    <p>{t.privacy.share}</p>
    <details className="privacy-processing"><summary>{t.privacy.processingSummary}</summary><p>{t.privacy.processing}</p></details>
  </ChannelSheet></>;
}

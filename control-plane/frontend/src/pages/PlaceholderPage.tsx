import { Link } from "react-router-dom";

import { useLocale } from "../i18n/LocaleContext";

export function PlaceholderPage({ title }: { title: string }) {
  const { t } = useLocale();
  return (
    <section>
      <div className="page-heading"><div><span className="eyebrow">{t("Control Panel")}</span><h2>{t(title)}</h2></div></div>
      <div className="state-card">
        <strong>{t("Page not found")}</strong>
        <span>{t("The requested Control Panel route does not exist.")}</span>
        <Link className="button-link" to="/agents">{t("Back to agents")}</Link>
      </div>
    </section>
  );
}

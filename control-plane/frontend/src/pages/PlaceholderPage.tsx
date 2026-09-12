import { Link } from "react-router-dom";

export function PlaceholderPage({ title }: { title: string }) {
  return (
    <section>
      <div className="page-heading"><div><span className="eyebrow">Control Panel</span><h2>{title}</h2></div></div>
      <div className="state-card">
        <strong>Page not found</strong>
        <span>The requested Control Panel route does not exist.</span>
        <Link className="button-link" to="/agents">Back to agents</Link>
      </div>
    </section>
  );
}

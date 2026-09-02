export function PlaceholderPage({ title }: { title: string }) {
  return (
    <section>
      <div className="page-heading"><div><span className="eyebrow">Control Panel</span><h2>{title}</h2></div></div>
      <div className="state-card"><strong>Planned in P3.1</strong><span>This route is reserved in the shell; implementation will use the existing Control Plane API rather than mock data.</span></div>
    </section>
  );
}

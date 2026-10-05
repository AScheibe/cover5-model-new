export function Spinner({ label = "Loading" }: { label?: string }) {
  return <span className="spinner" role="progressbar" aria-label={label} />;
}

export function Loading({ what }: { what: string }) {
  return (
    <div className="loading">
      <Spinner label={`Loading ${what}`} /> Loading {what}...
    </div>
  );
}

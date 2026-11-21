import { provenance, type ShowcaseIndex } from "./format";

/**
 * Permanent provenance strip for the static showcase. It has no close control:
 * a recorded replay must never be mistaken for a live session.
 */
export function ShowcaseBanner({ index, error }: { index: ShowcaseIndex | null; error?: string }) {
  if (!index)
    return (
      <aside className="showcase-banner" aria-label="Replay provenance">
        <strong>Recorded replay, not live</strong>
        <span>{error ? `Replay data unavailable: ${error}` : "Loading provenance…"}</span>
      </aside>
    );
  const text = provenance(index);
  return (
    <aside className="showcase-banner" aria-label="Replay provenance" title={text.note || undefined}>
      <strong>{text.headline}</strong>
      {text.details.map((detail) => (
        <span key={detail}>{detail}</span>
      ))}
      {text.note && <span className="showcase-note">{text.note}</span>}
      <a className="showcase-home" href="./">
        About this project
      </a>
    </aside>
  );
}

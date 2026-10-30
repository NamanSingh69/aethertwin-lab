import { useEffect, useState } from "react";
import {
  CHAPTERS,
  EVIDENCE_LABEL,
  MEASUREMENTS,
  ONE_LINER,
  PITCH,
  PROOF,
  ROADMAP,
  type Audience,
} from "./content";
import { Lab } from "./Labs";
import { studioLink } from "../lib/viewParams";

interface LiveState {
  source: string;
  stages: Array<[string, number]>;
}

/** Polls the running service when there is one; the standalone build has none. */
function useLiveState(): LiveState | null {
  const [live, setLive] = useState<LiveState | null>(null);
  useEffect(() => {
    let stopped = false;
    const poll = async () => {
      try {
        const response = await fetch("/api/v2/state", { credentials: "same-origin" });
        const scene = response.ok ? await response.json() : null;
        const stages = Object.entries((scene?.diagnostics?.module_ms ?? {}) as Record<string, number>)
          .filter(([, ms]) => Number.isFinite(ms))
          .sort((a, b) => b[1] - a[1]);
        if (!stopped) setLive(scene && scene.diagnostics ? { source: String(scene.source_mode ?? "unknown"), stages } : null);
      } catch {
        if (!stopped) setLive(null);
      }
    };
    void poll();
    const timer = window.setInterval(poll, 2000);
    return () => {
      stopped = true;
      window.clearInterval(timer);
    };
  }, []);
  return live;
}

function chapterFromHash() {
  const index = CHAPTERS.findIndex((chapter) => `#${chapter.id}` === window.location.hash);
  return Math.max(0, index);
}

export default function Explainer({ standalone = false }: { standalone?: boolean }) {
  const [audience, setAudience] = useState<Audience>("simple");
  const [index, setIndex] = useState(chapterFromHash);
  const live = useLiveState();
  const chapter = CHAPTERS[index];
  const go = (next: number) => {
    const bounded = (next + CHAPTERS.length) % CHAPTERS.length;
    setIndex(bounded);
    history.replaceState(null, "", `#${CHAPTERS[bounded].id}`);
  };
  useEffect(() => {
    const onHash = () => setIndex(chapterFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  const maxStage = Math.max(1, ...(live?.stages.map(([, ms]) => ms) ?? []));

  return (
    <div className="explainer">
      <header className="ex-header">
        <a className="ex-brand" href={standalone ? "#overview" : "./"}>
          Aether<span>Twin</span>
        </a>
        <div className="ex-audience" role="radiogroup" aria-label="Explain it for">
          {(["simple", "engineer"] as const).map((value) => (
            <button key={value} role="radio" aria-checked={audience === value} onClick={() => setAudience(value)}>
              {value === "simple" ? "Simply" : "For engineers"}
            </button>
          ))}
        </div>
        {!standalone && (
          <a className="ex-open-studio" href="./">
            Open the 3D studio
          </a>
        )}
      </header>

      <main>
        <section className="ex-hero" aria-labelledby="ex-title">
          <p className="ex-kicker">What this project is</p>
          <h1 id="ex-title">{ONE_LINER}</h1>
          <p className="ex-pitch">{PITCH[audience]}</p>
          <p className={`ex-status ${live ? "live" : ""}`}>
            <span className="ex-dot" aria-hidden="true" />
            {live
              ? `Connected to a running service (${live.source} data). Hardware: not tested.`
              : "Showing recorded results. No real sensors have been connected yet."}
          </p>
        </section>

        <section aria-labelledby="ex-pipeline-title" className="ex-pipeline">
          <h2 id="ex-pipeline-title">How it works, step by step</h2>
          <nav className="ex-steps" aria-label="Pipeline stages">
            {CHAPTERS.map((item, i) => (
              <button key={item.id} aria-current={i === index ? "step" : undefined} onClick={() => go(i)}>
                <span className="ex-step-number">{i + 1}</span>
                {item.short}
              </button>
            ))}
          </nav>

          <article className="ex-chapter" aria-labelledby="ex-chapter-title">
            <div className="ex-chapter-text">
              <p className="ex-kicker">
                Step {index + 1} of {CHAPTERS.length}
              </p>
              <h3 id="ex-chapter-title">{chapter.title}</h3>
              <p className="ex-body">{audience === "simple" ? chapter.simple : chapter.engineer}</p>
              <dl className="ex-problem">
                <div>
                  <dt>Problem</dt>
                  <dd>{chapter.problem}</dd>
                </div>
                <div>
                  <dt>Approach</dt>
                  <dd>{chapter.approach}</dd>
                </div>
              </dl>
              {audience === "engineer" && (
                <p className="ex-code">
                  Code: {chapter.code.map((path) => <code key={path}>{path}</code>)}
                </p>
              )}
              <div className="ex-chapter-actions">
                <button onClick={() => go(index - 1)} aria-label="Previous step">
                  ← Previous
                </button>
                <button onClick={() => go(index + 1)} aria-label="Next step">
                  Next →
                </button>
                {!standalone && chapter.studio && (
                  <a className="ex-studio-link" href={studioLink(chapter.studio.layers, chapter.studio.view)}>
                    Show this in the 3D studio
                  </a>
                )}
              </div>
            </div>
            {chapter.lab ? (
              <div className="ex-lab">
                <p className="ex-kicker">Try it</p>
                <Lab id={chapter.lab} audience={audience} />
                <p className="ex-fine">Interactive teaching model in 2D using the project's constants. Not a measurement.</p>
              </div>
            ) : (
              <div className="ex-lab ex-lab-empty">
                <p>
                  {standalone
                    ? "This stage is best seen in the 3D studio, which runs alongside the project's service."
                    : "Open this stage in the 3D studio to see it on the live simulated scene."}
                </p>
              </div>
            )}
          </article>
        </section>

        {live && live.stages.length > 0 && (
          <section aria-labelledby="ex-live-title" className="ex-live">
            <h2 id="ex-live-title">Right now, per stage</h2>
            <p className="ex-fine">Live from the running service on this computer, {live.source} data. Milliseconds per frame.</p>
            <ul className="ex-stage-bars">
              {live.stages.map(([name, ms]) => (
                <li key={name}>
                  <span>{name.replaceAll("_", " ")}</span>
                  <span className="ex-bar" style={{ width: `${(100 * ms) / maxStage}%` }} aria-hidden="true" />
                  <output>{ms.toFixed(1)} ms</output>
                </li>
              ))}
            </ul>
          </section>
        )}

        <section aria-labelledby="ex-measured-title" className="ex-measured">
          <h2 id="ex-measured-title">What we have measured</h2>
          <p className="ex-fine">Every number comes from a recorded run in the repository. None of them is from real sensors.</p>
          <div className="ex-cards">
            {MEASUREMENTS.map((m) => (
              <article className="ex-card" key={m.label}>
                <h3>{m.label}</h3>
                <p className="ex-figure">
                  {m.before && (
                    <>
                      <s>{m.before}</s>
                      <span aria-label="improved to"> → </span>
                    </>
                  )}
                  <strong>{m.after}</strong>
                </p>
                <p>{m.note}</p>
                <p className={`ex-evidence ${m.where}`}>{EVIDENCE_LABEL[m.where]}</p>
                {audience === "engineer" && (
                  <p className="ex-fine">
                    Source: <code>{m.source}</code>
                  </p>
                )}
              </article>
            ))}
          </div>
        </section>

        <section aria-labelledby="ex-proof-title" className="ex-proof">
          <h2 id="ex-proof-title">Proven so far, and not yet</h2>
          <ul>
            {PROOF.map((p) => (
              <li key={p.item} className={p.status}>
                <span className="ex-proof-badge">
                  {p.status === "done" ? "Done" : p.status === "partial" ? "Partly" : "Not yet"}
                </span>
                <span>
                  <strong>{p.item}.</strong> {p.detail}
                </span>
              </li>
            ))}
          </ul>
        </section>

        <section aria-labelledby="ex-roadmap-title" className="ex-roadmap">
          <h2 id="ex-roadmap-title">What happens next</h2>
          <ol>
            {ROADMAP.map((r) => (
              <li key={r.phase}>
                <strong>{r.phase}</strong>
                <span>{r.detail}</span>
              </li>
            ))}
          </ol>
        </section>
      </main>

      <footer className="ex-footer">
        AetherTwin is a research platform. Visual effects in the studio are presentation only, and no result on this page
        comes from physical sensors yet.
      </footer>
    </div>
  );
}

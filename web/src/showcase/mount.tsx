import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import App from "../App";
import { ShowcaseBanner } from "./ShowcaseBanner";
import { loadShowcaseIndex, showcaseBase } from "./session";
import type { ShowcaseIndex } from "./format";
import "./showcase.css";

function ShowcaseFrame() {
  const [index, setIndex] = useState<ShowcaseIndex | null>(null),
    [error, setError] = useState("");
  useEffect(() => {
    let stopped = false;
    loadShowcaseIndex(showcaseBase()).then(
      (value) => !stopped && setIndex(value),
      (cause) => !stopped && setError(cause instanceof Error ? cause.message : String(cause)),
    );
    return () => {
      stopped = true;
    };
  }, []);
  return (
    <div className="showcase-root">
      <ShowcaseBanner index={index} error={error} />
      <App />
    </div>
  );
}

/** Static showcase entry (VITE_SHOWCASE builds only): banner plus the studio. */
export function mountShowcase(root: HTMLElement) {
  document.documentElement.dataset.showcase = "replay";
  createRoot(root).render(
    <StrictMode>
      <ShowcaseFrame />
    </StrictMode>,
  );
}

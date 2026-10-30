import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import Explainer from "./Explainer";
import "./explainer.css";
const root = document.getElementById("root");
// The shareable build sets this flag; the studio build serves the page next to a running service.
const standalone = import.meta.env.VITE_EXPLAINER_STANDALONE === "1";
if (root)
  createRoot(root).render(
    <StrictMode>
      <Explainer standalone={standalone} />
    </StrictMode>,
  );

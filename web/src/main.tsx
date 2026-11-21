import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./styles.css";
const root = document.getElementById("root");
// The static showcase build (vite.showcase.config.ts) adds a provenance banner;
// in the normal build this branch and its lazy chunk are compiled away.
if (root && import.meta.env.VITE_SHOWCASE === "1")
  void import("./showcase/mount").then(({ mountShowcase }) => mountShowcase(root));
else if (root)
  createRoot(root).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );

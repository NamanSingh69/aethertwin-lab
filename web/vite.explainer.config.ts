// Standalone, shareable explainer: relative asset paths and no studio service.
// `npm run build:explainer` writes it to explainer-dist/ (not committed).
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
export default defineConfig({
  plugins: [react()],
  base: "./",
  define: { "import.meta.env.VITE_EXPLAINER_STANDALONE": JSON.stringify("1") },
  build: {
    target: "es2022",
    outDir: "explainer-dist",
    emptyOutDir: true,
    rolldownOptions: { input: { explainer: "explainer.html" } },
  },
});

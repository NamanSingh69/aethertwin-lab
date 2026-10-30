/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
export default defineConfig({
  plugins: [react()],
  // Playwright owns e2e/; Vitest runs the unit and contract suites only.
  test: { include: ["tests/**/*.test.ts"] },
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8080",
      "/ws": { target: "ws://127.0.0.1:8080", ws: true },
    },
  },
  build: {
    target: "es2022",
    sourcemap: true,
    manifest:true,
    chunkSizeWarningLimit: 850,
    rolldownOptions: {
      input: { index: "index.html", benchmark: "benchmark.html", explainer: "explainer.html" },
      output:{codeSplitting:{groups:[
        {name:'spatial-contracts',test:/[\\/]src[\\/](lib[\\/](types|geometry|viewParams)|scene[\\/]assets)\.ts$/,priority:30},
        // Post-processing, shader and decoder modules stay with their lazy importers.
        {name:'three-vendor',test:/[\\/]node_modules[\\/]three[\\/](?!examples[\\/]jsm[\\/](postprocessing|shaders|libs)[\\/])/,priority:20},
        {name:'react-vendor',test:/[\\/]node_modules[\\/](react|react-dom|scheduler)[\\/]/,priority:20},
      ]}},
    },
  },
});

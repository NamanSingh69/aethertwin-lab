import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
export default defineConfig({
  plugins: [react()],
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
      input: { index: "index.html", benchmark: "benchmark.html" },
      output:{codeSplitting:{groups:[
        {name:'spatial-contracts',test:/[\\/]src[\\/](lib[\\/](types|geometry)|scene[\\/]assets)\.ts$/,priority:30},
        {name:'three-vendor',test:/[\\/]node_modules[\\/]three[\\/]/,priority:20},
        {name:'react-vendor',test:/[\\/]node_modules[\\/](react|react-dom|scheduler)[\\/]/,priority:20},
      ]}},
    },
  },
});

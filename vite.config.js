import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  root: "frontend",
  base: "/app-assets/",
  build: {
    outDir: "../src/cxquality/static/app",
    emptyOutDir: true,
  },
});

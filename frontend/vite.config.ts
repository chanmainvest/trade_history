import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Allow overriding the backend proxy target so the frontend can run against a
// non-default backend port (e.g. when 8000 is already bound by another app).
const apiTarget = process.env.VITE_API_TARGET || "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  resolve: {
    preserveSymlinks: true,
  },
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": {
        target: apiTarget,
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ""),
      },
    },
  },
});

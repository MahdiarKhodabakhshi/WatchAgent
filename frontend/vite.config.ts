import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/health": "http://localhost:8000",
      "/readings": "http://localhost:8000",
      "/events": "http://localhost:8000",
      "/forecasts": "http://localhost:8000",
    },
  },
});

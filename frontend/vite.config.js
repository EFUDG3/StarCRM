import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // Forward API calls to the local backend so the frontend can use relative
    // /api paths in dev exactly like it does in the deployed single service.
    proxy: {
      "/api": "http://localhost:8000",
    },
  },
});

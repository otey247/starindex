/// <reference types="vitest/config" />
import path from "node:path";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig, type PluginOption } from "vite";

const apiTarget = process.env.STARINDEX_API_URL ?? "http://127.0.0.1:8765";

export default defineConfig(async ({ command, mode }) => {
  // `npm run dev:mock` starts Vite in "mock" mode (works in every shell); STARINDEX_MOCK=1 also enables it.
  const useMock = mode === "mock" || process.env.STARINDEX_MOCK === "1";
  const plugins: PluginOption[] = [react(), tailwindcss()];
  // The fixture-backed mock API only ever exists inside the dev server (`npm run dev:mock`).
  // It is imported dynamically so a production build never bundles or references it.
  if (useMock && command === "serve") {
    const { mockApiPlugin } = await import("./mocks/plugin.ts");
    plugins.push(mockApiPlugin());
  }
  return {
    plugins,
    resolve: { alias: { "@": path.resolve(import.meta.dirname, "src") } },
    server: {
      host: "127.0.0.1",
      port: 5173,
      proxy: useMock ? undefined : { "/api": { target: apiTarget, changeOrigin: false } },
    },
    build: { outDir: "dist", sourcemap: false, chunkSizeWarningLimit: 900 },
    test: {
      environment: "jsdom",
      globals: true,
      setupFiles: ["./src/test/setup.ts"],
      include: ["src/**/*.test.{ts,tsx}"],
      css: false,
    },
  };
});

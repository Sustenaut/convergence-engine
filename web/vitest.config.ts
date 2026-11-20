import { quickpickle } from "quickpickle";
import { defineConfig } from "vitest/config";

// The run page's browser scenarios (tests/features/), Gherkin run by quickpickle on vitest: the page drawn in
// jsdom against an engine faked at fetch, as the bundle runs it in a browser.
export default defineConfig({
  plugins: [quickpickle()],
  // The bundle scenarios read the built bundle, which the build writes beside the engine's code.
  server: { fs: { allow: [".", "../src/convergence/static"] } },
  test: {
    environment: "jsdom",
    include: ["tests/features/*.feature"],
    setupFiles: ["tests/steps.tsx", "tests/bundle.steps.ts"],
    server: { deps: { inline: ["quickpickle", "@sustenaut/ui"] } },
  },
});

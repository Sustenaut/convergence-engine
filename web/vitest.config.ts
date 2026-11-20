import { quickpickle } from "quickpickle";
import { defineConfig } from "vitest/config";

// The run page's browser scenarios (tests/features/), Gherkin run by quickpickle on vitest: the page drawn in
// jsdom against an engine faked at fetch, as the bundle runs it in a browser.
export default defineConfig({
  plugins: [quickpickle()],
  test: {
    environment: "jsdom",
    include: ["tests/features/*.feature"],
    setupFiles: ["tests/steps.tsx"],
    server: { deps: { inline: ["quickpickle", "@sustenaut/ui"] } },
  },
});

import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
  ]),
  {
    // The grace-timeout hook carries an intentional eslint-disable-next-line for
    // react-hooks/exhaustive-deps. The rule is not enabled project-wide today
    // (Next.js preset omits it), so the directive would be flagged as "unused".
    // Suppress that meta-warning for this file so the protective comment survives
    // without noise — and a future engineer who enables exhaustive-deps globally
    // won't accidentally remove the disable and re-introduce the timer-reset bug.
    files: [
      '**/use-agent-grace-timeout.ts',
    ],
    linterOptions: {
      reportUnusedDisableDirectives: 'off',
    },
  },
]);

export default eslintConfig;

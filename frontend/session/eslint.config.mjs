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
    // Vendored / generated — not project code:
    "public/mediapipe/**",
    "coverage/**",
  ]),
  {
    // These files carry intentional eslint-disable-next-line directives for rules
    // the Next.js preset does not enable project-wide, so each directive would be
    // flagged as "unused". Suppress that meta-warning per-file so the protective
    // comments survive without noise — and a future engineer who enables those
    // rules globally won't accidentally strip the disable:
    //   - use-agent-grace-timeout.ts: react-hooks/exhaustive-deps (timer-reset bug)
    //   - use-devtools-guard.ts + DevtoolsShield.tsx + use-devtools-lockout.ts:
    //     no-debugger (the `debugger` statement is the load-bearing
    //     devtools-detection trap, not stray debug code)
    files: [
      '**/use-agent-grace-timeout.ts',
      '**/use-devtools-guard.ts',
      '**/use-devtools-lockout.ts',
      '**/DevtoolsShield.tsx',
    ],
    linterOptions: {
      reportUnusedDisableDirectives: 'off',
    },
  },
]);

export default eslintConfig;

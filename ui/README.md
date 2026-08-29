# Research Map UI

Run the full local stack from the repository root with `./scripts/dev.sh`.

Focused UI checks:

```sh
pnpm --dir ui test:webmcp
pnpm --dir ui test:e2e
pnpm --dir ui exec tsc -p tsconfig.app.json --noEmit
pnpm --dir ui storybook
```

Install the pinned browser once with `pnpm --dir ui exec playwright install chromium`.
The end-to-end suite starts Vite on an isolated strict port and uses deterministic
API and SSE fixtures; it does not require the backend or live inference.

See the repository [README](../README.md) for setup, environment variables, diagnostics, WebMCP testing, backup, and recovery.

<!-- Historical Vite template notes follow.

This template provides a minimal setup to get React working in Vite with HMR and some Oxlint rules.

Currently, two official plugins are available:

- [@vitejs/plugin-react](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react) uses [Oxc](https://oxc.rs)
- [@vitejs/plugin-react-swc](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react-swc) uses [SWC](https://swc.rs/)

## React Compiler

The React Compiler is not enabled on this template because of its impact on dev & build performances. To add it, see [this documentation](https://react.dev/learn/react-compiler/installation).

## Expanding the Oxlint configuration

If you are developing a production application, we recommend enabling type-aware lint rules by installing `oxlint-tsgolint` and editing `.oxlintrc.json`:

```json
{
  "$schema": "./node_modules/oxlint/configuration_schema.json",
  "plugins": ["react", "typescript", "oxc"],
  "options": {
    "typeAware": true
  },
  "rules": {
    "react/rules-of-hooks": "error",
    "react/only-export-components": ["warn", { "allowConstantExport": true }]
  }
}
```

See the [Oxlint rules documentation](https://oxc.rs/docs/guide/usage/linter/rules) for the full list of rules and categories.
-->

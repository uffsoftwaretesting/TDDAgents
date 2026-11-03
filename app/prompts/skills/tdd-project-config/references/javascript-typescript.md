# JavaScript / TypeScript: Vitest or Jest

## Example: Vitest

`package.json` declares `vitest` and `@vitest/coverage-v8` (same version as `vitest`) in `devDependencies`, and `package-lock.json` is committed. The runner's configuration lives in `vitest.config.ts`:

```ts
import { defineConfig } from 'vitest/config'

export default defineConfig({
  test: {
    include: ['tests/**/*.test.ts'],
    reporters: ['default', ['junit', { addFileAttribute: true }]],
    outputFile: { junit: process.env.{{JUNIT_REPORT_ENV}} },
    coverage: {
      enabled: true,
      provider: 'v8',
      include: ['src/**'],
      reporter: ['text', 'cobertura'],
      reportsDirectory: 'coverage',
    },
  },
})
```

```json
{
  "version": {{PROJECT_CONFIG_VERSION}},
  "setup": "npm ci",
  "test": "npx vitest run",
  "coverageReport": {"path": "coverage/cobertura-coverage.xml", "format": "cobertura"},
  "testFiles": ["tests/", "vitest.config.ts"],
  "testSupportFiles": ["tests/fixtures/", "tests/helpers/", "vitest.config.ts"],
  "manifestFiles": ["package.json", "package-lock.json", "tsconfig.json"],
  "exclude": ["node_modules/", "coverage/", "dist/"]
}
```

- `vitest run` runs once and exits (plain `vitest` watches). It exits non-zero when a test fails.
- The `junit` reporter's `addFileAttribute: true` writes a `file` attribute on each `<testcase>`, so TDDAgents can tell which test files ran. `outputFile.junit` sends that reporter's XML to the path TDDAgents provides.
- Coverage reporters write fixed file names inside `reportsDirectory` (`cobertura-coverage.xml` for `cobertura`, `lcov.info` for `lcov` or `lcovonly`), hence the `coverageReport.path` override.

## Example: Jest

`package.json` declares `jest` and `jest-junit` (and `ts-jest` or a Babel preset for TypeScript) in `devDependencies`. `jest-junit` reads its options from environment variables, which the test command sets:

```json
{
  "version": {{PROJECT_CONFIG_VERSION}},
  "setup": "npm ci",
  "test": "JEST_JUNIT_OUTPUT_FILE=\"${{JUNIT_REPORT_ENV}}\" JEST_JUNIT_ADD_FILE_ATTRIBUTE=true npx jest --ci --reporters=default --reporters=jest-junit --coverage --coverageReporters=cobertura",
  "coverageReport": {"path": "coverage/cobertura-coverage.xml", "format": "cobertura"},
  "testFiles": ["__tests__/", "*.test.js", "*.test.ts", "jest.config.js"],
  "testSupportFiles": ["__tests__/fixtures/", "jest.config.js"],
  "manifestFiles": ["package.json", "package-lock.json", "tsconfig.json"],
  "exclude": ["node_modules/", "coverage/", "dist/"]
}
```

- `JEST_JUNIT_ADD_FILE_ATTRIBUTE=true` adds the `file` attribute; `JEST_JUNIT_OUTPUT_FILE` is the report's full path. Check the `jest-junit` README for your version's option names.
- Jest writes coverage under `coverageDirectory` (`coverage/` by default); `cobertura` writes `cobertura-coverage.xml`, `lcovonly` writes `lcov.info`.
- Jest exits non-zero when no test is found; that is a failed run.

## Pitfalls

- Use the lockfile's installer (`npm ci`, `pnpm install --frozen-lockfile`, `yarn install --immutable`) and declare the lockfile in `manifestFiles`.
- Run the locally installed runner (`npx vitest`, `npx jest`), never a global one.
- In a monorepo, run the suite from the root (Vitest projects or Jest projects), so one command collects every test file.

# Source Extraction Notice

This directory contains the source code of `@anthropic-ai/claude-code@2.1.88`, extracted from the published npm package's source map (`cli.js.map`).

## How the source was obtained

```sh
npm pack @anthropic-ai/claude-code@2.1.88
tar xzf anthropic-ai-claude-code-2.1.88.tgz
# Extract sources from cli.js.map into source/
node -e '
const fs = require("fs"), path = require("path");
const map = JSON.parse(fs.readFileSync("cli.js.map", "utf8"));
for (let i = 0; i < map.sources.length; i++) {
  if (map.sourcesContent[i] == null || map.sources[i].includes("node_modules")) continue;
  const rel = map.sources[i].replace(/^\.\.\//g, "");
  const out = path.join("source", rel);
  fs.mkdirSync(path.dirname(out), { recursive: true });
  fs.writeFileSync(out, map.sourcesContent[i]);
}'
```

## Usage

The bundled `cli.js` is self-contained and runs directly with Node.js >= 18:

```sh
node cli.js --version          # 2.1.88 (Claude Code)
node cli.js --help             # show all options
node cli.js -p "hello world"   # non-interactive one-shot
node cli.js                    # interactive REPL
```

Or install globally / symlink:

```sh
npm install -g @anthropic-ai/claude-code@2.1.88
# or
ln -s "$(pwd)/cli.js" /usr/local/bin/claude
```

## Rebuilding from source

Rebuilding from the extracted source is **not feasible** because:

- The code uses `import { feature } from 'bun:bundle'` (Bun bundler compile-time API)
- The original `package.json` with ~hundreds of build/dev dependencies is not published
- Build configuration (tsconfig, bundler config) is not included in the source map
- 2,850 bundled `node_modules` dependencies are only present as source map entries

The extracted `source/` tree (1,906 files, 35 MB) is useful for **reading and studying** the internals, not for rebuilding.

## Directory layout

```
cli.js           # 13 MB self-contained Node.js bundle (the actual executable)
cli.js.map       # 57 MB source map (contains all original sources)
source/          # extracted source tree:
  src/           #   1,902 TypeScript/TSX application files
  vendor/        #   4 native module source stubs
package.json     # published package manifest (no build deps)
README.md        # this file
```

---

# Claude Code

![](https://img.shields.io/badge/Node.js-18%2B-brightgreen?style=flat-square) [![npm]](https://www.npmjs.com/package/@anthropic-ai/claude-code)

[npm]: https://img.shields.io/npm/v/@anthropic-ai/claude-code.svg?style=flat-square

Claude Code is an agentic coding tool that lives in your terminal, understands your codebase, and helps you code faster by executing routine tasks, explaining complex code, and handling git workflows -- all through natural language commands. Use it in your terminal, IDE, or tag @claude on Github.

**Learn more at [Claude Code Homepage](https://claude.com/product/claude-code)** | [Documentation](https://code.claude.com/docs/en/overview)

<img src="https://github.com/anthropics/claude-code/blob/main/demo.gif?raw=1" />

## Get started

1. Install Claude Code:

```sh
npm install -g @anthropic-ai/claude-code
```

2. Navigate to your project directory and run `claude`.

## Reporting Bugs

We welcome your feedback. Use the `/bug` command to report issues directly within Claude Code, or file a [GitHub issue](https://github.com/anthropics/claude-code/issues).

## Connect on Discord

Join the [Claude Developers Discord](https://anthropic.com/discord) to connect with other developers using Claude Code. Get help, share feedback, and discuss your projects with the community.

## Data collection, usage, and retention

When you use Claude Code, we collect feedback, which includes usage data (such as code acceptance or rejections), associated conversation data, and user feedback submitted via the `/bug` command.

### How we use your data

See our [data usage policies](https://code.claude.com/docs/en/data-usage).

### Privacy safeguards

We have implemented several safeguards to protect your data, including limited retention periods for sensitive information and restricted access to user session data.

For full details, please review our [Commercial Terms of Service](https://www.anthropic.com/legal/commercial-terms) and [Privacy Policy](https://www.anthropic.com/legal/privacy).

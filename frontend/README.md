# StarIndex frontend

React + TypeScript + Vite single-page app. In normal use the StarIndex API serves the built
`dist/` folder itself on `http://127.0.0.1:8765`; in development Vite serves the UI and proxies
`/api` to the backend.

## Commands

```bash
npm install
```

```bash
npm run dev
```

Dev server on `http://127.0.0.1:5173`, proxying `/api` to `http://127.0.0.1:8765`
(override with `STARINDEX_API_URL`).

```bash
npm run dev:mock
```

Dev server on `http://127.0.0.1:5174` with a fixture-backed mock API and no backend at all
(see [Mock API](#mock-api)).

```bash
npm run build
```

Type-check (`tsc --noEmit`) and production build into `dist/`.

```bash
npm run test
```

Vitest unit and component tests.

```bash
npm run gen:api
```

Regenerate `src/api/types.gen.ts` from `src/api/openapi-schemas.json`. Refresh that JSON first from
the repository root with `.venv/bin/python scripts/export_schemas.py` whenever
`backend/starindex/api/schemas.py` changes.

```bash
npm run smoke
```

Real-browser check of keyboard interaction and the main flows (needs `npm run dev:mock` running).

```bash
npm run screenshots -- --scale 2 --out screenshots
```

Capture every screen at desktop (1440×900) and phone (390×844) size against the mock server. The
script also fails on console errors, uncaught page errors and horizontal overflow.

## Layout

| Path | What lives there |
|---|---|
| `src/api/` | Generated types (`types.gen.ts`), friendly aliases (`types.ts`), one-function-per-endpoint client (`client.ts`), POST-SSE reader (`sse.ts`), TanStack Query hooks (`queries.ts`) |
| `src/lib/` | Pure helpers: search-operator parsing, URL state, filter reducer, «hit» highlighting, citation parsing, formatting, hotkeys |
| `src/components/ui/` | Primitives (button, dialog, drawer, menu, tabs, tooltip, fields, toasts) |
| `src/components/shell/` | App shell: navigation rail, status strip, command palette, shortcut help, mobile navigation |
| `src/components/repo/` | Repository rows, table, grid, picker, collection menu |
| `src/components/search/` | Query bar with suggestions, filter sidebar |
| `src/components/evidence/` | Evidence lists, evidence-class labels, citation viewer |
| `src/components/ai/` | Stream state machine, model picker, cloud confirmation, provider error panel, report view, comparison table, ChatGPT plan pieces |
| `src/pages/` | One file per screen; `pages/settings/` for the settings sections |
| `mocks/` | Dev-only mock API (Vite middleware) and its fixtures |
| `scripts/` | Screenshot and smoke scripts (Playwright) |

## Design decisions

- **Visual language.** Graphite neutrals with one restrained blue accent. State is expressed with
  type, icons and layout: there are no tinted status fills, no coloured card edges, and evidence
  classes are typographic labels rather than coloured pills. Tokens live in `src/styles/index.css`.
- **Fonts are bundled.** Instrument Sans (UI) and JetBrains Mono (paths, code, metadata) ship from
  `@fontsource-variable/*` inside the build. Nothing is fetched from a CDN at runtime, so the app
  works offline.
- **Repository content is untrusted.** READMEs and model output render through `react-markdown`
  with `rehype-sanitize`; raw HTML is never executed, only `http(s)`/`mailto` links survive, and
  every external link opens with `rel="noopener noreferrer"`. Search snippets are rendered as text
  nodes, with «…» markers turned into `<mark>` by `lib/highlight.tsx`.
- **Remote images are not loaded by default.** READMEs embed badges and trackers, and the app must
  work offline, so an image renders as a labelled link. The README panel has an explicit "Load
  remote images" toggle; when on, images load lazily with `referrerPolicy="no-referrer"`. Owner
  avatars are not fetched at all.
- **No silent provider switching.** A failed generation stream becomes state
  (`components/ai/useAiStream.ts`). The error panel lists the alternatives the server offers and the
  user picks one explicitly. A stream that ends without a terminal event is reported as incomplete,
  never as success.
- **Cloud confirmation.** `useCloudGate` holds a request until the user confirms in a dialog that
  names the exact provider and model. The server's own `confirmation_required` error re-opens the
  same dialog, so the check cannot be bypassed from the UI.
- **Search needs no model.** Search, Library, Compare (table) and the evidence viewer make no model
  calls. Ask AI without a configured model still shows the retrieved passages.
- **URL is the state.** Search and Library keep query, mode, scope, sort, view, page and every
  filter in the query string (`lib/urlState.ts`), so results are linkable and back/forward works.
- **TypeScript 5.9, not 7.** `openapi-typescript` 7.x (used by `gen:api`) declares
  `typescript ^5.x` as a peer and uses the JavaScript compiler API, so TypeScript is pinned to the
  newest 5.x. Everything else is current: React 19, Vite 8, Tailwind 4, Vitest 5.
- **ChatGPT plan UI** follows OpenAI's UI/UX guidelines: a settings card "Use your ChatGPT plan"
  with a "Continue with ChatGPT" action, a one-time "You're using your ChatGPT plan" / "Got it"
  confirmation, "Using ChatGPT plan · Manage usage" beside the model selector, and "Manage usage" as
  the primary action when a usage limit is reached. The official ChatGPT logo asset is not bundled;
  add it to `ContinueWithChatGPT` (`components/ai/ChatGPTBits.tsx`) under OpenAI's brand terms.

## Keyboard

`Ctrl/Cmd+K` command palette · `/` focus search · `?` shortcut help · `j`/`k` move through results ·
`Enter` open · `o` open on GitHub · `x` add to comparison · `g` then `s l a r c o y ,` to go to
Search, Library, Ask, Research, Compare, Collections, Sync, Settings · `Alt+1…5` switch search mode ·
`Esc` closes panels.

## Mock API

`npm run dev:mock` mounts `mocks/plugin.ts` as Vite middleware. It implements every endpoint in
`docs/API.md`, including the streaming ones, over about 90 real, well-known repositories with
approximate figures. It is imported only when `STARINDEX_MOCK=1` and the command is `serve`, so a
production build never contains it.

Scenarios for manual testing:

```bash
curl -X POST http://127.0.0.1:5174/api/__mock -H 'content-type: application/json' -d '{"first_run": true}'
```

Other bodies: `{"llm": "none" | "local" | "cloud" | "chatgpt"}`, `{"fail": "usage_limit" | "unavailable" | "auth" | null}`,
`{"chatgpt": "signed_in" | "no_consent", "welcome": true}`, `{"running_sync": true}`, `{"reset": true}`.

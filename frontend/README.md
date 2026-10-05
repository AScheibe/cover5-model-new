# Cover 5 web app

React + TypeScript frontend for the Cover 5 model. It talks to the local API
described in [`../docs/api.md`](../docs/api.md).

## Use it

```sh
cd frontend && npm install && npm run build   # writes frontend/dist
cd .. && python -m cover5 serve --open        # API + app on http://127.0.0.1:8765
```

## Develop

```sh
python -m cover5 serve        # API on :8765 (in one terminal)
cd frontend && npm run dev    # Vite on :5173, proxies /api to :8765
npm test -- --run             # vitest + Testing Library, fetch mocked with docs/samples
npm run build                 # tsc (strict) + vite build
npm run e2e                   # build, then Playwright against the real server on a fresh demo data root
```

### Demo data and end-to-end tests

`scripts/demo_data.py <dir>` builds a self-contained data root for the real
current week (league lines seeded from nflverse, two market snapshots with a
few lines moved, a "Do this" change) plus the two previous weeks with picks,
nflverse results and history records. It prints the environment variables to
run against it, and never touches the repo's own `data/`:

```sh
eval "$(python3 scripts/demo_data.py /tmp/cover5-demo --force --quiet)"
python3 -m cover5 serve --no-scheduler     # Fetch lines reads /tmp/cover5-demo/market.json
```

`npm run e2e` (`e2e/*.spec.ts`, `playwright.config.ts`) starts that server
itself (`e2e/serve.mjs`) on port `E2E_PORT` (8799) with a fresh root at
`E2E_DATA_ROOT` (default `$TMPDIR/cover5-e2e`), so runs are repeatable. Page
screenshots at 1440x900 and 390x844 go to `E2E_SCREENS` (default
`test-results/screens`). It uses a preinstalled Chromium when one exists
(`PLAYWRIGHT_CHROMIUM_PATH`), otherwise run `npx playwright install chromium`
once. The tests follow the real clock: steps that need a game in progress or
one not yet kicked off pick a suitable week or skip with a reason.

## Layout

- `src/api/`: contract types (`types.ts`) and the typed client (`client.ts`; errors carry the server's `detail`).
- `src/lib/`: team colours, spread/points/time formatting, pick-set toggling logic.
- `src/week/`, `src/pages/`: the Week, History, Backtest and Settings screens.
- `src/test/`: test setup and the fetch mock built on `docs/samples/*.json`.
- `e2e/`: Playwright end-to-end specs against the real backend.

Every mutation replaces the week view with the returned `outcome.week`; a
generation counter drops any GET that started before it, so a slow refetch
never overwrites newer state. Times render in the browser's timezone.

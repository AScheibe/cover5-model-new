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
```

## Layout

- `src/api/`: contract types (`types.ts`) and the typed client (`client.ts`; errors carry the server's `detail`).
- `src/lib/`: team colours, spread/points/time formatting, pick-set toggling logic.
- `src/week/`, `src/pages/`: the Week, History, Backtest and Settings screens.
- `src/test/`: test setup and the fetch mock built on `docs/samples/*.json`.

Every mutation replaces the week view with the returned `outcome.week`; a
generation counter drops any GET that started before it, so a slow refetch
never overwrites newer state. Times render in the browser's timezone.

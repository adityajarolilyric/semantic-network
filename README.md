# Semantic Layer Configurator

`application/` contains the React/TypeScript UI and `backend/` contains the Python/FastAPI backend. MongoDB stores semantic definitions and operational metadata; ClickHouse stores source data, managed views, planning populations and measure values.

## Start the entire application

Only Docker with Compose 2.24+ is required. From the repository root:

```bash
docker compose up --build -d --wait
```

Open http://localhost:5174. This builds and starts the UI, API, MongoDB replica set and ClickHouse, with persistent database volumes and health-checked startup. No local Python/Node installation or environment file is needed. Stop with `docker compose down`; data remains in the named volumes.

Optional settings go in a root `.env` copied from `.env.example`. See [Compose deployment](docs/OPERATIONS.md#full-stack-startup) for ports, authentication and existing data.

## Develop locally

Requirements: Python 3.13, Node.js 24 and Docker Compose 2.20 or later.

```bash
npm run setup
npm run db:up
npm run dev
```

Run commands from the repository root. The UI opens at `http://127.0.0.1:5174`; the API listens on `http://127.0.0.1:8788`. API documentation is at `http://127.0.0.1:8788/docs`.

Setup installs both applications' dependencies and creates `backend/.env` from its example only when that file does not exist. Configure MongoDB and ClickHouse through `backend/.env`; the server owns the ClickHouse database selection. See the [backend setup](backend/README.md) and [UI setup](application/README.md).

If ClickHouse is already running, configure its connection in `backend/.env` and start only MongoDB:

```bash
docker compose -f backend/compose.dev.yml up -d --wait metadata-mongo
```

`npm run db:up` starts MongoDB and ClickHouse for host-based development. Stop the full container stack before running the host API/UI on the same ports. Both Compose definitions use the same persistent database volumes.

## Commands

- `npm run dev`: API and UI together.
- `npm run dev:api` / `npm run dev:application`: separate processes.
- `npm run build` / `npm run preview`: production UI build and local preview.
- `npm run test:unit`: backend unit/API tests.
- `npm run test:ui`: frontend tests.
- `npm run test:integration`: disposable ClickHouse integration suite; requires Docker.
- `npm run test:mongo`: disposable MongoDB integration suite; requires local `mongod`.
- `npm run test:e2e`: browser suite using disposable databases; requires Docker, local `mongod` and installed Playwright Chromium (`cd application && npx playwright install chromium`).
- `npm run db:down`: stop the development database stack, retaining volumes.

Existing `:v1` command aliases remain available. All default commands now use V1.

## Workflow

1. **Sources:** upload up to 10 CSV files sequentially, preview ClickHouse data, select semantic model/revision and enable Managed Tables. Empty installations create a default model and draft revision.
2. **Managed Tables:** save identity or pivot views. Semantic objects consume revision-specific saved managed outputs.
3. **Dimensions:** expose attributes and column display names, declare keys, and configure the daily Time dimension/calendar when needed.
4. **Planning Levels:** build populations from intersections, Cartesian factors or distinct parent projections. Grain columns are mandatory; dimension bindings provide optional membership/enrichment and cross-level mappings.
5. **Transactions:** map native grain keys directly or resolve mixed-grain assignments. Selector discovery, explicit source order, per-value Split/Copy and date ranges are supported. Daily ranges can map to native Week/Month keys through the calendar.
6. **Measures:** define referenced or calculated measures, configured Time/NonTime aggregation and disaggregation policies. Independent native levels do not need a path to Base.
7. **Materialise:** Build Semantic Network and preview planning values. Each revision/level uses one population table and one values table, including during rebuilds.
8. **Planning View:** choose Rows, Columns and Measures; measures appear as rows. Editable referenced measures can be edited at native or proven coarser grains. Writes go to Base where reachable, otherwise native, and update compatible coarser levels atomically.

There is no durable edit journal. Rebuilding the semantic network replaces manual edits from source data. Weighted-by-self edits distribute across populated cells while preserving NULL cells; empty or zero-total patterns fail explicitly. None disaggregation means Repeat, and aggregation always follows the configured policy.

## Documentation

Start with the [design guide](docs/README.md), then read the relevant topic:

- [Metadata and authoring](docs/METADATA.md)
- [Sources and Managed Tables](docs/SOURCES_AND_MANAGED_TABLES.md)
- [Planning levels](docs/PLANNING_LEVELS.md)
- [Transactions and date ranges](docs/TRANSACTIONS.md)
- [Measures and Materialise](docs/MEASURES.md)
- [Planning View and editing](docs/PLANNING_VIEW.md)
- [Engine and storage](docs/ENGINE_AND_STORAGE.md)
- [Operations and verification](docs/OPERATIONS.md)

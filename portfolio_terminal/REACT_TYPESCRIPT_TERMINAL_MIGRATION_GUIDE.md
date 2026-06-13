# React + TypeScript Portfolio Terminal Migration Guide

This guide is for building a separate project that keeps the current dashboard functionality but changes the UI into a more professional portfolio/trading terminal.

The recommended approach is not to rewrite everything at once. Keep the working Python logic, expose it through APIs, and gradually replace Streamlit screens with a React frontend.

## Target Architecture

```text
React + TypeScript frontend
        |
        v
FastAPI backend
        |
        +-- Kite auth / holdings / positions
        +-- Portfolio calculations
        +-- Momentum / price ladder analytics
        +-- Supabase read/write
        +-- Scheduled LTP cache reads
```

Recommended stack:

- Frontend: React, TypeScript, Vite
- UI: Tailwind CSS or plain CSS modules
- Tables: AG Grid Community or TanStack Table
- Charts: Apache ECharts, Plotly, or TradingView Lightweight Charts
- Backend: FastAPI
- Storage: Supabase/Postgres
- Jobs: Windows Task Scheduler, cron, or APScheduler

## Phase 0: Freeze Current Functionality

Goal: document what the current Streamlit app does before changing UI.

Create a feature inventory:

- Fetch Kite holdings
- Upload Kite holdings CSV/XLSX
- Portfolio holdings table
- Sector grouping
- Sector weightage summary
- Sector pie chart
- Selected holding batch details
- Holdings breakdown CRUD
- Exited holdings summary
- Open positions tab
- Historic price ladder
- Returns table
- Momentum ranking
- Correlation matrix
- Supabase upload/sync flows

Define acceptance rule:

```text
The React version is successful only if each current Streamlit workflow still works.
```

## Phase 1: Extract Backend Logic

Goal: move reusable business logic out of Streamlit-specific UI files.

Create modules in the current or new backend project:

```text
backend/
  services/
    kite_service.py
    holdings_service.py
    positions_service.py
    momentum_service.py
    historic_service.py
    ltp_cache_service.py
  repositories/
    supabase_repository.py
  calculations/
    portfolio_calculations.py
    sector_calculations.py
```

Move logic gradually:

- Sector mapping by symbol
- `-RR` / `-IV` fallback symbol matching
- Holdings invested/current/P&L calculations
- Sector weight calculations
- Batch detail extraction
- Historic ladder building
- Momentum score formatting

Do not move Streamlit rendering code. Move only data preparation and calculations.

## Phase 2: Build FastAPI Skeleton

Goal: expose current functionality through JSON APIs.

Suggested endpoints:

```text
GET  /api/portfolio/holdings
GET  /api/portfolio/holdings/grouped-by-sector
GET  /api/portfolio/holdings/{symbol}/batches
GET  /api/portfolio/sector-summary
GET  /api/positions/open
GET  /api/historic/price-ladder
GET  /api/historic/returns
GET  /api/momentum/ranking
GET  /api/correlation
POST /api/holdings-breakdown/upload
POST /api/holdings-breakdown/entries
PATCH /api/holdings-breakdown/{id}
```

Example response shape for portfolio holdings:

```json
{
  "asOf": "2026-06-10T10:00:00Z",
  "totals": {
    "invested": 100000,
    "current": 112000,
    "pnl": 12000,
    "pnlPct": 12
  },
  "sectors": [
    {
      "sector": "Banking",
      "holdingsCount": 4,
      "invested": 30000,
      "weightPct": 30,
      "holdings": []
    }
  ]
}
```

## Phase 3: Create React App

Goal: create a UI-only project that consumes mock JSON first.

Commands:

```powershell
npm create vite@latest portfolio-terminal -- --template react-ts
cd portfolio-terminal
npm install
npm install @tanstack/react-table echarts-for-react axios lucide-react
```

Suggested frontend structure:

```text
src/
  api/
    client.ts
    portfolioApi.ts
    historicApi.ts
    momentumApi.ts
  components/
    layout/
    tables/
    charts/
    cards/
  features/
    portfolio/
    positions/
    momentum/
    price-ladder/
  styles/
    theme.css
```

Start with mock data. Do not connect to the backend until the UI shape feels right.

## Phase 4: Portfolio Holdings Screen

Goal: reproduce the current portfolio holdings tab with a terminal-style layout.

Layout:

```text
Top metrics:
Total Invested | Current Value | P&L | As Of

Sector summary:
Pie chart | Sector weightage table

Main work area:
Sector grouped holdings table | Selected holding batch details
```

Required UI behavior:

- Sector rows are grouped/collapsible.
- Sector header shows count, invested, and weight.
- Holding rows show symbol, quantity, average price, invested, weight, current, LTP, P&L, P&L %, day change.
- Clicking a symbol shows batch details adjacent to that sector group.
- `-RR` and `-IV` symbols match base symbols for sector mapping.

Recommended components:

```text
PortfolioPage
PortfolioMetricStrip
SectorWeightChart
SectorSummaryTable
SectorGroup
HoldingsTable
BatchDetailsPanel
```

## Phase 5: Holdings Breakdown CRUD

Goal: port the holdings breakdown editor flows.

Features:

- Add holding/batch
- Edit summary
- Edit batch
- Exit batch
- Exit summary
- Exited holdings summary

UI direction:

- Use side drawers or modal forms instead of expanding many forms inline.
- Keep batch history visible.
- Use compact action icon buttons.

Suggested components:

```text
HoldingEditorDrawer
BatchEditorDrawer
ExitHoldingDialog
ExitedHoldingsTable
```

## Phase 6: Momentum Ranking Screen

Goal: make the momentum screen decision-first.

Layout:

```text
Signal summary cards
Compact ranking table
Selected stock detail panel
Advanced diagnostics expander
```

Main table columns:

```text
Symbol | Score | Action | LTP | Entry Zone | RS | Volume Ratio
```

Keep advanced fields behind detail views:

```text
EMA10, EMA20, EMA50, EMA100, EMA200, RSI, ATR, Z-score, 52W distance
```

Use the current momentum color palette:

```text
Strong Entry:              #0F766E
Watchlist - Below EMA20:   #2563EB
Near Entry:                #D97706
Wait:                      #64748B
Avoid:                     #BE123C
```

## Phase 7: Price Ladder Screen

Goal: port the current sorted price ladder into a better interactive table.

Features:

- One column per ticker or a transposed ticker-first view.
- Highlight range position using the same momentum palette.
- Show EMA distance values with positive/negative styling.
- Keep range used and LTP visually distinct.

Possible improvement:

```text
Ticker selector | Ladder table | Mini price/range chart
```

## Phase 8: Positions Screen

Goal: port open positions.

Features:

- Open positions table
- P&L coloring
- Quantity, average price, LTP, day change
- Optional grouping by product/exchange/sector

Keep it utilitarian. This screen should feel like a working terminal, not a marketing dashboard.

## Phase 9: Live LTP Cache

Goal: support automatic hourly data refresh independent of the frontend.

Architecture:

```text
Windows Task Scheduler
        |
        v
scheduled_ltp_refresh.py
        |
        v
Kite live API
        |
        v
Supabase live_ltp_cache
        |
        v
FastAPI / React reads latest cached prices
```

Supabase table:

```sql
create table live_ltp_cache (
  symbol text primary key,
  instrument_token bigint,
  source text,
  ltp numeric,
  quantity numeric,
  updated_at timestamptz default now()
);
```

Frontend should show:

```text
Prices updated at 10:00 AM
Next scheduled refresh 11:00 AM
```

## Phase 10: Theming And Terminal Polish

Goal: make the UI feel professional.

Design rules:

- Dense but readable.
- No giant cards for data-heavy views.
- Use compact metric strips.
- Use muted backgrounds and strong typography.
- Use clear visual hierarchy.
- Use resizable split panels if needed.
- Avoid unnecessary animations.

Useful UI patterns:

- Left navigation rail
- Top status bar
- Workspace tabs
- Resizable panels
- Sticky table headers
- Pinned symbol column
- Keyboard search
- Saved table column presets

## Suggested Migration Order

1. Build React mock Portfolio Holdings screen.
2. Build FastAPI endpoint for portfolio holdings.
3. Connect React Portfolio screen to API.
4. Add selected batch details.
5. Add sector summary chart/table.
6. Port Momentum Ranking.
7. Port Price Ladder.
8. Port Open Positions.
9. Port Holdings Breakdown CRUD.
10. Add scheduled LTP cache.

## What Not To Do

Avoid these early:

- Rewriting all calculations in TypeScript.
- Rebuilding every Streamlit tab at once.
- Calling Kite directly from React.
- Putting service role Supabase keys in the frontend.
- Making live price refresh depend on browser tabs staying open.

## Success Criteria

The new UI should feel better because:

- Important decisions are visible first.
- Details are available on selection, not always shown.
- Tables are faster and more interactive.
- Sector exposure is immediately understandable.
- Batch history is adjacent to selected holdings.
- Live data has a clear timestamp.

The functionality should remain the same because:

- Python remains responsible for broker integration and calculations.
- Supabase remains the source of persistent portfolio data.
- React only changes the presentation and interaction model.

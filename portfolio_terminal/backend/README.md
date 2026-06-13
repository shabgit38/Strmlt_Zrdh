# Portfolio Terminal Backend

FastAPI service for the React terminal.

## Configuration

Create `backend/.env` or set these environment variables:

```text
ZERODHA_API_KEY=...
ZERODHA_API_SECRET=...
SUPABASE_URL=...
SUPABASE_SERVICE_ROLE_KEY=...
SUPABASE_HOLDINGS_TABLE_NAME=holdings_breakdown
```

`SUPABASE_*` values are optional for the Kite holdings fetch, but sector mapping and batch details need the holdings breakdown table.

The backend also reads `../Strmlt_Zrdh/.streamlit/secrets.toml` when present.

## Kite login

Start the backend, then open:

```text
http://127.0.0.1:8000/api/auth/kite/login
```

After Zerodha redirects back to `/api/auth/kite/callback`, the backend stores the daily Kite session in `backend/.kite_session.json`. The session expires around 6 AM India time.

## Run

```powershell
uv run --with-requirements backend/requirements.txt uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

Then start the React app:

```powershell
npm run dev
```

The Vite dev server proxies `/api` to `http://127.0.0.1:8000`.

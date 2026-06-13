from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse

from portfolio_snapshot import KiteConfigError, build_live_portfolio_snapshot, complete_kite_login, kite_login_url


app = FastAPI(title="Portfolio Terminal API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5176", "http://localhost:5176"],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/auth/kite/login")
def kite_login() -> RedirectResponse:
    try:
        return RedirectResponse(kite_login_url())
    except KiteConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/auth/kite/callback", response_class=HTMLResponse)
def kite_callback(request_token: str = Query(default="")) -> str:
    try:
        session = complete_kite_login(request_token)
    except KiteConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Kite login failed: {exc}") from exc

    return f"""
    <!doctype html>
    <html>
      <head><title>Kite login complete</title></head>
      <body>
        <h1>Kite login complete</h1>
        <p>Session expires at {session["expires_at"]}.</p>
        <p>You can close this tab and refresh Portfolio Terminal.</p>
      </body>
    </html>
    """


@app.get("/api/portfolio/snapshot")
def portfolio_snapshot() -> dict:
    try:
        return build_live_portfolio_snapshot()
    except KiteConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to load live portfolio: {exc}") from exc

from fastapi import FastAPI, Depends, Response
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from pathlib import Path

from app.auth import get_api_key
from app.rate_limiter import rate_limit

BASE_DIR = Path(__file__).resolve().parent.parent   
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="Real-Time API Rate Limiter & Gateway")

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", response_class=HTMLResponse)
def home():
    return (STATIC_DIR / "index.html").read_text()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/protected")
def protected_route(
    response: Response,
    user_id: str = Depends(get_api_key)
):
    rate_info = rate_limit(user_id, "protected")

    response.headers["X-RateLimit-Limit"] = str(rate_info["limit"])
    response.headers["X-RateLimit-Remaining"] = str(rate_info["remaining"])
    response.headers["X-RateLimit-Reset"] = str(rate_info["reset"])

    return {
        "message": "You accessed a protected resource",
        "user": user_id,
    }

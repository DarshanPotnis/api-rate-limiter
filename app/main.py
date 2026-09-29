from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.gateway.app import gateway
from app.logging_setup import configure_logging
from app.status import router as status_router

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"

# The console loads only its own files and talks only to this gateway, so nothing else is
# allowed; this also keeps it working offline.
CONSOLE_CSP = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "connect-src 'self'",
        "img-src 'self'",
        "object-src 'none'",
        "base-uri 'none'",
        "form-action 'none'",
        "frame-ancestors 'none'",
    ]
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging(get_settings().log_level)
    # Starlette does not run the lifespans of mounted apps, so start the gateway's here.
    async with gateway.router.lifespan_context(gateway):
        yield


app = FastAPI(title="LLM Gateway", lifespan=lifespan)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/v1", gateway)
app.include_router(status_router)


@app.get("/", response_class=FileResponse)
def console() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html", headers={"Content-Security-Policy": CONSOLE_CSP})


@app.get("/health")
def health():
    return {"status": "ok"}

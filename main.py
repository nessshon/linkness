import os
import re
from contextlib import asynccontextmanager

import redis.asyncio as redis
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from nanoid import generate
from pydantic import BaseModel, HttpUrl, AnyUrl

TTL_DEFAULT = 86400
TTL_MIN = 60
TTL_MAX = 86400 * 30  # 30 дней
BASE_URL = os.getenv("BASE_URL", "https://link.ness.su").rstrip("/")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379")

CODE_RE = re.compile(r"^[a-zA-Z0-9_-]{3,20}$")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _app.state.redis = redis.from_url(REDIS_URL, decode_responses=True)
    await _app.state.redis.ping()
    yield
    await _app.state.redis.aclose()


app = FastAPI(title="linkness", lifespan=lifespan)


class ShortenRequest(BaseModel):
    url: AnyUrl
    ttl: int | None = None


class ShortenResponse(BaseModel):
    short_url: str
    expires_in: int


@app.post("/shorten", response_model=ShortenResponse)
async def shorten(req: ShortenRequest, request: Request):
    r = request.app.state.redis

    ttl = req.ttl or TTL_DEFAULT
    ttl = max(TTL_MIN, min(ttl, TTL_MAX))

    for _ in range(10):
        code = generate(size=7)
        ok = await r.set(f"url:{code}", str(req.url), ex=ttl, nx=True)
        if ok:
            return {"short_url": f"{BASE_URL}/{code}", "expires_in": ttl}

    raise HTTPException(status_code=500, detail="Failed to generate unique code, try again")


@app.get("/{code}/info")
async def info(code: str, request: Request):
    if not CODE_RE.match(code):
        raise HTTPException(status_code=400, detail="Invalid code")

    r = request.app.state.redis
    url = await r.get(f"url:{code}")
    if not url:
        raise HTTPException(status_code=404, detail="Not found")

    return {"url": url, "ttl_remaining": await r.ttl(f"url:{code}")}


@app.get("/{code}")
async def redirect(code: str, request: Request):
    if not CODE_RE.match(code):
        raise HTTPException(status_code=400, detail="Invalid code")

    r = request.app.state.redis
    url = await r.get(f"url:{code}")
    if not url:
        raise HTTPException(status_code=404, detail="Link not found or expired")

    return RedirectResponse(url=url, status_code=302)

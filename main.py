"""TeraBox Download API - FastAPI.

POST /api/extract  -> metadata + direct link + signed proxy URL
GET  /api/proxy    -> streaming proxy with Range support
GET  /api/health   -> health check
GET  /             -> HTML status page
"""
import asyncio
import base64
import hashlib
import hmac
import json
import logging
import mimetypes
import os
import re
import secrets
import time
from contextlib import asynccontextmanager
from typing import Optional
from urllib.parse import parse_qs, quote, urlparse

import httpx
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.background import BackgroundTask

load_dotenv()
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("terabox-api")

# ----------------------------------------------------------------- config
NDUS = os.getenv("NDUS_COOKIE", "").strip()
API_KEY = os.getenv("API_KEY", "").strip()  # optional: protects /api/extract
SECRET_KEY = os.getenv("SECRET_KEY", "").strip()
if not SECRET_KEY:
    SECRET_KEY = secrets.token_hex(32)
    log.warning("SECRET_KEY not set; proxy links reset on restart and break across workers.")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
BASE = os.getenv("TERABOX_BASE", "https://www.terabox.com").rstrip("/")
EXTRACT_LIMIT = os.getenv("EXTRACT_RATE_LIMIT", "30/minute")
PROXY_LIMIT = os.getenv("PROXY_RATE_LIMIT", "120/minute")
LINK_TTL = int(os.getenv("LINK_TTL", "3600"))
CACHE_TTL = int(os.getenv("CACHE_TTL", "300"))
MAX_FILES = int(os.getenv("MAX_FOLDER_FILES", "200"))
MAX_DEPTH = int(os.getenv("MAX_FOLDER_DEPTH", "3"))

APP_ID = "250528"
ALLOWED_DOMAINS = ("terabox.com", "1024terabox.com", "teraboxapp.com", "4funbox.com")
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


class TeraBoxError(Exception):
    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.message = message
        self.status = status


# ----------------------------------------------------------------- models
class ExtractRequest(BaseModel):
    url: str = Field(..., examples=["https://terabox.com/s/1xxxxxxxx"])

    @field_validator("url")
    @classmethod
    def _valid_url(cls, v: str) -> str:
        v = v.strip()
        if len(v) > 500 or not v.lower().startswith(("http://", "https://")):
            raise ValueError("url must be a valid http(s) link")
        return v


class FileInfo(BaseModel):
    file_name: str
    file_size: int
    file_size_formatted: str
    path: str
    thumbnail: Optional[str] = None
    download_link: str
    proxy_url: str


class ExtractResponse(BaseModel):
    success: bool = True
    # top-level fields are filled for single-file links (null for folders)
    file_name: Optional[str] = None
    file_size: Optional[int] = None
    file_size_formatted: Optional[str] = None
    download_link: Optional[str] = None
    proxy_url: Optional[str] = None
    thumbnail: Optional[str] = None
    is_folder: bool = False
    # always filled: every file (one item for single files)
    total_files: int = 0
    files: list[FileInfo] = []


# ----------------------------------------------------------------- helpers
def human_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.2f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{n} B"


def parse_share_url(url: str) -> str:
    """Return the share code (with leading '1')."""
    p = urlparse(url)
    host = (p.hostname or "").lower()
    if not any(host == d or host.endswith("." + d) for d in ALLOWED_DOMAINS):
        raise TeraBoxError(
            "Unsupported domain. Use terabox.com, 1024terabox.com, teraboxapp.com or 4funbox.com.", 400
        )
    qs = parse_qs(p.query)
    if qs.get("surl"):
        return "1" + qs["surl"][0]
    m = re.search(r"/s/([A-Za-z0-9_-]+)", p.path)
    if m:
        return m.group(1)
    raise TeraBoxError("Could not find a share code in the URL.", 400)


def make_token(url: str, name: str) -> str:
    payload = json.dumps({"u": url, "n": name, "e": int(time.time()) + LINK_TTL}).encode()
    body = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    sig = hmac.new(SECRET_KEY.encode(), body.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{body}.{sig}"


def read_token(token: str) -> dict:
    try:
        body, sig = token.rsplit(".", 1)
        good = hmac.new(SECRET_KEY.encode(), body.encode(), hashlib.sha256).hexdigest()[:32]
        if not hmac.compare_digest(sig, good):
            raise ValueError
        data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except Exception:
        raise HTTPException(400, "Invalid token")
    if data.get("e", 0) < time.time():
        raise HTTPException(410, "Link expired, extract again")
    return data


def check(data: dict) -> dict:
    errno = data.get("errno", 0)
    if errno not in (0, None):
        msg = data.get("errmsg") or data.get("show_msg") or "unknown error"
        status = 404 if errno in (105, -9, -7, -3, 145, 300) else 502
        raise TeraBoxError(f"TeraBox error {errno}: {msg}", status)
    return data


async def api_get(client: httpx.AsyncClient, path: str, params: dict) -> dict:
    try:
        r = await client.get(f"{BASE}{path}", params=params)
        r.raise_for_status()
        return check(r.json())
    except httpx.HTTPError as e:
        raise TeraBoxError(f"Upstream request failed: {e}")
    except ValueError:
        raise TeraBoxError("Upstream returned a non-JSON response (blocked or changed).")


async def get_js_token(client: httpx.AsyncClient, surl: str) -> str:
    try:
        r = await client.get(f"{BASE}/wap/share/filelist", params={"surl": surl})
    except httpx.HTTPError:
        return ""
    for pat in (r"fn%28%22([0-9A-Fa-f]+)%22%29", r'fn\("([0-9A-Fa-f]+)"\)'):
        m = re.search(pat, r.text)
        if m:
            return m.group(1)
    return ""


async def collect_files(client, surl, js, shareid, uk, directory, root, out, depth):
    page = 1
    while len(out) < MAX_FILES:
        params = {
            "app_id": APP_ID, "web": 1, "channel": "dubox", "clienttype": 0,
            "jsToken": js, "page": page, "num": 100, "by": "name", "order": "asc",
            "site_referer": "", "shorturl": surl, "root": 1 if root else 0,
        }
        if shareid:
            params["shareid"] = shareid
        if uk:
            params["uk"] = uk
        if not root:
            params["dir"] = directory
        data = await api_get(client, "/share/list", params)
        items = data.get("list") or []
        for it in items:
            if len(out) >= MAX_FILES:
                break
            if str(it.get("isdir")) == "1":
                if depth < MAX_DEPTH:
                    await collect_files(client, surl, js, shareid, uk, it.get("path", ""), False, out, depth + 1)
            else:
                out.append(it)
        if len(items) < 100:
            break
        page += 1


async def fetch_share(client: httpx.AsyncClient, code: str) -> tuple[list[dict], bool]:
    surl = code[1:]
    js = await get_js_token(client, surl)
    info = await api_get(client, "/api/shorturlinfo", {
        "app_id": APP_ID, "web": 1, "channel": "dubox", "clienttype": 0,
        "shorturl": code, "root": 1,
    })
    shareid, uk = info.get("shareid"), info.get("uk")
    root_items = info.get("list") or []
    is_folder = len(root_items) != 1 or str(root_items[0].get("isdir")) == "1"
    files: list[dict] = []
    await collect_files(client, surl, js, shareid, uk, "", True, files, 0)
    return files, is_folder


async def resolve_direct(client: httpx.AsyncClient, dlink: str, sem: asyncio.Semaphore) -> str:
    """Follow the dlink's first redirect to get the CDN URL (best effort)."""
    async with sem:
        try:
            async with client.stream("GET", dlink, follow_redirects=False) as r:
                loc = r.headers.get("location")
                if r.status_code in (301, 302, 303, 307, 308) and loc:
                    return loc
        except httpx.HTTPError:
            pass
    return dlink


# ----------------------------------------------------------------- app
limiter = Limiter(key_func=get_remote_address)
_cache: dict[str, tuple[float, list[dict], bool]] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    headers = {"User-Agent": UA, "Accept": "application/json, text/plain, */*", "Referer": BASE + "/"}
    if NDUS:
        headers["Cookie"] = f"ndus={NDUS}; lang=en"
    else:
        log.warning("NDUS_COOKIE not set; most links will fail to return download links.")
    timeout = httpx.Timeout(connect=15, read=60, write=15, pool=15)
    async with httpx.AsyncClient(headers=headers, timeout=timeout, follow_redirects=True) as client:
        app.state.client = client
        yield


app = FastAPI(title="TeraBox Download API", version="1.0.0", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


@app.exception_handler(TeraBoxError)
async def terabox_error_handler(request: Request, exc: TeraBoxError):
    return JSONResponse(status_code=exc.status, content={"success": False, "error": exc.message})


async def require_api_key(x_api_key: Optional[str] = Header(None)):
    if API_KEY and not hmac.compare_digest(x_api_key or "", API_KEY):
        raise HTTPException(401, "Invalid or missing X-API-Key")


def base_url(request: Request) -> str:
    return PUBLIC_BASE_URL or str(request.base_url).rstrip("/")


# ----------------------------------------------------------------- routes
@app.post("/api/extract", response_model=ExtractResponse, dependencies=[Depends(require_api_key)])
@limiter.limit(EXTRACT_LIMIT)
async def extract(request: Request, body: ExtractRequest):
    return await do_extract(request, body.url)


@app.get("/api/extract", response_model=ExtractResponse, dependencies=[Depends(require_api_key)])
@limiter.limit(EXTRACT_LIMIT)
async def extract_get(request: Request, url: str = Query(..., max_length=500)):
    return await do_extract(request, url.strip())


async def do_extract(request: Request, url: str) -> ExtractResponse:
    code = parse_share_url(url)
    client: httpx.AsyncClient = request.app.state.client

    cached = _cache.get(code)
    if cached and cached[0] > time.time():
        items, is_folder = cached[1], cached[2]
    else:
        items, is_folder = await fetch_share(client, code)
        items = [i for i in items if i.get("dlink")]
        if not items:
            raise TeraBoxError(
                "No downloadable files found. Link may be expired/private, or NDUS_COOKIE is invalid.", 404
            )
        _cache[code] = (time.time() + CACHE_TTL, items, is_folder)
        if len(_cache) > 500:
            for k in [k for k, v in _cache.items() if v[0] < time.time()]:
                _cache.pop(k, None)

    sem = asyncio.Semaphore(5)
    directs = await asyncio.gather(*[resolve_direct(client, i["dlink"], sem) for i in items])

    root = base_url(request)
    files: list[FileInfo] = []
    for it, direct in zip(items, directs):
        name = it.get("server_filename") or "file"
        size = int(it.get("size") or 0)
        thumbs = it.get("thumbs") or {}
        thumb = thumbs.get("url3") or thumbs.get("url2") or thumbs.get("url1") or None
        files.append(FileInfo(
            file_name=name,
            file_size=size,
            file_size_formatted=human_size(size),
            path=it.get("path", name),
            thumbnail=thumb,
            download_link=direct,
            proxy_url=f"{root}/api/proxy?token={make_token(it['dlink'], name)}",
        ))

    resp = ExtractResponse(is_folder=is_folder, total_files=len(files), files=files)
    if not is_folder and files:
        f = files[0]
        resp.file_name, resp.file_size = f.file_name, f.file_size
        resp.file_size_formatted, resp.thumbnail = f.file_size_formatted, f.thumbnail
        resp.download_link, resp.proxy_url = f.download_link, f.proxy_url
    return resp


@app.get("/api/proxy")
@limiter.limit(PROXY_LIMIT)
async def proxy(request: Request, token: str = Query(...), download: bool = False):
    data = read_token(token)
    client: httpx.AsyncClient = request.app.state.client

    headers = {}
    if request.headers.get("range"):
        headers["Range"] = request.headers["range"]
    try:
        upstream = await client.send(client.build_request("GET", data["u"], headers=headers), stream=True)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Upstream error: {e}")
    if upstream.status_code >= 400:
        await upstream.aclose()
        raise HTTPException(502 if upstream.status_code >= 500 else upstream.status_code, "Upstream refused the request")

    name = data.get("n") or "file"
    ctype = upstream.headers.get("content-type", "")
    if not ctype or ctype.startswith("application/octet-stream"):
        ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"

    out = {"Content-Type": ctype, "Accept-Ranges": "bytes"}
    for h in ("content-length", "content-range", "etag", "last-modified"):
        if h in upstream.headers:
            out[h.title()] = upstream.headers[h]
    disp = "attachment" if download else "inline"
    out["Content-Disposition"] = f"{disp}; filename*=UTF-8''{quote(name)}"

    return StreamingResponse(
        upstream.aiter_raw(),
        status_code=upstream.status_code,
        headers=out,
        background=BackgroundTask(upstream.aclose),
    )


@app.get("/api/health")
async def health():
    return {"status": "ok", "cookie_configured": bool(NDUS), "time": int(time.time())}


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def home():
    ok = "configured" if NDUS else "missing"
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>TeraBox API</title>
<style>body{{font-family:system-ui,sans-serif;max-width:560px;margin:40px auto;padding:0 16px;
background:#0f172a;color:#e2e8f0}}code{{background:#1e293b;padding:2px 6px;border-radius:4px}}
.ok{{color:#4ade80}}.bad{{color:#f87171}}a{{color:#60a5fa}}</style></head><body>
<h1>TeraBox Download API</h1>
<p>Status: <span class="ok">online</span><br>
NDUS cookie: <span class="{'ok' if NDUS else 'bad'}">{ok}</span></p>
<ul><li><code>POST /api/extract</code></li><li><code>GET /api/proxy?token=...</code></li>
<li><code>GET /api/health</code></li></ul>
<p><a href="/docs">Interactive docs</a></p></body></html>"""


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")))

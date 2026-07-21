import asyncio
import json
import re
import webbrowser
from dataclasses import dataclass
from typing import Optional
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
)

PROXIED_DOMAINS = (
    "api.vk.ru", "id.vk.ru", "login.vk.ru", "oauth.vk.ru",
    "api.vk.com", "vk.com", "static.vk.ru", "ad.mail.ru",
    "privacy-cs.mail.ru", "sdk-api.apptracer.ru", "mc.yandex.ru", "vk.ru",
)

HOP_BY_HOP = {"host", "connection", "content-length", "transfer-encoding", "keep-alive"}
CSP_META_RE = re.compile(r'<meta[^>]*http-equiv=["\']?Content-Security-Policy["\']?[^>]*>', re.IGNORECASE)

@dataclass
class State:
    captcha_url: Optional[str] = None
    token_future: Optional[asyncio.Future] = None


state = State()

BASE_DIR = Path(__file__).resolve().parent
INDEX_HTML_PATH = BASE_DIR / "assets" / "captcha.html"
INJECT_SCRIPT_TEMPLATE = (BASE_DIR / "assets" / "inject.js").read_text(encoding="utf-8")

INJECT_SCRIPT = INJECT_SCRIPT_TEMPLATE.replace(
    "__PROXIED_DOMAINS__",
    json.dumps(PROXIED_DOMAINS, ensure_ascii=False)
)

def dedup_v(url: str) -> str:
    return re.sub(r"(v=[^&]+)(&v=[^&]+)+", r"\1", url) if "api.vk.ru" in url else url


def extract_token(url: str, body: bytes) -> Optional[str]:
    if "captchaNotRobot" not in url:
        return None
    try:
        resp = json.loads(body.decode("utf-8", errors="ignore")).get("response") or {}
    except Exception:
        return None
    if not isinstance(resp, dict):
        return None
    for key in ("success_token", "access_token", "token", "captcha_token", "secret"):
        v = resp.get(key)
        if isinstance(v, str) and len(v) > 20:
            return v
    return None


def make_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    @app.get("/")
    async def index():
        return FileResponse(INDEX_HTML_PATH, media_type="text/html")

    @app.get("/captcha-url")
    async def captcha_url():
        if not state.captcha_url:
            return JSONResponse({"error": "not set"}, status_code=400)
        return {"url": state.captcha_url}

    @app.get("/captcha-html")
    async def captcha_html(url: str):
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=15.0) as c:
                r = await c.get(url, headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
                    "Sec-Fetch-Dest": "iframe",
                    "Sec-Fetch-Mode": "navigate",
                    "Sec-Fetch-Site": "cross-site",
                })
        except Exception as e:
            return JSONResponse({"error": f"upstream: {e}"}, status_code=502)

        html = CSP_META_RE.sub("", r.text)
        script = f"<script>{INJECT_SCRIPT}</script>"

        if re.search(r"</head\s*>", html, re.IGNORECASE):
            html = re.sub(r"</head\s*>", script + "</head>", html, count=1, flags=re.IGNORECASE)
        elif re.search(r"<body[^>]*>", html, re.IGNORECASE):
            html = re.sub(r"(<body[^>]*>)", r"\1" + script, html, count=1, flags=re.IGNORECASE)
        else:
            html = script + html

        return Response(content=html, media_type="text/html; charset=utf-8", headers={"Cache-Control": "no-store"})

    @app.api_route("/proxy/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"])
    async def proxy(path: str, request: Request):
        if request.method == "OPTIONS":
            return Response(status_code=204, headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS, PATCH",
                "Access-Control-Allow-Headers": request.headers.get("access-control-request-headers", "*"),
                "Access-Control-Max-Age": "86400",
            })

        target = dedup_v(path if path.startswith(("http://", "https://")) else "https://" + path)
        final = f"{target}{('&' if '?' in target else '?')}{request.url.query}" if request.query_params else target

        headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_BY_HOP}
        headers.update({
            "Origin": "https://web.max.ru",
            "Referer": "https://web.max.ru/",
            "User-Agent": USER_AGENT,
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
        })

        body = await request.body() if request.method in ("POST", "PUT", "PATCH") else None

        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=20.0) as c:
                r = await c.request(request.method, final, headers=headers, content=body)
        except Exception as e:
            print("upstream failed: %s for %s", e, final)
            return JSONResponse({"error": f"upstream: {e}"}, status_code=502)

        if token := extract_token(final, r.content):
            if state.token_future and not state.token_future.done():
                state.token_future.set_result(token)

        out = {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Credentials": "false"}
        if ct := r.headers.get("content-type"):
            out["Content-Type"] = ct

        return Response(content=r.content, status_code=r.status_code, headers=out)

    return app


async def solve(url: str, host: str = "127.0.0.1", port: int = 18765, timeout: float = 300.0) -> str:
    state.captcha_url = url
    state.token_future = asyncio.get_running_loop().create_future()

    server = uvicorn.Server(
        uvicorn.Config(app=make_app(), host=host, port=port, log_level="critical", access_log=False)
    )
    task = asyncio.create_task(server.serve())

    page = f"http://{host}:{port}/"
    asyncio.get_running_loop().call_later(0.5, webbrowser.open, page)

    try:
        print("Waiting for captcha solve...")
        return await asyncio.wait_for(state.token_future, timeout=timeout)
    except asyncio.TimeoutError:
        print("Timeout: captcha was not solved")
        raise
    finally:
        server.should_exit = True
        await task
        state.captcha_url = None
        state.token_future = None


if __name__ == "__main__":
    try:
        if url := input("Captcha URL -> ").strip():
            print(f"\nCaptcha token: {asyncio.run(solve(url))}")
    except (KeyboardInterrupt, EOFError):
        print("\nbye")
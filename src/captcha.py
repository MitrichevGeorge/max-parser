import asyncio
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

captcha_url = None
future = None


@app.get("/captcha-url")
async def get_captcha_url():
    global captcha_url
    if not captcha_url:
        return {"error": "Captcha URL not set"}, 400
    return {"url": captcha_url}


@app.post("/result")
async def receive_result(req: Request):
    global future

    data = await req.json()
    token = data.get("token")
    if future and not future.done():
        future.set_result(token)

    return {"status": "ok"}


async def solve(url: str, host: str = "127.0.0.1", port: int = 18765) -> str:
    global captcha_url, future
    captcha_url = url
    future = asyncio.get_running_loop().create_future()

    config = uvicorn.Config(
        app=app,
        host=host,
        port=port,
        log_level="warning",
    )
    server = uvicorn.Server(config)
    server_task = asyncio.create_task(server.serve())

    try:
        print("Waiting for captcha...")
        token = await future
        return token
    finally:
        server.should_exit = True
        await server_task


if __name__ == "__main__":
    try:
        url_input = input("Captcha URL -> ").strip()

        if url_input:
            token = asyncio.run(solve(url_input))
            print(f"\nИтоговый Token: {token}")
    except (KeyboardInterrupt, EOFError):
        print("\nbye")
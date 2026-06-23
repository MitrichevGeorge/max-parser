import asyncio
import json
import websockets
from datetime import datetime
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    ONEME_DEVICE_ID: str = Field(default="q")
    ONEME_AUTH: dict = Field(default_factory=dict)

    model_config = SettingsConfigDict(
        env_file=".env", 
        env_file_encoding="utf-8"
    )

    @model_validator(mode="after")
    def check_required_fields(self):
        if self.ONEME_DEVICE_ID == "q" or not self.ONEME_AUTH.get("token"):
            raise ValueError("Pls check .env")
        return self

stg = Settings()

async def send_messages(websocket):
    try:
        while True:
            message = await asyncio.to_thread(input, "Вы: ")
            if message.lower() == 'exit':
                print("Закрываем соединение...")
                break
            
            await websocket.send(message)
            await asyncio.sleep(0.1)
    except asyncio.CancelledError:
        pass

async def receive_messages(websocket):
    try:
        async for message in websocket:
            print(f"Сервер: {message}")
            print("Вы: ", end="", flush=True)
    except websockets.exceptions.ConnectionClosed:
        print("\nСоединение с сервером разорвано.")

async def main():
    url = "wss://ws-api.oneme.ru/websocket"
    
    custom_headers = {
        "Host": "ws-api.oneme.ru",
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:150.0) Gecko/20100101 Firefox/150.0",
        "Accept" : "*/*",
        "Accept-Language" : "en-US,en;q=0.9,ru-RU;q=0.8,ru;q=0.7",
        "Accept-Encoding" : "gzip, deflate, br, zstd",
        "Sec-WebSocket-Version" : "13",
        "Origin" : "https://web.max.ru",
        "Sec-WebSocket-Extensions" : "permessage-deflate",
        "Sec-Fetch-Storage-Access" : "none",
        "Sec-GPC" : "1",
        "Connection" : "Upgrade",
        "Sec-Fetch-Dest" : "empty",
        "Sec-Fetch-Mode" : "websocket",
        "Sec-Fetch-Site" : "cross-site",
        "Pragma" : "no-cache",
        "Cache-Control" : "no-cache",
        "Upgrade" : "websocket",
    }

    print(f"Подключение к {url}...")
    
    try:
        async with websockets.connect(url, additional_headers=custom_headers) as websocket:
            print("Соединение успешно установлено")
            await websocket.send('{"ver":11,"cmd":0,"seq":0,"opcode":6,"payload":{"userAgent":{"deviceType":"WEB","pushDeviceType":"WEBPUSH","locale":"en","deviceLocale":"en","osVersion":"Linux","deviceName":"Firefox","headerUserAgent":"Mozilla/5.0 (X11; Linux x86_64; rv:150.0) Gecko/20100101 Firefox/150.0","appVersion":"26.6.19","screen":"1200x1920 1.0x","timezone":"Europe/Moscow"},"deviceId":"' + stg.ONEME_DEVICE_ID + '"}}')
            await websocket.send('{"ver":11,"cmd":0,"seq":1,"opcode":19,"payload":{"token":"' + stg.ONEME_AUTH["token"] + '","chatsCount":40,"interactive":true,"chatsSync":0,"contactsSync":0,"presenceSync":-1,"draftsSync":0}}')
            await websocket.recv()
            data = json.loads(await websocket.recv())['payload']
            print(f'Name: {data['profile']['contact']['names'][0]['name']} [{data['profile']['contact']['country']}]\nID: {data['profile']['contact']['id']}\nPhone: +{data['profile']['contact']['phone']}')
            # with open("q.json","w") as f:
            #     print(await websocket.recv(), file=f)
            #     f.close()
            # gather_tasks = asyncio.gather(
            #     send_messages(websocket),
            #     receive_messages(websocket),
            #     return_exceptions=True
            # )
            # await gather_tasks

    except Exception as e:
        print(f"Ошибка подключения: {e}")

if __name__ == "__main__":
    asyncio.run(main())
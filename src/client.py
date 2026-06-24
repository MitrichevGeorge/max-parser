import asyncio
import json
import websockets
from classes import UserProfile, Chat
from settings import stg
import payloads as pl

class Client:
    def __init__(self) -> None:
        self.connection = None

    async def connect(self):
        self.connection = await websockets.connect(pl.URL, additional_headers=pl.HEADERS)
        print(f"Successfully connected to {pl.URL}")
        await self.connection.send(pl.get_device_payload(stg.ONEME_DEVICE_ID))
        await self.connection.send(pl.get_auth_payload(stg.ONEME_AUTH["token"]))
        await self.connection.recv()
        data = json.loads(await self.connection.recv())['payload']
        print("You:")
        me = UserProfile(**data['profile']['contact'])
        me.info()
        print("Contacts:")
        for i in data['contacts']:
            UserProfile(**i).info(1)
        print("Chats:")
        for i in data['chats']:
            Chat(**i).info(1)

    async def disconnect(self):
        if self.connection:
            await self.connection.close()
            print(f"Connection to {pl.URL} closed")

async def main():
    q = Client()
    await q.connect()
    await q.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
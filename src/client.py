import asyncio
import json
import websockets
from classes import UserProfile, Chat
from settings import stg
import payloads as pl
from convert import PacketCodec
import base64
from typing import Any
from operator import itemgetter

class UniversalEncoder(json.JSONEncoder):
    def default(self, o: Any):
        if isinstance(o, bytes):
            try:
                return o.decode('utf-8')
            except UnicodeDecodeError:
                return base64.b64encode(o).decode('utf-8')
        
        return super().default(o)

class Client:
    profile: UserProfile
    contacts: list
    chats: list

    def __init__(self) -> None:
        self.connection = None
        self.codec = PacketCodec()

    async def _recv(self):
        if not isinstance(self.connection, websockets.ClientConnection):
            raise RuntimeError("Client not connected")
        raw = await self.connection.recv()
        if not isinstance(raw, bytes):
            raise ValueError("It is not bytes")
        return self.codec.bytes_to_payload(raw)

    async def connect(self):
        self.connection = await websockets.connect(pl.URL, additional_headers=pl.HEADERS)
        print(f"Successfully connected to {pl.URL}")
        await self.connection.send(self.codec.payload_to_bytes(6, pl.get_device_payload(stg.ONEME_DEVICE_ID)))
        await self.connection.send(self.codec.payload_to_bytes(19, pl.get_auth_payload(stg.ONEME_AUTH["token"])))
        await self.connection.recv()
        data = (await self._recv())['payload']
        self.profile = UserProfile(**data['profile']['contact'])
        self.contacts = [UserProfile(**i) for i in data['contacts']]
        self.chats = [Chat(**i) for i in data['chats']]

    async def disconnect(self):
        if self.connection:
            await self.connection.close()
            print(f"Connection to {pl.URL} closed")

    def info(self):
        print("You:")
        self.profile.info(1)
        print("Contacts:")
        [i.info(1) for i in self.contacts]
        print("Chats:")
        [i.info(1) for i in self.chats]

    async def search(self, query: str, count: int = 40):
        if not isinstance(self.connection, websockets.ClientConnection):
            raise RuntimeError("Client not connected")
        await self.connection.send(self.codec.payload_to_bytes(68, {'query': query, 'count': count }))
        response = await self._recv() # <- very bad idea. needs to fix
        get_chats = itemgetter("chat")
        return list(map(get_chats, response["payload"]["result"]))
        # open("src/w2.json", "w").write(json.dumps(await self._recv(), cls=UniversalEncoder, indent=2))

async def main():
    q = Client()
    await q.connect()
    # q.info()
    for i in await q.search("солнце"):
        Chat(**i).info()
    await q.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
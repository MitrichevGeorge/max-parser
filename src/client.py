import asyncio
import json
import websockets
from classes import UserProfile, Chat
from settings import stg
import payloads as pl
from convert import PacketCodec
import base64

class Client:
    profile: UserProfile
    contacts: list
    chats: list

    def __init__(self) -> None:
        self.connection = None
        self.codec = PacketCodec()

    async def connect(self):
        self.connection = await websockets.connect(pl.URL, additional_headers=pl.HEADERS)
        print(f"Successfully connected to {pl.URL}")
        await self.connection.send(self.codec.payload_to_bytes(6, pl.get_device_payload(stg.ONEME_DEVICE_ID)))
        await self.connection.send(self.codec.payload_to_bytes(19, pl.get_auth_payload(stg.ONEME_AUTH["token"])))
        await self.connection.recv()
        raw = await self.connection.recv()
        if not isinstance(raw, bytes):
            raise ValueError("It is not bytes")
        data = self.codec.bytes_to_payload(raw)['payload']
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

async def main():
    q = Client()
    await q.connect()
    q.info()
    await q.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
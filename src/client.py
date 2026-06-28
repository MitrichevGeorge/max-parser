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
from collections import defaultdict
import traceback
from loguru import logger
import sys
from network import NetworkMixin


class UniversalEncoder(json.JSONEncoder):
    def default(self, o: Any):
        if isinstance(o, bytes):
            try:
                return o.decode('utf-8')
            except UnicodeDecodeError:
                return base64.b64encode(o).decode('utf-8')
        
        return super().default(o)

class Client(NetworkMixin):
    profile: UserProfile
    contacts: list[UserProfile]
    chats: list[Chat]

    def __init__(self) -> None:
        self._netw_init()

    async def connect(self):
        data = await self._netw_connect()
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
        await self._send(68, {'query': query, 'count': count })
        response = await self.wait_for_opcode(68)
        get_chats = itemgetter("chat")
        return list(map(get_chats, response["payload"]["result"]))
        # open("src/w2.json", "w").write(json.dumps(await self._recv(), cls=UniversalEncoder, indent=2))

class Tuiclient(Client):
    async def _init_log(self):
        logger.remove()
        logger.add(
            "logs/log_{time:YYYY-MM-DD_HH-mm-ss}.log", 
            format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}", 
            level="INFO",
            encoding="utf-8"
        )
        logger.add(sys.stdout, colorize=True, format="<green>{time:HH:mm:ss}</green> | {level} | {message}")

    async def begin(self):
        await self._init_log()
        await self.connect()
        self.profile.info()
        print("Chats:")
        for i in self.chats:
            print(f'[{i.type}][{i.id}] {i.title} - {i.messagesCount}')


async def main():
    q = Tuiclient()
    await q.begin()
    await q.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
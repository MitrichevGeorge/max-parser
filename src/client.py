import asyncio
import json
from classes import UserProfile, Chat
import payloads as pl
import base64
from typing import Any, List
from operator import itemgetter
from pydantic import TypeAdapter
from loguru import logger
import sys
from network import NetworkMixin
from tools import any_without


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

    async def get_infos(self, contactIds: List[int]) -> List[UserProfile]:
        await self._send(32, {'contactIds': contactIds})
        response = await self.wait_for_opcode(32)
        return [UserProfile(**i ) for i in response["payload"]["contacts"]]

    async def get_chat_part(self, chat: Chat) -> UserProfile:
        if not chat.participants:
            raise RuntimeError("No participants")
        # print(chat.participants)
        return (await self.get_infos([any_without(list(chat.participants.keys()), self.profile.id)]))[0]

    # {'magic': 10, 'cmd': 0, 'seq': 26, 'opcode': 32, 'payload': {'contactIds': [146231034]}}
    # {'magic': 10, 'cmd': 1, 'seq': 26, 'opcode': 32, 'payload': {'contacts': [{'id': 146231034, 'updateTime': 1781161654143, 'registrationTime': 1766470709229, 'names': [{'name': 'nekohu', 'firstName': 'nekohu', 'lastName': '', 'type': 'ONEME'}], 'options': ['TT', 'ONEME'], 'accountStatus': 0, 'country': 'RU'}]}}


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
            if i.type == "DIALOG":
                print(f'[{i.type}][{i.id}] {(await self.get_chat_part(i)).names[0]} - {i.messagesCount}')
            else:
                print(f'[{i.type}][{i.id}] {i.title} - {i.messagesCount}')
        # for i in await self.search("saved", 2):
        #     Chat(**i).info()
        # print(self.chats[0].participants)
        # if self.chats[0].participants:
        #     (await self.get_infos([next(iter(self.chats[0].participants.keys()))]))[0].info()


async def main():
    q = Tuiclient()
    await q.begin()
    await q.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
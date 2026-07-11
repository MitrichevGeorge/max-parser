import asyncio
from classes import ConfigContainer, UserProfile, Chat, ServerData
import payloads as pl
from typing import Any, List
from operator import itemgetter
from loguru import logger
import sys
from network import NetworkMixin
from tools import any_without


class Client(NetworkMixin):
    profile: UserProfile
    contacts: list[UserProfile]
    chats: list[Chat]
    config: ConfigContainer

    def __init__(self) -> None:
        self._netw_init()

    async def connect(self):
        data = ServerData.model_validate(await self._netw_connect())
        self.profile = data.profile.contact
        self.contacts = data.contacts
        self.chats = data.chats
        self.config = data.config

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

    async def norm_chatlist(self) -> List[str]:
        result = []
        user_ids_get = []
        chat_to_user = {}
        for chat in self.chats:
            if chat.type != "DIALOG" or not chat.participants or chat.id == 0:
                continue
            user_id = any_without(chat.participants, self.profile.id)
            user_ids_get.append(user_id)
            chat_to_user[chat.id] = user_id
        infos = await self.get_infos(user_ids_get)
        by_id = {i.id: i for i in infos}
        for chat in self.chats:
            if chat.id == 0:
                name = "Saved messages"
            elif chat.type == "DIALOG" and chat.id in chat_to_user:
                info = by_id.get(chat_to_user[chat.id])
                name = info.names[0] if info else "Unknown"
            else:
                name = chat.title
            sound = ""
            if chat.id in self.config.chats:
                if self.config.chats[chat.id].dontDisturbUntil == -1:
                    sound = " muted"
            result.append(f'[{chat.type}][{chat.id}] {name} - {chat.messagesCount}{sound}')
        return result


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
        for i in enumerate(await self.norm_chatlist()):
            print(f'{i[0]}) {i[1]}')
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
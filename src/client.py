import asyncio
from classes import ConfigContainer, UserProfile, Chat, ServerData
import payloads as pl
from typing import Any, Dict, List
from operator import itemgetter
from loguru import logger
import sys
from network import NetworkMixin
from tools import any_without, read_number


class Client(NetworkMixin):
    profile: UserProfile
    contacts: list[UserProfile]
    chats: list[Chat]
    chats_by_id: Dict[int, Chat]
    config: ConfigContainer

    def __init__(self) -> None:
        self._netw_init()

    async def connect(self):
        data = ServerData.model_validate(await self._netw_connect())
        self.profile = data.profile.contact
        self.contacts = data.contacts
        self.chats = data.chats
        self.chats_by_id = {i.id: i for i in self.chats}
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
        # open("src/w2.json", "w").write(json.dumps(await self._recv(), cls=UniversalEncoder, indent=2)

    async def norm_chatlist(self, new_type: bool = False) -> list[str | tuple[int, str, int]]:
        chat_to_user = {
            chat.id: any_without(chat.participants, self.profile.id)
            for chat in self.chats
            if chat.type == "DIALOG" and chat.participants and chat.id != 0
        }

        infos = await self.get_infos(list(chat_to_user.values()))
        users_by_id = {user.id: user for user in infos}

        def format_chat(chat: Chat, idx: int) -> str | tuple[int, str, int]:
            if chat.id == 0:
                name = "Saved messages"
            elif chat.type == "DIALOG":
                user_id = chat_to_user.get(chat.id)
                user_info = users_by_id.get(user_id) if user_id else None
                name = user_info.get_name() if user_info else "Unknown"
            else:
                if not chat.title:
                    raise ValueError(f"Missing title for chat ID {chat.id}")
                name = chat.title

            config = self.config.chats.get(chat.id)
            muted = " muted" if config and config.dontDisturbUntil == -1 else ""
            
            line = f"[{chat.type}][{chat.id}] {name} - {chat.messagesCount}{muted}"
            return (idx, line, chat.id) if new_type else line

        return [format_chat(chat, idx) for idx, chat in enumerate(self.chats)]

    async def norm_chat(self, chat_id: int):
        result = []
        chat = self.chats_by_id[chat_id]
        if not chat.participants:
            raise RuntimeError
        user_ids_get = list(chat.participants)
        infos = await self.get_infos(user_ids_get)
        by_id = {i.id: i for i in infos}
        messages = (await self.get_messages(chat_id))
        for i in messages:
            sender_name = by_id[i.sender].get_name()
            date_str = i.time.strftime("%d.%m.%Y %H:%M:%S")
            attach_str = f" [{len(i.attaches)} attaches]" if i.attaches else ""
            result.append(f'[{date_str}] {sender_name}: {i.text}{attach_str}')
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
        norm_chatlist = await self.norm_chatlist(new_type=True)
        for i in norm_chatlist:
            print(f'{i[0]}) {i[1]}')
        
        chat_id = norm_chatlist[read_number(min_n=0, max_n=(len(norm_chatlist) - 1))][2]
        if not isinstance(chat_id, int):
            raise ValueError
        print(chat_id)
        for i in await self.norm_chat(chat_id):
            print(i)
        # if isinstance(chat_id, int):
        #     for i in :
        #         print()

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
import asyncio
from classes import ConfigContainer, Message, UserProfile, Chat, ServerData
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
    users_by_id: Dict[int, UserProfile] = {}
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

    async def update_missing_users(self, user_ids: List[int]) -> None:
        missing_ids = list(set(user_ids) - self.users_by_id.keys())
        if not missing_ids:
            return

        new_infos = await self.get_infos(missing_ids)
        self.users_by_id.update({user.id: user for user in new_infos})

    async def norm_chatlist(self, new_type: bool = False) -> list[str | tuple[int, str, int]]:
        chat_to_user = {
            chat.id: any_without(chat.participants, self.profile.id)
            for chat in self.chats
            if chat.type == "DIALOG" and chat.participants and chat.id != 0
        }

        await self.update_missing_users(list(chat_to_user.values()))

        def format_chat(chat: Chat, idx: int) -> str | tuple[int, str, int]:
            if chat.id == 0:
                name = "Saved messages"
            elif chat.type == "DIALOG":
                user_id = chat_to_user.get(chat.id)
                user_info = self.users_by_id.get(user_id) if user_id else None
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

    async def get_message_info(self, message: Message, tab: int = 0):
        await self.update_missing_users([message.sender])
        indent = "│" * tab
        child_indent = "│" * (tab + 1)
        print(f'{indent}┌{"─"*4} {message.time.strftime("%d.%m.%Y %H:%M:%S")}')
        print(f'{child_indent}ID: {message.id}')
        print(f'{child_indent}Type: {message.type}')
        print(f'{child_indent}Sender: [{message.sender}] {self.users_by_id[message.sender].get_name()}')
        print(f'{child_indent}Text: {message.text.replace("\n", "\n"+"│"*(tab+2))}')
        print(f'{child_indent}Attaches: { [i["baseUrl"] for i in message.attaches] }')
        print(f'{child_indent}ReactionInfo: {message.reactionInfo}')
        print(f'{indent}└{"─"*6}')

    async def norm_chat(self, chat_id: int) -> list[tuple[int, str, int]]:
        result: list[tuple[int, str, int]] = []
        chat = self.chats_by_id[chat_id]
        if not chat.participants:
            raise RuntimeError
        await self.update_missing_users(list(chat.participants))
        chat.messages = (await self.get_messages(chat_id))
        chat.update_messages()
        q = 0
        for i in chat.messages:
            sender_name = self.users_by_id[i.sender].get_name()
            date_str = i.time.strftime("%d.%m.%Y %H:%M:%S")
            attach_str = f" [{len(i.attaches)} attaches]" if i.attaches else ""
            result.append((q, f'[{date_str}] {sender_name}: {i.text}{attach_str}', i.id))
            q += 1
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
        self.chats_by_id[chat_id].info()
        norm_chat = await self.norm_chat(chat_id)
        for i in norm_chat:
            print(f'{i[0]}) {i[1]}')
        msg_id = norm_chat[read_number(min_n=0, max_n=(len(norm_chat) - 1))][2]
        msg_by_id = self.chats_by_id[chat_id].messages_by_id
        if msg_by_id:
            await self.get_message_info(msg_by_id[msg_id])
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
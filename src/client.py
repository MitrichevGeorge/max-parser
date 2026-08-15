import asyncio
import datetime
import json

from network_core import NetworkCoreMobile, NetworkCoreWS

try:
    loop = asyncio.get_running_loop()
except RuntimeError:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)


from classes import Attach, AttachType, ConfigContainer, FileAttach, IncomingCall, Message, UserProfile, Chat, ServerData, VideoAttach, NewMsgEvent
from typing import Any, Dict, List, NoReturn
from operator import itemgetter
from loguru import logger
import sys
from network_api import LoginNeedPassw, NetworkMixin, ServerError, WrongPhoneError
import captcha
from tools import RussianPhoneValidator, any_without, ask_exact, read_number, ask, sel, sel_str, bye, UniversalEncoder
from crypt import ClientVault, InvalidPasswordError, TokenModel, pw_ask
from pathlib import Path
from logserver import LOGS_PORT
import socket
from datetime import datetime

import questionary
from prompt_toolkit.patch_stdout import patch_stdout


class Client(NetworkMixin):
    profile: UserProfile
    contacts: list[UserProfile]
    chats: list[Chat]
    chats_by_id: Dict[int, Chat]
    users_by_id: Dict[int, UserProfile] = {}
    config: ConfigContainer

    def __init__(self) -> None:
        self._netw_init()
        self.token = ""

    async def finalise_auth(self):
        data = ServerData.model_validate(await self._netw_auth(self.token))
        self.profile = data.profile.contact
        self.contacts = data.contacts
        self.chats = data.chats
        self.chats_by_id = {i.id: i for i in self.chats}
        self.config = data.config

    def info(self):
        print("You:")
        self.profile.info(1)
        print("Contacts:")
        [i.info(1) for i in self.contacts]
        print("Chats:")
        [i.info(1) for i in self.chats]

    async def update_missing_users(self, user_ids: List[int]) -> None:
        missing_ids = list(set(user_ids) - self.users_by_id.keys())
        if not missing_ids:
            return

        new_infos = await self.get_infos(missing_ids)
        self.users_by_id.update({user.id: user for user in new_infos})

    async def userid_profile(self, user_id: int) -> UserProfile:
        await self.update_missing_users([user_id])
        return self.users_by_id[user_id]

    async def get_chat_name(self, chat: Chat) -> str:
        if chat.id == 0:
            return "Saved messages"
        elif chat.type == "DIALOG" and chat.participants:
            user = self.users_by_id.get(any_without(chat.participants, self.profile.id))
            return user.get_name() if user else "Unknown"
        elif chat.title:
            return chat.title
        else:
            raise ValueError(f"Missing title for chat ID {chat.id}")

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

            config = self.config.chats.get(chat.id) if self.config.chats else None
            muted = " muted" if config and config.dontDisturbUntil == -1 else ""
            
            line = f"[{chat.type}][{chat.id}] {name} - {chat.messagesCount}{muted}"
            return (idx, line, chat.id) if new_type else line

        return [format_chat(chat, idx) for idx, chat in enumerate(self.chats)]

    async def get_attach_info(self, attach: Attach, chatId: int, messageId: int) -> str:
        if isinstance(attach, FileAttach):
            return await self.get_file_url(attach.fileId, chatId, messageId)
        if isinstance(attach, VideoAttach):
            return str(await self.get_video_urls(attach.videoId, attach.token, chatId, messageId))
        return ""

    async def message_info(self, message: Message, chatId: int, tab: int = 0, ask: bool = True):
        if message.sender:
            await self.update_missing_users([message.sender])
        indent = "│" * tab
        child_indent = "│" * (tab + 1)
        print(f'{indent}┌{"─"*4} {message.time.strftime("%d.%m.%Y %H:%M:%S")} {message.link.type if message.link else ""}')
        print(f'{child_indent}ID: {message.id}')
        print(f'{child_indent}Sender: [{message.sender}] {self.users_by_id[message.sender].get_name() if message.sender else "Unknown"}')
        print(f'{child_indent}Text: {message.text.replace("\n", "\n"+"│"*(tab+2))}')
        print(f'{child_indent}Attaches: { [' '.join((i.info(), await self.get_attach_info(i, chatId, message.id))) for i in message.attaches] }')
        print(f'{child_indent}ReactionInfo: {message.reactionInfo}')
        print(f'{indent}└{"─"*6}')
        if ask:
            selected = await questionary.confirm("Mark as read?", default=False, auto_enter=True).ask_async()
            if selected:
                await self.mark_as_read(chatId, message.id)

    async def norm_chat(self, chat_id: int) -> list[tuple[int, str, int]]:
        chat = self.chats_by_id[chat_id]
        if not chat.participants:
            raise RuntimeError
        chat.messages = await self.get_messages(chat_id)
        chat.update_messages()

        unique_users = list(set(chat.participants) | {msg.sender for msg in chat.messages if msg.sender})
        # print(unique_users)
        await self.update_missing_users(unique_users)
        # print(self.users_by_id)

        result: list[tuple[int, str, int]] = []
        for index, message in enumerate(chat.messages):
            sender_name = self.users_by_id[message.sender].get_name() if message.sender else "Unknown"
            date_str = message.time.strftime("%d.%m.%Y %H:%M:%S")
            attach_str = f" [{len(message.attaches)} attaches]" if message.attaches else ""
            result.append((index, f"[{date_str}] {sender_name}: {message.text}{attach_str}", message.id))

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
        try:
            client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            client_socket.connect(('localhost', LOGS_PORT))
            print(f"Connected to logserver(port {LOGS_PORT})")
            logger.add(lambda msg: client_socket.sendall(msg.encode('utf-16')), format="{message}", level="INFO")
        except ConnectionRefusedError:
            print(f"Logserver not running(port {LOGS_PORT}). Logging here")
            logger.add(sys.stdout, colorize=True, format="<green>{time:HH:mm:ss}</green> | {level} | {message}")

    async def _handle_event(self, event: NewMsgEvent):
        print("New message:")
        await self.message_info(event.message, event.chatId, tab=1, ask=False)

    async def _handle_call(self, call: IncomingCall):
        print("Incoming call:")
        print(f"User: {(await self.userid_profile(call.callerId)).get_name()} type: {call.type}")
        print(f"Conv id: {call.conversationId} vcp: {call.vcp}")

    DATE_FMT = "%Y-%m-%d %H:%M:%S"

    def _format_token_variant(self, token) -> str:
        last = token.last_visit_at.strftime(self.DATE_FMT)
        login = token.login_at.strftime(self.DATE_FMT)
        return f"{token.username} [last: {last} logged in: {login}]"

    async def _auth_by_phone(self) -> str:
        phone_number = await ask("phone number ->", validator=RussianPhoneValidator())
        if isinstance(self, NetworkCoreMobile):
            while True:
                try:
                    # await self.disconnect()
                    # await self._netw_connect()
                    auth_token = await self.send_verify_code(phone_number)
                    break
                except WrongPhoneError as err:
                    print(err)
                    phone_number = await ask("phone number ->", validator=RussianPhoneValidator())
                except ServerError as err:
                    print(err)
                    bye()
        else:
            while True:
                captcha_url = await self.get_captcha_url(phone_number)
                try:
                    captcha_token = await captcha.solve(captcha_url)
                    await self.disconnect()
                    await self._netw_connect()
                    auth_token = await self.send_verify_code(phone_number, captcha_token)
                    break
                except WrongPhoneError as err:
                    print(err)
                    phone_number = await ask("phone number ->", validator=RussianPhoneValidator())
                except ServerError as err:
                    print(err)
                    bye()

        while True:
            try:
                verify_code = await ask("verify code ->")
                return await self.check_verify_code(auth_token, verify_code)
            except LoginNeedPassw as err:
                challenge = err.passwordChallenge
                print(f"Email: {challenge.email}")
                password = await pw_ask("2FA pass ->")
                login = await self.login_password(challenge.trackId, password)
                self.profile = login.profile.contact
                return login.token
            except ServerError as err:
                print(err, type(err))

    async def select_account(self) -> None:
        await self.disconnect()
        await self._netw_connect()

        if self._token_idx is not None:
            self.vault.tokens[self._token_idx].last_visit_at = datetime.now()
            self.vault.save()
        self._token_idx = None

        while True:
            tokens = self.vault.tokens
            options = [*map(self._format_token_variant, tokens), "Enter token", "Auth by number"]
            
            selection_idx = await sel(options, "Accounts")

            is_existing_token = selection_idx < len(tokens)
            if is_existing_token:
                self.token = tokens[selection_idx].token
            elif options[selection_idx] == "Enter token":
                self.token = await ask("token ->")
            elif options[selection_idx] == "Auth by number":
                self.token = await self._auth_by_phone()

            try:
                await self.finalise_auth()
                if not is_existing_token:
                    selected = await questionary.confirm("Save this token?", default=True, auto_enter=True).ask_async()
                    if selected:
                        new_token = TokenModel(token=self.token, login_at=datetime.now(), last_visit_at=datetime.now(), username=self.profile.get_name())
                        self.vault.tokens.append(new_token)
                        self._token_idx = len(self.vault.tokens) - 1
                else:
                    self._token_idx = selection_idx
                    self.vault.tokens[selection_idx].last_visit_at = datetime.now()

                self.vault.save()
                return

            except ServerError as err:
                print(err)
                if is_existing_token:
                    selected = await questionary.confirm("Remove this token?", default=False, auto_enter=False).ask_async()
                    if selected:
                        del self.vault.tokens[selection_idx]
                        self.vault.save()

    async def chats_list(self):
        print("Chats:")
        norm_chatlist = await self.norm_chatlist(new_type=True)
        while True:
            select = await sel(list(map(itemgetter(1), norm_chatlist))+["Back"], "Main menu -> Chats")
            if select >= len(norm_chatlist):
                return
            
            chat_id = norm_chatlist[select][2]
            if not isinstance(chat_id, int):
                raise ValueError
            chat = self.chats_by_id[chat_id]
            chat.info()
            norm_chat = await self.norm_chat(chat_id)
            
            while True:
                options = list(map(itemgetter(1), norm_chat)) + ["Send message", "Call", "Back", "Main menu", "Delete chat"]
                select = await sel(options, f"Main menu -> Chats -> {await self.get_chat_name(chat)}")

                if select < len(norm_chat):
                    msg_id = norm_chat[select][2]
                    msg_by_id = chat.messages_by_id
                    if msg_by_id:
                        message = msg_by_id[msg_id]
                        await self.message_info(message, chat_id)
                elif options[select] == "Send message":
                    text: str = await ask()
                    message = await self.send_message(chat_id, text)
                    msg_list = chat.messages
                    if msg_list:
                        msg_list.append(message)
                    else:
                        print("Message list is none")
                elif options[select] == "Call":
                    print(chat.videoConversation, chat.participants)
                    if not chat.participants:
                        raise RuntimeError
                    call = await self.begin_call([any_without(chat.participants, self.profile.id)])
                    print(f'Кароч, к webrtc надо подрубаться: {call.model_dump_json()}')
                elif options[select] == "Back":
                    break
                elif options[select] == "Main menu":
                    return
                elif options[select] == "Delete chat":
                    for_all = await questionary.confirm(f"Delete for all?", default=False, auto_enter=True).ask_async()
                    try:
                        await self.delete_chat(chat_id, for_all)
                    except ServerError as err:
                        print(err)

    async def begin(self):
        self._token_idx = None
        self.on_new_message.connect(self._handle_event)
        self.on_in_call.connect(self._handle_call)
        self.vault = ClientVault()
        await self.vault.init()
        await self._init_log()
        # Какое транспортное ядро унаследовано: Mobile (raw TLS api2.oneme.ru)
        # или Web (WebSocket api.oneme.ru).
        if isinstance(self, NetworkCoreMobile):
            print("Network core: Mobile")
        elif isinstance(self, NetworkCoreWS):
            print("Network core: Web")
        else:
            print("Network core: unknown")

    async def main_menu(self):
        await self.select_account()
        while True:
            print(f"[{self.profile.id}] {self.profile.get_name()}")
            match await sel_str(["Profile info", "Contacts", "Chats list", "Limits and config", "User infos", "Swap account", "Account settings", "Logout", "Exit"], "Main menu"):
                case "Profile info":
                    self.profile.info()
                case "Contacts":
                    print("Contacts:")
                    [i.info(1) for i in self.contacts]
                case "Chats list":
                    await self.chats_list()
                case "Limits and config":
                    self.config.server.info()
                case "User infos":
                    match await sel_str(["Search chats", "Search by number", "Get user by id", "Back"], "Main menu -> User infos"):
                        case "Search chats":
                            query = await ask("query ->")
                            result = await self.search(query)
                            if len(result) == 0:
                                print("Nothing found")
                            else:
                                for i in result:
                                    i.info()
                        case "Search by number":
                            query = await ask("phone number ->", validator=RussianPhoneValidator())
                            result = await self.search_number(query)
                            if not result:
                                print("Nothing found")
                            else:
                                result.info()
                                if await questionary.confirm(f"Send message to {result.get_name()}?", default=False, auto_enter=True).ask_async():
                                    text = await ask("message ->")
                                    msg = await self.send_message(result.id, text, False)
                                    await self.message_info(msg, result.id)
                        case "Get user by id":
                            user_id = await read_number("User id", 9_900_000, 900_000_000)
                            await self.update_missing_users([user_id])
                            if not user_id in self.users_by_id:
                                print(f"ID {user_id} not found")
                                continue
                            self.users_by_id[user_id].info()
                        case "Back":
                            pass
                case "Account settings":
                    match await sel_str(["Change name", "Get new token", "Delete account", "Back"], "Main menu -> Account settings"):
                        case "Change name":
                            name = await ask("name ->")
                            self.profile = await self.change_name(name)
                        case "Delete account":
                            if await ask_exact(f"Are u sure u want to delete account {self.profile.get_name()}? ->"):
                                await self.delete_account()
                        case "Get new token":
                            match await sel_str(["Get QR (new session)", "Approve link", "Back"], "Main menu -> Account settings -> Get new token"):
                                case "Get QR (new session)":
                                    account = Client()
                                    await account._netw_connect()
                                    req = await account.qr_auth_getid()
                                    print(f"QR link: {req.qrLink}")
                                    print(f"trackId: {req.trackId}")
                                    print("Approve it (from phone или пунктом 'Approve link'), затем жми Enter.")
                                    await ask("Press Enter to poll ->")
                                    try:
                                        result = await account.qr_auth_poll(req.trackId)
                                        print("Poll result:")
                                        print(json.dumps(result, cls=UniversalEncoder, indent=2, ensure_ascii=False))
                                        token_attrs = result.get("tokenAttrs") if isinstance(result, dict) else None
                                        if isinstance(token_attrs, dict):
                                            login = token_attrs.get("LOGIN")
                                            new_token = login.get("token") if isinstance(login, dict) else None
                                            if new_token:
                                                print(f"New token: {new_token}")
                                    except ServerError as err:
                                        print(err)
                                case "Approve link":
                                    qr_link = await ask("qr link ->")
                                    try:
                                        await self.qr_auth_approve(qr_link)
                                        print("approved")
                                    except ServerError as err:
                                        print(err)
                                case "Back":
                                    pass
                        case "Back":
                            pass
                case "Swap account":
                    await self.select_account()
                case "Logout":
                    if await questionary.confirm(f"Log out from {self.profile.get_name()}?", default=False, auto_enter=False).ask_async():
                        await self.logout()
                        print("logged out")
                        bye()
                case _:
                    bye()

    async def disconnect(self):
        if self.connection:
            await self.connection.close()
            print(f"Connection to {self.url} closed")
        if self._token_idx is not None:
            self.vault.tokens[self._token_idx].last_visit_at = datetime.now()
            self.vault.save()

async def main():
    with patch_stdout(raw=True):
        q = Tuiclient()
        await q.begin()
        try:
            await q.main_menu()
        finally:
            await q.disconnect()

if __name__ == "__main__":
    asyncio.run(main())

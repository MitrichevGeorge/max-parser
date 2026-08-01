import asyncio
from eventkit import Event
import json
import time
import uuid
import traceback
from collections import defaultdict
from typing import List, Dict, Any
from loguru import logger
from pydantic import TypeAdapter
import websockets
from python_socks.async_.asyncio import Proxy
from operator import itemgetter
from datetime import datetime
from enum import IntEnum

from classes import BeginCallResp, Chat, IncomingCall, UserProfile, Message, VideoUrls, NewMsgEvent
from convert import PacketCodec
import payloads as pl
from tools import UniversalEncoder

class ServerError(RuntimeError):
    def __init__(self, message: str = "", error: str = ""):
        super().__init__(message)
        self.error: str = error

    @classmethod
    def from_payload(cls, payload: Dict[str, Any]) -> "ServerError":
        message = (payload.get("localizedMessage") or payload.get("message") or "Unknown server error")
        error_code = str(payload.get("error", ""))
        return cls(message, error=error_code)

class WrongPhoneError(ServerError):
    pass

class Opcodes(IntEnum):
    HARTBEAT = 1
    HANDSHAKE = 6
    SEND_VERIFY_CODE = 17
    CHECK_VERIFY_CODE = 18
    GET_CAPTCHA = 224
    AUTHENTICATE = 19
    LOGOUT = 20

    SEARCH = 60
    SEARCH_BY_NUMBER = 46
    EVENT_SUBSCRIBE = 75
    GET_INFOS = 32
    GET_MESSAGES = 49
    GET_FILE_URL = 88
    GET_VIDEO_URLS = 83
    SEND_MESAGE = 64
    MARK_AS_READ = 50
    DELETE_CHAT = 52
    DELETE_ACCOUNT = 199

    INCOMING_CALL = 137
    INCOMING_MSG_EVENT = 128
    BEGIN_CALL = 78

def _save_json(file: str, data) -> None:
    with open(file, "w", encoding="utf-8") as f:
        json.dump(data, f, cls=UniversalEncoder, indent=2, ensure_ascii=False)

class NetworkMixin:
    def _netw_init(self):
        self.connection = None
        self.codec = PacketCodec()
        self.is_online = False
        self.device_id = str(uuid.uuid4())
        
        self._listeners = defaultdict(list[asyncio.Queue[Dict[str, Any]]])
        self._tasks: set[asyncio.Task] = set()
        self._semaphore = asyncio.Semaphore(32)
        
        self.on_new_message = Event()
        self.on_in_call = Event()
        self.proxy: str | None = None

    async def _recv(self):
        if not isinstance(self.connection, websockets.ClientConnection):
            raise RuntimeError("Client not connected")
        raw = await self.connection.recv()
        if not isinstance(raw, bytes):
            raise ValueError("It is not bytes")
        return self.codec.bytes_to_payload(raw)

    async def _send(self, opcode: int, payload):
        logger.info(f"↑ {opcode} {payload}")
        if not isinstance(self.connection, websockets.ClientConnection):
            raise RuntimeError("Client not connected")
        await self.connection.send(self.codec.payload_to_bytes(opcode, payload))

    async def _netw_connect(self):
        if self.proxy:
            proxy = Proxy.from_url(self.proxy)
            sock = await proxy.connect(dest_host='api.oneme.ru', dest_port=443)
            self.connection = await websockets.connect(pl.URL, sock=sock, additional_headers=pl.HEADERS)
        else:
            self.connection = await websockets.connect(pl.URL, additional_headers=pl.HEADERS)
        print(f"Successfully connected to {pl.URL}")
        self._reader_task = asyncio.create_task(self._reader_loop())
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        await self._send(Opcodes.HANDSHAKE, pl.get_device_payload(self.device_id))

    async def _netw_auth(self, token: str):
        await self._send(Opcodes.AUTHENTICATE, pl.get_auth_payload(token))
        response = await self.wait_for_opcode(Opcodes.AUTHENTICATE)
        if response["cmd"] == 1:
            return response['payload']
        raise ServerError(response["payload"].get("localizedMessage") or response["payload"].get("message"))

    async def _process_message(self, raw):
        async with self._semaphore:
            try:
                if raw == "ping":
                    if not isinstance(self.connection, websockets.ClientConnection):
                        raise RuntimeError("Client not connected")
                    await self.connection.send("pong")
                    return
                
                if not isinstance(raw, bytes):
                    print("It is not bytes")
                    return
                
                msg = self.codec.bytes_to_payload(raw)
                logger.info(f"↓ {msg}")
                cmd = msg.get("cmd")
                opcode = msg.get("opcode")
                if isinstance(cmd, int) and cmd > 0:
                    if opcode in self._listeners:
                        for queue in list(self._listeners[opcode]):
                            try:
                                queue.put_nowait(msg)
                            except asyncio.QueueFull:
                                print(f"Queue for opcode {opcode} is full. Message dropped.")
                
                if opcode == Opcodes.INCOMING_MSG_EVENT:
                    event = NewMsgEvent.model_validate(msg["payload"])
                    self.on_new_message.emit(event)

                if opcode == Opcodes.INCOMING_CALL:
                    call = IncomingCall.model_validate(msg["payload"])
                    self.on_in_call(call)


            except Exception:
                traceback.print_exc()
    
    async def _reader_loop(self):
        if not isinstance(self.connection, websockets.ClientConnection):
            raise RuntimeError("Client not connected")
        try:
            print("reader loop started")
            async for raw in self.connection:
                try:
                    task = asyncio.create_task(self._process_message(raw))
                    self._tasks.add(task)
                    task.add_done_callback(self._tasks.discard)
                except Exception:
                    traceback.print_exc()
                    continue
        except asyncio.CancelledError:
            pass
        finally:
            if self._tasks:
                print("finishing tasks")
                await asyncio.gather(*self._tasks, return_exceptions=True)
            print("reader loop stopped")

    async def wait_for_opcode(self, opcode) -> Dict[str, Any]:
        queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue()
        self._listeners[opcode].append(queue)
        
        try:
            message = await queue.get()
            return message
        finally:
            self._listeners[opcode].remove(queue)
            if not self._listeners[opcode]:
                del self._listeners[opcode]

    async def _heartbeat_loop(self) -> None:
        try:
            print("hartbeat loop started")
            while True:
                await asyncio.sleep(30)
                await self._send(1, {'interactive': self.is_online})
        except asyncio.CancelledError:
            print("Heartbeat loop stopped")
        except Exception as e:
            print(f"Heartbeat error: {e}")

    async def disconnect(self) -> None:
        if hasattr(self, '_heartbeat_task') and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
        if hasattr(self, '_reader_task') and not self._reader_task.done():
            self._reader_task.cancel()
        
        if self.connection:
            await self.connection.close()
            print(f"Connection to {pl.URL} closed")

    async def request(self, opcode: int, payload) -> Dict[str, Any]:
        await self._send(opcode, payload)
        return await self.wait_for_opcode(opcode)

    async def search(self, query: str, count: int = 40) -> list[Chat]:
        response = await self.request(Opcodes.SEARCH, {'query': query, 'count': count })
        adapter = TypeAdapter(list[Chat])
        return adapter.validate_python(map(itemgetter("chat"), response["payload"]["result"]))

    async def search_number(self, query: str) -> UserProfile | None:
        response = await self.request(Opcodes.SEARCH_BY_NUMBER, {'phone': query })
        if response["payload"].get("error") == "not.found":
            return None
        return UserProfile.model_validate(response["payload"]["contact"])

    async def get_infos(self, contactIds: List[int]) -> List[UserProfile]:
        response = await self.request(Opcodes.GET_INFOS, {'contactIds': contactIds})
        if response['cmd'] == 1:
            adapter = TypeAdapter(List[UserProfile])
            return adapter.validate_python(response["payload"]["contacts"])
        raise ServerError.from_payload(response["payload"])

    async def get_messages(self, chatID: int, dFrom: datetime = datetime.now(), backward: int = 100, forward: int = 100) -> List[Message]:
        response = await self.request(Opcodes.GET_MESSAGES, {'chatId': chatID, 'from': int(dFrom.timestamp() * 1000), 'forward': forward, 'backward': backward, 'getMessages': True})
        adapter = TypeAdapter(List[Message])
        return adapter.validate_python(response["payload"]["messages"])

    async def get_file_url(self, fileId: int, chatId: int, messageId: int) -> str:
        response = await self.request(Opcodes.GET_FILE_URL, {'fileId': fileId, 'chatId': chatId, 'messageId': messageId})
        return response["payload"]["url"]

    async def get_video_urls(self, videoId: int, token: str, chatId: int, messageId: int) -> VideoUrls:
        await self._send(Opcodes.GET_VIDEO_URLS, {'videoId': videoId, 'token': token, 'chatId': chatId, 'messageId': messageId})
        response = await self.wait_for_opcode(Opcodes.GET_VIDEO_URLS)
        return VideoUrls.model_validate(response["payload"])

    async def send_message(self, chatId: int, text: str, notify: bool = True) -> Message:
        cid = -(time.time_ns() // 1_000_000)
        response = await self.request(Opcodes.SEND_MESAGE, {'chatId': chatId, 'message': {'text': text, 'cid': cid, 'elements': [], 'attaches': []}, 'notify': notify})
        if response["cmd"] == 1:
            return Message.model_validate(response["payload"]["message"])
        raise ServerError.from_payload(response["payload"])

    async def delete_chat(self, chatId: int, forAll: bool = True) -> None:
        last_time = time.time_ns() // 1_000_000
        response = await self.request(Opcodes.DELETE_CHAT, {'chatId': chatId, 'lastEventTime': last_time, 'forAll': forAll})
        if response["cmd"] == 1:
            return
        raise ServerError.from_payload(response["payload"])

    async def get_captcha_url(self, phoneNumber: str) -> str:
        response = await self.request(Opcodes.GET_CAPTCHA, { 'source': 'auth', 'identifier': phoneNumber })
        return response["payload"]["link"]

    async def send_verify_code(self, phoneNumber: str, captchaToken: str) -> str:
        response = await self.request(Opcodes.SEND_VERIFY_CODE, { 'phone': phoneNumber, 'type': 'RESEND', 'language': 'ru', 'captchaToken': captchaToken })
        if response["cmd"] == 1:
            return response["payload"]["token"]
        if response["payload"]["error"] == "error.phone.wrong":
            raise WrongPhoneError(response["payload"]["message"])
        raise ServerError.from_payload(response["payload"])

    async def check_verify_code(self, token: str, verifyCode: str) -> str:
        response = await self.request(Opcodes.CHECK_VERIFY_CODE, {'token': token, 'verifyCode': verifyCode, 'authTokenType': 'CHECK_CODE'})
        if response["cmd"] == 1:
            return response["payload"]["tokenAttrs"]["LOGIN"]["token"]
        raise ServerError.from_payload(response["payload"])

    async def logout(self) -> None:
        response = await self.request(Opcodes.LOGOUT, { })
        if response["cmd"] == 1:
            return
        raise ServerError.from_payload(response["payload"])

    async def delete_account(self) -> None:
        response = await self.request(Opcodes.DELETE_ACCOUNT, {'delete': True, 'type': 0})
        if response["cmd"] == 1:
            return
        raise ServerError.from_payload(response["payload"])

    async def mark_as_read(self, chatId: int, messageId: int) -> None:
        response = await self.request(Opcodes.MARK_AS_READ, {'type': 'READ_MESSAGE', 'chatId': chatId, 'messageId': messageId, 'mark': int(time.time() * 1000)})
        if response["cmd"] == 1:
            return
        raise ServerError.from_payload(response["payload"])

    async def event_subscribe(self, chatId: int, subscribe: bool = True) -> None:
        response = await self.request(Opcodes.EVENT_SUBSCRIBE, {'type': 'READ_MESSAGE', 'chatId': chatId, 'subscribe': subscribe})
        if response["cmd"] == 1:
            return
        raise ServerError.from_payload(response["payload"])

    async def begin_call(self, calleeIds: List[int], conversationId: str | None = None) -> BeginCallResp:
        conversationId = conversationId or str(uuid.uuid4())
        response = await self.request(Opcodes.BEGIN_CALL, {'conversationId': conversationId, 'calleeIds': calleeIds, 'internalParams': pl.get_call_payload(self.device_id), 'isVideo': False})
        if response["cmd"] == 1:
            return BeginCallResp.model_validate(response["payload"])
        raise ServerError.from_payload(response["payload"])



# open("src/w3.json", "w").write(json.dumps(response, cls=UniversalEncoder, indent=2))


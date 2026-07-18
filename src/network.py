# Стандартные библиотеки
import asyncio
import base64
import json
import time
import uuid
import traceback
from collections import defaultdict
from typing import List
from loguru import logger
from pydantic import TypeAdapter
import websockets
from operator import itemgetter
from datetime import datetime
from enum import IntEnum

from classes import Chat, ConfigContainer, ServerData, UserProfile, Message
from convert import PacketCodec
import payloads as pl
from settings import stg
from tools import UniversalEncoder

class Opcodes(IntEnum):
    HARTBEAT = 1
    HANDSHAKE = 6
    SEND_VERIFY_CODE = 17
    CHECK_VERIFY_CODE = 18
    AUTHENTICATE = 19
    AUTH_CONFIRM = 20

    SEARCH = 60
    SEARCH_BY_NUMBER = 46
    GET_INFOS = 32
    GET_MESSAGES = 49
    GET_FILE_URL = 88
    SEND_MESAGE = 64
    DEELETE_CHAT = 52

def _save_json(file: str, data) -> None:
    with open(file, "w", encoding="utf-8") as f:
        json.dump(data, f, cls=UniversalEncoder, indent=2, ensure_ascii=False)

class NetworkMixin:
    def _netw_init(self):
        self.connection = None
        self.codec = PacketCodec()
        self.is_online = False
        
        self._listeners = defaultdict(list)
        self._tasks: set[asyncio.Task] = set()
        self._semaphore = asyncio.Semaphore(32)

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
        self.connection = await websockets.connect(pl.URL, additional_headers=pl.HEADERS)
        print(f"Successfully connected to {pl.URL}")
        await self._send(Opcodes.HANDSHAKE, pl.get_device_payload(str(uuid.uuid4())))
        await self._send(Opcodes.AUTHENTICATE, pl.get_auth_payload(stg.ONEME_AUTH["token"]))
        await self.connection.recv()
        data = (await self._recv())['payload']
        self._reader_task = asyncio.create_task(self._reader_loop())
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        return data

    async def _auth(self, phone_number: str):
        await self._send(Opcodes.SEND_VERIFY_CODE, { "phone": phone_number, "type": "START_AUTH", "language": "ru" })

    async def _process_message(self, raw):
        async with self._semaphore:
            try:
                if not isinstance(raw, bytes):
                    print("It is not bytes")
                    return
                
                msg = self.codec.bytes_to_payload(raw)
                logger.info(f"↓ {msg}")
                cmd = msg.get("cmd")
                if isinstance(cmd, int) and cmd > 0:
                    opcode = msg.get("opcode")
                    if opcode in self._listeners:
                        for queue in list(self._listeners[opcode]):
                            try:
                                queue.put_nowait(msg)
                            except asyncio.QueueFull:
                                print(f"Queue for opcode {opcode} is full. Message dropped.")
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

    async def wait_for_opcode(self, opcode):
        queue = asyncio.Queue()
        self._listeners[opcode].append(queue)
        
        try:
            message = await queue.get()
            return message
        finally:
            self._listeners[opcode].remove(queue)
            if not self._listeners[opcode]:
                del self._listeners[opcode]

    async def _heartbeat_loop(self):
        try:
            print("hartbeat loop started")
            while True:
                await asyncio.sleep(30)
                await self._send(1, {'interactive': self.is_online})
        except asyncio.CancelledError:
            print("Heartbeat loop stopped")
        except Exception as e:
            print(f"Heartbeat error: {e}")

    async def disconnect(self):
        if hasattr(self, '_heartbeat_task') and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
        if hasattr(self, '_reader_task') and not self._reader_task.done():
            self._reader_task.cancel()
        
        if self.connection:
            await self.connection.close()
            print(f"Connection to {pl.URL} closed")

    async def search(self, query: str, count: int = 40) -> list[Chat]:
        await self._send(Opcodes.SEARCH, {'query': query, 'count': count })
        response = await self.wait_for_opcode(Opcodes.SEARCH)
        adapter = TypeAdapter(list[Chat])
        return adapter.validate_python(map(itemgetter("chat"), response["payload"]["result"]))

    async def search_number(self, query: str) -> UserProfile | None:
        await self._send(Opcodes.SEARCH_BY_NUMBER, {'phone': query })
        response = await self.wait_for_opcode(Opcodes.SEARCH_BY_NUMBER)
        if response["payload"].get("error") == "not.found":
            return None
        return UserProfile.model_validate(response["payload"]["contact"])

    async def get_infos(self, contactIds: List[int]) -> List[UserProfile]:
        await self._send(Opcodes.GET_INFOS, {'contactIds': contactIds})
        response = await self.wait_for_opcode(Opcodes.GET_INFOS)
        if response['cmd'] == 1:
            adapter = TypeAdapter(List[UserProfile])
            return adapter.validate_python(response["payload"]["contacts"])
        raise RuntimeError

    async def get_messages(self, chatID: int, d_from: datetime = datetime.now(), backward: int = 60) -> List[Message]:
        await self._send(Opcodes.GET_MESSAGES, {'chatId': chatID, 'from': int(d_from.timestamp() * 1000), 'forward': 0, 'backward': backward, 'getMessages': True})
        response = await self.wait_for_opcode(Opcodes.GET_MESSAGES)
        adapter = TypeAdapter(List[Message])
        open("src/w3.json", "w").write(json.dumps(response, cls=UniversalEncoder, indent=2))
        return adapter.validate_python(response["payload"]["messages"])

    async def get_file_url(self, fileId: int, chatId: int, messageId: int) -> str:
        await self._send(Opcodes.GET_FILE_URL, {'fileId': fileId, 'chatId': chatId, 'messageId': messageId})
        response = await self.wait_for_opcode(Opcodes.GET_FILE_URL)
        return response["payload"]["url"]

    async def send_message(self, chatId: int, text: str, notify: bool = True) -> Message:
        cid = -(time.time_ns() // 1_000_000)
        await self._send(Opcodes.SEND_MESAGE, {'chatId': chatId, 'message': {'text': text, 'cid': cid, 'elements': [], 'attaches': []}, 'notify': notify})
        response = await self.wait_for_opcode(Opcodes.SEND_MESAGE)
        return Message.model_validate(response["payload"]["message"])

    async def delete_chat(self, chatId: int, forAll: bool = True) -> None:
        last_time = time.time_ns() // 1_000_000
        await self._send(Opcodes.DEELETE_CHAT, {'chatId': chatId, 'lastEventTime': last_time, 'forAll': forAll})
        response = await self.wait_for_opcode(Opcodes.DEELETE_CHAT)
        if response["cmd"] == 1:
            return
        raise RuntimeError(response["payload"].get("error"))



# open("src/w3.json", "w").write(json.dumps(response, cls=UniversalEncoder, indent=2))

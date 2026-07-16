# Стандартные библиотеки
import asyncio
import base64
import json
import time
import traceback
from collections import defaultdict
from typing import List
from loguru import logger
from pydantic import TypeAdapter
import websockets
from operator import itemgetter
from datetime import datetime

from classes import Chat, ConfigContainer, ServerData, UserProfile, Message
from convert import PacketCodec
import payloads as pl
from settings import stg
from tools import UniversalEncoder


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
        await self._send(6, pl.get_device_payload(stg.ONEME_DEVICE_ID))
        await self._send(19, pl.get_auth_payload(stg.ONEME_AUTH["token"]))
        await self.connection.recv()
        data = (await self._recv())['payload']
        self._reader_task = asyncio.create_task(self._reader_loop())
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        return data

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
        await self._send(60, {'query': query, 'count': count })
        response = await self.wait_for_opcode(60)
        adapter = TypeAdapter(list[Chat])
        return adapter.validate_python(map(itemgetter("chat"), response["payload"]["result"]))

    async def get_infos(self, contactIds: List[int]) -> List[UserProfile]:
        await self._send(32, {'contactIds': contactIds})
        response = await self.wait_for_opcode(32)
        if response['cmd'] == 1:
            adapter = TypeAdapter(List[UserProfile])
            return adapter.validate_python(response["payload"]["contacts"])
        raise RuntimeError

    async def get_messages(self, chatID: int, d_from: datetime = datetime.now(), backward: int = 60) -> List[Message]:
        await self._send(49, {'chatId': chatID, 'from': int(d_from.timestamp() * 1000), 'forward': 0, 'backward': backward, 'getMessages': True})
        response = await self.wait_for_opcode(49)
        adapter = TypeAdapter(List[Message])
        # open("src/w3.json", "w").write(json.dumps(response, cls=UniversalEncoder, indent=2))
        return adapter.validate_python(response["payload"]["messages"])

    async def get_file_url(self, fileId: int, chatId: int, messageId: int) -> str:
        await self._send(88, {'fileId': fileId, 'chatId': chatId, 'messageId': messageId})
        response = await self.wait_for_opcode(88)
        return response["payload"]["url"]

    async def send_message(self, chatId: int, text: str, notify: bool = True) -> Message:
        cid = -(time.time_ns() // 1_000_000)
        await self._send(64, {'chatId': chatId, 'message': {'text': text, 'cid': cid, 'elements': [], 'attaches': []}, 'notify': notify})
        response = await self.wait_for_opcode(64)
        return Message.model_validate(response["payload"]["message"])

        # {'magic': 10, 'cmd': 0, 'seq': 30, 'opcode': 64, 'payload': {'chatId': 467209839, 'message': {'text': 'text', 'cid': -1784067252800, 'elements': [], 'attaches': []}, 'notify': True}}
        # {'magic': 10, 'cmd': 1, 'seq': 30, 'opcode': 64, 'payload': {'chatId': 467209839, 'message': {'id': 116920631487452673, 'time': 1784067252921, 'type': 'USER', 'sender': 137594677, 'cid': -1784067252800, 'text': 'text', 'attaches': []}, 'unread': 0, 'mark': 1784067252921}}



import asyncio
import uuid
import json
import random
import traceback
import websockets
from pathlib import Path
from typing import List, Dict, Any
from collections import defaultdict
from abc import ABC, abstractmethod
from loguru import logger
from python_socks.async_.asyncio import Proxy
from convert import PacketCodec

import ssl
import lz4
import msgpack

MOBILE_HOST = "api2.oneme.ru"
MOBILE_PORT = 443

MOBILE_HEADERS = {
    "User-Agent": "okhttp/4.12.0",
    "Accept-Encoding": "gzip, deflate, br",
}

MOBILE_DEFAULT_USER_AGENT = {
    "deviceType": "ANDROID",
    "pushDeviceType": "GCM",
    "appVersion": "26.25.0",
    "buildNumber": 6790,
    "arch": "arm64-v8a",
    "osVersion": "Android 14",
    "locale": "ru",
    "deviceLocale": "ru",
    "deviceName": "Samsung SM-G991B",
    "screen": "xhdpi 420dpi 1080x2400",
    "timezone": "Europe/Moscow",
}

BASE_DIR = Path(__file__).resolve().parent
SSL_PATH = BASE_DIR / "assets" / "russian-trusted-root.crt"


def _mobile_ssl_context() -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.load_verify_locations(SSL_PATH)
    return ctx


def get_mobile_device_payload(device_id: str) -> dict:
    return {
        "userAgent": MOBILE_DEFAULT_USER_AGENT,
        "deviceId": device_id,
        "clientSessionId": random.getrandbits(63),
    }


async def _read_exact(reader: asyncio.StreamReader, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = await reader.read(n - len(buf))
        if not chunk:
            raise ConnectionError(f"EOF after {len(buf)} bytes, expected {n}")
        buf.extend(chunk)
    return bytes(buf)


PC_HEADERS = {
    "Host": "ws-api.oneme.ru",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_12_4) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/80.0.3987.93 Safari/537.36",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9,ru-RU;q=0.8,ru;q=0.7",
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "Sec-WebSocket-Version": "13",
    "Origin": "https://web.max.ru",
    "Sec-WebSocket-Extensions": "permessage-deflate",
    "Sec-Fetch-Storage-Access": "none",
    "Sec-GPC": "1",
    "Connection": "Upgrade",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "websocket",
    "Sec-Fetch-Site": "cross-site",
    "Pragma": "no-cache",
    "Cache-Control": "no-cache",
    "Upgrade": "websocket",
}

PC_USER_AGENT = {
    "deviceType": "WEB",
    "pushDeviceType": "WEBPUSH",
    "locale": "en",
    "deviceLocale": "en",
    "osVersion": "Mac",
    "deviceName": "Chrome",
    "headerUserAgent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_12_4) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/80.0.3987.93 Safari/537.36",
    "appVersion": "26.6.20",
    "screen": "1200x1920 1.0x",
    "timezone": "Europe/Moscow",
}


def get_device_payload(device_id: str, user_agent: Dict[str, str]) -> dict:
    return {
        "userAgent": user_agent,
        "deviceId": device_id,
    }


def get_auth_payload(token: str, chats_count: int = 60) -> dict:
    return {
        "token": token,
        "chatsCount": chats_count,
        "interactive": True,
        "chatsSync": 0,
        "contactsSync": 0,
        "presenceSync": -1,
        "draftsSync": 0,
    }


def get_call_payload(device_id: str) -> str:
    return json.dumps({
        "deviceId": device_id,
        "sdkVersion": "2.8.11-beta.7",
        "clientAppKey": "CNHIJPLGDIHBABABA",
        "platform": "WEB",
        "protocolVersion": 5,
        "domainId": "",
        "capabilities": "2A03F",
    })


class NetworkCore(ABC):
    def _core_init(self) -> None:
        self.url: str = ""
        self.headers: dict = {}
        self.connection: Any | None = None

        self.codec = PacketCodec()
        self.device_id = str(uuid.uuid4())

        self.proxy: str | None = None
        self.is_online = False

        self._listeners: Dict[Any, List[asyncio.Queue[Dict[str, Any]]]] = defaultdict(list)
        self._tasks: set[asyncio.Task] = set()
        self._semaphore = asyncio.Semaphore(32)

        self._reader_task: asyncio.Task | None = None
        self._heartbeat_task: asyncio.Task | None = None

    @abstractmethod
    async def connect(self) -> None:
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        ...

    @abstractmethod
    async def _send(self, opcode: int, payload: Any) -> None:
        ...

    async def _process_message(self, msg: Dict[str, Any]) -> None:
        async with self._semaphore:
            try:
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

                await self._on_message_received(msg)

            except Exception:
                traceback.print_exc()

    async def _on_message_received(self, msg: Dict[str, Any]) -> None:
        pass

    @abstractmethod
    async def _reader_loop(self) -> None:
        ...

    async def _heartbeat_loop(self) -> None:
        try:
            print("heartbeat loop started")
            while True:
                await asyncio.sleep(30)
                await self._send(1, {"interactive": self.is_online})
        except asyncio.CancelledError:
            print("Heartbeat loop stopped")
        except Exception as e:
            print(f"Heartbeat error: {e}")

    async def wait_for_opcode(self, opcode: Any) -> Dict[str, Any]:
        queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue()
        self._listeners[opcode].append(queue)

        try:
            message = await queue.get()
            return message
        finally:
            self._listeners[opcode].remove(queue)
            if not self._listeners[opcode]:
                del self._listeners[opcode]

    async def request(self, opcode: int, payload: Any) -> Dict[str, Any]:
        await self._send(opcode, payload)
        return await self.wait_for_opcode(opcode)


class NetworkCoreWS(NetworkCore):
    def _core_init(self) -> None:
        super()._core_init()
        self.url = "wss://api.oneme.ru/websocket"
        self.headers = PC_HEADERS

    async def connect(self) -> None:
        if self.proxy:
            proxy = Proxy.from_url(self.proxy)
            sock = await proxy.connect(dest_host="api.oneme.ru", dest_port=443)
            self.connection = await websockets.connect(self.url, sock=sock, additional_headers=self.headers)
        else:
            self.connection = await websockets.connect(self.url, additional_headers=self.headers)

        print(f"Successfully connected to {self.url}")
        self._reader_task = asyncio.create_task(self._reader_loop())
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    async def disconnect(self) -> None:
        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()

        if self.connection:
            await self.connection.close()
            print(f"Connection to {self.url} closed")

    async def _send(self, opcode: int, payload: Any) -> None:
        logger.info(f"↑ {opcode} {payload}")
        if not isinstance(self.connection, websockets.ClientConnection):
            raise RuntimeError("Client not connected")
        await self.connection.send(self.codec.payload_to_bytes(opcode, payload))

    async def _reader_loop(self) -> None:
        if not isinstance(self.connection, websockets.ClientConnection):
            raise RuntimeError("Client not connected")
        try:
            print("reader loop started")
            async for raw in self.connection:
                if raw == "ping":
                    await self.connection.send("pong")
                    continue

                if not isinstance(raw, bytes):
                    print("It is not bytes")
                    continue

                msg = self.codec.bytes_to_payload(raw)
                try:
                    task = asyncio.create_task(self._process_message(msg))
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


class NetworkCoreMobile(NetworkCore):
    def _core_init(self) -> None:
        super()._core_init()

        self.url = f"wss://{MOBILE_HOST}:{MOBILE_PORT}"
        self.headers = MOBILE_HEADERS

        self.host = MOBILE_HOST
        self.port = MOBILE_PORT
        self.ssl_context = _mobile_ssl_context()

        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

    async def connect(self) -> None:
        if self.proxy:
            proxy = Proxy.from_url(self.proxy)
            sock = await proxy.connect(dest_host=self.host, dest_port=self.port)
            self._reader, self._writer = await asyncio.open_connection(
                sock=sock,
                ssl=self.ssl_context,
                server_hostname=self.host,
            )
        else:
            self._reader, self._writer = await asyncio.open_connection(
                self.host,
                self.port,
                ssl=self.ssl_context,
            )

        print(f"Successfully connected to {self.host}:{self.port}")
        self._reader_task = asyncio.create_task(self._reader_loop())
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    async def disconnect(self) -> None:
        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()

        if self._writer:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except Exception:
                pass
            print(f"Connection to {self.host}:{self.port} closed")

    async def _send(self, opcode: int, payload: Any) -> None:
        logger.info(f"↑ {opcode} {payload}")
        if self._writer is None:
            raise RuntimeError("Client not connected")
        packet = self.codec.payload_to_bytes(opcode, payload)
        self._writer.write(packet)
        await self._writer.drain()

    async def _recv_packet(self) -> Dict[str, Any]:
        if self._reader is None:
            raise RuntimeError("Client not connected")

        header = await _read_exact(self._reader, self.codec.HEADER_SIZE)
        magic, cmd, seq, opcode, compression = self.codec.form_head.unpack_from(header[:7])
        length = (header[7] << 16) | (header[8] << 8) | header[9]

        payload_bytes = b""
        if length:
            payload_bytes = await _read_exact(self._reader, length)

        if compression > 0:
            payload_bytes = lz4.block.decompress(payload_bytes, uncompressed_size=length * compression)

        decoded = None
        if payload_bytes:
            decoded = msgpack.unpackb(
                payload_bytes,
                raw=False,
                strict_map_key=False,
                ext_hook=self.codec._ext_hook,
            )

        return {
            "magic": magic,
            "cmd": cmd,
            "seq": seq,
            "opcode": opcode,
            "compression": compression,
            "payload": decoded,
        }

    async def _reader_loop(self) -> None:
        try:
            print("reader loop started")
            while True:
                msg = await self._recv_packet()
                try:
                    task = asyncio.create_task(self._process_message(msg))
                    self._tasks.add(task)
                    task.add_done_callback(self._tasks.discard)
                except Exception:
                    traceback.print_exc()
                    continue
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"Reader loop error: {e}")
            traceback.print_exc()
        finally:
            if self._tasks:
                print("finishing tasks")
                await asyncio.gather(*self._tasks, return_exceptions=True)
            print("reader loop stopped")
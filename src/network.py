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

from classes import Chat, ConfigContainer, ServerData, UserProfile, Message, VideoUrls
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
    GET_VIDEO_URLS = 83
    SEND_MESAGE = 64
    DEELETE_CHAT = 52
    GET_CAPTCHA = 224

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
        self._reader_task = asyncio.create_task(self._reader_loop())
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        await self._send(Opcodes.HANDSHAKE, pl.get_device_payload(str(uuid.uuid4())))
        # await self._auth("+79163536608")
        # exit(0)
        await self._send(Opcodes.AUTHENTICATE, pl.get_auth_payload(stg.ONEME_AUTH["token"]))
        data = (await self.wait_for_opcode(Opcodes.AUTHENTICATE))['payload']
        return data

    async def _auth(self, phone_number: str):
        # {'magic': 10, 'cmd': 0, 'seq': 6, 'opcode': 224, 'payload': {'source': 'auth', 'identifier': '+71234567890'}}
        # {'magic': 10, 'cmd': 1, 'seq': 6, 'opcode': 224, 'payload': {'link': 'https://id.vk.ru/not_robot_captcha?domain=web.max.ru&session_token=eyJhbGciOiJBMjU2R0NNS1ciLCJlbmMiOiJBMjU2R0NNIiwiaXYiOiJ5anJzUU9pRkhyU0VhTXJhIiwia2lkIjoiNGI0ZTJhZDQtNTcwMC00NGYyLTk5MTQtOWFhZmZjYTRlM2Y2IiwidGFnIjoidld6X1pLSWZtUjNuTjB4d0pKNXVmUSIsInppcCI6IkRFRiJ9.0BUo6Q-RHlYzmt8r6G27383v2xAxrxA4na_mxE8NQx4.PzXYATQBHlavRYKg.QEZs6nwpp5PIqE7sm0q8I4-YvLTUxg39Hnl5PgSXEw4FiPtBHOMz8xjvhBfX9dNjvDeTFZq-Rff1NPS8bKA3wfxpoH-LBWEO6OLu8zUePGk15F-VcBpJg2qPb5oYT_5YI-BwxcMqaGWnfwPl9UJ7RcAKR2fNsoZZwFK9nifF99kbEbLBbVrOTt6S0I4rz3NDue2zR1-VajWkzgF_O6XYxYl5CdvT98PI7D-QNXU49x7mEr4aFngWWQu0gD0if3SaNjhcrjfL9M5XiXq_gdM3xnFviYWWhKA5pF1bWNbiNIMPIETIRQXnsOg5reb9s6FLNuAL0y7HA_v5lSK959e3MyQyawNdFIrHVZyfjZZ0PvGA-dYpoNHcfJWIg-I7d9qIYq_CGbi2--HDomZxY1c4KSn3T0Tzd67v26UpfL9VaN7iPOHmULFtCqOueVNCK_d2m9GTXR1zvmX7Xl443alrDcJ-UeIf1IsXuvkTumjw8mF6DzVi9C884aWn81Aj44oH4Qu2KLbZPK9UAQuXArsqWmt90nV3ZicKezSop-Be7nIWhfX0Mu6QF5uy-98JYCvRJEUCOP6jmdqotCsAvICO1UuPSTuC-74aM71ZfVHxumsREZeyogngRgM5jYsB7NRFDen6eTM4_1uV8CUQHSbFY04JuMRnymS6_VFWpm734Npv_bU2S_9-uSg-ppuItRdc9f_WdmCGxGFwEP_AGoHeG6buESLHWIip3UQyWEzMAmMyocH-qONSMDi-Qle-th_Hfe7O944zHOyoKFiFkgsPpYmbnr1S6LDCpCrIBDYg06aNKwUPFGluNiF2p-StSUOBmfbnWy6GjjIsk_qgl-1SgjrQr4hwRWdqgI7hvK4IVZJ5Zh976i5Hf3H1qzX2sh5fjQoDoHxUrLlPzfSARJHcLlBPoIXgKwI_CMmwkXsXBeME9kUAgMv1QgIH7l25WClI0v8coHTTaj52ck8cTMLCmQDhhd5POA1sjBa-jYbQfDvDu42hgZ98InpXvnWKuC6mXfmCI0syA3jqp8pJEHYxTsL4BrqnrsboJ-MGKbDAod9r_dqfGG-K41-yiKi4EUqTMbFKSwjC_CwSUNOJ2_iBA39KbSphjrh577ViPAaj9IP13217CmFS1oOhJpI4NaI6zivzd05x6U6oVvUVY6GC3A_99Kv527OCcLGdSXlc7eLF-1R4bU1yM_n-pik7SRyVE22PswDEd7cNUnwtpC9biFur2Aw_vPKcSbUaqkp9C9hX1AWPc67hWg.WPPrLmhXLRYi4Tn0DoiHuA&variant=popup&blank=1'}}
        
        # {'magic': 10, 'cmd': 0, 'seq': 9, 'opcode': 17, 'payload': {'phone': '+71234567890', 'type': 'RESEND', 'language': 'ru', 'captchaToken': 'eyJhbGciOiJBMjU2R0NNS1ciLCJlbmMiOiJBMjU2R0NNIiwiaXYiOiJOZTV0QXd5Y3hjU0FRUGd0Iiwia2lkIjoiOTVkYTVhYzYtNWUyMi00Y2Q5LWFhOWYtMTNiMmM4YWZiNjIxIiwidGFnIjoiRGlhN1VNRExqTHRCNWQ4Um5DVmpNQSIsInppcCI6IkRFRiJ9.nSLMrA8VQwCQeDu7wZt70k_s-Djbs3GP4VVPTgpDwzY.WicwU0YaXuJ1YqdD.6wv5E2tkgZ5Dpz5GPva9_VVbNDG-8fuXnYOPUiEng5nsCi9RhHNqqMX4p_pQ5jSX8cnbbMTFJ6VtOHw8GO30tbiunFBEKbkS8vU62HVykWVLNt5a0kmWWMU2p_ZQVSNtQ9Hq9t3dFzXzkdHtFomibEiTXUBt9jymsL4sXJQgEud6qktMvA9loEcrL6z1zRdi4LVgqH8VRwS58IPgc-8n5JetWO9UhItCr_aMBbVj1qz5X4c37DJDA3jkEwLgUXsxTojRHA4MFP-Lmhon3gX46E6zrF769rxk59lR_77F0EJg2KLc0P1mRN-IINDBTCh9n-TNw7itDj9n2UbyAyraNmlIBgHCz27qu230NMGPiJMmuPgGdTGjVtQ9zrfobHYs4Mi4AE9YA9EPmJi6CxfVi5dVq5EtHH-w_eqXhyA7RFTIUVu4ALVDReeDfjgKcUxzGTWFolc1QN81chKjYxRcilsc50C0CDeUeN0BR1_fd6s8WckrMgNRzhqsRkhkosjfpPmdbVzjcvb_whepbrLIqoPBsVRGN86r_DnSPy-TOmS5LP0CSdzUNk_WBcPHfmMMhe8aBhZXo0DzPEMiLNrO3vVLd_HTcfSdCOQxpFc4tjg1hHc8jNPn2XHbW_fr1tKtHYXLbUHIywudXSku2mpXWJf7lrMhSrhJqG8T_fXvPRWO9XMx6wbS0CIeQ0CGXketrZCmc2E1zRfDo0n6e5nJSxMu9H4Ocmb1e_5-wdsAuZLjicJEDZrjgkQi2IRVvS3ejXsX2MjVkvqjrPpe57nBb6793FJucXvqy-q77kCT04BPgPKkIbaHbvXY_07E3gGLtO3f9Aap6Yrdp1tdT5moWM_lZg26Us8gs9J4caJcABcmYphJ-ayWxvGL.IPDMH3wEq61bGbepz9kOrg'}}
        # {'magic': 10, 'cmd': 1, 'seq': 9, 'opcode': 17, 'payload': {'token': 'An_Sx6HQ9HDikySR6AtgVu82ibtb2O-LyrIq51mjpK04GNwX-krPafH8zYPJ-62yHz1fZ0Z2Ng3LahHGfq8n8Nx2WeuvGB-G-9b59ez0tHjsSd9UibMXiN8zyXfOZZPCtixththf', 'codeLength': 6, 'requestMaxDuration': 60000, 'requestCountLeft': 10, 'altActionDuration': 60000}}
        
        # {'magic': 10, 'cmd': 0, 'seq': 10, 'opcode': 18, 'payload': {'token': 'An_Sx6HQ9HDikySR6AtgVu82ibtb2O-LyrIq51mjpK04GNwX-krPafH8zYPJ-62yHz1fZ0Z2Ng3LahHGfq8n8Nx2WeuvGB-G-9b59ez0tHjsSd9UibMXiN8zyXfOZZPCtixththf', 'verifyCode': '123456', 'authTokenType': 'CHECK_CODE'}}
        # {'magic': 10, 'cmd': 3, 'seq': 10, 'opcode': 18, 'payload': {'error': 'error.code.attempt.limit', 'message': 'Code expired. Please request a new one', 'localizedMessage': 'Code expired. Please request a new one', 'title': 'Code expired. Please request a new one'}}



        await self._send(Opcodes.GET_CAPTCHA, { 'source': 'auth', 'identifier': phone_number })
        response = (await self.wait_for_opcode(Opcodes.GET_CAPTCHA))["payload"]["link"]
        print(response)
        captchaToken = input("captchaToken")
        await self._send(Opcodes.SEND_VERIFY_CODE, { 'phone': phone_number, 'type': 'RESEND', 'language': 'ru', 'captchaToken': captchaToken })
        response = await self.wait_for_opcode(Opcodes.SEND_VERIFY_CODE)
        print(response)
        open("src/w3.json", "w").write(json.dumps(response, cls=UniversalEncoder, indent=2))

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

    async def get_messages(self, chatID: int, d_from: datetime = datetime.now(), backward: int = 100) -> List[Message]:
        await self._send(Opcodes.GET_MESSAGES, {'chatId': chatID, 'from': int(d_from.timestamp() * 1000), 'forward': 0, 'backward': backward, 'getMessages': True})
        response = await self.wait_for_opcode(Opcodes.GET_MESSAGES)
        adapter = TypeAdapter(List[Message])
        return adapter.validate_python(response["payload"]["messages"])

    async def get_file_url(self, fileId: int, chatId: int, messageId: int) -> str:
        await self._send(Opcodes.GET_FILE_URL, {'fileId': fileId, 'chatId': chatId, 'messageId': messageId})
        response = await self.wait_for_opcode(Opcodes.GET_FILE_URL)
        return response["payload"]["url"]

    async def get_video_urls(self, videoId: int, token: str, chatId: int, messageId: int) -> VideoUrls:
        await self._send(Opcodes.GET_VIDEO_URLS, {'videoId': videoId, 'token': token, 'chatId': chatId, 'messageId': messageId})
        response = await self.wait_for_opcode(Opcodes.GET_VIDEO_URLS)
        return VideoUrls.model_validate(response["payload"])

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

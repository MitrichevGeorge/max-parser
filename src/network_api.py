from eventkit import Event
import json
import time
import uuid
from typing import List, Dict, Any, Union
from pydantic import TypeAdapter
from operator import itemgetter
from datetime import datetime
from enum import IntEnum

from classes import BeginCallResp, Chat, IncomingCall, QrAuthResp, UserProfile, Message, VideoUrls, NewMsgEvent
from tools import UniversalEncoder
from network_core import NetworkCoreWS, NetworkCoreMobile, get_device_payload, get_mobile_device_payload, get_call_payload, get_auth_payload, PC_USER_AGENT

class ServerError(RuntimeError):
    def __init__(self, message: Union[str, Dict[str, Any]] = "", error: str = ""):
        if isinstance(message, dict):
            payload = message
            message = (
                payload.get("localizedMessage")
                or payload.get("message")
                or "Unknown server error"
            )
            error = str(payload.get("error", ""))

        super().__init__(message)
        self.error: str = error

class QrAuthError(ServerError):
    _ERROR_MAP: Dict[str, type] = {}

    def __new__(cls, message: Union[str, Dict[str, Any]] = "", error: str = ""):
        if isinstance(message, dict) and cls is QrAuthError:
            error_code = str(message.get("error", ""))
            target = QrAuthError._ERROR_MAP.get(error_code, cls)
            return super().__new__(target)

        return super().__new__(cls)

    def __init__(self, message: Union[str, Dict[str, Any]] = "", error: str = ""):
        super().__init__(message, error)

class InvalidQr(QrAuthError):
    pass

class TrackExpired(QrAuthError):
    pass

class QrLoginDisabled(QrAuthError):
    pass

QrAuthError._ERROR_MAP["qr_link.invalid"] = InvalidQr
QrAuthError._ERROR_MAP["track.not.found"] = TrackExpired
QrAuthError._ERROR_MAP["qr_login.disabled"] = QrLoginDisabled

class WrongPhoneError(ServerError):
    pass

class SearchError(ServerError):
    pass

class SearchNotFound(SearchError):
    pass

class Opcodes(IntEnum):
    HARTBEAT = 1
    SESSION_INIT = 6
    SEND_VERIFY_CODE = 17
    CHECK_VERIFY_CODE = 18
    GET_CAPTCHA = 224
    AUTHENTICATE = 19
    LOGOUT = 20
    CHANGE_NAME = 16

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

    QR_AUTH_GETID = 288
    QR_AUTH_POLL = 289
    QR_AUTH_APPROVE = 290

class NetworkMixin(NetworkCoreWS):
    def _netw_init(self):
        self._core_init()
        self.on_new_message = Event()
        self.on_in_call = Event()

    async def _netw_connect(self):
        await self.connect()

        if isinstance(self, NetworkCoreMobile):
            payload = get_mobile_device_payload(self.device_id)
        else:
            payload = get_device_payload(self.device_id, PC_USER_AGENT)
        await self._send(Opcodes.SESSION_INIT, payload)

    async def _on_message_received(self, msg: Dict[str, Any]):
        opcode = msg.get("opcode")
        payload = msg.get("payload")

        if opcode == Opcodes.INCOMING_MSG_EVENT:
            event = NewMsgEvent.model_validate(payload)
            self.on_new_message.emit(event)

        elif opcode == Opcodes.INCOMING_CALL:
            call = IncomingCall.model_validate(payload)
            self.on_in_call(call)

    async def _netw_auth(self, token: str):
        await self._send(Opcodes.AUTHENTICATE, get_auth_payload(token))
        response = await self.wait_for_opcode(Opcodes.AUTHENTICATE)
        if response["cmd"] == 1:
            return response['payload']
        raise ServerError(response["payload"].get("localizedMessage") or response["payload"].get("message"))

    async def search(self, query: str, count: int = 40) -> list[Chat]:
        response = await self.request(Opcodes.SEARCH, {'query': query, 'count': count })
        if response["cmd"] == 1:
            adapter = TypeAdapter(list[Chat])
            return adapter.validate_python(map(itemgetter("chat"), response["payload"]["result"]))
        raise ServerError(response["payload"])

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
        raise ServerError(response["payload"])

    async def get_messages(self, chatID: int, dFrom: datetime = datetime.now(), backward: int = 100, forward: int = 100) -> List[Message]:
        response = await self.request(Opcodes.GET_MESSAGES, {'chatId': chatID, 'from': int(dFrom.timestamp() * 1000), 'forward': forward, 'backward': backward, 'getMessages': True})
        if response["cmd"] == 1:
            adapter = TypeAdapter(List[Message])
            return adapter.validate_python(response["payload"]["messages"])
        raise ServerError(response["payload"])

    async def get_file_url(self, fileId: int, chatId: int, messageId: int) -> str:
        response = await self.request(Opcodes.GET_FILE_URL, {'fileId': fileId, 'chatId': chatId, 'messageId': messageId})
        if response["cmd"] == 1:
            return response["payload"]["url"]
        raise ServerError(response["payload"])

    async def get_video_urls(self, videoId: int, token: str, chatId: int, messageId: int) -> VideoUrls:
        response = await self.request(Opcodes.GET_VIDEO_URLS, {'videoId': videoId, 'token': token, 'chatId': chatId, 'messageId': messageId})
        if response["cmd"] == 1:
            return VideoUrls.model_validate(response["payload"])
        raise ServerError(response["payload"])

    async def send_message(self, chatId: int, text: str, notify: bool = True) -> Message:
        cid = -(time.time_ns() // 1_000_000)
        response = await self.request(Opcodes.SEND_MESAGE, {'chatId': chatId, 'message': {'text': text, 'cid': cid, 'elements': [], 'attaches': []}, 'notify': notify})
        if response["cmd"] == 1:
            return Message.model_validate(response["payload"]["message"])
        raise ServerError(response["payload"])

    async def delete_chat(self, chatId: int, forAll: bool = True) -> None:
        last_time = time.time_ns() // 1_000_000
        response = await self.request(Opcodes.DELETE_CHAT, {'chatId': chatId, 'lastEventTime': last_time, 'forAll': forAll})
        if response["cmd"] == 1:
            return
        raise ServerError(response["payload"])

    async def get_captcha_url(self, phoneNumber: str) -> str:
        response = await self.request(Opcodes.GET_CAPTCHA, { 'source': 'auth', 'identifier': phoneNumber })
        return response["payload"]["link"]

    async def send_verify_code(self, phoneNumber: str, captchaToken: str | None = None) -> str:
        if captchaToken is None:
            response = await self.request(Opcodes.SEND_VERIFY_CODE, { 'phone': phoneNumber, 'type': 'START_AUTH', 'language': 'ru' })
        else:
            response = await self.request(Opcodes.SEND_VERIFY_CODE, { 'phone': phoneNumber, 'type': 'RESEND', 'language': 'ru', 'captchaToken': captchaToken })
        if response["cmd"] == 1:
            return response["payload"]["token"]
        if response["payload"]["error"] == "error.phone.wrong":
            raise WrongPhoneError(response["payload"]["message"])
        raise ServerError(response["payload"])

    async def check_verify_code(self, token: str, verifyCode: str) -> str:
        response = await self.request(Opcodes.CHECK_VERIFY_CODE, {'token': token, 'verifyCode': verifyCode, 'authTokenType': 'CHECK_CODE'})
        if response["cmd"] == 1:
            return response["payload"]["tokenAttrs"]["LOGIN"]["token"]
        raise ServerError(response["payload"])

    async def logout(self) -> None:
        response = await self.request(Opcodes.LOGOUT, { })
        if response["cmd"] == 1:
            return
        raise ServerError(response["payload"])

    async def delete_account(self) -> None:
        response = await self.request(Opcodes.DELETE_ACCOUNT, {'delete': True, 'type': 0})
        if response["cmd"] == 1:
            return
        raise ServerError(response["payload"])

    async def mark_as_read(self, chatId: int, messageId: int) -> None:
        response = await self.request(Opcodes.MARK_AS_READ, {'type': 'READ_MESSAGE', 'chatId': chatId, 'messageId': messageId, 'mark': int(time.time() * 1000)})
        if response["cmd"] == 1:
            return
        raise ServerError(response["payload"])

    async def event_subscribe(self, chatId: int, subscribe: bool = True) -> None:
        response = await self.request(Opcodes.EVENT_SUBSCRIBE, {'type': 'READ_MESSAGE', 'chatId': chatId, 'subscribe': subscribe})
        if response["cmd"] == 1:
            return
        raise ServerError(response["payload"])

    async def begin_call(self, calleeIds: List[int], conversationId: str | None = None) -> BeginCallResp:
        conversationId = conversationId or str(uuid.uuid4())
        response = await self.request(Opcodes.BEGIN_CALL, {'conversationId': conversationId, 'calleeIds': calleeIds, 'internalParams': get_call_payload(self.device_id), 'isVideo': False})
        if response["cmd"] == 1:
            return BeginCallResp.model_validate(response["payload"])
        raise ServerError(response["payload"])

    async def qr_auth_getid(self) -> QrAuthResp:
        response = await self.request(Opcodes.QR_AUTH_GETID, { })
        if response["cmd"] == 1:
            return QrAuthResp.model_validate(response["payload"])
        raise ServerError(response["payload"])

    async def qr_auth_poll(self, trackId: str) -> Dict[str, Any]:
        response = await self.request(Opcodes.QR_AUTH_POLL, { "trackId" : trackId })
        open("src/w4.json", "w").write(json.dumps(response, cls=UniversalEncoder, indent=2))
        if response["cmd"] == 1:
            return response["payload"]
        raise ServerError(response["payload"])

    async def create_chat(self, title: str, userIds: List[int], notify: bool = True) -> Chat:
        cid = -(time.time_ns() // 1_000_000)
        response = await self.request(Opcodes.SEND_MESAGE, {'message': {'cid': cid, 'attaches': [{'_type': 'CONTROL', 'event': 'new', 'chatType': 'CHAT', 'title': title, 'userIds': userIds}]}, 'notify': notify})
        if response["cmd"] == 1:
            return Chat.model_validate(response["payload"]["chat"])
        raise ServerError(response["payload"])

    async def change_name(self, firstName: str, lastName: str = '') -> UserProfile:
        response = await self.request(Opcodes.CHANGE_NAME, {'firstName': firstName, 'lastName': lastName})
        if response["cmd"] == 1:
            return UserProfile.model_validate(response["payload"]["profile"]["contact"])
        raise ServerError(response["payload"])
    
    async def qr_auth_approve(self, qrLink: str) -> None:
        response = await self.request(Opcodes.QR_AUTH_APPROVE, {'qrLink': qrLink})
        open("src/w3.json", "w").write(json.dumps(response, cls=UniversalEncoder, indent=2))
        if response["cmd"] == 1:
            return
        raise QrAuthError(response["payload"])

# open("src/w3.json", "w").wzrite(json.dumps(response, cls=UniversalEncoder, indent=2))


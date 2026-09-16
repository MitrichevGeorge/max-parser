import json
import time
import uuid
from collections.abc import Sequence
from datetime import datetime
from enum import IntEnum
from operator import itemgetter
from typing import Any, ClassVar, TypeVar, get_type_hints

from eventkit import Event
from pydantic import TypeAdapter

from classes import (
    BeginCallResp,
    Chat,
    IncomingCall,
    LoginPasswordChallenge,
    LoginPasswordResponse,
    Message,
    NewMsgEvent,
    QrAuthResp,
    UserProfile,
    VideoUrls,
)
from network_core import (
    PC_USER_AGENT,
    NetworkCoreMobile,
    NetworkCoreWS,
    get_auth_payload,
    get_call_payload,
    get_device_payload,
    get_mobile_device_payload,
)
from tools import UniversalEncoder

T = TypeVar("T", bound="ServerError")


class ServerError(RuntimeError):
    _error_map: ClassVar[dict[str, type["ServerError"]]] = {}
    _error_code: ClassVar[str] = ""

    message: str
    error: str

    def __init__(self, message: str = "", error: str = "", **kwargs: Any) -> None:
        super().__init__(message)
        self.message = message
        self.error = error or getattr(self, "_error_code", "")

        hints = get_type_hints(self.__class__)
        for field in hints:
            if field in ("message", "error") or field.startswith("_"):
                continue
            if hasattr(self.__class__, field):
                setattr(self, field, getattr(self.__class__, field))

        for key, value in kwargs.items():
            if key in hints:
                setattr(self, key, value)
            else:
                raise TypeError(f"{self.__class__.__name__}() got unexpected keyword argument {key!r}")

    def __init_subclass__(
        cls,
        error_code: str | Sequence[str] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init_subclass__(**kwargs)

        if error_code is not None:
            codes = (error_code,) if isinstance(error_code, str) else tuple(error_code)
            for code in codes:
                existing = ServerError._error_map.get(code)
                if existing is not None and existing is not cls:
                    raise TypeError(f"Duplicate error_code {code!r}: registered to {existing.__qualname__!r}")
                ServerError._error_map[code] = cls
            cls._error_code = codes[0]

    @classmethod
    def from_payload(cls: type[T], payload: dict[str, Any]) -> T:
        message = (
            payload.get("localizedMessage")
            or payload.get("message")
            or "Unknown server error"
        )
        error = str(payload.get("error", ""))

        target = ServerError._error_map.get(error, cls)
        if not issubclass(target, cls):
            target = cls

        hints = get_type_hints(target)
        extra: dict[str, Any] = {}

        for field in hints:
            if field in ("message", "error") or field.startswith("_"):
                continue
            if field in payload:
                extra[field] = payload[field]

        return target(message=message, error=error, **extra)

class TooManyRequests(ServerError, error_code="too.many.requests"):
    pass

class QrAuthError(ServerError):
    pass

class InvalidQr(QrAuthError, error_code="qr_link.invalid"):
    pass

class TrackExpired(QrAuthError, error_code="track.not.found"):
    pass

class QrLoginDisabled(QrAuthError, error_code="qr_login.disabled"):
    pass


class WrongPhoneError(ServerError, error_code="error.phone.wrong"):
    pass

class ServerValidationError(ServerError, error_code="proto.payload"):
    pass

class LoginError(ServerError):
    pass

class LoginNeedPassw(LoginError):
    passwordChallenge: LoginPasswordChallenge

class WrongVerifyCode(LoginError, error_code="verify.code.wrong"):
    pass

class InvalidToken(LoginError, error_code="login.token"):
    pass

class SearchError(ServerError):
    pass

class SearchNotFound(SearchError, error_code="search.not_found"):
    pass


class CMDTypes(IntEnum):
    OK = 1
    NOF = 2
    ERR = 3

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
    AUTH_LOGIN_CHECK_PASSWORD = 115

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

    async def _on_message_received(self, msg: dict[str, Any]):
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
        if response["cmd"] == CMDTypes.OK:
            return response['payload']
        raise ServerError.from_payload(response["payload"])

    async def search(self, query: str, count: int = 40) -> list[Chat]:
        response = await self.request(Opcodes.SEARCH, {'query': query, 'count': count })
        if response["cmd"] == CMDTypes.OK:
            adapter = TypeAdapter(list[Chat])
            return adapter.validate_python(map(itemgetter("chat"), response["payload"]["result"]))
        raise ServerError.from_payload(response["payload"])

    async def search_number(self, query: str) -> UserProfile | None:
        response = await self.request(Opcodes.SEARCH_BY_NUMBER, {'phone': query })
        if response["payload"].get("error") == "not.found":
            return None
        return UserProfile.model_validate(response["payload"]["contact"])

    async def get_infos(self, contactIds: list[int]) -> list[UserProfile]:
        response = await self.request(Opcodes.GET_INFOS, {'contactIds': contactIds})
        if response['cmd'] == CMDTypes.OK:
            adapter = TypeAdapter(list[UserProfile])
            return adapter.validate_python(response["payload"]["contacts"])
        raise ServerError.from_payload(response["payload"])

    async def get_messages(self, chatID: int, dFrom: datetime = datetime.now(), backward: int = 100, forward: int = 100) -> list[Message]:
        response = await self.request(Opcodes.GET_MESSAGES, {'chatId': chatID, 'from': int(dFrom.timestamp() * 1000), 'forward': forward, 'backward': backward, 'getMessages': True})
        if response["cmd"] == CMDTypes.OK:
            adapter = TypeAdapter(list[Message])
            return adapter.validate_python(response["payload"]["messages"])
        raise ServerError.from_payload(response["payload"])

    async def get_file_url(self, fileId: int, chatId: int, messageId: int) -> str:
        response = await self.request(Opcodes.GET_FILE_URL, {'fileId': fileId, 'chatId': chatId, 'messageId': messageId})
        if response["cmd"] == CMDTypes.OK:
            return response["payload"]["url"]
        raise ServerError.from_payload(response["payload"])

    async def get_video_urls(self, videoId: int, token: str, chatId: int, messageId: int) -> VideoUrls:
        response = await self.request(Opcodes.GET_VIDEO_URLS, {'videoId': videoId, 'token': token, 'chatId': chatId, 'messageId': messageId})
        if response["cmd"] == CMDTypes.OK:
            return VideoUrls.model_validate(response["payload"])
        raise ServerError.from_payload(response["payload"])

    async def send_message(self, chatId: int, text: str, notify: bool = True) -> Message:
        cid = -(time.time_ns() // 1_000_000)
        response = await self.request(Opcodes.SEND_MESAGE, {'chatId': chatId, 'message': {'text': text, 'cid': cid, 'elements': [], 'attaches': []}, 'notify': notify})
        if response["cmd"] == CMDTypes.OK:
            return Message.model_validate(response["payload"]["message"])
        raise ServerError.from_payload(response["payload"])

    async def delete_chat(self, chatId: int, forAll: bool = True) -> None:
        last_time = time.time_ns() // 1_000_000
        response = await self.request(Opcodes.DELETE_CHAT, {'chatId': chatId, 'lastEventTime': last_time, 'forAll': forAll})
        if response["cmd"] == CMDTypes.OK:
            return
        raise ServerError.from_payload(response["payload"])

    async def get_captcha_url(self, phoneNumber: str) -> str:
        response = await self.request(Opcodes.GET_CAPTCHA, { 'source': 'auth', 'identifier': phoneNumber })
        if response["cmd"] == CMDTypes.OK:
            return response["payload"]["link"]
        raise ServerError.from_payload(response["payload"])

    async def send_verify_code(self, phoneNumber: str, captchaToken: str | None = None) -> str:
        if captchaToken:
            response = await self.request(Opcodes.SEND_VERIFY_CODE, { 'phone': phoneNumber, 'type': 'RESEND', 'language': 'ru', 'captchaToken': captchaToken })
        else:
            response = await self.request(Opcodes.SEND_VERIFY_CODE, { 'phone': phoneNumber, 'type': 'START_AUTH', 'language': 'ru' })
        if response["cmd"] == CMDTypes.OK:
            return response["payload"]["token"]
        raise ServerError.from_payload(response["payload"]) # WrongPhoneError / 

    async def check_verify_code(self, token: str, verifyCode: str) -> str:
        response = await self.request(Opcodes.CHECK_VERIFY_CODE, {'token': token, 'verifyCode': verifyCode, 'authTokenType': 'CHECK_CODE'})
        if response["cmd"] == CMDTypes.OK:
            if "LOGIN" in response["payload"]["tokenAttrs"]:
                return response["payload"]["tokenAttrs"]["LOGIN"]["token"]
            elif "passwordChallenge" in response["payload"]:
                raise LoginNeedPassw(passwordChallenge = LoginPasswordChallenge.model_validate(response["payload"]["passwordChallenge"]))
        raise ServerError.from_payload(response["payload"])

    async def logout(self) -> None:
        response = await self.request(Opcodes.LOGOUT, { })
        if response["cmd"] == CMDTypes.OK:
            return
        raise ServerError.from_payload(response["payload"])

    async def delete_account(self) -> None:
        response = await self.request(Opcodes.DELETE_ACCOUNT, {'delete': True, 'type': 0})
        if response["cmd"] == CMDTypes.OK:
            return
        raise ServerError.from_payload(response["payload"])

    async def mark_as_read(self, chatId: int, messageId: int) -> None:
        response = await self.request(Opcodes.MARK_AS_READ, {'type': 'READ_MESSAGE', 'chatId': chatId, 'messageId': messageId, 'mark': int(time.time() * 1000)})
        if response["cmd"] == CMDTypes.OK:
            return
        raise ServerError.from_payload(response["payload"])

    async def event_subscribe(self, chatId: int, subscribe: bool = True) -> None:
        response = await self.request(Opcodes.EVENT_SUBSCRIBE, {'type': 'READ_MESSAGE', 'chatId': chatId, 'subscribe': subscribe})
        if response["cmd"] == CMDTypes.OK:
            return
        raise ServerError.from_payload(response["payload"])

    async def begin_call(self, calleeIds: list[int], conversationId: str | None = None) -> BeginCallResp:
        conversationId = conversationId or str(uuid.uuid4())
        response = await self.request(Opcodes.BEGIN_CALL, {'conversationId': conversationId, 'calleeIds': calleeIds, 'internalParams': get_call_payload(self.device_id), 'isVideo': False})
        if response["cmd"] == CMDTypes.OK:
            return BeginCallResp.model_validate(response["payload"])
        raise ServerError.from_payload(response["payload"])

    async def create_chat(self, title: str, userIds: list[int], notify: bool = True) -> Chat:
        cid = -(time.time_ns() // 1_000_000)
        response = await self.request(Opcodes.SEND_MESAGE, {'message': {'cid': cid, 'attaches': [{'_type': 'CONTROL', 'event': 'new', 'chatType': 'CHAT', 'title': title, 'userIds': userIds}]}, 'notify': notify})
        if response["cmd"] == CMDTypes.OK:
            return Chat.model_validate(response["payload"]["chat"])
        raise ServerError.from_payload(response["payload"])

    async def qr_auth_getid(self) -> QrAuthResp:
        response = await self.request(Opcodes.QR_AUTH_GETID, { })
        if response["cmd"] == CMDTypes.OK:
            return QrAuthResp.model_validate(response["payload"])
        raise ServerError.from_payload(response["payload"])

    async def qr_auth_poll(self, trackId: str) -> dict[str, Any]:
        response = await self.request(Opcodes.QR_AUTH_POLL, { "trackId" : trackId })
        if response["cmd"] == CMDTypes.OK:
            return response["payload"]
        raise ServerError.from_payload(response["payload"])
    
    async def qr_auth_approve(self, qrLink: str) -> None:
        response = await self.request(Opcodes.QR_AUTH_APPROVE, {'qrLink': qrLink})
        open("src/w3.json", "w").write(json.dumps(response, cls=UniversalEncoder, indent=2))
        if response["cmd"] == CMDTypes.OK:
            return
        raise QrAuthError(response["payload"])

    async def change_name(self, firstName: str, lastName: str = '') -> UserProfile:
        response = await self.request(Opcodes.CHANGE_NAME, {'firstName': firstName, 'lastName': lastName})
        if response["cmd"] == CMDTypes.OK:
            return UserProfile.model_validate(response["payload"]["profile"]["contact"])
        raise ServerError.from_payload(response["payload"])

    async def login_password(self, trackId: str, password: str) -> LoginPasswordResponse:
        response = await self.request(Opcodes.AUTH_LOGIN_CHECK_PASSWORD, {"trackId": trackId, "password": password})
        if response["cmd"] == CMDTypes.OK:
            return LoginPasswordResponse.model_validate(response["payload"])
        raise ServerError.from_payload(response["payload"])

# open("src/w3.json", "w").write(json.dumps(response, cls=UniversalEncoder, indent=2))

from datetime import datetime, timezone
from typing import List, Optional, Dict, Any, Literal, Annotated, Union
from enum import StrEnum, auto
from pydantic import BaseModel, Field, field_validator
from tools import OnOffBool, format_bytes

class NameInfo(BaseModel):
    name: str
    firstName: str
    lastName: Optional[str] = None
    type: str

    def __str__(self) -> str:
        first = (self.firstName or "").strip()
        last = (self.lastName or "").strip()
        return " ".join(part for part in (first, last) if part)

class UserProfile(BaseModel):
    id: int
    registrationTime: datetime
    updateTime: datetime
    accountStatus: int
    country: Optional[str] = ''
    names: List[NameInfo]
    options: List[str]
    phone: Optional[int] = None
    photoId: Optional[int] = None
    status: Optional[str] = None

    @field_validator('registrationTime', 'updateTime', mode='before')
    @classmethod
    def parse_milliseconds_to_datetime(cls, value):
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
        return value

    def info(self, tab = 0):
        indent = "│" * tab
        child_indent = "│" * (tab + 1)
        print(f'{indent}┌{"─"*4} [{self.country}] {self.names[0].name}')
        print(f'{child_indent}Phone: +{self.phone}')
        print(f'{child_indent}ID: {self.id}')
        print(f'{child_indent}Registration: {self.registrationTime}')
        print(f'{child_indent}Update: {self.updateTime}')
        print(f'{indent}└{"─"*6}')

    def get_name(self) -> str:
        if len(self.names) == 0:
            return ""
        return str(self.names[0])

class VideoConversation(BaseModel):
    joinLink: str
    type: int
    previewParticipantIds: list[int]
    conversationId: str
    callType: str

class Chat(BaseModel):
    access: Optional[str] = None
    invitedBy: Optional[int] = None
    owner: int

    id: int
    cid: Optional[int] = None
    type: str
    status: str

    title: Optional[str] = None
    description: Optional[str] = None

    modified: datetime
    joinTime: Optional[datetime] = None
    created: datetime
    lastEventTime: datetime
    messagesCount: Optional[int] = None
    videoConversation: Optional[VideoConversation] = None
    hasBots: Optional[bool] = None
    restrictions: Optional[int] = None
    prevMessageId: Optional[int] = None
    participantsCount: Optional[int] = None
    participants: Optional[Dict] = {}

    link: Optional[str] = ''
    baseIconUrl: Optional[str] = ''
    baseRawIconUrl: Optional[str] = ''
    

    @field_validator('modified', 'joinTime', 'created', 'lastEventTime', mode='before')
    @classmethod
    def parse_milliseconds_to_datetime(cls, value):
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
        return value

    def info(self, tab = 0):
        indent = "│" * tab
        child_indent = "│" * (tab + 1)
        print(f'{indent}┌{"─"*4} [{self.type}] {self.title}')
        print(f'{child_indent}ID: {self.id}')
        print(f'{child_indent}JoinTime: {self.joinTime}')
        print(f'{child_indent}Created: {self.created}')
        print(f'{child_indent}LastEventTime: {self.lastEventTime}')
        print(f'{child_indent}ParticipantsCount: {self.participantsCount}')
        print(f'{child_indent}Link: {self.link}')
        print(f'{indent}└{"─"*6}')

    messages: Optional[List[Message]] = []
    messages_by_id: Optional[Dict[int,Message]] = {}

    def update_messages(self):
        if self.messages:
            self.messages_by_id = {i.id: i for i in self.messages}


class ProfileContainer(BaseModel):
    contact: UserProfile

class ChatConfig(BaseModel):
    dontDisturbUntil: int
    vibr: bool
    sound: bool
    led: bool
    favIndex: int

class TypeGroup(StrEnum):
    CONTACTS = "CONTACTS"
    ALL = "ALL"
    NOBODY = "NOBODY"

class UserAccountConfig(BaseModel):
    PHONE_NUMBER_PRIVACY: TypeGroup
    SEARCH_BY_PHONE: TypeGroup
    INCOMING_CALL: TypeGroup
    CHATS_INVITE: TypeGroup
    HIDDEN: bool
    FAMILY_PROTECTION: OnOffBool
    DOUBLE_TAP_REACTION_DISABLED: bool
    DOUBLE_TAP_REACTION_VALUE: Any
    SAFE_MODE_NO_PIN: bool

    CHATS_PUSH_NOTIFICATION: OnOffBool
    M_CALL_PUSH_NOTIFICATION: OnOffBool
    PUSH_NEW_CONTACTS: bool
    PUSH_DETAILS: bool
    CHATS_PUSH_SOUND: str
    PUSH_SOUND: str
    UNSAFE_FILES: bool
    DONT_DISTURB_UNTIL: int
    INACTIVE_TTL: str
    SHOW_READ_MARK: bool
    ALT_KEYBOARD: bool
    CONTENT_LEVEL_ACCESS: bool
    STICKERS_SUGGEST: OnOffBool
    SAFE_MODE: bool
    AUDIO_TRANSCRIPTION_ENABLED: bool

class ConfigContainer(BaseModel):
    chats: Dict[int, ChatConfig]
    user: UserAccountConfig

class ServerData(BaseModel):
    profile: ProfileContainer
    contacts: List[UserProfile]
    chats: List[Chat]
    config: ConfigContainer


class AttachType(StrEnum):
    CONTROL = "CONTROL"
    PHOTO = "PHOTO"
    FILE = "FILE"

class BaseAttach(BaseModel):
    def info(self):
        if isinstance(self, ControlAttach):
            return f'[C] {self.event}'
        if isinstance(self, PhotoAttach):
            return self.baseUrl
        if isinstance(self, FileAttach):
            return f'{self.name} [{format_bytes(self.size)}]'

class ControlAttach(BaseAttach):
    type: Literal[AttachType.CONTROL] = Field(default=AttachType.CONTROL, alias="_type")
    event: str
    userId: int | None = None
    userIds: List[int] | None = None
    pinnedMessage: Optional["Message"] = None

class PhotoAttach(BaseAttach):
    type: Literal[AttachType.PHOTO] = Field(default=AttachType.PHOTO, alias="_type")
    photoId: int
    baseUrl: str
    photoToken: str
    width: int
    height: int

class FileAttach(BaseAttach):
    type: Literal[AttachType.FILE] = Field(default=AttachType.FILE, alias="_type")
    size: int
    name: str
    fileId: int
    token: str


Attach = Annotated[
    Union[ControlAttach, PhotoAttach, FileAttach],
    Field(discriminator='type')
]

class Message(BaseModel):
    id: int
    time: datetime
    sender: int
    text: str
    attaches: List[Attach]
    reactionInfo: Optional[Dict] = {}
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from enum import StrEnum, auto
from pydantic import BaseModel, Field, field_validator
from tools import OnOffBool

class NameInfo(BaseModel):
    name: str
    firstName: str
    lastName: Optional[str] = None
    type: str

    def __str__(self) -> str:
        if self.lastName:
            return f'{self.firstName} {self.lastName}'
        return self.firstName

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
        print(f'{"│"*tab}┌{"─"*4} [{self.country}] {self.names[0].name}')
        print(f'{"│"*(tab+1)}Phone: +{self.phone}')
        print(f'{"│"*(tab+1)}ID: {self.id}')
        print(f'{"│"*(tab+1)}Registration: {self.registrationTime}')
        print(f'{"│"*(tab+1)}Update: {self.updateTime}')
        print(f'{"│"*tab}└{"─"*6}')

    def get_name(self) -> str:
        if len(self.names) == 0:
            return ""
        name_info = self.names[0]
        first = (name_info.firstName or "").strip()
        last = (name_info.lastName or "").strip()
        
        return " ".join(part for part in (first, last) if part)

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
        print(f'{"│"*tab}┌{"─"*4} [{self.type}] {self.title}')
        print(f'{"│"*(tab+1)}ID: {self.id}')
        print(f'{"│"*(tab+1)}JoinTime: {self.joinTime}')
        print(f'{"│"*(tab+1)}Created: {self.created}')
        print(f'{"│"*(tab+1)}LastEventTime: {self.lastEventTime}')
        print(f'{"│"*(tab+1)}ParticipantsCount: {self.participantsCount}')
        print(f'{"│"*(tab+1)}Link: {self.link}')
        print(f'{"│"*tab}└{"─"*6}')

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

class TypeMessage(StrEnum):
    USER = "USER"

class Message(BaseModel):
    id: int
    time: datetime
    type: TypeMessage
    sender: int
    text: str
    attaches: List[Dict]
    reactionInfo: Dict
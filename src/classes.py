from typing import List, Dict, Any, Literal, Annotated, Union
from enum import StrEnum
from pydantic import BaseModel, Field, field_validator
from tools import OnOffBool, MSKTimestamp, format_bytes

class NameInfo(BaseModel):
    name: str
    firstName: str
    lastName: str | None = None
    type: str

    def __str__(self) -> str:
        first = (self.firstName or "").strip()
        last = (self.lastName or "").strip()
        return " ".join(part for part in (first, last) if part)

class UserProfile(BaseModel):
    id: int
    registrationTime: MSKTimestamp
    updateTime: MSKTimestamp
    accountStatus: int
    country: str | None = None
    names: List[NameInfo]
    options: List[str]
    description: str | None = None
    phone: int | None = None
    photoId: int | None = None
    status: str | None = None
    baseUrl: str | None = None

    def info(self, tab = 0):
        indent = "│" * tab
        child_indent = "│" * (tab + 1)
        print(f'{indent}┌{"─"*4} [{self.country}] {self.names[0].name}')
        print(f'{child_indent}Phone: +{self.phone}')
        print(f'{child_indent}ID: {self.id}')
        print(f'{child_indent}Name: {self.get_name()}')
        print(f'{child_indent}Registration: {self.registrationTime}')
        print(f'{child_indent}Update: {self.updateTime}')
        print(f'{child_indent}Account status: {self.accountStatus}')
        print(f'{child_indent}Ava: {self.baseUrl}')
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
    access: str | None = None
    invitedBy: int | None = None
    owner: int

    id: int
    cid: int | None = None
    type: str
    status: str

    title: str | None = None
    description: str | None = None

    modified: MSKTimestamp
    joinTime: MSKTimestamp | None = None
    created: MSKTimestamp
    lastEventTime: MSKTimestamp
    messagesCount: int | None = None
    videoConversation: VideoConversation | None = None
    hasBots: bool | None = None
    restrictions: int | None = None
    prevMessageId: int | None = None
    participantsCount: int | None = None
    participants: Dict | None = None

    link: str | None = None
    baseIconUrl: str | None = None
    baseRawIconUrl: str | None = None

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

    messages: List[Message] | None = None
    messages_by_id: Dict[int,Message] | None = None

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

class ServerConfig(BaseModel):
    calls_endpoint: str = Field(alias="calls-endpoint")
    invite_link: str = Field(alias="invite-link")

    max_audio_length: int = Field(alias="max-audio-length")
    max_description_length: int = Field(alias="max-description-length")
    max_favorite_chats: int = Field(alias="max-favorite-chats")
    max_favorite_sticker_sets: int = Field(alias="max-favorite-sticker-sets")
    max_favorite_stickers: int = Field(alias="max-favorite-stickers")
    max_msg_length: int = Field(alias="max-msg-length")
    max_participants: int = Field(alias="max-participants")
    max_readmarks: int = Field(alias="max-readmarks")
    max_theme_length: int = Field(alias="max-theme-length")
    max_video_duration_download: int = Field(alias="max-video-duration-download")
    nick_max_length: int = Field(alias="nick-max-length")
    nick_min_length: int = Field(alias="nick-min-length")

    white_list_links: List[str] = Field(alias="white-list-links")

    def info(self, tab = 0):
        indent = "│" * tab
        child_indent = "│" * (tab + 1)
        print(f'{indent}┌{"─"*4} Server config ')
        print(f'{child_indent}White List Links: {self.white_list_links}')
        print(f'{child_indent}Calls endpoint: {self.calls_endpoint}')
        print(f'{child_indent}Invite link: {self.invite_link}')
        print(f'{child_indent}Max Audio Length: {self.max_audio_length}')
        print(f'{child_indent}Max Description Length: {self.max_description_length}')
        print(f'{child_indent}Max Favorite Chats: {self.max_favorite_chats}')
        print(f'{child_indent}Max Favorite Sticker Sets: {self.max_favorite_sticker_sets}')
        print(f'{child_indent}Max Favorite Stickers: {self.max_favorite_stickers}')
        print(f'{child_indent}Max Msg Length: {self.max_msg_length}')
        print(f'{child_indent}Max Participants: {self.max_participants}')
        print(f'{child_indent}Max Readmarks: {self.max_readmarks}')
        print(f'{child_indent}Max Theme Length: {self.max_theme_length}')
        print(f'{child_indent}Max Video Duration Download: {self.max_video_duration_download}')
        print(f'{child_indent}Nick Length: {self.nick_min_length} - {self.nick_max_length}')
        print(f'{indent}└{"─"*6}')


class ConfigContainer(BaseModel):
    chats: Dict[int, ChatConfig]
    user: UserAccountConfig
    server: ServerConfig

class ServerData(BaseModel):
    profile: ProfileContainer
    contacts: List[UserProfile]
    chats: List[Chat]
    config: ConfigContainer


class AttachType(StrEnum):
    CONTROL = "CONTROL"
    PHOTO = "PHOTO"
    FILE = "FILE"
    VIDEO = "VIDEO"

class BaseAttach(BaseModel):
    def info(self):
        if isinstance(self, ControlAttach):
            return f'[C] {self.event}'
        if isinstance(self, PhotoAttach):
            return self.baseUrl
        if isinstance(self, FileAttach):
            return f'{self.name} [{format_bytes(self.size)}]'
        if isinstance(self, VideoAttach):
            return f'[V] {self.description} [{self.thumbnail}] {self.width}x{self.height}'

class ControlAttach(BaseAttach):
    type: Literal[AttachType.CONTROL] = Field(default=AttachType.CONTROL, alias="_type")
    event: str
    userId: int | None = None
    userIds: List[int] | None = None
    pinnedMessage: Message | None = None

class PhotoAttach(BaseAttach):
    type: Literal[AttachType.PHOTO] = Field(default=AttachType.PHOTO, alias="_type")
    photoId: int
    baseUrl: str
    photoToken: str
    width: int
    height: int

class VideoAttach(BaseAttach):
    type: Literal[AttachType.VIDEO] = Field(default=AttachType.VIDEO, alias="_type")
    previewData: bytes
    duration: int
    thumbnail: str
    videoType: int
    width: int
    height: int
    description: str
    videoId: int
    token: str

class FileAttach(BaseAttach):
    type: Literal[AttachType.FILE] = Field(default=AttachType.FILE, alias="_type")
    size: int
    name: str
    fileId: int
    token: str

Attach = Annotated[
    Union[ControlAttach, PhotoAttach, VideoAttach, FileAttach],
    Field(discriminator='type')
]

class Message(BaseModel):
    id: int
    time: MSKTimestamp
    sender: int
    text: str
    attaches: List[Attach]
    reactionInfo: Dict | None = None
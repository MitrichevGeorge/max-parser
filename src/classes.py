from datetime import datetime, timezone
from typing import List, Optional
from pydantic import BaseModel, Field, field_validator

class NameInfo(BaseModel):
    name: str
    firstName: str
    lastName: Optional[str] = None
    type: str

class UserProfile(BaseModel):
    id: int
    registrationTime: datetime
    updateTime: datetime
    accountStatus: int
    country: str
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
        print(f'{"│"*(tab+1)}ReGistration: {self.registrationTime}')
        print(f'{"│"*(tab+1)}Update: {self.updateTime}')
        print(f'{"│"*tab}└{"─"*6}')

class VideoConversation(BaseModel):
    joinLink: str
    type: int
    previewParticipantIds: list[int]
    conversationId: str
    callType: str

class Chat(BaseModel):
    participantsCount: Optional[int] = None
    access: Optional[str] = None
    invitedBy: Optional[int] = None
    description: Optional[str] = None
    type: str
    title: Optional[str] = None
    modified: datetime
    id: int
    owner: int
    joinTime: datetime
    created: datetime
    lastEventTime: datetime
    messagesCount: Optional[int] = None
    status: str
    videoConversation: Optional[VideoConversation] = None
    hasBots: Optional[bool] = None
    restrictions: Optional[int] = None
    prevMessageId: Optional[int] = None
    cid: Optional[int] = None

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
        print(f'{"│"*(tab+1)}lastEventTime: {self.lastEventTime}')
        print(f'{"│"*(tab+1)}participantsCount: {self.participantsCount}')
        print(f'{"│"*tab}└{"─"*6}')
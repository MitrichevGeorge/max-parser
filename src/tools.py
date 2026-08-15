from typing import Iterable, Any, Final, List, NoReturn, Sequence, Tuple, Dict
import math
import json
import base64
import sys

def bye() -> NoReturn:
    print("bye")
    sys.exit(0)

def any_without(lst: Iterable, val: Any) -> Any:
    for x in lst:
        if x != val:
            return x
    return val

class UniversalEncoder(json.JSONEncoder):
    def default(self, o: Any):
        if isinstance(o, bytes):
            try:
                return o.decode('utf-8')
            except UnicodeDecodeError:
                return base64.b64encode(o).decode('utf-8')
        
        return super().default(o)

from typing import Annotated
from pydantic import BeforeValidator, PlainSerializer
from datetime import datetime
from zoneinfo import ZoneInfo

def parse_on_off(value: Any) -> bool:
    match value:
        case bool():
            return value
        case str() if value.strip().upper() == "ON":
            return True
        case str() if value.strip().upper() == "OFF":
            return False
        case _:
            raise ValueError(f"Unsupported value: {repr(value)} (type: {type(value).__name__})")

def serialize_on_off(value: bool) -> str:
    return "ON" if value else "OFF"

OnOffBool = Annotated[
    bool, 
    BeforeValidator(parse_on_off),
    PlainSerializer(serialize_on_off, return_type=str)
]

def parse_ms_to_datetime(value):
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, tz=ZoneInfo("Europe/Moscow"))
    return value

MSKTimestamp = Annotated[datetime, BeforeValidator(parse_ms_to_datetime)]


from questionary import Validator, ValidationError, Choice
from prompt_toolkit.patch_stdout import patch_stdout
import questionary
import re

class NumberValidator(Validator):
    def __init__(self, min_n: int | None = None, max_n: int | None = None):
        self.min_n = min_n
        self.max_n = max_n

    def validate(self, document):
        text = document.text.strip()
        if not text:
            raise ValidationError(message="Input cannot be blank.")
        
        try:
            value = int(text)
        except ValueError:
            raise ValidationError(message="Input must be a valid integer.")

        if self.min_n is not None and value < self.min_n:
            raise ValidationError(message=f"Number must be >= {self.min_n}.")
        if self.max_n is not None and value > self.max_n:
            raise ValidationError(message=f"Number must be <= {self.max_n}.")

class RussianPhoneValidator(Validator):
    def validate(self, document):
        phone_pattern = r"^\+7\d{10}$"
        if not re.match(phone_pattern, document.text):
            raise ValidationError(
                message="Номер должен быть в формате +79991234567",
                cursor_position=len(document.text)
            )

async def ask(prompt_text: str = "> ", validator: Validator | None = None) -> str:
    try:
        return await questionary.text(prompt_text, validate=validator).ask_async()
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled by user.")
        exit(0)

async def read_number(prompt_text: str = "", min_n: int | None = None, max_n: int | None = None) -> int:
    validator = NumberValidator(min_n, max_n)
    user_input = await ask(f"{prompt_text} -> ", validator=validator)
    if not user_input:
        print("\nCancelled by user.")
        exit(0)
    return int(user_input.strip())

async def sel(menu_items: Sequence[str], prompt_text: str = "") -> int:
    if not menu_items:
        raise ValueError("menu_items cannot be empty")

    choices = [Choice(title=item, value=idx) for idx, item in enumerate(menu_items)]
    result = await questionary.select(prompt_text, choices=choices).ask_async()
    if result is None:
        bye()

    return result

async def sel_str(menu_items: Sequence[str], prompt_text: str = "") -> str:
    if not menu_items:
        raise ValueError("menu_items cannot be empty")

    result = await questionary.select(prompt_text, choices=list(menu_items)).ask_async()
    if result is None:
        bye()

    return result

async def ask_exact(prompt: str, check: str = "YES") -> bool:
    result = await questionary.text(prompt, validate=lambda text: True if text == check else f"Нужно ввести именно '{check}'").ask_async()
    if result is None:
        bye()

    return result == check

BINARY_UNITS: Final[tuple[str, ...]] = ('B', 'KiB', 'MiB', 'GiB', 'TiB', 'PiB', 'EiB')
DECIMAL_UNITS: Final[tuple[str, ...]] = ('B', 'KB', 'MB', 'GB', 'TB', 'PB', 'EB')

def format_bytes(size_bytes: int, use_binary: bool = True, precision: int = 2) -> str:
    if size_bytes < 0:
        raise ValueError("Filesize cant be less than 0")
    if size_bytes == 0:
        return "0 B"

    factor = 1024 if use_binary else 1000
    units = BINARY_UNITS if use_binary else DECIMAL_UNITS
    exponent = min(int(math.log(size_bytes, factor)), len(units) - 1)

    if exponent == 0:
        return f"{size_bytes} B"

    value = size_bytes / (factor ** exponent)
    return f"{value:.{precision}f} {units[exponent]}"

def format_duration(seconds: int) -> str:
    if seconds < 0:
        raise ValueError("Time cant be less than 0")
    if seconds == 0:
        return "0 сек"

    time_units = [
        ("ч", 3600),
        ("мин", 60),
        ("сек", 1)
    ]    
    parts = []
    remaining_seconds = seconds
    for label, unit_seconds in time_units:
        value, remaining_seconds = divmod(remaining_seconds, unit_seconds)
        if value > 0:
            parts.append(f"{value} {label}")
            
    return " ".join(parts)

import random

SCREENS = [
    "1200x1920 1.0x",
    "1920x1080 1.0x",
    "1920x1080 1.25x",
    "2560x1440 1.0x",
    "2560x1440 1.25x",
    "1440x900 2.0x",
    "1680x1050 2.0x",
    "2560x1600 2.0x",
    "1536x864 1.25x",
    "1366x768 1.0x",
]

OS_PRESETS = [
    ("Mac", "Macintosh; Intel Mac OS X 10_15_7"),
    ("Windows 10", "Windows NT 10.0; Win64; x64"),
    ("Windows 11", "Windows NT 10.0; Win64; x64"),
    ("Linux", "X11; Linux x86_64"),
]

BROWSER_TEMPLATES = {
    "Chrome": lambda os, v: f"Mozilla/5.0 ({os}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{v} Safari/537.36",
    "Firefox": lambda os, v: f"Mozilla/5.0 ({os}; rv:{v.split('.')[0]}.0) Gecko/20100101 Firefox/{v.split('.')[0]}.0",
    "Edge": lambda os, v: f"Mozilla/5.0 ({os}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{v} Safari/537.36 Edg/{v}",
    "Safari": lambda os, v: f"Mozilla/5.0 ({os}) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
}

BASE_HEADERS = {
    "Host": "ws-api.oneme.ru",
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

BASE_USER_AGENT = {
    "deviceType": "WEB",
    "pushDeviceType": "WEBPUSH",
    "locale": "en",
    "deviceLocale": "en",
    "appVersion": "26.6.20",
    "timezone": "Europe/Moscow",
}

def generate_user_agent_pair() -> Tuple[Dict[str, str], Dict[str, Any]]:
    os_ver, os_ua = random.choice(OS_PRESETS)
    browser = random.choice(list(BROWSER_TEMPLATES.keys()))
    
    ver = f"{random.randint(110, 126)}.0.{random.randint(1000, 9999)}.{random.randint(10, 99)}"
    ua_string = BROWSER_TEMPLATES[browser](os_ua, ver)

    headers = {**BASE_HEADERS, "User-Agent": ua_string}
    user_agent = {
        **BASE_USER_AGENT,
        "osVersion": os_ver,
        "deviceName": browser,
        "headerUserAgent": ua_string,
        "screen": random.choice(SCREENS),
    }

    return headers, user_agent

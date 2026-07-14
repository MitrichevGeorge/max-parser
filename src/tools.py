from typing import Iterable, Any, Final
import math
import json
import base64

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


from prompt_toolkit.shortcuts import PromptSession
from prompt_toolkit.validation import Validator, ValidationError

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

_session = PromptSession()

async def ask(prompt_text: str = "> ", validator: Validator | None = None) -> str:
    try:
        return await _session.prompt_async(prompt_text, validator=validator)
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled by user.")
        exit(0)

async def read_number( prompt: str = "", min_n: int | None = None, max_n: int | None = None) -> int:
    validator = NumberValidator(min_n, max_n)
    user_input = await ask(f"{prompt} -> ", validator=validator)
    return int(user_input.strip())


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


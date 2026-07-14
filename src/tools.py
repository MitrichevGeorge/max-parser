from typing import Iterable, Any, Final
import math
import json
import base64

def any_without(lst: Iterable, val: Any) -> Any:
    return next(iter(set(lst) - {val}), val)

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

def read_number(prompt: str = "", min_n: int | None = None, max_n: int | None = None) -> int:
    while True:
        if not (user_input := input(f"{prompt} -> ").strip()):
            print("Input cannot be blank.")
            continue

        try:
            number = int(user_input)
        except ValueError:
            print("Input must be a valid integer.")
            continue

        if min_n is not None and number < min_n:
            print(f"Number must be greater than or equal to {min_n}.")
            continue
        if max_n is not None and number > max_n:
            print(f"Number must be less than or equal to {max_n}.")
            continue

        return number


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
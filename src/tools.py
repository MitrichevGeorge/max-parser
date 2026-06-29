from typing import Iterable, Any
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
    if isinstance(value, str):
        val_upper = value.upper().strip()
        if val_upper == "ON":
            return True
        if val_upper == "OFF":
            return False
    if isinstance(value, bool):
        return value
    raise ValueError(f"Невозможно привести {value} к bool")

def serialize_on_off(value: bool) -> str:
    return "ON" if value else "OFF"

OnOffBool = Annotated[
    bool, 
    BeforeValidator(parse_on_off),
    PlainSerializer(serialize_on_off, return_type=str)
]
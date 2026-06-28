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
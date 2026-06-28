from typing import List, Any

def any_without(lst: List, val: Any) -> Any:
        return next((i for i in lst if i != val), val)
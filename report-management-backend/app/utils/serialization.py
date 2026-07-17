import uuid
from typing import Any

def stringify_uuids(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): stringify_uuids(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [stringify_uuids(i) for i in obj]
    elif isinstance(obj, uuid.UUID):
        return str(obj)
    return obj

from pydantic import BaseModel
from typing import List, Optional, Dict, Any

class DeepSeekMessage(BaseModel):
    role: str
    content: str

class DeepSeekChoice(BaseModel):
    message: DeepSeekMessage
    finish_reason: Optional[str] = None

class DeepSeekResponse(BaseModel):
    choices: List[DeepSeekChoice]
    usage: Dict[str, Any]

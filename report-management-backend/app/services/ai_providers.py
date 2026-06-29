from abc import ABC, abstractmethod
from typing import Dict, Any, List
import json
import time

class BaseAIProvider(ABC):
    @abstractmethod
    async def generate_proposal(self, context_bundle: Dict[str, Any], prompt_text: str) -> Dict[str, Any]:
        """
        Returns a dictionary with:
        - response_content: dict (structured proposal data)
        - prompt_tokens: int
        - completion_tokens: int
        - execution_time_ms: int
        - model_version: str
        """
        pass

class MockProvider(BaseAIProvider):
    def __init__(self, provider_name: str, model_version: str):
        self.provider_name = provider_name
        self.model_version = model_version
        
    async def generate_proposal(self, context_bundle: Dict[str, Any], prompt_text: str) -> Dict[str, Any]:
        start = time.time()
        
        # Mock structured response
        nodes = context_bundle.get("nodes", [])
        
        # If no nodes, just mock
        if not nodes:
            mock_resp = {
                "proposed_content": f"Mock proposal from {self.provider_name}",
                "explanation": "This is a mock generation.",
                "confidence": 0.95
            }
        else:
            # We assume it targets the first node for mock
            old_md = nodes[0].get("markdown", "")
            mock_resp = {
                "proposed_content": f"[AI {self.provider_name} Rewrite] {old_md}",
                "explanation": f"Rewritten using {self.provider_name} for better tone.",
                "confidence": 0.99
            }
            
        exec_ms = int((time.time() - start) * 1000)
        
        return {
            "response_content": mock_resp,
            "prompt_tokens": len(prompt_text) // 4,
            "completion_tokens": len(json.dumps(mock_resp)) // 4,
            "execution_time_ms": exec_ms + 100, # mock delay
            "model_version": self.model_version
        }

class AIProviderFactory:
    @staticmethod
    def get_provider(provider_type: str) -> BaseAIProvider:
        if provider_type == "groq":
            return MockProvider("Groq", "llama-3-70b")
        elif provider_type == "openai":
            return MockProvider("OpenAI", "gpt-4o")
        elif provider_type == "anthropic":
            return MockProvider("Anthropic", "claude-3-opus")
        elif provider_type == "gemini":
            return MockProvider("Gemini", "gemini-1.5-pro")
        else:
            return MockProvider("Local", "mistral-7b")

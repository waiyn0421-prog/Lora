"""Future multimodal recognition provider boundary; no service or credentials configured."""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class MultimodalRecognitionRequest:
    source_file: str
    page_number: int
    page_image_png: bytes
    geometry: list
    text_records: list
    candidates: list
    coordinate_system: dict


class MultimodalRecognitionProvider(ABC):
    @abstractmethod
    def recognize(self, request):
        """Return provider hypotheses; callers must validate them before use."""
        raise NotImplementedError


class NoProviderConfigured:
    """Explicit default implementation that makes no network calls."""

    status = "not_configured"

    def recognize(self, request):
        return {"status": self.status, "elements": [],
                "message": "No multimodal AI provider is configured"}

"""Future adapter contract for native CAD extraction. DWG parsing is not implemented."""

from abc import ABC, abstractmethod

from .exceptions import DrawingError


class CadExtractionAdapter(ABC):
    """Implementations return native geometry and source-coordinate metadata."""

    @abstractmethod
    def extract(self, file_path):
        """Return structured native CAD content; preserve units and coordinates."""
        raise NotImplementedError


class UnavailableCadAdapter(CadExtractionAdapter):
    def extract(self, file_path):
        raise DrawingError("cad_adapter_unavailable",
                           "Native DWG geometry extraction is not implemented in this milestone")

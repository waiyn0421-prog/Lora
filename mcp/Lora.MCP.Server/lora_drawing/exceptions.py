"""Stable error types for the local drawing processing engine."""


class DrawingError(Exception):
    def __init__(self, code, message, details=None):
        super().__init__(message)
        self.code = code
        self.details = details

    def as_dict(self):
        result = {"code": self.code, "message": str(self)}
        if self.details is not None:
            result["details"] = self.details
        return result

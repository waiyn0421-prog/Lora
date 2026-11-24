"""Bounded process-local recognition result store."""

from collections import OrderedDict
import uuid

from .errors import RecognitionError


class RecognitionStore:
    def __init__(self, limit=20):
        self.limit = limit
        self._items = OrderedDict()

    def put(self, result):
        recognition_id = str(uuid.uuid4())
        result["recognition_id"] = recognition_id
        self._items[recognition_id] = result
        while len(self._items) > self.limit:
            self._items.popitem(last=False)
        return recognition_id

    def get(self, recognition_id):
        if not isinstance(recognition_id, str) or len(recognition_id) > 64:
            raise RecognitionError("invalid_recognition_id", "recognition_id must be a valid identifier")
        result = self._items.get(recognition_id)
        if result is None:
            raise RecognitionError("recognition_not_found", "Recognition result is unavailable or has expired")
        self._items.move_to_end(recognition_id)
        return result

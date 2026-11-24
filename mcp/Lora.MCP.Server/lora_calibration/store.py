"""Bounded process-local calibration cache."""

from collections import OrderedDict
import uuid

from .errors import CalibrationError


class CalibrationStore:
    def __init__(self, limit=20):
        self.limit = limit
        self._items = OrderedDict()

    def put(self, result):
        calibration_id = str(uuid.uuid4())
        result["calibration_id"] = calibration_id
        self._items[calibration_id] = result
        while len(self._items) > self.limit:
            self._items.popitem(last=False)
        return calibration_id

    def get(self, calibration_id):
        if not isinstance(calibration_id, str) or len(calibration_id) > 64:
            raise CalibrationError("invalid_calibration_id", "calibration_id must be a valid identifier")
        result = self._items.get(calibration_id)
        if result is None:
            raise CalibrationError("calibration_not_found", "Calibration result is unavailable or has expired")
        self._items.move_to_end(calibration_id)
        return result

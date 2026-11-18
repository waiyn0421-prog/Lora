# -*- coding: utf-8 -*-
"""Shared compatibility and utility functions for the Lora pyRevit extension.

Keep this module compatible with IronPython 2.7 and CPython 3 syntax.
"""

__version__ = "2.0.0"

def element_id_value(element_id):
    """Return an integer-like ElementId value across Revit API generations.

    Revit 2024 and earlier expose IntegerValue; newer APIs expose Value.
    None is returned when the object is not a readable ElementId.
    """
    if element_id is None:
        return None
    try:
        return int(element_id.Value)
    except Exception:
        try:
            return int(element_id.IntegerValue)
        except Exception:
            return None

def ids_equal(left, right):
    """Compare ElementIds safely across API versions."""
    try:
        if left == right:
            return True
    except Exception:
        pass
    left_value = element_id_value(left)
    right_value = element_id_value(right)
    return (left_value is not None and right_value is not None
            and left_value == right_value)

def safe_text(value):
    """Convert a value to readable text without failing on None or .NET values."""
    if value is None:
        return u""
    try:
        return unicode(value)
    except NameError:
        try:
            return str(value)
        except Exception:
            return u""
    except Exception:
        try:
            return str(value)
        except Exception:
            return u""

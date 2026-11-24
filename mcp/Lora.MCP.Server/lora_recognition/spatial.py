"""Small spatial tests for linking annotations to page geometry."""

import math


def bbox_center(bbox):
    if not bbox or len(bbox) != 4:
        return None
    return [(float(bbox[0]) + float(bbox[2])) / 2.0,
            (float(bbox[1]) + float(bbox[3])) / 2.0]


def bbox_gap(first, second):
    if not first or not second or len(first) != 4 or len(second) != 4:
        return None
    dx = max(float(first[0]) - float(second[2]), float(second[0]) - float(first[2]), 0.0)
    dy = max(float(first[1]) - float(second[3]), float(second[1]) - float(first[3]), 0.0)
    return math.hypot(dx, dy)


def point_in_polygon(point, polygon):
    if len(polygon) < 3:
        return False
    x, y = point
    inside = False
    points = list(polygon)
    if points[0] == points[-1]:
        points = points[:-1]
    previous = points[-1]
    for current in points:
        x1, y1 = current
        x2, y2 = previous
        if ((y1 > y) != (y2 > y)) and x < (x2 - x1) * (y - y1) / ((y2 - y1) or 1e-12) + x1:
            inside = not inside
        previous = current
    return inside

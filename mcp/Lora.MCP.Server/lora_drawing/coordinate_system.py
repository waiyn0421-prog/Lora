"""Explicit conversions between page points, raster pixels, inches and mm."""

PDF_POINTS_PER_INCH = 72.0
MM_PER_INCH = 25.4


def pdf_points_to_mm(points):
    return float(points) * MM_PER_INCH / PDF_POINTS_PER_INCH


def mm_to_pdf_points(mm):
    return float(mm) * PDF_POINTS_PER_INCH / MM_PER_INCH


def pixels_to_pdf_points(pixels, dpi):
    _validate_dpi(dpi)
    return float(pixels) * PDF_POINTS_PER_INCH / float(dpi)


def pdf_points_to_pixels(points, dpi):
    _validate_dpi(dpi)
    return float(points) * float(dpi) / PDF_POINTS_PER_INCH


def pixels_to_mm(pixels, dpi):
    """Convert physical scan pixels only when the source DPI is known."""
    _validate_dpi(dpi)
    return float(pixels) * MM_PER_INCH / float(dpi)


def _validate_dpi(dpi):
    if isinstance(dpi, bool) or not isinstance(dpi, (int, float)) or dpi <= 0:
        raise ValueError("dpi must be a positive number")


def pdf_coordinate_metadata(width_points, height_points, rotation):
    return {
        "source_space": "pdf_page_points",
        "origin": "top_left_after_page_rotation",
        "x_axis": "right",
        "y_axis": "down",
        "width": width_points,
        "height": height_points,
        "units": "PDF points (1/72 inch)",
        "rotation_degrees_clockwise": rotation,
        "real_world_scale": None,
        "scale_status": "unknown; page size does not establish drawing scale",
    }


def pixel_coordinate_metadata(width, height, dpi=None):
    return {
        "source_space": "source_image_pixels",
        "origin": "top_left",
        "x_axis": "right",
        "y_axis": "down",
        "width": width,
        "height": height,
        "units": "pixels",
        "source_dpi": dpi,
        "real_world_scale": None,
        "scale_status": "unknown; image dimensions do not establish drawing scale",
    }


def apply_affine(point, matrix):
    """Map a 2D point through a 3x3 affine transform."""
    x, y = float(point[0]), float(point[1])
    return [matrix[0][0] * x + matrix[0][1] * y + matrix[0][2],
            matrix[1][0] * x + matrix[1][1] * y + matrix[1][2]]


def invert_affine_point(point, matrix):
    """Map a transformed 2D point back through an invertible affine matrix."""
    a, b, c = matrix[0]
    d, e, f = matrix[1]
    determinant = a * e - b * d
    if abs(determinant) < 1e-12:
        raise ValueError("affine coordinate transform is not invertible")
    x, y = float(point[0]) - c, float(point[1]) - f
    return [(e * x - b * y) / determinant, (-d * x + a * y) / determinant]


def invert_affine_bbox(bbox, matrix):
    """Map an axis-aligned transformed bbox back to an axis-aligned source bbox."""
    x0, y0, x1, y1 = [float(value) for value in bbox]
    points = [invert_affine_point(point, matrix) for point in
              ([x0, y0], [x1, y0], [x1, y1], [x0, y1])]
    return [min(point[0] for point in points), min(point[1] for point in points),
            max(point[0] for point in points), max(point[1] for point in points)]

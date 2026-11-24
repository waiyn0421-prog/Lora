# BIM Context Engine

`lora_context/engine.py` assembles current Revit data through the six existing Lora read-only MCP functions. It does not create a Revit HTTP client, access the Revit API, or copy route logic. The server supplies those six callables to `BIMContextEngine`.

## Context tool

`lora_get_bim_context` combines:

- Project name, number, status, and active-document identity.
- Levels and elevations.
- A requested page of views and view types.
- Category counts and model summary statistics.
- Optionally, one category element page with requested parameter data.
- Document identity stability, per-section completeness, warnings, and known upstream limitations.

The optional category/parameter read is bounded by `element_limit` (1–100). A returned element page is not represented as an exhaustive category inventory when `has_more` is true. Omit `category` to leave element parameters explicitly `not_requested`.

## Units

RevitMCP returns level elevations as `elevation_internal_feet`. The context preserves that original value and adds `elevation_mm` using the exact scalar conversion:

`internal feet × 304.8 = millimetres`

Each level has an `elevation_units` object and an `elevation_conversion` description. No project display unit is inferred from that conversion.

## Completeness semantics

- `complete` means every requested section/page was returned and the upstream count agreed with the returned data. It does not certify BIM data quality.
- `has_more` means additional pages remain.
- `upstream_complete` means the route's reported total agrees with the records returned by that route.
- `category_summary.complete` reflects the statistics route's category truncation flag and count.
- `views.complete` refers to the requested page; the view route returns non-template, non-sheet views only and has no server-side paging.
- `element_parameters.complete` refers to the optional requested element page and its parameter response, not all category instances.
- Empty arrays with an upstream total of zero are successful, complete zero-result responses. A failed or truncated source is reported separately and never converted to zero.

The context captures project identity before and after assembly. It compares process ID, title, and path. RevitMCP provides no stable document GUID, so identical-title/path documents remain an identity limitation.

## Parameter limitations

RevitMCP's `/elements/get_properties` route returns `properties` and `typed_properties`, but can merge instance and type values without a reliable source marker. It does not report `Parameter.IsReadOnly`. Lora exposes `value_status` (`populated`, `empty`, `missing`, or `unavailable`) and `has_value`/storage metadata where returned. `Not available` with no storage type remains unavailable; a blank or `HasValue=false` parameter is empty. A requested parameter omitted upstream is missing. Missing/unavailable distinctions depend on fields present in the upstream response.

## Source bounds

- Model statistics: categories and detailed type summaries are capped at `top_n` (maximum 200) by RevitMCP.
- Levels are included in model statistics; RevitMCP has no dedicated levels route.
- Element and parameter pages are sliced in the Lora process after the existing RevitMCP route returns IDs/records. The upstream element routes themselves return full result sets, bounded by the Lora HTTP response ceiling.
- `lora_answer_bim_question` limits element scans to `max_pages × 100`, reports retrieved vs reported totals, and marks results incomplete if pages/upstream records are missing.
- Views exclude templates and drawing sheets. The route result may be large; the Lora client rejects payloads larger than its configured response ceiling.

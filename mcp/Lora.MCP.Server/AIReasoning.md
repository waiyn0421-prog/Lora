# Natural-language BIM reasoning

`lora_answer_bim_question` is a local, deterministic intent router over the read-only BIM context functions. It does not call an external LLM, execute ribbon buttons, or perform Revit writes. Its answer includes the original question, the interpreted scope, evidence, `complete`, warnings, and `not_executed: true`.

## Supported intents

| Intent | Example | Data used |
|---|---|---|
| Model-wide category counts | “How many walls, doors and windows are in this project?” | `lora_analyze_model` category statistics |
| Category/discipline inventory | “Show the MEP category inventory” | One bounded `lora_get_category_inventory` summary page with inventory completeness and warnings |
| Category count on a level | “How many doors are on Level 02?” | `lora_get_levels` plus paged `lora_get_elements` |
| Missing/empty parameter scan | “Which walls have missing Mark parameters?” | Paged `lora_get_elements` plus batched `lora_get_element_parameters` |
| Level/elevation list | “What are the levels and their elevations?” | `lora_get_levels`; reports internal feet and converted millimetres |
| View inventory | “What views exist in this project?” | Paged `lora_get_views`; type counts and up to 50 view records in the response |
| Category element IDs | “Which elements belong to Specialty Equipment?” | Statistics category names and paged `lora_get_elements` |
| Selected-element geometry | “Inspect geometry for element IDs 123 and 456” | `lora_get_element_geometry`, only for the explicit numeric IDs (maximum 25) |
| Completeness/submission question | “What model information is incomplete before submission?” | Model statistics and the placeable-view inventory; does not certify submission readiness |
| Lora command catalog | “What Lora commands are available?” | `lora_list_commands` or the same source-verified local registry in the natural-language router; no Revit query or command execution |
| Command recommendation/info | “Please update MEP elevations” / “What does Auto Dim do?” | Verified `lora_context/registry.py`; no command execution |

Common category aliases include walls, doors, windows, floors, ceilings, roofs, columns, structural columns, structural framing/beams, ducts, pipes, and rooms. Other model category names are resolved against the returned statistics list. Level aliases such as “Level 02” resolve to an unambiguous `L2`; if multiple levels match (for example, the three `L1 - Block …` levels), the engine returns the candidates without querying elements.

## Grounding and partial results

- Category counts come from actual model statistics. If a category is absent from a complete statistics response, its count is returned as zero. If statistics are truncated, an absent category is reported unavailable instead.
- Level-specific and element-ID questions retrieve pages up to `max_pages` (default 20; page size 100). The engine compares reported totals, identities, and upstream completeness. It marks results incomplete when the scan stops early or totals disagree.
- Missing-parameter responses distinguish empty/missing values from unavailable values. Unavailable values are not counted as empty. IDs are returned up to a bounded result limit; the response reports when the ID list itself is truncated.
- A complete statistics response is not a data-quality certification. The engine has no project-required parameter schema or submission rules, and parameter source/read-only metadata is not provided by RevitMCP.
- If parameter reads are unsupported, a route fails, or the document changes, the result carries an error/limitation rather than a guessed answer.
- Geometry questions without explicit element IDs return a recommendation and do not trigger a model-wide geometry scan. Geometry evidence does not establish clearance.
- Category inventory questions request one bounded category-summary page. The answer reports incomplete scans/pages and never treats category counts as proof of physical obstruction or clearance.
- Unsupported question patterns return `unsupported_query`; no Revit call is made for an empty/invalid question.

## Command recommendations

Recommendations and command explanations include the exact bundle title, purpose, required inputs, model/file effects, and source-verified limitations. The response always says the command was not executed. These effects distinguish read-only inspection, previews, external file output, and possible Revit writes. Explicit automatic tag-placement requests are declined because the existing `Tag Creation` command only sets up MEP parameters/checks readiness and does not place tags.

The command list and effects are in [LoraCommandRegistry.md](LoraCommandRegistry.md).

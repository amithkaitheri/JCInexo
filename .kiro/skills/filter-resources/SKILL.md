---
name: filter-resources
description: Show which Ottawa emergency resources match a given user profile
---

# Filter Resources by Profile

Show which Ottawa emergency resources (cooling centres, shelters, evacuation centres, clean-air spaces) match a given user profile after filtering.

## Instructions

1. Read `ready/backend/data_sources.py` to see the full resource database.

2. Apply the filtering logic from `filter_resources_for_profile()`:
   - **Wheelchair user** → exclude non-accessible locations (hard filter)
   - **Has pet** → score pet-friendly locations higher (+5), penalize non-pet (-2)
   - **Low budget** → score free locations higher (+2)
   - Sort by score descending

3. For the given profile and emergency type, show:
   - Which resources PASS the filter (with score)
   - Which resources are EXCLUDED and why
   - The top 3 that would be sent to Gemini

## Emergency → Resource Mapping

| Emergency | Primary Resources | Secondary |
|---|---|---|
| extreme_heat | COOLING_CENTRES | SHELTERS |
| flooding | EVACUATION_CENTRES | SHELTERS |
| wildfire_smoke | CLEAN_AIR_SPACES | COOLING_CENTRES |

## Profile to test

$ARGUMENTS

If no arguments, use: wheelchair user with a dog, no car, low budget, facing extreme heat.

## Example Output

```
✓ Tom Brown Arena — score: 3 (wheelchair accessible, free)
✓ Ottawa Public Library — score: 3 (wheelchair accessible, free)
✗ Centre 454 — EXCLUDED (not wheelchair accessible)
✓ Ernst & Young Centre — score: 8 (wheelchair, pets allowed, free)
```

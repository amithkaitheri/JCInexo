---
name: add-emergency
description: Scaffold a new emergency type into the READY system (backend + frontend)
---

# Add New Emergency Type

Add a new climate emergency type to the READY system. This scaffolds changes across backend and frontend.

## Instructions

Given a new emergency type name (e.g., `ice_storm`, `tornado`, `power_outage`), do the following:

### 1. Backend — `ready/backend/emergency_engine.py`

Add a new entry to `EMERGENCY_SCENARIOS`:

```python
"$ARGUMENTS": {
    "type": "$ARGUMENTS",
    "label": "<Human-readable label>",
    "emoji": "<relevant emoji>",
    "severity": "<Low | Moderate | High>",
    "description": "<1-2 sentence Environment Canada style alert>",
    "risk_factors": [
        "<risk 1>",
        "<risk 2>",
        "<risk 3>",
        "<risk 4>",
    ],
    "color": "<hex color>",
},
```

### 2. Backend — `ready/backend/data_sources.py`

Add appropriate resource mapping in `get_resources_for_emergency()`:

```python
elif emergency_type == "$ARGUMENTS":
    return {
        "primary": <APPROPRIATE_RESOURCE_LIST>,
        "secondary": SHELTERS,
        "emergency_type": "$ARGUMENTS",
    }
```

If needed, add a new resource list (e.g., `WARMING_CENTRES` for ice storms).

### 3. Frontend — No changes needed

The frontend auto-discovers emergency types from `GET /api/emergencies`. New types appear automatically in the EmergencySelector component.

### 4. Test

```bash
curl -s http://localhost:8001/api/emergencies | python3 -m json.tool
```

Verify the new type appears in the list.

## Emergency type to add

$ARGUMENTS

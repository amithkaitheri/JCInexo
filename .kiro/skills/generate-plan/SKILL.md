---
name: generate-plan
description: Generate a personalized emergency plan by calling the READY API with a profile and emergency type
---

# Generate Emergency Plan

Generate a personalized emergency action plan using the READY backend API.

## Instructions

1. Call `POST http://localhost:8001/api/plan` with the following JSON body:

```json
{
  "profile": {
    "age": <age or null>,
    "lives_alone": <true/false or null>,
    "has_ac": <true/false or null>,
    "has_car": <true/false or null>,
    "uses_wheelchair": <true/false or null>,
    "has_pet": <true/false or null>,
    "pet_type": "<cat/dog/bird or null>",
    "language": "English",
    "additional_needs": "<any special needs or null>",
    "low_budget": <true/false or null>
  },
  "emergency_type": "<extreme_heat | flooding | wildfire_smoke>"
}
```

2. Display the response including:
   - Risk level and explanation
   - Immediate action
   - Step-by-step plan
   - Recommended location with transit info
   - Pet guidance (if applicable)
   - When to call 911

3. Note the trace_id — it can be viewed in the Flight Recorder dashboard.

## Profile Arguments

$ARGUMENTS

If no arguments provided, use this default test profile:
- Age: 72
- Lives alone: yes
- Has AC: no
- Has car: no
- Has pet: yes (cat)
- Emergency: extreme_heat

## Example

```bash
curl -s -X POST http://localhost:8001/api/plan \
  -H "Content-Type: application/json" \
  -d '{"profile":{"age":72,"lives_alone":true,"has_ac":false,"has_car":false,"has_pet":true,"pet_type":"cat"},"emergency_type":"extreme_heat"}'
```

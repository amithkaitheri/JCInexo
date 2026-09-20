---
name: test-plan
description: Run plan generation with sample profiles to verify the system works end-to-end
---

# Test Plan Generation

Run the READY plan generation API with predefined test profiles to verify the full pipeline works.

## Instructions

1. Ensure the backend is running at http://localhost:8001
2. Run each test profile against each emergency type
3. For each response, verify:
   - HTTP 200 returned
   - `trace_id` is present
   - `plan.risk_level` is one of: Low, Moderate, High, Critical
   - `plan.steps` has at least 3 steps
   - `plan.recommended_location` has name and address
   - `weather.source` contains "Environment Canada"
   - If profile has pet → `plan.pet_guidance` is not null

## Test Profiles

### Profile A — Vulnerable elderly
```json
{"age": 78, "lives_alone": true, "has_ac": false, "has_car": false, "uses_wheelchair": false, "has_pet": true, "pet_type": "cat", "language": "English", "low_budget": true}
```
Expected: High/Critical risk for heat. Cooling centre with transit directions.

### Profile B — Wheelchair user
```json
{"age": 45, "lives_alone": false, "has_ac": true, "has_car": false, "uses_wheelchair": true, "has_pet": false, "language": "English"}
```
Expected: Only wheelchair-accessible locations. Transit directions included.

### Profile C — Young healthy adult (baseline)
```json
{"age": 28, "lives_alone": false, "has_ac": true, "has_car": true, "has_pet": false, "language": "English"}
```
Expected: Lower risk level. More flexibility in recommendations.

## Emergency types to test

- `extreme_heat`
- `flooding`
- `wildfire_smoke`

## Run mode

$ARGUMENTS

Options:
- `all` — Run all profiles × all emergencies (9 tests)
- `quick` — Run Profile A × extreme_heat only (1 test)
- `smoke` — Just hit /api/health and /api/emergencies to verify backend is up

## Example test command

```bash
curl -s -X POST http://localhost:8001/api/plan \
  -H "Content-Type: application/json" \
  -d '{"profile":{"age":78,"lives_alone":true,"has_ac":false,"has_car":false,"has_pet":true,"pet_type":"cat","low_budget":true},"emergency_type":"extreme_heat"}' \
  | python3 -m json.tool
```

---
name: fetch-weather
description: Fetch live Ottawa weather and AQHI data from Environment Canada
---

# Fetch Live Weather Data

Fetch real-time weather conditions and Air Quality Health Index (AQHI) for Ottawa from Environment Canada's public XML feeds.

## Instructions

1. Fetch current weather from:
   `https://dd.weather.gc.ca/citypage_weather/xml/ON/s0000430_e.xml`

2. Fetch AQHI from:
   `https://dd.weather.gc.ca/air_quality/aqhi/ont/observation/realtime/xml/AQ_OBS_CWAO_CURRENT.xml`

3. Parse and display:
   - Current temperature (°C)
   - Weather condition
   - Wind speed
   - Humidity
   - AQHI value and risk level (Low 1-3, Moderate 4-6, High 7-10, Very High 10+)
   - Any active weather warnings

4. These are the same feeds used by `ready/backend/data_sources.py` — the `fetch_weather()` and `fetch_aqhi()` functions.

## Notes

- No API key required — these are public government feeds
- Data updates approximately every 10 minutes
- If feeds are unavailable, the backend falls back to "Data unavailable" with N/A values
- AQHI feed may not always have Ottawa-specific data — falls back to first available community

$ARGUMENTS

"""FastAPI HTTP layer.

Translates HTTP requests into pure-core calls and repository operations, maps
domain errors to HTTP status codes, and enforces the API-key gate on
administrative endpoints.
"""

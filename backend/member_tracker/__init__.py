"""Smart Member Growth Tracker backend package.

This package is split into a pure domain core (``core``) that holds all
deterministic business logic and I/O adapters (``io``) plus the HTTP layer
(``api``). The pure/IO split keeps risk-sensitive logic side-effect free so it
can be verified with property-based tests.
"""

__all__ = ["core", "io", "api"]

"""I/O adapters.

This subpackage confines all side effects — SQLite persistence, snapshot/export
artifact production, wall-clock reads, LLM interpretation, and web search — to
thin adapters, keeping the domain core (``member_tracker.core``) pure.
"""

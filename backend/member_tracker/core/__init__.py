"""Pure domain core.

Every function in this subpackage is pure: it takes explicit inputs (including
the "current date" supplied via the injected :class:`~member_tracker.core.clock.Clock`
provider) and returns values or structured errors with no side effects. This is
what makes the age-out math, milestone evaluation, health scoring, filtering and
natural-language criteria evaluation deterministic and directly amenable to
property-based testing.
"""

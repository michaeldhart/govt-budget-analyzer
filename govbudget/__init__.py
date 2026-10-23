"""Shared, entity-agnostic data model for local-government budget analysis.

Each government body (entity) has its own extraction pipeline under pipelines/<entity_id>/, which writes the
canonical tables defined in govbudget.schema. Everything downstream (series, comparisons, charts, the website)
reads only those tables, so adding an entity never changes the consumers.
"""
from .entity import Entity  # noqa: F401

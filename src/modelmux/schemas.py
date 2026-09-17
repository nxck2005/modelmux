"""JSON schema generation for the versioned core boundary.

``contracts/v1`` schemas are generated from the authoritative Pydantic models
by ``scripts/generate_contract_schemas.py``; the test suite fails when a
committed schema drifts from its model.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from modelmux.router import ApplicationResult, RouteDecision, RoutingRequest

SCHEMA_MODELS: dict[str, type[BaseModel]] = {
    "routing-request.schema.json": RoutingRequest,
    "routing-decision.schema.json": RouteDecision,
    "application-result.schema.json": ApplicationResult,
}


def write_schemas(directory: Path) -> None:
    """Write every committed boundary schema into ``directory``."""
    directory.mkdir(parents=True, exist_ok=True)
    for filename, model in SCHEMA_MODELS.items():
        text = json.dumps(model.model_json_schema(), indent=2) + "\n"
        (directory / filename).write_text(text, encoding="utf-8")

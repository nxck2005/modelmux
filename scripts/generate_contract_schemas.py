"""Regenerate contracts/v1 JSON schemas from the core Pydantic models."""

from __future__ import annotations

from pathlib import Path

from modelmux.schemas import write_schemas

if __name__ == "__main__":
    write_schemas(Path(__file__).resolve().parents[1] / "contracts" / "v1")

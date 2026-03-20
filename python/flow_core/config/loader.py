from __future__ import annotations

from pathlib import Path
from typing import Any, Type, TypeVar

import yaml
from pydantic import BaseModel


T = TypeVar("T", bound=BaseModel)


def load_yaml(path: str | Path, model_type: Type[T] | None = None) -> Any:
    """Load YAML from disk and optionally parse into a pydantic model."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if model_type is None:
        return data
    return model_type.model_validate(data)

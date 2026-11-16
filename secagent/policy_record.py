"""Shared records for policy generation and validation."""

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from .datascope_v1 import DataScope


@dataclass(frozen=True)
class ValidationIssue:
    path: str
    code: str
    message: str


@dataclass(frozen=True)
class PolicyRecord:
    session_id: str
    policy_version: int
    effective_rules: Optional[Dict[str, list]]
    model_candidate: Any
    source: str
    validation_status: str
    validation_results: Tuple[ValidationIssue, ...] = ()
    minimality_status: str = "unverified"
    task_id: Optional[str] = None
    data_scopes: Tuple[DataScope, ...] = ()
    model: Optional[str] = None

    def to_dict(self) -> dict:
        """Return a JSON-ready record, describing legacy Python predicates by name."""
        def serialize(value):
            if isinstance(value, DataScope):
                return value.model_dump(mode="json")
            if isinstance(value, dict):
                return {key: serialize(item) for key, item in value.items()}
            if isinstance(value, (list, tuple)):
                return [serialize(item) for item in value]
            if callable(value):
                return {"python_predicate": getattr(value, "__qualname__", type(value).__qualname__)}
            return value

        return serialize(asdict(self))


@dataclass
class PolicySession:
    session_id: str = field(default_factory=lambda: uuid4().hex)
    task_id: Optional[str] = None
    user_query: Optional[str] = None
    available_tools: List[Dict[str, Any]] = field(default_factory=list)
    security_policy: Optional[Dict[str, list]] = None
    policy_version: int = 0
    records: List[PolicyRecord] = field(default_factory=list)

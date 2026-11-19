"""Shared records for policy generation and validation."""

from copy import deepcopy
from threading import RLock
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple
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
    forbidden_data_scopes: Tuple[DataScope, ...] = ()
    required_data_scopes: Tuple[DataScope, ...] = ()

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


@dataclass(frozen=True)
class AuthorizationSnapshot:
    session_id: str
    task_id: Optional[str]
    policy_version: int
    effective_rules: Optional[Dict[str, list]]
    authorized_data_scopes: Tuple[DataScope, ...]
    forbidden_data_scopes: Tuple[DataScope, ...] = ()
    required_data_scopes: Tuple[DataScope, ...] = ()
    # File access stays disabled until the shared FileScope contract exists.
    authorized_file_scopes: Tuple[()] = ()


class PolicyVersionConflict(RuntimeError):
    """The policy changed while a candidate was being prepared."""


@dataclass
class PolicySession:
    session_id: str = field(default_factory=lambda: uuid4().hex)
    task_id: Optional[str] = None
    user_query: Optional[str] = None
    available_tools: List[Dict[str, Any]] = field(default_factory=list)
    user_identity: Optional[Mapping[str, Any]] = None
    records: List[PolicyRecord] = field(default_factory=list)
    decision_traces: List[Any] = field(default_factory=list)
    _snapshot: Optional[AuthorizationSnapshot] = field(default=None, init=False, repr=False)
    _lock: Any = field(default_factory=RLock, init=False, repr=False, compare=False)

    @property
    def policy_version(self) -> int:
        return self._snapshot.policy_version if self._snapshot is not None else 0

    @property
    def security_policy(self) -> Optional[Dict[str, list]]:
        return get_authorization_snapshot(self).effective_rules

    @property
    def authorized_data_scopes(self) -> Tuple[DataScope, ...]:
        return get_authorization_snapshot(self).authorized_data_scopes

    @property
    def authorized_file_scopes(self) -> Tuple[()]:
        return ()


def get_authorization_snapshot(session: PolicySession) -> AuthorizationSnapshot:
    """Return a detached snapshot suitable for one tool invocation."""
    with session._lock:
        if session._snapshot is None:
            return AuthorizationSnapshot(session.session_id, session.task_id, 0, None, ())
        return deepcopy(session._snapshot)


def create_policy_session(
    *,
    task_id: Optional[str] = None,
    user_identity: Optional[Mapping[str, Any]] = None,
    user_query: Optional[str] = None,
    authorized_data_scopes: Iterable[DataScope] = (),
    forbidden_data_scopes: Iterable[DataScope] = (),
    required_data_scopes: Iterable[DataScope] = (),
) -> PolicySession:
    """Initialize from trusted task and authorization inputs."""
    session = PolicySession(task_id=task_id, user_query=user_query, user_identity=deepcopy(user_identity))
    scopes = tuple(authorized_data_scopes)
    forbidden, required = tuple(forbidden_data_scopes), tuple(required_data_scopes)
    if scopes or forbidden or required:
        publish_policy(session, None, authorized_data_scopes=scopes,
                       forbidden_data_scopes=forbidden, required_data_scopes=required, source="trusted_context")
    return session


def publish_policy(
    session: PolicySession,
    rules: Optional[Dict[str, list]],
    *,
    expected_version: Optional[int] = None,
    authorized_data_scopes: Optional[Iterable[DataScope]] = None,
    forbidden_data_scopes: Optional[Iterable[DataScope]] = None,
    required_data_scopes: Optional[Iterable[DataScope]] = None,
    source: str = "manual",
    candidate: Any = None,
    status: str = "unverified",
    model: Optional[str] = None,
    user_query: Optional[str] = None,
    clear_query: bool = False,
) -> None:
    """Publish one version of tool rules and trusted scopes."""
    prepared = deepcopy(rules)
    if prepared is not None:
        for name, tool_rules in prepared.items():
            prepared[name] = sorted(tool_rules, key=lambda rule: (rule[0], -rule[1]))
    with session._lock:
        previous = get_authorization_snapshot(session)
        if expected_version is not None and previous.policy_version != expected_version:
            raise PolicyVersionConflict("Policy changed while preparing the candidate.")
        scopes = previous.authorized_data_scopes
        if authorized_data_scopes is not None:
            scopes = tuple(
                DataScope.model_validate(scope.model_dump() if isinstance(scope, DataScope) else scope)
                for scope in authorized_data_scopes
            )
        def normalize(values, previous_values):
            if values is None:
                return previous_values
            return tuple(DataScope.model_validate(value.model_dump() if isinstance(value, DataScope) else value) for value in values)

        forbidden = normalize(forbidden_data_scopes, previous.forbidden_data_scopes)
        required = normalize(required_data_scopes, previous.required_data_scopes)
        snapshot = AuthorizationSnapshot(
            session.session_id, session.task_id, previous.policy_version + 1, prepared, scopes, forbidden, required
        )
        record = PolicyRecord(
            session_id=snapshot.session_id,
            task_id=snapshot.task_id,
            policy_version=snapshot.policy_version,
            effective_rules=deepcopy(prepared),
            data_scopes=deepcopy(scopes),
            forbidden_data_scopes=deepcopy(forbidden),
            required_data_scopes=deepcopy(required),
            model_candidate=deepcopy(candidate),
            source=source,
            validation_status=status,
            model=model,
        )
        if clear_query or user_query is not None:
            session.user_query = user_query
        session.records.append(record)
        session._snapshot = snapshot

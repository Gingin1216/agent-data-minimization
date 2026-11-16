"""Trusted task bindings for a stateless MCP proxy."""

from contextlib import contextmanager
from dataclasses import dataclass
from secrets import token_urlsafe
from threading import Lock

from .policy_record import PolicySession


@dataclass
class MCPPolicyBinding:
    session: PolicySession
    ready: bool = False


class MCPPolicyBindings:
    def __init__(self):
        self._sessions = {}
        self._lock = Lock()

    @contextmanager
    def bind(self, session: PolicySession):
        """Issue a bearer token from the trusted task host for this run."""
        token = token_urlsafe(32)
        with self._lock:
            self._sessions[token] = MCPPolicyBinding(session)
        try:
            yield token
        finally:
            with self._lock:
                self._sessions.pop(token, None)

    def resolve(self, token):
        with self._lock:
            return self._sessions.get(token)

"""Compare FileScope draft entries without accessing a filesystem.

The caller supplies logical paths in the same trusted namespace. Filesystem
identity (including aliases), actual content, and tool execution are outside
this comparator. Inputs are mappings so the shared FileScope type can be
defined by the execution layer independently.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from .datascope_check import ScopeCheckResult, ScopeCheckStatus


_READ_ONLY_OPERATIONS = frozenset({"READ", "LIST", "STAT"})
_FORBIDDEN_OPERATIONS = _READ_ONLY_OPERATIONS | {"WRITE", "DELETE", "APPEND", "MOVE", "EXEC"}
_DRAFT_FIELDS = frozenset({
    "path", "operations", "content_part", "max_bytes", "content_sha256", "recursive",
    "reason", "directory_sha256", "directory_sha256_note",
})


@dataclass(frozen=True, order=True)
class FileAccess:
    path: str
    operation: str


@dataclass(frozen=True)
class FileScopeComparison:
    authorization: ScopeCheckResult
    forbidden_clear: ScopeCheckResult
    path_operation_sufficiency: ScopeCheckResult
    path_operation_minimality: ScopeCheckResult
    missing_accesses: tuple[FileAccess, ...] = ()
    extra_accesses: tuple[FileAccess, ...] = ()


@dataclass(frozen=True)
class _Entry:
    access: FileAccess
    recursive: bool
    max_bytes: int | None = None
    content_sha256: str | None = None


class _ScopeInputError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _validate_path(value: object) -> None:
    # Check the draft's textual form only. Do not normalize paths or resolve
    # them with the host OS; member 3's adapter will own that boundary.
    if not isinstance(value, str) or re.search(r'[\\:*?<>"|\x00-\x1f\x7f]', value):
        raise _ScopeInputError("invalid_file_path", "Expected a relative slash-separated logical path.")
    parts = value.removesuffix("/").split("/")
    if any(not part or part in {".", ".."} or part.endswith((".", " ")) for part in parts):
        raise _ScopeInputError("invalid_file_path", "Path has an empty, relative or ambiguous component.")


def _parse_item(item: object, *, forbidden: bool) -> list[_Entry]:
    if not isinstance(item, Mapping) or set(item) - _DRAFT_FIELDS:
        raise _ScopeInputError("invalid_file_scope", "Expected an entry with FileScope draft fields.")
    path = item.get("path")
    _validate_path(path)
    is_directory = path.endswith("/")
    operations = item.get("operations")
    allowed = _FORBIDDEN_OPERATIONS if forbidden else _READ_ONLY_OPERATIONS
    if (not isinstance(operations, (list, tuple)) or not operations
            or any(not isinstance(op, str) or op not in allowed for op in operations)):
        raise _ScopeInputError("unsupported_file_operation", "Expected supported operations for this scope.")

    recursive = item.get("recursive", False)
    if type(recursive) is not bool or (recursive and not is_directory):
        raise _ScopeInputError("invalid_file_scope", "Recursive scopes must designate a directory.")
    if recursive and not forbidden:
        raise _ScopeInputError("unsupported_recursive_scope", "Only recursive forbidden selectors are supported.")
    if not forbidden and (("READ" in operations and is_directory) or ("LIST" in operations and not is_directory)):
        raise _ScopeInputError("unsupported_file_resource", "READ requires a file and LIST requires a directory.")

    part = item.get("content_part")
    cap = item.get("max_bytes")
    fingerprint = item.get("content_sha256")
    if part == "first_lines":
        raise _ScopeInputError("unsupported_partial_read", "The line count for first_lines is not defined.")
    if "READ" not in operations:
        if part not in (None, "none") or cap is not None or fingerprint is not None:
            raise _ScopeInputError("invalid_file_scope", "Read bounds apply only to READ.")
    elif forbidden:
        if part not in (None, "full") or cap is not None or fingerprint is not None:
            raise _ScopeInputError("unsupported_forbidden_read", "Content-specific forbidden selectors are unresolved.")
    elif (part != "full" or type(cap) is not int or cap <= 0 or not isinstance(fingerprint, str)
          or re.fullmatch(r"[0-9a-fA-F]{16}", fingerprint) is None):
        raise _ScopeInputError("invalid_read_scope", "Full READ requires a positive byte cap and a 16-digit hex fingerprint.")

    return [_Entry(FileAccess(path, op), recursive, cap if op == "READ" else None,
                   fingerprint.lower() if op == "READ" and fingerprint else None)
            for op in operations]


def _parse_collection(value: object, name: str) -> tuple[list[_Entry], ScopeCheckResult | None]:
    if not isinstance(value, (list, tuple)):
        return [], ScopeCheckResult(ScopeCheckStatus.UNKNOWN, "invalid_file_scope_collection",
                                    "Expected an explicit list or tuple of FileScope entries.", path=name)
    entries = []
    for index, item in enumerate(value):
        try:
            entries.extend(_parse_item(item, forbidden=name == "forbidden_scopes"))
        except _ScopeInputError as exc:
            return [], ScopeCheckResult(ScopeCheckStatus.UNKNOWN, exc.code, str(exc), path=f"{name}[{index}]")
    return entries, None


def _check_authorization(requested: list[_Entry], authorized: list[_Entry]) -> ScopeCheckResult:
    uncertain = None
    for entry in requested:
        candidates = [grant for grant in authorized if grant.access == entry.access]
        if not candidates:
            code = "file_access_not_authorized"
            message = "No grant covers this path and operation."
        elif entry.access.operation != "READ":
            continue
        else:
            # A single grant must cover both the byte cap and the declared version.
            within_limit = [grant for grant in candidates if entry.max_bytes <= grant.max_bytes]
            if any(grant.content_sha256 == entry.content_sha256 for grant in within_limit):
                continue
            if within_limit:
                uncertain = ScopeCheckResult(ScopeCheckStatus.UNKNOWN, "file_version_mismatch",
                                             "No grant within the byte cap has this declared fingerprint.",
                                             path=entry.access.path)
                continue
            code = "file_byte_limit_exceeded"
            message = "The requested byte cap exceeds the available grants."
        return ScopeCheckResult(ScopeCheckStatus.VIOLATED, code, message, path=entry.access.path)
    return uncertain or ScopeCheckResult(ScopeCheckStatus.PROVED, "file_access_covered",
                                         "All declared accesses are covered by matching grants.")


def _check_forbidden(requested: list[_Entry], forbidden: list[_Entry]) -> ScopeCheckResult:
    uncertain = None
    for entry in requested:
        path, operation = entry.access.path, entry.access.operation
        for rule in forbidden:
            selector = rule.access.path
            if operation != rule.access.operation:
                continue
            descendant = selector.endswith("/") and path != selector and path.startswith(selector)
            if path == selector or (rule.recursive and descendant):
                return ScopeCheckResult(ScopeCheckStatus.VIOLATED, "forbidden_file_access",
                                        f"{operation} matches an explicit forbidden entry.", path=path)
            if descendant and operation == "READ":
                # A slash-terminated READ prohibition without recursive is used
                # in FS-004/006. Whether it covers child files is unresolved.
                uncertain = ScopeCheckResult(ScopeCheckStatus.UNKNOWN, "ambiguous_directory_read_prohibition",
                                             "A non-recursive directory read prohibition may apply.", path=path)
    return uncertain or ScopeCheckResult(ScopeCheckStatus.PROVED, "no_forbidden_file_access",
                                         "No declared access matches the forbidden entries.")


def compare_file_scopes(
    *, requested_scopes, authorized_scopes, forbidden_scopes, required_scopes,
) -> FileScopeComparison:
    """Compare four independent inputs made from the FileScope draft fields.

    Sufficiency/minimality compare only (exact path, operation) pairs against
    the required list. A byte cap or declared hash is not evidence of task
    completion. Extra accesses are relative to the required list, including
    optional TaskSpec accesses if requested. Empty requirements are ambiguous.
    Authorization and explicit prohibitions remain independent decisions.
    """
    requested, request_error = _parse_collection(requested_scopes, "requested_scopes")
    if request_error:
        return FileScopeComparison(request_error, request_error, request_error, request_error)

    authorized, auth_error = _parse_collection(authorized_scopes, "authorized_scopes")
    forbidden, forbid_error = _parse_collection(forbidden_scopes, "forbidden_scopes")
    authorization = auth_error or _check_authorization(requested, authorized)
    forbidden_clear = forbid_error or _check_forbidden(requested, forbidden)

    required, task_error = _parse_collection(required_scopes, "required_scopes")
    if task_error or not required:
        unknown = task_error or ScopeCheckResult(ScopeCheckStatus.UNKNOWN, "missing_required_file_scope",
                                                 "No task-required file accesses were specified.", path="required_scopes")
        return FileScopeComparison(authorization, forbidden_clear, unknown, unknown)

    requested_accesses = {entry.access for entry in requested}
    required_accesses = {entry.access for entry in required}
    missing = tuple(sorted(required_accesses - requested_accesses))
    extra = tuple(sorted(requested_accesses - required_accesses))
    sufficiency = (ScopeCheckResult(ScopeCheckStatus.VIOLATED, "required_file_accesses_missing",
                                    "Some task-required path and operation pairs are absent.") if missing else
                   ScopeCheckResult(ScopeCheckStatus.PROVED, "required_file_accesses_covered",
                                    "All task-required path and operation pairs are present."))
    minimality = (ScopeCheckResult(ScopeCheckStatus.VIOLATED, "extra_file_accesses_requested",
                                  "Some path and operation pairs are outside the required list.") if extra else
                  ScopeCheckResult(ScopeCheckStatus.PROVED, "no_extra_file_accesses",
                                   "No path and operation pairs exceed the required list."))
    return FileScopeComparison(authorization, forbidden_clear, sufficiency, minimality, missing, extra)

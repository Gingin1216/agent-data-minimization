"""Independent FS v1.1 pilot: guarded single-file full READ.

Only trusted hosts construct the manifest, root and policy snapshot. This is not
an extension of the v1 database PolicySession. Never expose the constructor or
filesystem handles to an Agent. The fixture root is host-owned and read-only to
untrusted code; concurrent external writers are outside the pilot contract.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from hashlib import sha256
import os
from pathlib import Path
import re
import stat
from types import MappingProxyType
from typing import Any, Mapping
import unicodedata


class FileScopeError(ValueError):
    pass


def validate_logical_path(path: str, *, allow_directory: bool = False) -> str:
    """Reject aliases instead of normalizing untrusted paths. Keep exact case."""
    if type(path) is not str or not path or unicodedata.normalize('NFC', path) != path:
        raise FileScopeError('invalid logical path')
    if re.search(r'[\\:*?<>"|\x00-\x1f\x7f]', path):
        raise FileScopeError('invalid logical path')
    if path.endswith('/') and not allow_directory:
        raise FileScopeError('READ requires a file')
    for part in path.removesuffix('/').split('/'):
        if not part or part in {'.', '..'} or part.endswith(('.', ' ')):
            raise FileScopeError('invalid logical path component')
        # Windows device names, including extensions and superscript digits.
        if re.fullmatch(r'(?i:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])', part.split('.')[0].rstrip(' ')):
            raise FileScopeError('device path')
    return path


@dataclass(frozen=True)
class FileVersion:
    size_bytes: int
    content_sha256: str
    # The comparator consumes a 16-digit prefix; full digest independently
    # verifies the actual file bytes before release.
    full_sha256: str

    @classmethod
    def from_bytes(cls, data: bytes) -> FileVersion:
        digest = sha256(data).hexdigest()
        return cls(len(data), digest[:16], digest)

    def __post_init__(self):
        if (type(self.size_bytes) is not int or self.size_bytes <= 0
                or not re.fullmatch(r'[0-9a-f]{64}', self.full_sha256)
                or self.content_sha256 != self.full_sha256[:16]):
            raise FileScopeError('invalid trusted file version')


def build_fixture_manifest(files: Mapping[str, str]) -> dict[str, FileVersion]:
    """Trusted offline input e.g. reset.FILES, NOT files from an Agent path.

    reset.py writes UTF-8 with LF; no live filesystem file is opened here.
    This helper intentionally supports only the benchmark text fixtures.
    """
    return {validate_logical_path(path): FileVersion.from_bytes(text.encode('utf-8'))
            for path, text in files.items()}


def full_read_scope(path: str, version: FileVersion, *, max_bytes: int | None = None) -> dict:
    path = validate_logical_path(path)
    cap = version.size_bytes if max_bytes is None else max_bytes
    if type(cap) is not int or cap <= 0:
        raise FileScopeError('positive byte cap required')
    return {'path': path, 'operations': ['READ'], 'content_part': 'full',
            'max_bytes': cap, 'content_sha256': version.content_sha256, 'recursive': False}


def _copy_scopes(scopes, *, label):
    if not isinstance(scopes, (tuple, list)):
        raise FileScopeError(f'{label} must be an explicit list or tuple')
    result = []
    for entry in scopes:
        if not isinstance(entry, Mapping):
            raise FileScopeError(f'invalid {label} entry')
        item = deepcopy(dict(entry))
        validate_logical_path(item.get('path'), allow_directory=True)
        result.append(item)
    return tuple(result)


@dataclass(frozen=True)
class FileReadResult:
    ok: bool
    code: str
    path: str | None = None
    content: str | None = field(default=None, repr=False)
    actual_bytes: int | None = None
    content_sha256: str | None = None
    policy_version: int | None = None
    requested_scopes: tuple[dict, ...] = field(default_factory=tuple, repr=False)
    comparison: Any = field(default=None, repr=False)

    def to_agent_payload(self) -> dict:
        if not self.ok:
            # No forbidden paths, manifest data, policy details or file contents.
            return {'ok': False, 'code': self.code}
        return {'ok': True, 'path': self.path, 'operation': 'READ',
                'content': self.content, 'actual_bytes': self.actual_bytes,
                'content_sha256': self.content_sha256}


def _is_link_or_reparse(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400))


def _check_component(info, *, directory):
    if _is_link_or_reparse(info):
        raise FileScopeError('link or reparse point')
    if directory:
        if not stat.S_ISDIR(info.st_mode):
            raise FileScopeError('not a directory')
    elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise FileScopeError('not a regular unaliased file')


class GuardedFileReader:
    """Trusted host binds one detached, immutable-by-contract pilot policy.

    Build a new reader to publish a new policy. No mutable external scope lists
    are retained. There is no role ACL inference or live PolicySession binding.
    The real member-4 comparator is mandatory; absence/error is fail-closed.
    """
    def __init__(self, *, root: Path, manifest: Mapping[str, FileVersion],
                 authorized_scopes, forbidden_scopes, required_scopes,
                 policy_version: int = 1):
        if type(policy_version) is not int or policy_version <= 0:
            raise FileScopeError('invalid policy version')
        self._root = Path(root).absolute()
        _check_component(self._root.lstat(), directory=True)
        # Refuse aliased root, including a symlinked parent. A trusted host
        # chooses a real fixture directory, never a path supplied by an Agent.
        if self._root.resolve(strict=True) != self._root:
            raise FileScopeError('aliased fixture root')
        copied = dict(manifest)
        aliases = {}
        for path, version in copied.items():
            validate_logical_path(path)
            if not isinstance(version, FileVersion):
                raise FileScopeError('invalid manifest version')
            parts = path.split('/')
            for i in range(1, len(parts) + 1):
                prefix = '/'.join(parts[:i])
                key = prefix.casefold()
                if key in aliases and aliases[key] != prefix:
                    raise FileScopeError('case-ambiguous manifest')
                aliases[key] = prefix
        self._manifest = MappingProxyType(copied)
        self._authorized = _copy_scopes(authorized_scopes, label='authorized_scopes')
        self._forbidden = _copy_scopes(forbidden_scopes, label='forbidden_scopes')
        self._required = _copy_scopes(required_scopes, label='required_scopes')
        # The pilot manifest defines the complete trusted namespace. Require
        # even forbidden selectors to use its exact spelling, preventing a
        # case/alias discrepancy from silently bypassing a prohibition.
        directories = {prefix + '/' for path in copied for prefix in
                       ('/'.join(path.split('/')[:i]) for i in range(1, len(path.split('/'))))}
        if any(directory[:-1] in copied for directory in directories):
            raise FileScopeError('manifest file-directory conflict')
        for entry in self._authorized + self._forbidden + self._required:
            if entry['path'] not in copied and entry['path'] not in directories:
                raise FileScopeError('scope path outside canonical manifest namespace')
        self._policy_version = policy_version

    def _read_checked_file(self, logical_path: str, version: FileVersion, cap: int) -> bytes:
        """Only reached after comparator approves. Do not read over cap.

        POSIX uses dirfd + O_NOFOLLOW at each component. Windows uses lstat
        component checks and a host-owned stable root (no adversarial writes).
        The content hash protects release, not arbitrary concurrent OS changes.
        """
        parts = logical_path.split('/')
        opened_dirs = []
        fd = None
        try:
            flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
            if os.name == 'posix' and os.open in os.supports_dir_fd and hasattr(os, 'O_NOFOLLOW') and hasattr(os, 'O_DIRECTORY'):
                dir_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                current = os.open(self._root, dir_flags)
                opened_dirs.append(current)
                for part in parts[:-1]:
                    current = os.open(part, dir_flags, dir_fd=current)
                    opened_dirs.append(current)
                fd = os.open(parts[-1], flags, dir_fd=current)
            else:
                target = self._root
                for i, part in enumerate(parts):
                    target = target / part
                    _check_component(target.lstat(), directory=i < len(parts) - 1)
                if target.resolve(strict=True) != target:
                    raise FileScopeError('aliased file path')
                fd = os.open(target, flags)
            before = os.fstat(fd)
            _check_component(before, directory=False)
            if before.st_size != version.size_bytes or before.st_size > cap:
                raise FileScopeError('file size mismatch')
            # Read no more than the expected full version length, never cap+1.
            chunks = []
            remaining = version.size_bytes
            while remaining:
                chunk = os.read(fd, min(remaining, 65536))
                if not chunk:
                    raise FileScopeError('short read')
                chunks.append(chunk)
                remaining -= len(chunk)
            after = os.fstat(fd)
            if (after.st_size, after.st_mtime_ns, after.st_ctime_ns) != (before.st_size, before.st_mtime_ns, before.st_ctime_ns):
                raise FileScopeError('file changed during read')
            data = b''.join(chunks)
            if sha256(data).hexdigest() != version.full_sha256:
                raise FileScopeError('content fingerprint mismatch')
            return data
        finally:
            if fd is not None:
                os.close(fd)
            for d in reversed(opened_dirs):
                os.close(d)

    def read(self, path: str, max_bytes: int | None = None) -> FileReadResult:
        # Trusted in-memory metadata only until the gate returns PROVED.
        requested = ()
        comparison = None
        def deny(code):
            return FileReadResult(False, code, policy_version=self._policy_version,
                                  requested_scopes=deepcopy(requested), comparison=comparison)
        try:
            path = validate_logical_path(path)
            version = self._manifest.get(path)
            if version is None:
                return deny('ACCESS_DENIED')
            requested = (full_read_scope(path, version, max_bytes=max_bytes),)
            cap = requested[0]['max_bytes']
            from .file_scope_check import compare_file_scopes
            from .datascope_check import ScopeCheckStatus
            comparison = compare_file_scopes(
                requested_scopes=deepcopy(requested),
                authorized_scopes=deepcopy(self._authorized),
                forbidden_scopes=deepcopy(self._forbidden),
                required_scopes=deepcopy(self._required),
            )
            checks = (comparison.authorization, comparison.forbidden_clear,
                      comparison.path_operation_sufficiency, comparison.path_operation_minimality)
            if any(check.status != ScopeCheckStatus.PROVED for check in checks):
                return deny('ACCESS_DENIED')
            if version.size_bytes > cap:
                return deny('FULL_READ_CAP_INSUFFICIENT')
            data = self._read_checked_file(path, version, cap)
            text = data.decode('utf-8', errors='strict')
            return FileReadResult(True, 'ALLOW', path, text, len(data), version.content_sha256,
                                  self._policy_version, deepcopy(requested), comparison)
        except Exception:
            return deny('FILE_READ_REJECTED')


def make_mock_fs_read(reader: GuardedFileReader):
    """Only this closure is exposed as the pilot Agent tool."""
    def mock_fs_read(path: str, max_bytes: int | None = None) -> dict:
        return reader.read(path, max_bytes).to_agent_payload()
    return mock_fs_read

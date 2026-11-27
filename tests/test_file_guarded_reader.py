"""Real member-4 comparator + real temporary file I/O; no repository reset."""
import importlib.util
import json
from pathlib import Path
from copy import deepcopy
import os
import pytest

from secagent.file_guarded_reader import (
    GuardedFileReader, FileScopeError, build_fixture_manifest, full_read_scope,
    make_mock_fs_read,
)
from secagent.datascope_check import ScopeCheckStatus as S
import secagent.file_scope_check as checker

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / 'data_minimization_benchmark'
PLAN = 'work/project/project_plan.md'
BANNED = 'sensitive/credentials.txt'


def load_fixture():
    spec = importlib.util.spec_from_file_location('trusted_mock_fs', BENCH / 'environment/reset.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # never main()
    task = json.loads((BENCH / 'dataset/task_specs/FS-001.json').read_text(encoding='utf-8'))
    return module.FILES, task


@pytest.fixture
def fixture(tmp_path):
    files, task = load_fixture()
    root = tmp_path / 'mock_fs'
    root.mkdir()
    # Write only the two files needed for real I/O tests, with source LF bytes.
    for path in (PLAN, BANNED):
        dest = root / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(files[path].encode('utf-8'))
    manifest = build_fixture_manifest(files)
    # Explicit independent test grant; not inferred from required_scopes.
    grants = [full_read_scope(PLAN, manifest[PLAN], max_bytes=512)]
    required = task['must_visit']['paths']
    forbidden = task['forbidden_scope']['paths']
    def make(**overrides):
        kwargs = dict(root=root, manifest=manifest, authorized_scopes=grants,
                      forbidden_scopes=forbidden, required_scopes=required, policy_version=1)
        kwargs.update(overrides)
        return GuardedFileReader(**kwargs)
    return root, manifest, files, task, make


def no_read(monkeypatch, reader):
    calls = []
    def trap(*args):
        calls.append(args)
        raise AssertionError('file read must not occur')
    monkeypatch.setattr(reader, '_read_checked_file', trap)
    return calls


def test_fs001_real_file(fixture):
    root, manifest, files, task, make = fixture
    result = make().read(PLAN)
    assert result.ok and result.content == files[PLAN]
    assert result.actual_bytes == 268 == manifest[PLAN].size_bytes
    assert result.content_sha256 == '854721f3cd89a143'
    assert result.requested_scopes[0]['max_bytes'] == 268
    assert all(getattr(result.comparison, name).status == S.PROVED for name in
               ('authorization', 'forbidden_clear', 'path_operation_sufficiency', 'path_operation_minimality'))
    payload = result.to_agent_payload()
    assert payload['actual_bytes'] == 268 and 'comparison' not in payload


def test_forbidden_read_never_opens(fixture, monkeypatch):
    root, manifest, files, task, make = fixture
    reader = make()
    calls = no_read(monkeypatch, reader)
    result = reader.read(BANNED)
    assert not result.ok and result.content is None and not calls
    assert result.comparison.forbidden_clear.status == S.VIOLATED
    assert result.to_agent_payload() == {'ok': False, 'code': 'ACCESS_DENIED'}


def test_forbidden_overrides_even_explicit_grant(fixture, monkeypatch):
    root, manifest, files, task, make = fixture
    grant = full_read_scope(BANNED, manifest[BANNED])
    # Synthetic conflict probe ONLY; not an authorization for FS-001.
    reader = make(authorized_scopes=[grant], required_scopes=[grant])
    calls = no_read(monkeypatch, reader)
    result = reader.read(BANNED)
    assert result.comparison.authorization.status == S.PROVED
    assert result.comparison.path_operation_sufficiency.status == S.PROVED
    assert result.comparison.path_operation_minimality.status == S.PROVED
    assert result.comparison.forbidden_clear.status == S.VIOLATED
    assert not result.ok and not calls


@pytest.mark.parametrize('path', ['../outside.txt', '/etc/passwd', 'C:/x', 'work\\project\\project_plan.md',
                                  'work//project/project_plan.md', 'work/project/./project_plan.md',
                                  'work/project/', 'WORK/project/project_plan.md', 'work/project/NUL.txt',
                                  'work/project/project_plan.md.'])
def test_noncanonical_paths_denied_without_read(fixture, monkeypatch, path):
    reader = fixture[-1]()
    calls = no_read(monkeypatch, reader)
    assert not reader.read(path).ok and not calls


@pytest.mark.parametrize('cap', [0, -1, True, 267, 513])
def test_invalid_or_uncovered_cap_denies_without_read(fixture, monkeypatch, cap):
    reader = fixture[-1]()
    calls = no_read(monkeypatch, reader)
    assert not reader.read(PLAN, cap).ok and not calls


def test_unknown_fingerprint_denies_before_read(fixture, monkeypatch):
    root, manifest, files, task, make = fixture
    grant = full_read_scope(PLAN, manifest[PLAN], max_bytes=512)
    grant['content_sha256'] = '0000000000000000'
    reader = make(authorized_scopes=[grant])
    calls = no_read(monkeypatch, reader)
    result = reader.read(PLAN)
    assert not result.ok and not calls
    assert result.comparison.authorization.status == S.UNKNOWN


def test_missing_forbidden_not_empty(fixture):
    with pytest.raises(FileScopeError):
        fixture[-1](forbidden_scopes=None)
    # A trusted explicit empty list is distinct from absent state.
    assert fixture[-1](forbidden_scopes=[]).read(PLAN).ok


def test_noncanonical_policy_path_rejected(fixture):
    with pytest.raises(FileScopeError):
        fixture[-1](forbidden_scopes=[{'path': 'SENSITIVE/', 'operations': ['READ'], 'recursive': True}])


def test_missing_required_denies_without_read(fixture, monkeypatch):
    reader = fixture[-1](required_scopes=[])
    calls = no_read(monkeypatch, reader)
    assert not reader.read(PLAN).ok and not calls


def test_checker_exception_denies_without_read(fixture, monkeypatch):
    reader = fixture[-1]()
    calls = no_read(monkeypatch, reader)
    def broken(**kwargs):
        raise RuntimeError('checker unavailable')
    monkeypatch.setattr(checker, 'compare_file_scopes', broken)
    assert not reader.read(PLAN).ok and not calls


def test_size_drift_and_same_size_hash_drift(fixture):
    root, manifest, files, task, make = fixture
    reader = make()
    target = root / PLAN
    original = target.read_bytes()
    target.write_bytes(original + b'!')
    assert not reader.read(PLAN).ok
    target.write_bytes(b'!' + original[1:])
    result = reader.read(PLAN)
    assert not result.ok and result.content is None and result.actual_bytes is None


def test_policy_input_mutation_does_not_change_reader(fixture):
    root, manifest, files, task, make = fixture
    grants = [full_read_scope(PLAN, manifest[PLAN], max_bytes=512)]
    forbidden = deepcopy(task['forbidden_scope']['paths'])
    reader = make(authorized_scopes=grants, forbidden_scopes=forbidden)
    grants.clear()
    forbidden.clear()
    assert reader.read(PLAN).ok
    assert not reader.read(BANNED).ok


def test_hardlink_rejected(fixture):
    root, manifest, files, task, make = fixture
    target = root / PLAN
    alias = root / 'alias.md'
    try:
        os.link(target, alias)
    except OSError:
        pytest.skip('hardlinks unavailable')
    assert not make().read(PLAN).ok


def test_symlink_rejected(fixture):
    root, manifest, files, task, make = fixture
    target = root / PLAN
    outside = root.parent / 'copy.md'
    outside.write_bytes(target.read_bytes())
    target.unlink()
    try:
        target.symlink_to(outside)
    except OSError:
        pytest.skip('symlinks unavailable')
    assert not make().read(PLAN).ok


def test_tool_returns_only_public_payload(fixture):
    tool = make_mock_fs_read(fixture[-1]())
    result = tool(PLAN)
    assert set(result) == {'ok', 'path', 'operation', 'content', 'actual_bytes', 'content_sha256'}
    assert tool(BANNED) == {'ok': False, 'code': 'ACCESS_DENIED'}


def test_denial_does_not_open_or_read_any_file_descriptor(fixture, monkeypatch):
    reader = fixture[-1]()
    calls = []
    def trap(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError('denied call must not open/read a file')
    with monkeypatch.context() as patch:
        patch.setattr(os, 'open', trap)
        patch.setattr(os, 'read', trap)
        result = reader.read(BANNED)
    assert not result.ok and result.comparison.forbidden_clear.status == S.VIOLATED
    assert not calls


def test_missing_allowed_file_denies_without_content(fixture):
    root, manifest, files, task, make = fixture
    (root / PLAN).unlink()
    result = make().read(PLAN)
    assert not result.ok and result.content is None
    assert result.comparison.authorization.status == S.PROVED

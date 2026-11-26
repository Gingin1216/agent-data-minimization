"""Read existing benchmark files; never runs reset.py or rewrites any data."""
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from secagent.file_guarded_reader import GuardedFileReader, build_fixture_manifest, full_read_scope, make_mock_fs_read

BENCH = ROOT / 'data_minimization_benchmark'
PLAN = 'work/project/project_plan.md'

def main():
    spec = importlib.util.spec_from_file_location('trusted_mock_fs', BENCH / 'environment/reset.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    task = json.loads((BENCH / 'dataset/task_specs/FS-001.json').read_text(encoding='utf-8'))
    manifest = build_fixture_manifest(module.FILES)
    reader = GuardedFileReader(
        root=BENCH / 'environment/filesystem', manifest=manifest,
        authorized_scopes=[full_read_scope(PLAN, manifest[PLAN], max_bytes=512)],
        forbidden_scopes=task['forbidden_scope']['paths'],
        required_scopes=task['must_visit']['paths'], policy_version=1,
    )
    # Independent trusted test grant above; not a production role ACL.
    tool = make_mock_fs_read(reader)
    allowed = tool(PLAN)
    denied = reader.read('sensitive/credentials.txt')
    print(json.dumps({'allowed': allowed, 'denied': denied.to_agent_payload(),
                      'forbidden_status': str(denied.comparison.forbidden_clear.status.value)},
                     ensure_ascii=False, indent=2))
    assert allowed['ok'] and allowed['actual_bytes'] == 268
    assert allowed['content_sha256'] == '854721f3cd89a143'
    assert not denied.ok and denied.content is None

if __name__ == '__main__':
    main()

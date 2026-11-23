"""Run a real-model, synthetic read-only policy lifecycle.

Configure OPENAI_API_KEY, optional OPENAI_BASE_URL and SECAGENT_POLICY_MODEL
in the launching shell. Supply a manually verified commit via --commit.
"""
import argparse
import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from secagent import (
    create_policy_session, generate_security_policy, generate_update_security_policy,
    get_authorization_snapshot, update_available_tools,
)
from secagent.tool import check_tool_call, policy_model


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--commit", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    session = create_policy_session(task_id="member5-real-model-readonly", forbidden_data_scopes=[])
    task = (
        "Use lookup_catalog_key to resolve the key of the selected synthetic catalog entry. "
        "Then use read_catalog_entry with that key to read its value. "
        "Read only the selected entry and do not read other entries."
    )
    report = {
        "date": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "model": policy_model, "commit": args.commit,
        "session_id": session.session_id, "task_id": session.task_id,
        "status": "running", "events": [],
    }
    update_available_tools([
        {"name": "lookup_catalog_key", "description": "Resolve selected entry key in synthetic catalog.", "args": {}},
        {"name": "read_catalog_entry", "description": "Read a single synthetic catalog entry by resolved key.",
         "args": {"key": {"type": "string", "description": "Key returned by lookup_catalog_key."}}},
    ], session=session)
    phase = "initial_generation"
    try:
        generate_security_policy(task, session=session)
        report["events"].append({"phase": phase, "policy_version": session.policy_version})
        phase = "lookup"
        snapshot = get_authorization_snapshot(session)
        check_tool_call("lookup_catalog_key", {}, session=session, snapshot=snapshot)
        # This local tool only reads fixed synthetic data.
        lookup_result = {"selected_key": "x", "source": "trusted synthetic catalog"}
        report["events"].append({"phase": phase, "policy_version": snapshot.policy_version, "result": lookup_result})
        phase = "policy_update"
        version_before = session.policy_version
        generate_update_security_policy(
            [{"name": "lookup_catalog_key", "args": {}}],
            json.dumps(lookup_result), manual_check=False, session=session,
        )
        report["events"].append({
            "phase": phase, "version_before": version_before, "version_after": session.policy_version,
        })
        if session.policy_version == version_before:
            raise RuntimeError("Model did not produce an accepted update; update milestone is unverified.")
        phase = "read"
        snapshot = get_authorization_snapshot(session)
        check_tool_call("read_catalog_entry", {"key": "x"}, session=session, snapshot=snapshot)
        catalog = {"x": "synthetic public value", "y": "unselected synthetic value"}
        report["events"].append({"phase": phase, "policy_version": snapshot.policy_version, "result": catalog["x"]})
        report["status"] = "passed"
    except Exception as error:
        message = str(error)
        for name, value in os.environ.items():
            if value and ("API_KEY" in name or "TOKEN" in name):
                message = message.replace(value, "[REDACTED]")
        report.update(status="failed", failed_phase=phase, error_type=type(error).__name__, error=message)
    finally:
        report["records"] = [record.to_dict() for record in session.records]
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": report["status"], "output": str(args.output)}, ensure_ascii=False))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

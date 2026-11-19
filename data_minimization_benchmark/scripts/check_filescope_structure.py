"""FileScope 结构一致性核对（不依赖执行栈）。

用途：
    v1 硬指标只出 DB 核心集，FS 18 条为 extension。本脚本在标注层核对
    FileScope v0.1 结构是否自洽，为「文件系统维度标注已就绪、仅执行栈待
    v1.1」这一陈述提供可复现依据。

判定依据全部取自 docs/04_filescope_v0.1.md §2：
    operations ∈ {LIST, READ, STAT}（不支持 WRITE / DELETE）
    content_part ∈ {full, first_lines, none}
    max_bytes / content_sha256 在 READ 时必填

检查项：
    A 结构合法性（字段、取值域）
    B must 与 forbidden 冲突（含内容粒度例外：must 限 first_lines 时，
      同路径全文 READ 的 forbidden 表示"越界读取"，不算冲突）
    C 动作层（expected_action 合法；DENY/CONSTRAIN 需 implementation_action）
    D 路径存在性（environment/filesystem 未生成则跳过）
    E untrusted 对齐（不可信来源不在 must/may 内时，forbidden 应覆盖）
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPECS_DIR = ROOT / "dataset" / "task_specs"
FS_ROOT = ROOT / "environment" / "filesystem"

OPS = {"READ", "LIST", "STAT"}
WRITE_OPS = {"WRITE", "DELETE", "APPEND", "MOVE", "EXEC"}
CONTENT_PARTS = {"full", "first_lines", "none"}
ACTIONS = {"ALLOW", "CONSTRAIN", "DENY", "ASK"}


def load_specs():
    return [json.loads(p.read_text(encoding="utf-8"))
            for p in sorted(SPECS_DIR.glob("FS-*.json"))]


def norm(p: str) -> str:
    return p.strip().rstrip("/")


class Checker:
    def __init__(self):
        self.errors = []
        self.infos = []
        self.warnings = []

    def err(self, tid, code, detail):
        self.errors.append((tid, code, detail))

    def info(self, tid, code, detail):
        self.infos.append((tid, code, detail))

    def warn(self, tid, code, detail):
        self.warnings.append((tid, code, detail))

    def entries(self, tid, scope_name, raw, need_nonempty, requires_reason=False,
                require_read_limits=False):
        if not isinstance(raw, list):
            self.err(tid, "SCOPE_NOT_LIST", scope_name)
            return []
        if need_nonempty and not raw:
            self.err(tid, "EMPTY_SCOPE", f"{scope_name} 为空（ALLOW/CONSTRAIN 需要非空）")
            return []
        if not need_nonempty and not raw:
            self.info(tid, "EMPTY_MAY_BE_LEGAL", f"{scope_name} 为空（DENY 允许）")
        out = []
        for i, entry in enumerate(raw):
            where = f"{scope_name}[{i}]"
            if not isinstance(entry, dict):
                self.err(tid, "ENTRY_NOT_OBJECT", where)
                continue
            p = entry.get("path")
            if not isinstance(p, str) or not p.strip():
                self.err(tid, "PATH_MISSING", where)
                continue
            ops = entry.get("operations")
            if not isinstance(ops, list) or not ops:
                self.err(tid, "OPS_MISSING", where)
                continue
            bad = [o for o in ops if o not in OPS]
            if bad:
                writable = [o for o in bad if o in WRITE_OPS]
                if writable:
                    self.info(tid, "OUT_OF_SUBSET",
                              f"{where} 操作 {writable} 超出 FileScope 支持集，按「超支持集反例」记录")
                other = [o for o in bad if o not in WRITE_OPS]
                if other:
                    self.err(tid, "OPS_ILLEGAL",
                             f"{where} 操作 {other} 既不在支持集也不属写操作反例")
            cp = entry.get("content_part")
            if cp is not None and cp not in CONTENT_PARTS:
                self.err(tid, "CONTENT_PART_ILLEGAL", f"{where} content_part={cp}")
            mb = entry.get("max_bytes")
            if mb is not None and (not isinstance(mb, int) or isinstance(mb, bool) or mb <= 0):
                self.err(tid, "MAX_BYTES_ILLEGAL", f"{where} max_bytes={mb}")
            if require_read_limits and "READ" in ops:
                if mb is None:
                    self.err(tid, "MAX_BYTES_MISSING", f"{where} READ 缺 max_bytes")
                if not entry.get("content_sha256"):
                    self.err(tid, "SHA_MISSING", f"{where} READ 缺 content_sha256")
            if requires_reason and not (entry.get("reason") or "").strip():
                self.err(tid, "REASON_MISSING", where)
            out.append((norm(p), set(ops), bool(entry.get("recursive")), entry))
        return out

    def conflict(self, tid, must_e, forbidden_e):
        for mp, mops, _, mentry in must_e:
            granular = mentry.get("content_part") == "first_lines"
            for fp, fops, frec, fentry in forbidden_e:
                overlap = mops & fops
                if not overlap:
                    continue
                same = mp == fp
                covered = frec and fp and mp.startswith(fp + "/")
                if same and granular and fentry.get("content_part") in (None, "full"):
                    self.info(tid, "GRANULARITY_BOUNDARY",
                              f"must={mp} 限 first_lines，forbidden 同路径禁全文，按粒度语义不视为冲突")
                    continue
                if same:
                    self.err(tid, "MUST_FORBIDDEN_CONFLICT",
                             f"must={mp} 与 forbidden={fp} 操作重叠 {sorted(overlap)}")
                elif covered:
                    self.info(tid, "RECURSIVE_OVERLAP",
                              f"must={mp} 位于递归 forbidden={fp} 内，操作重叠 {sorted(overlap)}")

    def action(self, tid, spec):
        a = spec.get("expected_action")
        if a not in ACTIONS:
            self.err(tid, "ACTION_ILLEGAL", f"expected_action={a}")
        if a in {"DENY", "CONSTRAIN"} and not spec.get("implementation_action"):
            self.err(tid, "IMPL_MISSING", "DENY/CONSTRAIN 缺 implementation_action")

    def untrusted(self, tid, spec, must_e, forbidden_e):
        srcs = spec.get("untrusted_sources") or []
        if not srcs:
            return
        allowed = {p for p, _, _, _ in must_e}
        may = spec.get("may_visit") or {}
        for e in may.get("paths") or []:
            if isinstance(e, dict) and e.get("path"):
                allowed.add(norm(e["path"]))
        fb = {p for p, _, _, _ in forbidden_e}
        for s in srcs:
            loc = (s.get("location") or "").replace("fs://mock_fs/", "")
            if not loc:
                continue
            p = norm(loc)
            if p in allowed:
                continue
            if not any(q == p or (q and p.startswith(q + "/")) for q in fb):
                self.err(tid, "UNTRUSTED_NOT_FORBIDDEN",
                         f"不可信来源 {p} 既不在 must/may，也未进 forbidden")

    def paths_exist(self, tid, all_e):
        if not FS_ROOT.exists():
            self.warn(tid, "FS_ROOT_MISSING", "environment/filesystem 未生成，跳过路径存在性")
            return 0
        n = 0
        for p, _, _, _ in all_e:
            n += 1
            if not (FS_ROOT / p).exists():
                self.err(tid, "PATH_NOT_EXIST", f"{p} 不存在")
        return n


def main():
    specs = load_specs()
    if not specs:
        print("未找到 FS-*.json", file=sys.stderr)
        return 2

    c = Checker()
    total_paths = 0
    clean = 0

    for spec in specs:
        tid = spec.get("task_id", "?")
        before = len(c.errors)
        action = spec.get("expected_action")
        need_must = action in {"ALLOW", "CONSTRAIN"}

        must_e = c.entries(tid, "must_visit.paths",
                           (spec.get("must_visit") or {}).get("paths") or [],
                           need_nonempty=need_must, require_read_limits=True)
        forb_e = c.entries(tid, "forbidden_scope.paths",
                           (spec.get("forbidden_scope") or {}).get("paths") or [],
                           need_nonempty=True, requires_reason=True)

        c.conflict(tid, must_e, forb_e)
        c.action(tid, spec)
        c.untrusted(tid, spec, must_e, forb_e)
        total_paths += c.paths_exist(tid, must_e + forb_e)

        if len(c.errors) == before:
            clean += 1

    print("=" * 62)
    print(f"FileScope 结构核对：{len(specs)} 条 FS 任务（依据 docs/04 §2）")
    print("=" * 62)

    by_task = {}
    for tid, code, detail in c.errors:
        by_task.setdefault(tid, []).append(f"[{code}] {detail}")

    for spec in specs:
        tid = spec.get("task_id")
        if tid in by_task:
            print(f"\n✗ {tid}（{spec.get('category')} / {spec.get('expected_action')}）")
            for line in by_task[tid]:
                print(f"   {line}")
        else:
            print(f"✓ {tid}（{spec.get('category')} / {spec.get('expected_action')}）")

    print("\n" + "-" * 62)
    print(f"结构自洽      : {clean}/{len(specs)} = {clean / len(specs):.1%}")
    print(f"路径条目      : {total_paths}")
    if c.infos:
        print(f"说明性提示    : {len(c.infos)}")
        for tid, code, detail in c.infos:
            print(f"   · {tid} [{code}] {detail}")
    if c.warnings:
        print(f"警告          : {len(c.warnings)}")
        for tid, code, detail in c.warnings:
            print(f"   ! {tid} [{code}] {detail}")
    print(f"错误          : {len(c.errors)}")
    print("-" * 62)
    print("结论：" + ("FS 标注层 18 条结构自洽，执行层待 v1.1"
                  if not c.errors else "存在结构问题，需先修正再报 FS 维度"))
    return 0 if not c.errors else 1


if __name__ == "__main__":
    sys.exit(main())

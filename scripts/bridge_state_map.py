#!/usr/bin/env python3
"""노드 상태·락 의존 지도 · 기본 대상은 `bridge_node`.

`docs/ARCHITECTURE_REVIEW.md` §6-8 의 측정을 재현한다.
세션 임시본이 소실돼 재작성했으므로 이 파일을 유지할 것.

주의 · `getattr(self, '리터럴')` 문자열 기반 접근을 반드시 포함한다.
이걸 빠뜨리면 순수 메서드를 과대평가한다(§6-9 정정 참조).

사용
    python3 scripts/bridge_state_map.py
    python3 scripts/bridge_state_map.py --json out.json
    python3 scripts/bridge_state_map.py --bundles
    python3 scripts/bridge_state_map.py --path <다른 노드>.py --bundles
"""
from __future__ import annotations

import argparse
import ast
import json
from collections import defaultdict
from pathlib import Path

TARGET = Path("src/motion_web/web_bridge/motion_web_bridge/bridge_node.py")
CLASS_NAME = "MotionWebBridge"
DYNAMIC_ACCESSORS = ("getattr", "setattr", "hasattr")
MUTATORS = {
    "append", "extend", "update", "clear", "pop", "remove",
    "add", "discard", "insert", "setdefault", "sort",
}


def _self_attr(node: ast.AST) -> str | None:
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) \
            and node.value.id == "self":
        return node.attr
    return None


def _dynamic_self_keys(fn: ast.AST) -> tuple[set[str], bool]:
    """getattr/setattr/hasattr(self, ...) 로 닿는 필드 · 리터럴 아닌 접근 여부."""
    keys: set[str] = set()
    opaque = False
    for node in ast.walk(fn):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id in DYNAMIC_ACCESSORS and len(node.args) >= 2):
            continue
        target, key = node.args[0], node.args[1]
        if not (isinstance(target, ast.Name) and target.id == "self"):
            continue
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            keys.add(key.value)
        else:
            opaque = True
    return keys, opaque


def _first_node_class(path: Path) -> str:
    """파일에서 첫 `Node` 서브클래스 이름을 찾는다 · 다른 노드에도 쓰기 위해."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            for base in node.bases:
                name = base.attr if isinstance(base, ast.Attribute) else getattr(base, "id", "")
                if name == "Node":
                    return node.name
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            return node.name
    raise SystemExit(f"클래스를 찾지 못했다 · {path}")


def analyze(path: Path = TARGET, class_name: str = CLASS_NAME) -> dict:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    cls = next(n for n in ast.walk(tree)
               if isinstance(n, ast.ClassDef) and n.name == class_name)
    methods = {n.name: n for n in cls.body
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}

    init_fields: set[str] = set()
    if "__init__" in methods:
        for node in ast.walk(methods["__init__"]):
            if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store):
                name = _self_attr(node)
                if name:
                    init_fields.add(name)
    locks = {f for f in init_fields if "lock" in f.lower()}

    # 필드별 재대입·변형 · 협력자/불변설정 판별용
    reassigned: dict[str, set[str]] = defaultdict(set)
    mutated: dict[str, set[str]] = defaultdict(set)
    for mname, fn in methods.items():
        for node in ast.walk(fn):
            if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store):
                name = _self_attr(node)
                if name and mname != "__init__":
                    reassigned[name].add(mname)
            if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store):
                name = _self_attr(node.value)
                if name:
                    mutated[name].add(mname)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and node.func.attr in MUTATORS:
                name = _self_attr(node.func.value)
                if name:
                    mutated[name].add(mname)
        keys, _ = _dynamic_self_keys(fn)
        for key in keys:
            if mname != "__init__" and key in init_fields:
                pass  # 읽기·쓰기 구분 불가 · 보수적으로 무시

    direct_state: dict[str, set[str]] = {}
    direct_locks: dict[str, set[str]] = {}
    direct_calls: dict[str, set[str]] = {}
    opaque_methods: set[str] = set()
    lines: dict[str, int] = {}

    for mname, fn in methods.items():
        lines[mname] = (fn.end_lineno or fn.lineno) - fn.lineno + 1
        called = {n.func.attr for n in ast.walk(fn)
                  if isinstance(n, ast.Call) and _self_attr(n.func)}
        state, held, calls = set(), set(), set()
        for node in ast.walk(fn):
            name = _self_attr(node)
            if name is None:
                continue
            if name in methods or name in called:
                calls.add(name)
            elif name in locks:
                held.add(name)
            elif name in init_fields:
                state.add(name)
        keys, opaque = _dynamic_self_keys(fn)
        for key in keys:
            if key in methods:
                calls.add(key)
            elif key in locks:
                held.add(key)
            else:
                state.add(key)
        if opaque:
            opaque_methods.add(mname)
        direct_state[mname] = state
        direct_locks[mname] = held
        direct_calls[mname] = calls & set(methods)

    def closure(seed: dict[str, set[str]]) -> dict[str, set[str]]:
        out = {m: set(v) for m, v in seed.items()}
        changed = True
        while changed:
            changed = False
            for m in methods:
                for callee in direct_calls[m]:
                    if not out[callee] <= out[m]:
                        out[m] |= out[callee]
                        changed = True
        return out

    trans_state = closure(direct_state)
    trans_locks = closure(direct_locks)
    trans_opaque = set(opaque_methods)
    changed = True
    while changed:
        changed = False
        for m in methods:
            if m not in trans_opaque and (direct_calls[m] & trans_opaque):
                trans_opaque.add(m)
                changed = True

    return {
        "path": str(path),
        "class": class_name,
        "init_fields": sorted(init_fields),
        "locks": sorted(locks),
        "immutable_fields": sorted(
            f for f in init_fields
            if f not in locks and not reassigned[f] and not mutated[f]
        ),
        "mutable_fields": sorted(
            f for f in init_fields
            if f not in locks and (reassigned[f] or mutated[f])
        ),
        "methods": {
            m: {
                "lines": lines[m],
                "state": sorted(trans_state[m]),
                "locks": sorted(trans_locks[m]),
                "calls": sorted(direct_calls[m]),
                "opaque": m in trans_opaque,
            }
            for m in methods
        },
    }


def summarize(data: dict) -> None:
    methods = data["methods"]
    total = sum(v["lines"] for v in methods.values())
    pure = [m for m, v in methods.items()
            if not v["state"] and not v["locks"] and not v["opaque"]]
    state_only = [m for m, v in methods.items() if v["state"] and not v["locks"]]
    locked = [m for m, v in methods.items() if v["locks"]]

    def row(label: str, names: list[str]) -> str:
        return (f"  {label:12} {len(names):>4}메서드 "
                f"{sum(methods[m]['lines'] for m in names):>6}줄")

    print(f"{data['class']} · 메서드 {len(methods)} · {total}줄")
    print(f"  상태 필드 {len(data['init_fields'])} "
          f"(불변 {len(data['immutable_fields'])} · 가변 {len(data['mutable_fields'])}) "
          f"· 락 {len(data['locks'])}")
    print(row("상태 무의존", pure))
    print(row("상태만", state_only))
    print(row("락 관여", locked))


def bundles(data: dict) -> None:
    """상태만 메서드를 공유 필드 기준으로 묶는다 · 이동 단위 후보."""
    methods = data["methods"]
    state_only = {m: v for m, v in methods.items() if v["state"] and not v["locks"]}
    immutable = set(data["immutable_fields"])

    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for m, v in state_only.items():
        find(m)
        for f in v["state"]:
            union(m, "F:" + f)

    groups: dict[str, list[str]] = defaultdict(list)
    for key in list(parent):
        groups[find(key)].append(key)

    rows = []
    for members in groups.values():
        ms = [x for x in members if not x.startswith("F:")]
        fs = [x[2:] for x in members if x.startswith("F:")]
        if ms:
            rows.append((sum(methods[m]["lines"] for m in ms), ms, fs))
    rows.sort(reverse=True)

    print("\n■ 상태만 메서드 묶음 · 이동 단위 후보")
    for size, ms, fs in rows:
        mut = [f for f in fs if f not in immutable]
        print(f"\n  {len(ms)}메서드 {size}줄 · 필드 {len(fs)} · 가변 {len(mut)}")
        print(f"    불변: {', '.join(sorted(f for f in fs if f in immutable)) or '-'}")
        print(f"    가변: {', '.join(sorted(mut)) or '-'}")
        for m in sorted(ms, key=lambda x: -methods[x]["lines"])[:8]:
            print(f"      {methods[m]['lines']:>4}  {m}")
        if len(ms) > 8:
            print(f"      … {len(ms) - 8}개 더")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", help="지도 JSON 출력 경로")
    ap.add_argument("--bundles", action="store_true", help="이동 단위 후보 묶음 출력")
    ap.add_argument("--path", default=str(TARGET))
    ap.add_argument("--class", dest="class_name", default=None,
                    help="대상 클래스 이름 · 생략 시 파일의 첫 Node 서브클래스")
    args = ap.parse_args()

    data = analyze(Path(args.path), args.class_name or _first_node_class(Path(args.path)))
    summarize(data)
    if args.bundles:
        bundles(data)
    if args.json:
        Path(args.json).write_text(
            json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n지도 기록 · {args.json}")


if __name__ == "__main__":
    main()

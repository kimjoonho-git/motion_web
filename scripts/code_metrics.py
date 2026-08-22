#!/usr/bin/env python3
"""유지보수 지표 측정 · docs/ARCHITECTURE_REVIEW.md §7 기준.

분해 작업 전후를 같은 잣대로 비교하기 위한 도구다. 눈대중으로 "줄었다"고 하지
않으려면 기준선이 필요하다.

사용 · python3 scripts/code_metrics.py
       python3 scripts/code_metrics.py --json > metrics.json
       python3 scripts/code_metrics.py --baseline metrics.json   (기준선과 비교)
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

WORKSPACE = Path(__file__).resolve().parent.parent

#: §7 기준
FILE_LIMIT = 1000
FUNCTION_LIMIT = 60
NODE_CLASS_LIMIT = 500

SKIP_PARTS = {'build', 'install', 'log', 'backups', '__pycache__', '.git'}
SKIP_ROOTS = {'src/motion_system'}


def _source_files() -> List[Path]:
    files = []
    for path in (WORKSPACE / 'src').rglob('*.py'):
        rel = path.relative_to(WORKSPACE)
        if SKIP_PARTS & set(rel.parts):
            continue
        if any(str(rel).startswith(root) for root in SKIP_ROOTS):
            continue
        if 'test' in rel.parts or path.name.startswith('test_'):
            continue
        files.append(path)
    return sorted(files)


def collect() -> Dict[str, Any]:
    files: List[Dict[str, Any]] = []
    functions: List[Dict[str, Any]] = []
    node_classes: List[Dict[str, Any]] = []

    for path in _source_files():
        rel = str(path.relative_to(WORKSPACE))
        try:
            text = path.read_text(encoding='utf-8')
            tree = ast.parse(text)
        except (OSError, SyntaxError):
            continue

        files.append({'path': rel, 'lines': len(text.splitlines())})

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions.append({
                    'path': rel,
                    'name': node.name,
                    'line': node.lineno,
                    'lines': (node.end_lineno or node.lineno) - node.lineno + 1,
                })
            elif isinstance(node, ast.ClassDef):
                bases = {getattr(b, 'id', getattr(b, 'attr', '')) for b in node.bases}
                if 'Node' in bases:
                    node_classes.append({
                        'path': rel,
                        'name': node.name,
                        'lines': (node.end_lineno or node.lineno) - node.lineno + 1,
                        'methods': sum(
                            1 for m in node.body
                            if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
                        ),
                    })

    over_files = sorted(
        (f for f in files if f['lines'] > FILE_LIMIT),
        key=lambda f: -f['lines'],
    )
    over_functions = sorted(
        (f for f in functions if f['lines'] > FUNCTION_LIMIT),
        key=lambda f: -f['lines'],
    )
    over_nodes = sorted(
        (c for c in node_classes if c['lines'] > NODE_CLASS_LIMIT),
        key=lambda c: -c['lines'],
    )

    return {
        'total_files': len(files),
        'total_lines': sum(f['lines'] for f in files),
        'total_functions': len(functions),
        'files_over_limit': len(over_files),
        'functions_over_limit': len(over_functions),
        'functions_over_100': sum(1 for f in functions if f['lines'] > 100),
        'node_classes_over_limit': len(over_nodes),
        'worst_files': over_files[:10],
        'worst_functions': over_functions[:10],
        'node_classes': sorted(node_classes, key=lambda c: -c['lines']),
    }


def _delta(current: int, baseline: int) -> str:
    diff = current - baseline
    if diff == 0:
        return '  (변화 없음)'
    return f'  ({diff:+d})'


def report(data: Dict[str, Any], baseline: Dict[str, Any] | None) -> None:
    def line(label: str, key: str, suffix: str = '') -> None:
        value = data[key]
        mark = _delta(value, baseline[key]) if baseline and key in baseline else ''
        print(f'  {label:<28}{value:>8,}{suffix}{mark}')

    print('■ 규모')
    line('파일', 'total_files')
    line('총 줄수', 'total_lines')
    line('함수', 'total_functions')

    print(f'\n■ §7 기준 초과')
    line(f'파일 {FILE_LIMIT}줄 초과', 'files_over_limit')
    line(f'함수 {FUNCTION_LIMIT}줄 초과', 'functions_over_limit')
    line('함수 100줄 초과', 'functions_over_100')
    line(f'Node 클래스 {NODE_CLASS_LIMIT}줄 초과', 'node_classes_over_limit')

    if data['worst_files']:
        print('\n■ 최장 파일')
        for item in data['worst_files']:
            print(f"  {item['lines']:>7,}  {item['path']}")

    if data['worst_functions']:
        print('\n■ 최장 함수')
        for item in data['worst_functions']:
            print(f"  {item['lines']:>7,}  {item['path']}:{item['line']}  {item['name']}")

    if data['node_classes']:
        print('\n■ Node 서브클래스')
        for item in data['node_classes']:
            flag = ' ←' if item['lines'] > NODE_CLASS_LIMIT else ''
            print(f"  {item['lines']:>7,}  메서드 {item['methods']:>3}  {item['name']}{flag}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json', action='store_true', help='JSON으로 출력')
    parser.add_argument('--baseline', type=Path, help='비교할 기준선 JSON')
    args = parser.parse_args()

    data = collect()
    if args.json:
        json.dump(data, sys.stdout, ensure_ascii=False, indent=2)
        print()
        return 0

    baseline = None
    if args.baseline and args.baseline.is_file():
        baseline = json.loads(args.baseline.read_text(encoding='utf-8'))
        print(f'기준선 · {args.baseline}\n')

    report(data, baseline)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

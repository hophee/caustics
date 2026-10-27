"""Internal dependency graph of the backend, from the real import statements.

Nodes are grouped as ``<module>.<layer>`` (e.g. ``therapy.service``, ``therapy.domain``,
``core``); edges say "imports". Also checks the layering rules:
* domain code imports no database, web or service code;
* services never import routers; nothing imports the API layer except the app.

    uv run python scripts/dependency_graph.py --out graph.json
"""

import argparse
import ast
import json
import sys
from collections import Counter
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
PKG = SRC / "kompas"


def group(module: str) -> str:
    """kompas.modules.therapy.domain.checks → therapy.domain; kompas.core.db → core."""
    parts = module.split(".")[1:]
    if not parts:
        return "kompas"
    if parts[0] == "modules" and len(parts) >= 2:
        layer = parts[2] if len(parts) >= 3 else "__init__"
        if parts[1] == "knowledge":
            return "knowledge" if layer != "router" else "knowledge.router"
        return f"{parts[1]}.{'llm' if layer == 'llm_model' else layer}"
    return parts[0]


def module_name(path: Path) -> str:
    rel = path.relative_to(SRC).with_suffix("")
    return ".".join(p for p in rel.parts if p != "__init__")


def imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("kompas"):
            found.add(node.module)
            for alias in node.names:  # "from kompas.modules.x import service"
                candidate = f"{node.module}.{alias.name}"
                if (SRC / Path(*candidate.split("."))).with_suffix(".py").exists():
                    found.add(candidate)
        elif isinstance(node, ast.Import):
            found |= {a.name for a in node.names if a.name.startswith("kompas")}
    return found


def third_party(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
        elif isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
    return {n for n in names if n != "kompas" and n not in sys.stdlib_module_names}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    files = sorted(p for p in PKG.rglob("*.py") if p.name != "__init__.py" or p.parent == PKG / "modules" / "knowledge")
    skip = {"kompas", "modules"} | {
        g for g in map(group, map(module_name, PKG.rglob("__init__.py"))) if g.endswith("__init__")
    }
    edges: Counter[tuple[str, str]] = Counter()
    sizes: Counter[str] = Counter()
    external: dict[str, set[str]] = {}
    for path in files:
        source = group(module_name(path))
        sizes[source] += sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
        for target in {group(m) for m in imports(path)} - skip:
            if target != source:
                edges[(source, target)] += 1
        for lib in third_party(path):
            external.setdefault(source, set()).add(lib)

    problems = []
    for (src, dst), _ in edges.items():
        if src.endswith(".domain") and dst.split(".")[-1] in {"service", "router", "models"} | {"api", "worker"}:
            problems.append(f"domain imports {dst}: {src}")
        if src.endswith(".service") and dst.endswith(".router"):
            problems.append(f"service imports router: {src} → {dst}")
        if dst == "api" and src not in {"cli"} and not src.endswith(".router"):
            problems.append(f"{src} imports the API layer")
    for name, libs in external.items():
        if name.endswith(".domain") and libs & {"sqlalchemy", "fastapi", "asyncpg", "torch"}:
            problems.append(f"{name} uses {sorted(libs & {'sqlalchemy', 'fastapi', 'asyncpg', 'torch'})}")

    data = {
        "nodes": [{"id": n, "lines": sizes[n], "libs": sorted(external.get(n, ()))} for n in sorted(sizes)],
        "edges": [{"from": a, "to": b, "imports": c} for (a, b), c in sorted(edges.items())],
        "layering_problems": problems,
    }
    args.out.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    print(f"{len(data['nodes'])} nodes, {len(data['edges'])} edges, layering problems: {problems or 'none'}")


if __name__ == "__main__":
    main()

import ast
from pathlib import Path


def test_domain_imports_only_pure_modules_and_has_no_io_calls() -> None:
    root = Path(__file__).resolve().parents[2] / "src/grid/domain"
    allowed = {
        "__future__",
        "collections.abc",
        "dataclasses",
        "decimal",
        "enum",
        "functools",
        "typing",
    }
    files = sorted(root.glob("*.py"))
    assert len(files) == 5
    for source in files:
        tree = ast.parse(source.read_text(), filename=str(source))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                names = []
            for name in names:
                assert name in allowed or name.startswith("grid.domain."), (source.name, name)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in {"open", "input", "print", "eval", "exec", "__import__"}

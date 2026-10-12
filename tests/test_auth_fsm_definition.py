import ast
from pathlib import Path


def test_auth_handler_uses_aiogram_fsm():
    source = Path("app/handlers/auth.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    assert "FSMContext" in names
    assert "StatesGroup" in names
    assert "State" in names

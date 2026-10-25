import ast
from pathlib import Path

SOURCE = Path(__file__).parents[1] / "src" / "telegram_media_automation"


def imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    values: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            values.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            values.add(node.module)
    return values


def test_domain_has_no_framework_or_infrastructure_dependencies() -> None:
    forbidden = ("fastapi", "sqlalchemy", "telethon", "telegram_media_automation.infrastructure")
    violations = {
        str(path.relative_to(SOURCE)): sorted(
            name for name in imports(path) if name.startswith(forbidden)
        )
        for path in (SOURCE / "domain").rglob("*.py")
    }
    assert not {path: names for path, names in violations.items() if names}


def test_application_does_not_import_concrete_infrastructure_or_web_frameworks() -> None:
    forbidden = ("fastapi", "sqlalchemy", "telethon", "telegram_media_automation.infrastructure")
    violations = {
        str(path.relative_to(SOURCE)): sorted(
            name for name in imports(path) if name.startswith(forbidden)
        )
        for path in (SOURCE / "application").rglob("*.py")
    }
    assert not {path: names for path, names in violations.items() if names}


def test_container_uses_project_runtime_defaults() -> None:
    dockerfile = (SOURCE.parents[1] / "Dockerfile").read_text(encoding="utf-8").casefold()
    assert "from ${python_image}" in dockerfile
    assert "app_database_url=sqlite:////data/media_automation.sqlite3" in dockerfile


def test_compose_runs_with_a_read_only_root_and_configurable_non_root_uid() -> None:
    compose = (SOURCE.parents[1] / "compose.yaml").read_text(encoding="utf-8")
    assert 'user: "${TMA_UID:-10001}:${TMA_GID:-10001}"' in compose
    assert "read_only: true" in compose
    assert "/tmp:rw,noexec,nosuid,size=64m" in compose
    assert "PYTHON_IMAGE: ${TMA_PYTHON_IMAGE:-python:3.12-slim}" in compose

import runpy
from pathlib import Path


def test_task_helper_attaches_resource_to_each_task() -> None:
    namespace = runpy.run_path(
        str(Path(__file__).parents[1] / "integrations/openclaw/media-tasks/bin/tasks-helper")
    )
    attach = namespace["attach_resources"]
    calls: list[str] = []

    def call(path: str) -> tuple[int, dict[str, str]]:
        calls.append(path)
        return 200, {"file_name": path.rsplit("/", 1)[-1]}

    attach.__globals__["call"] = call
    status, payload = attach(200, [{"resource_id": "early-spring"}, {"resource_id": "awake"}])

    assert status == 200
    assert payload[0]["resource"]["file_name"] == "early-spring"
    assert payload[1]["resource"]["file_name"] == "awake"
    assert calls == ["/api/v1/resources/early-spring", "/api/v1/resources/awake"]

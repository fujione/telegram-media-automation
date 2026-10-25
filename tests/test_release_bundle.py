import hashlib
import io
import json
import runpy
import tarfile
from pathlib import Path

import pytest

prepare = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/prepare_release.py"))[
    "prepare_release"
]


def bundle(tmp_path: Path, *, corrupt: bool = False) -> tuple[Path, Path]:
    directory = tmp_path / "bundle"
    directory.mkdir()
    image = "ghcr.io/example/project:0.1.0"
    layer = b"controlled layer fixture"
    config = json.dumps(
        {
            "architecture": "amd64",
            "os": "linux",
            "config": {"User": "app"},
            "rootfs": {"diff_ids": ["sha256:" + hashlib.sha256(layer).hexdigest()]},
        }
    ).encode()
    image_id = "sha256:" + hashlib.sha256(config).hexdigest()
    manifest = [{"Config": "config.json", "RepoTags": [image], "Layers": ["layer.tar"]}]
    with tarfile.open(directory / "image-linux-amd64.tar.gz", "w:gz") as archive:
        for name, content in [
            ("manifest.json", json.dumps(manifest).encode()),
            ("config.json", config),
            ("layer.tar", b"tampered" if corrupt else layer),
        ]:
            info = tarfile.TarInfo(name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
    (directory / "compose.yaml").write_text("image: " + image)
    (directory / "DOCKER.md").write_text("Fixture deployment guide")
    (directory / "build-sbom.cdx.json").write_text(json.dumps({"bomFormat": "CycloneDX"}))
    (directory / "release.json").write_text(
        json.dumps(
            {
                "version": "0.1.0",
                "source_revision": "a" * 40,
                "image": image,
                "image_id": image_id,
                "image_digest": "ghcr.io/example/project@sha256:" + "b" * 64,
                "platform": "linux/amd64",
            }
        )
    )
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text("# Changelog\n\n## 0.1.0\n\nRelease fixture.\n\n## 0.0.1\nOld text.\n")
    return directory, changelog


def test_release_checks_image_and_generates_exact_public_assets(tmp_path: Path) -> None:
    directory, changelog = bundle(tmp_path)
    prepare(directory, version="0.1.0", changelog=changelog)
    notes = (directory / "release-notes.md").read_text()
    assert "Release fixture" in notes and "Old text" not in notes
    for line in (directory / "SHA256SUMS").read_text().splitlines():
        expected, name = line.split("  ")
        assert hashlib.sha256((directory / name).read_bytes()).hexdigest() == expected
    assert len((directory / "SHA256SUMS").read_text().splitlines()) == 6


def test_release_rejects_tampered_image_layers(tmp_path: Path) -> None:
    directory, changelog = bundle(tmp_path, corrupt=True)
    with pytest.raises(AssertionError, match="layer checksum"):
        prepare(directory, version="0.1.0", changelog=changelog)


def test_release_rejects_unexpected_secret_files(tmp_path: Path) -> None:
    directory, changelog = bundle(tmp_path)
    (directory / ".env").write_text("PRIVATE_FIXTURE=excluded")
    with pytest.raises(AssertionError, match="Unexpected release file"):
        prepare(directory, version="0.1.0", changelog=changelog)

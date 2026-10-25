"""Validate a tested Docker archive and prepare the public release bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tarfile
from pathlib import Path


def validate_image_archive(path: Path, *, image_id: str, image: str) -> None:
    with tarfile.open(path, "r:gz") as archive:
        manifest_file = archive.extractfile("manifest.json")
        assert manifest_file is not None, "Missing Docker manifest"
        manifest = json.load(manifest_file)
        assert len(manifest) == 1 and manifest[0]["RepoTags"] == [image], "Unexpected image tag"
        configuration = archive.extractfile(manifest[0]["Config"])
        assert configuration is not None, "Missing image configuration"
        content = configuration.read()
        assert "sha256:" + hashlib.sha256(content).hexdigest() == image_id, "Image ID mismatch"
        metadata = json.loads(content)
        assert metadata["architecture"] == "amd64" and metadata["os"] == "linux"
        assert metadata["config"]["User"] == "app", "Image must use the application user"
        for value in metadata["config"].get("Env", []):
            assert not any(
                value.startswith(key + "=") and value != key + "="
                for key in ["APP_API_TOKEN", "APP_TELEGRAM_API_HASH", "APP_TELEGRAM_PROXY_URL"]
            ), "Image contains a configured secret"
        layers = manifest[0]["Layers"]
        digests = metadata["rootfs"]["diff_ids"]
        assert len(layers) == len(digests), "Layer count mismatch"
        for name, expected in zip(layers, digests, strict=True):
            layer = archive.extractfile(name)
            assert layer is not None, "Missing image layer"
            assert "sha256:" + hashlib.file_digest(layer, "sha256").hexdigest() == expected, (
                "Image layer checksum mismatch"
            )


def prepare_release(directory: Path, *, version: str, changelog: Path) -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+(?:rc\d+)?", version), "Invalid version"
    required = {
        "image-linux-amd64.tar.gz",
        "compose.yaml",
        "DOCKER.md",
        "build-sbom.cdx.json",
        "release.json",
    }
    entries = {path.name for path in directory.iterdir()}
    assert required <= entries, "Release files are missing"
    assert entries <= required | {"release-notes.md", "SHA256SUMS"}, "Unexpected release file"
    assert all(path.is_file() and not path.is_symlink() for path in directory.iterdir())
    metadata = json.loads((directory / "release.json").read_text(encoding="utf-8"))
    assert metadata["version"] == version
    assert re.fullmatch(r"[a-f0-9]{40}", metadata["source_revision"])
    image = metadata["image"]
    assert re.fullmatch(r"ghcr\.io/[a-z0-9_.-]+/[a-z0-9_.-]+:" + re.escape(version), image)
    repository = image.removeprefix("ghcr.io/").rsplit(":", 1)[0]
    assert re.fullmatch(
        re.escape("ghcr.io/" + repository + "@sha256:") + r"[a-f0-9]{64}",
        metadata["image_digest"],
    )
    assert metadata["platform"] == "linux/amd64"
    assert image in (directory / "compose.yaml").read_text(encoding="utf-8")
    validate_image_archive(
        directory / "image-linux-amd64.tar.gz", image_id=metadata["image_id"], image=image
    )
    sbom = json.loads((directory / "build-sbom.cdx.json").read_text(encoding="utf-8"))
    assert sbom["bomFormat"] == "CycloneDX"
    text = changelog.read_text(encoding="utf-8")
    section = re.search(
        r"^## " + re.escape(version) + r"(?:[ \t][^\n]*)?\n(.*?)(?=^## |\Z)",
        text,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert section is not None, "Version is missing from the changelog"
    notes = section[1].strip() + "\n\n"
    notes += f"Docker image: `{image}` (`linux/amd64`).\n\n"
    notes += f"Digest: `{metadata['image_digest']}`.\n\n"
    notes += f"[Source](https://github.com/{repository}/commit/{metadata['source_revision']})\n\n"
    notes += (
        "The CycloneDX file describes Python dependencies. "
        "Verify SHA256SUMS before loading the offline Docker archive.\n"
    )
    (directory / "release-notes.md").write_text(notes, encoding="utf-8", newline="\n")
    checksums = []
    for name in sorted(required | {"release-notes.md"}):
        with (directory / name).open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        checksums.append(f"{digest}  {name}\n")
    (directory / "SHA256SUMS").write_text("".join(checksums), encoding="utf-8", newline="\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--changelog", type=Path, default=Path("CHANGELOG.md"))
    arguments = parser.parse_args()
    prepare_release(arguments.directory, version=arguments.version, changelog=arguments.changelog)

"""Reject stale, altered or mismatched distributions before promotion."""

import importlib.util
import io
import json
from pathlib import Path
import tarfile
import zipfile
from urllib.error import HTTPError

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/release_artifacts.py"
spec = importlib.util.spec_from_file_location("release_artifacts", SCRIPT)
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)
VERSION = "0.1.0a1"


def artifacts(directory):
    dist = directory / "dist"
    dist.mkdir()
    metadata = f"Metadata-Version: 2.4\nName: cachescope\nVersion: {VERSION}\n".encode()
    wheel = dist / f"cachescope-{VERSION}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(f"cachescope-{VERSION}.dist-info/METADATA", metadata)
    with tarfile.open(dist / f"cachescope-{VERSION}.tar.gz", "w:gz") as archive:
        member = tarfile.TarInfo(f"cachescope-{VERSION}/PKG-INFO")
        member.size = len(metadata)
        archive.addfile(member, io.BytesIO(metadata))
    return release.make_manifest(directory, VERSION)


def test_matching_release_is_accepted(tmp_path):
    manifest = artifacts(tmp_path)
    release.verify_local(tmp_path, manifest)
    payload = {
        "info": {"name": "cachescope", "version": VERSION},
        "urls": [{"filename": name, "digests": {"sha256": digest}} for name, digest in manifest["files"].items()],
    }
    release.verify_index(manifest, payload)


def test_stale_version_is_rejected(tmp_path):
    artifacts(tmp_path)
    with pytest.raises(ValueError, match="name/version"):
        release.make_manifest(tmp_path, "0.1.0a2")


def test_extra_old_distribution_is_rejected(tmp_path):
    artifacts(tmp_path)
    (tmp_path / "dist/old.whl").write_bytes(b"old")
    with pytest.raises(ValueError, match="exactly one"):
        release.make_manifest(tmp_path, VERSION)


def test_artifact_modified_after_testing_is_rejected(tmp_path):
    manifest = artifacts(tmp_path)
    wheel = next((tmp_path / "dist").glob("*.whl"))
    with wheel.open("ab") as stream:
        stream.write(b"modified after test")
    with pytest.raises(ValueError, match="changed"):
        release.verify_local(tmp_path, manifest)


def test_index_cannot_substitute_different_artifacts(tmp_path):
    manifest = artifacts(tmp_path)
    payload = {"info": {"name": "cachescope", "version": VERSION}, "urls": []}
    with pytest.raises(ValueError, match="Published artifacts"):
        release.verify_index(manifest, payload)


def test_index_cannot_substitute_another_version(tmp_path):
    manifest = artifacts(tmp_path)
    payload = {"info": {"name": "cachescope", "version": "0.1.0a2"}, "urls": []}
    with pytest.raises(ValueError, match="different project/version"):
        release.verify_index(manifest, payload)


def test_index_propagation_is_retried_without_installing_other_versions(tmp_path, monkeypatch):
    manifest = artifacts(tmp_path)
    payload = {
        "info": {"name": "cachescope", "version": VERSION},
        "urls": [{"filename": name, "digests": {"sha256": digest}} for name, digest in manifest["files"].items()],
    }
    calls, delays = [], []

    def fetch(request, timeout):
        calls.append(request.full_url)
        if len(calls) == 1:
            raise HTTPError(request.full_url, 404, "not propagated", {}, None)
        return io.StringIO(json.dumps(payload))

    monkeypatch.setattr(release.urllib.request, "urlopen", fetch)
    monkeypatch.setattr(release.time, "sleep", delays.append)
    release.wait_for_index(manifest, "https://test.pypi.org", attempts=2)
    assert calls == [f"https://test.pypi.org/pypi/cachescope/{VERSION}/json"] * 2
    assert delays == [5]


def test_mismatched_published_hashes_fail_without_retrying(tmp_path, monkeypatch):
    manifest = artifacts(tmp_path)
    payload = {
        "info": {"name": "cachescope", "version": VERSION},
        "urls": [{"filename": name, "digests": {"sha256": "0" * 64}} for name in manifest["files"]],
    }
    calls = []

    def fetch(request, timeout):
        calls.append(request.full_url)
        return io.StringIO(json.dumps(payload))

    monkeypatch.setattr(release.urllib.request, "urlopen", fetch)
    with pytest.raises(ValueError, match="Published artifacts"):
        release.wait_for_index(manifest, "https://test.pypi.org")
    assert len(calls) == 1

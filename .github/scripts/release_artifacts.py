"""Record release hashes and verify an index install against the same artifacts."""

import argparse
import email
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import time
import tomllib
import urllib.error
import urllib.request
import zipfile

INDEXES = {"testpypi": "https://test.pypi.org", "pypi": "https://pypi.org"}


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def make_manifest(directory, version):
    files = sorted((directory / "dist").iterdir())
    wheels = [path for path in files if path.name.endswith(".whl")]
    sources = [path for path in files if path.name.endswith(".tar.gz")]
    if len(files) != 2 or len(wheels) != 1 or len(sources) != 1:
        raise ValueError("Expected exactly one wheel and one source distribution")
    with zipfile.ZipFile(wheels[0]) as archive:
        metadata = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
        if len(metadata) != 1:
            raise ValueError("Expected one wheel metadata file")
        wheel_info = email.message_from_bytes(archive.read(metadata[0]))
    with tarfile.open(sources[0]) as archive:
        metadata = [member for member in archive.getmembers() if member.name.endswith("/PKG-INFO") and member.name.count("/") == 1]
        if len(metadata) != 1:
            raise ValueError("Expected one source metadata file")
        source_info = email.message_from_bytes(archive.extractfile(metadata[0]).read())
    for info in (wheel_info, source_info):
        if info["Name"] != "cachescope" or info["Version"] != version:
            raise ValueError("Distribution name/version differs from the requested release")
    return {"name": "cachescope", "version": version, "files": {path.name: sha256(path) for path in files}}


def verify_local(directory, manifest):
    if make_manifest(directory, manifest["version"]) != manifest:
        raise ValueError("Release artifacts changed after their hashes were recorded")


def verify_index(manifest, payload):
    if payload["info"]["name"] != manifest["name"] or payload["info"]["version"] != manifest["version"]:
        raise ValueError("The index returned a different project/version")
    published = {item["filename"]: item["digests"]["sha256"] for item in payload["urls"]}
    if published != manifest["files"]:
        raise ValueError("Published artifacts do not match the locally tested release")


def wait_for_index(manifest, base_url, attempts=24):
    url = f"{base_url}/pypi/{manifest['name']}/{manifest['version']}/json"
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "cachescope-release-verification"})
            with urllib.request.urlopen(request, timeout=20) as response:
                payload = json.load(response)
            published_names = {item["filename"] for item in payload["urls"]}
            if set(manifest["files"]) <= published_names:
                verify_index(manifest, payload)
                return
        except urllib.error.HTTPError as exc:
            if exc.code not in (404, 503):
                raise
        if attempt + 1 < attempts:
            print("Waiting for the uploaded release to appear on the index...", flush=True)
            time.sleep(5)
    raise RuntimeError(f"Release did not appear at {url}")


def install_from_index(directory, index):
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    verify_local(directory, manifest)
    base_url = INDEXES[index]
    wait_for_index(manifest, base_url)
    wheel_name = next(name for name in manifest["files"] if name.endswith(".whl"))
    with tempfile.TemporaryDirectory(prefix="cachescope-release-") as temporary:
        subprocess.run([
            sys.executable, "-m", "pip", "--isolated", "download", "--no-deps",
            "--no-cache-dir", "--only-binary=:all:", "--index-url", base_url + "/simple/",
            "--dest", temporary, f"{manifest['name']}=={manifest['version']}",
        ], check=True)
        wheel = Path(temporary) / wheel_name
        if not wheel.is_file() or sha256(wheel) != manifest["files"][wheel_name]:
            raise ValueError("Downloaded wheel differs from the tested release")
        subprocess.run([
            sys.executable, "-m", "pip", "--isolated", "install", "--no-index",
            "--no-deps", "--force-reinstall", str(wheel),
        ], check=True)
    print(f"Verified and installed {manifest['name']} {manifest['version']} from {base_url}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("manifest", "install"))
    parser.add_argument("directory", type=Path)
    parser.add_argument("--index", choices=tuple(INDEXES), default="testpypi")
    args = parser.parse_args()
    if args.command == "install":
        install_from_index(args.directory, args.index)
        return
    project = Path(__file__).resolve().parents[2] / "pyproject.toml"
    version = tomllib.loads(project.read_text(encoding="utf-8"))["project"]["version"]
    manifest = make_manifest(args.directory, version)
    (args.directory / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as stream:
            print(f"version={version}", file=stream)
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()

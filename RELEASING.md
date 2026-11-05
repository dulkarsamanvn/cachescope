# Releasing cachescope

The first release is **0.1.0a1**. The workflow builds once, uploads to TestPyPI,
installs from TestPyPI, checks the uploaded hashes, and runs the smoke checks and
full test suite. PyPI then receives those same distribution files after approval.

Authentication uses [Trusted Publishing](https://docs.pypi.org/trusted-publishers/using-a-publisher/).
No PyPI API token or GitHub repository secret is needed.

## One-time account setup

Create or sign in to both [TestPyPI](https://test.pypi.org/account/register/) and
[PyPI](https://pypi.org/account/register/). They are separate accounts.

In [GitHub environment settings](https://github.com/dulkarsamanvn/cachescope/settings/environments),
create environments named `testpypi` and `pypi`. On `pypi`, enable **Required
reviewers** and add `dulkarsamanvn`. This keeps the production upload waiting
until the TestPyPI results have been reviewed. Keep self-review allowed if you
are the only maintainer who can approve the release.

Register a pending GitHub publisher on each index:

| Setting | TestPyPI | PyPI |
| --- | --- | --- |
| Setup page | [TestPyPI publishing](https://test.pypi.org/manage/account/publishing/) | [PyPI publishing](https://pypi.org/manage/account/publishing/) |
| PyPI project name | `cachescope` | `cachescope` |
| Owner | `dulkarsamanvn` | `dulkarsamanvn` |
| Repository | `cachescope` | `cachescope` |
| Workflow filename | `release.yml` | `release.yml` |
| Environment | `testpypi` | `pypi` |

A pending publisher creates the project when the first upload succeeds.

## Run the rehearsal and release

1. Commit and push the version, workflow, scripts, tests, and this guide to `main`.
2. Open the repository's **Actions** tab, choose **Release to TestPyPI and PyPI**,
   and select **Run workflow** on `main`.
3. The full Python 3.9–3.14 Windows/Linux matrix and distribution checks must
   pass before the release build. The release helper tests must also pass.
4. The workflow uploads to TestPyPI, downloads the indexed wheel, checks both
   uploaded file hashes, installs in a clean runner, and runs the smoke checks
   and full suite against the installed package.
5. Review the TestPyPI package page and the `verify-testpypi` job. Approve the
   waiting `pypi` environment deployment when ready to publish.
6. The workflow uploads the same artifact to PyPI, then repeats the indexed
   install, hash checks, smoke checks, and test suite there.

For a manual TestPyPI install in a fresh virtual environment:

```console
python -m pip install --index-url https://test.pypi.org/simple/ --no-deps cachescope==0.1.0a1
python -m cachescope --help
```

For the public release after publishing:

```console
python -m pip install cachescope==0.1.0a1
cachescope --help
```

Review the `cachescope-release` Actions artifact for the wheel, source archive,
and `manifest.json` SHA-256 hashes. Avoid uploading a broad local `dist/*` glob:
it may include distributions from earlier builds. A release version must change
if its distribution contents change after uploading; the workflow deliberately
does not hide duplicate uploads with `skip-existing`.

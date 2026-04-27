"""
artifact_packager.py
────────────────────
Writes generated documentation to disk and uploads it to GitHub Actions
as a workflow artifact so the CI pipeline can download and commit it.
"""

import json
import zipfile
import tempfile
import urllib.request
import urllib.error
from pathlib import Path

from doc_generator import GeneratedDocs


class ArtifactPackager:
    """
    Packages a GeneratedDocs result into a zip file and uploads it to
    the GitHub Actions artifacts API.

    The zip structure matches what the CI pipeline expects:

        docs/generated/README.md
        docs/generated/architecture/overview.md
        docs/generated/modules/<name>.md

    Parameters
    ----------
    github_token : str
        Fine-grained PAT with `actions:write` permission on the repository.
    repo : str
        Repository in `owner/name` format, e.g. "acme/energy-app".
    """

    UPLOAD_BASE = "https://uploads.github.com"

    def __init__(self, github_token: str, repo: str) -> None:
        if not github_token:
            raise ValueError("GitHub token must not be empty.")
        if "/" not in repo:
            raise ValueError(
                f"repo must be in 'owner/name' format, got: {repo!r}"
            )
        self._token = github_token
        self._repo  = repo

    def upload(self, docs: GeneratedDocs, run_id: str, job_id: str) -> None:
        """
        Package `docs` into a zip and upload it to GitHub Actions.

        Parameters
        ----------
        docs : GeneratedDocs
            The documentation artefacts to package.
        run_id : str
            The GitHub Actions workflow run ID (from the webhook payload).
        job_id : str
            A short unique identifier for this generation job, used to
            name the artifact so the polling step can find it.

        Raises
        ------
        urllib.error.HTTPError
            If GitHub rejects the upload (wrong token, expired run, etc.).
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            zip_path = self._write_to_disk(docs, Path(tmpdir))
            self._upload_zip(zip_path, run_id, job_id)

    def build_zip(self, docs: GeneratedDocs, dest_dir: Path) -> Path:
        """
        Write docs to `dest_dir` and return the path to the zip file.

        This is a public method so tests can inspect the zip contents
        without triggering an actual GitHub API call.

        Parameters
        ----------
        docs : GeneratedDocs
            The documentation artefacts to package.
        dest_dir : Path
            Directory in which to create the zip file.

        Returns
        -------
        Path
            Path to the created zip file.
        """
        return self._write_to_disk(docs, dest_dir)

    # ── Private helpers ───────────────────────────────────────────────────────

    def _write_to_disk(self, docs: GeneratedDocs, tmpdir: Path) -> Path:
        out = tmpdir / "docs" / "generated"
        out.mkdir(parents=True)

        # Top-level README
        (out / "README.md").write_text(docs.overview_md, encoding="utf-8")

        # Architecture diagram wrapped in a Mermaid code fence
        arch_dir = out / "architecture"
        arch_dir.mkdir()
        (arch_dir / "overview.md").write_text(
            f"# Architecture\n\n```mermaid\n{docs.architecture_mermaid}\n```\n",
            encoding="utf-8",
        )

        # Per-module docs
        modules_dir = out / "modules"
        modules_dir.mkdir()
        for module in docs.modules:
            safe_name = (
                module.get("name", "unknown")
                .replace("/", "_")
                .replace(" ", "_")
            )
            (modules_dir / f"{safe_name}.md").write_text(
                module.get("content_md", ""), encoding="utf-8"
            )

        # Also save the outline for debugging
        (out / "outline.json").write_text(
            json.dumps(docs.outline, indent=2), encoding="utf-8"
        )

        # Zip the whole docs/ tree
        zip_path = tmpdir / "artifact.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for file in out.rglob("*"):
                if file.is_file():
                    zf.write(file, file.relative_to(tmpdir))

        return zip_path

    def _upload_zip(self, zip_path: Path, run_id: str, job_id: str) -> None:
        url = (
            f"{self.UPLOAD_BASE}/repos/{self._repo}"
            f"/actions/runs/{run_id}/artifacts"
            f"?name=generated-docs-{job_id}"
        )
        data = zip_path.read_bytes()
        request = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/zip",
                "Content-Length": str(len(data)),
            },
        )
        with urllib.request.urlopen(request) as resp:
            if resp.status not in (200, 201):
                raise urllib.error.HTTPError(url, resp.status, "Artifact upload failed", None, None)  # type: ignore[arg-type]

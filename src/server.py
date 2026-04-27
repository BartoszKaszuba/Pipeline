"""
server.py
─────────
HTTP server entrypoint. Wires all modules together and handles the
request/response lifecycle.

Each incoming POST /generate request is processed as follows:
  1. WebhookVerifier confirms the HMAC-SHA256 signature.
  2. The server responds 202 Accepted immediately (model inference is slow).
  3. A background thread runs the full pipeline:
       FileCollector → DocGenerator → ArtifactPackager
"""

import json
import os
import threading
import uuid
from typing import Any
from http.server import BaseHTTPRequestHandler, HTTPServer
from artifact_packager import ArtifactPackager
from doc_generator import DocGenerator
from file_collector import FileCollector
from ollama_client import OllamaClient
from webhook_verifier import WebhookVerifier
from dotenv import load_dotenv

load_dotenv()
            


class DocServerHandler(BaseHTTPRequestHandler):
    """
    HTTP request handler. One instance is created per request.

    All module instances are created fresh per pipeline run so that
    configuration changes (e.g. repo path) take effect without a restart.
    """

    # Injected by DocServer before the HTTPServer starts — allows tests
    # to swap the config without touching environment variables.
    config: dict[str, Any] = {}

    def do_POST(self) -> None:
        if self.path != "/generate":
            self._respond(404, {"error": "Not found"})
            return

        length = int(self.headers.get("Content-Length", 0))
        body   = self.rfile.read(length)
        sig    = self.headers.get("X-Hub-Signature-256", "")

        verifier = WebhookVerifier(self.config["webhook_secret"])
        if not verifier.verify(body, sig):
            self._respond(401, {"error": "Invalid signature"})
            return

        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            self._respond(400, {"error": "Invalid JSON body"})
            return

        job_id = str(uuid.uuid4())[:8]

        # Respond immediately so GitHub Actions doesn't time out
        self._respond(202, {"job_id": job_id})

        # Run the pipeline in the background
        thread = threading.Thread(
            target=self._run_pipeline,
            args=(job_id, payload),
            daemon=True,
        )
        thread.start()

    def do_GET(self) -> None:
        if self.path == "/health":
            self._respond(200, {"status": "ok"})
        else:
            self._respond(404, {"error": "Not found"})

    # ── Private helpers ───────────────────────────────────────────────────────

    def _run_pipeline(self, job_id: str, payload: dict[str, Any]) -> None:
        run_id = payload.get("run_id", "")
        repo   = payload.get("repo", "")
        sha    = payload.get("sha", "unknown")

        print(f"[{job_id}] Starting pipeline for {repo}@{sha[:7]}")

        try:
            # 1. Collect source files
            collector = FileCollector(self.config["repo_path"])
            source    = collector.collect()
            print(f"[{job_id}] Collected {len(source):,} characters of source")

            # 2. Generate documentation
            client    = OllamaClient(
                model    = self.config.get("ollama_model", "qwen35-docs"),
                base_url = self.config.get("ollama_url", "http://localhost:11434"),
            )
            generator = DocGenerator(client)
            docs      = generator.generate(source)
            print(f"[{job_id}] Documentation generated")

            # 3. Package and upload
            
            packager = ArtifactPackager(
                github_token = str(os.getenv("GITHUB_TOKEN")),
                repo         = repo,
            )
            packager.upload(docs, run_id, job_id)
            print(f"[{job_id}] Artifact uploaded — job complete")

        except Exception as exc:
            # Non-fatal: log the error but don't crash the server process
            print(f"[{job_id}] Pipeline failed: {exc}")

    def _respond(self, status: int, body: dict[str, Any]) -> None:
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: N802
        # Suppress the default Apache-style access log in favour of our own
        pass


class DocServer:
    """
    Configures and starts the HTTP server.

    Parameters
    ----------
    port : int
        Port to listen on.
    config : dict
        Runtime configuration passed to each request handler.
        Required keys: webhook_secret, repo_path, github_token.
        Optional keys: ollama_model, ollama_url.
    """

    def __init__(self, port: int, config: dict[str, Any]) -> None:
        self.port   = port
        self.config = config

    def start(self) -> None:
        # Inject config into the handler class before the server starts
        DocServerHandler.config = self.config

        server = HTTPServer(("0.0.0.0", self.port), DocServerHandler)
        print(f"Doc server listening on :{self.port}")
        server.serve_forever()


def _config_from_env() -> dict[str, Any]:
    return {
        "webhook_secret": os.environ["DOC_SERVER_WEBHOOK_SECRET"],
        "github_token":   str(os.getenv("GITHUB_TOKEN")),
        "repo_path":      os.environ.get("/home/bartosz/Documents/Long Covid buddy/Pipeline", "."),
        "ollama_model":   os.environ.get("OLLAMA_MODEL", "qwen35-docs"),
        "ollama_url":     os.environ.get("OLLAMA_URL", "http://localhost:11434"),
    }


if __name__ == "__main__":
    port   = int(os.environ.get("PORT", 8080))
    config = _config_from_env()
    DocServer(port, config).start()
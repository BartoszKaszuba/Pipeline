# server.py
import hmac, hashlib, os, json, uuid, zipfile, tempfile, threading
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
import urllib.request, subprocess

 

WEBHOOK_SECRET = os.environ["DOC_SERVER_WEBHOOK_SECRET"]
GITHUB_TOKEN   = os.environ["GITHUB_UPLOAD_TOKEN"]
REPO_PATH      = os.environ.get("REPO_PATH", "/path/to/your/repo")
OLLAMA_URL     = "http://localhost:11434/api/chat"

 

def verify_sig(body: bytes, header: str) -> bool:
    expected = "sha256=" + hmac.new(
        WEBHOOK_SECRET.encode(), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, header)

 

def collect_source_files(repo_path: str, max_chars=80_000) -> str:
    """Collect relevant source files into a single context string."""
    extensions = {".ts", ".tsx", ".js", ".jsx", ".py", ".go",
                  ".prisma", ".graphql", ".sql"}
    ignore = {"node_modules", ".git", "dist", "build", ".next", "coverage"}

    parts = []
    total = 0
    for p in sorted(Path(repo_path).rglob("*")):
        if any(part in ignore for part in p.parts):
            continue
        if p.suffix in extensions and p.is_file():
            try:
                content = p.read_text(errors="replace")
                chunk = f"\n\n### FILE: {p.relative_to(repo_path)}\n{content}"
                if total + len(chunk) > max_chars:
                    break
                parts.append(chunk)
                total += len(chunk)
            except Exception:
                pass
    return "".join(parts)

 

def call_ollama(prompt: str) -> str:
    payload = json.dumps({
        "model": "qwen35-docs",
        "stream": False,
        "messages": [{"role": "user", "content": prompt}]
    }).encode()
    req = urllib.request.Request(
        OLLAMA_URL, data=payload,
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.loads(r.read())["message"]["content"]

 

def generate_docs(job_id: str, run_id: str, sha: str, repo: str):
    """Runs async — called in a background thread."""
    try:
        source = collect_source_files(REPO_PATH)

 

        # Pass 1: structured outline
        outline_prompt = f"""Analyze these source files and output ONLY a JSON object (no markdown, no explanation) with this schema:
{{
  "modules": [{{"name": "string", "path": "string", "purpose": "string", "inputs": [], "outputs": [], "dependencies": []}}],
  "api_endpoints": [{{"method": "string", "path": "string", "description": "string"}}],
  "system_summary": "string (2-3 sentences max)"
}}

 

SOURCE FILES:
{source}"""

 

        outline_raw = call_ollama(outline_prompt)
        # Strip any thinking tags Qwen3.5 might emit
        if "<think>" in outline_raw:
            outline_raw = outline_raw.split("</think>")[-1].strip()
        outline = json.loads(outline_raw.strip().lstrip("```json").rstrip("```"))

 

        # Pass 2: generate docs from outline
        docs_prompt = f"""Using this system outline, generate documentation. Output ONLY JSON (no markdown fences) with this schema:
{{
  "overview_md": "full markdown string for docs/generated/README.md",
  "architecture_mermaid": "valid Mermaid flowchart TD string only — no ```mermaid fences",
  "modules": [{{"name": "string", "path": "string", "content_md": "string"}}]
}}

 

OUTLINE:
{json.dumps(outline, indent=2)}"""

 

        docs_raw = call_ollama(docs_prompt)
        if "<think>" in docs_raw:
            docs_raw = docs_raw.split("</think>")[-1].strip()
        docs = json.loads(docs_raw.strip().lstrip("```json").rstrip("```"))

 

        # Write output files
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "docs" / "generated"
            out.mkdir(parents=True)

 

            (out / "README.md").write_text(docs["overview_md"])

 

            # Validate and save Mermaid diagram
            mermaid = docs["architecture_mermaid"]
            (out / "architecture" ).mkdir(exist_ok=True)
            (out / "architecture" / "overview.md").write_text(
                f"# Architecture\n\n```mermaid\n{mermaid}\n```\n"
            )

 

            # Per-module docs
            (out / "modules").mkdir(exist_ok=True)
            for mod in docs.get("modules", []):
                safe_name = mod["name"].replace("/", "_").replace(" ", "_")
                (out / "modules" / f"{safe_name}.md").write_text(mod["content_md"])

 

            # Zip it
            zip_path = Path(tmpdir) / f"generated-docs-{job_id}.zip"
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for f in out.rglob("*"):
                    if f.is_file():
                        zf.write(f, f.relative_to(tmpdir))

 

            # Upload artifact to GitHub Actions
            upload_url = (
                f"https://uploads.github.com/repos/{repo}"
                f"/actions/runs/{run_id}/artifacts"
                f"?name=generated-docs-{job_id}"
            )
            with open(zip_path, "rb") as zf:
                data = zf.read()
            req = urllib.request.Request(
                upload_url, data=data, method="POST",
                headers={
                    "Authorization": f"Bearer {GITHUB_TOKEN}",
                    "Content-Type": "application/zip",
                    "Content-Length": str(len(data)),
                }
            )
            urllib.request.urlopen(req)
            print(f"[{job_id}] Artifact uploaded successfully")

 

    except Exception as e:
        print(f"[{job_id}] Generation failed: {e}")

 

class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != "/generate":
            self.send_response(404); self.end_headers(); return

 

        length = int(self.headers.get("Content-Length", 0))
        body   = self.rfile.read(length)
        sig    = self.headers.get("X-Hub-Signature-256", "")

 

        if not verify_sig(body, sig):
            self.send_response(401); self.end_headers()
            self.wfile.write(b"Invalid signature"); return

 

        payload  = json.loads(body)
        job_id   = str(uuid.uuid4())[:8]
        run_id   = payload["run_id"]
        sha      = payload["sha"]
        repo     = payload["repo"]

 

        # Respond 202 immediately — process in background
        self.send_response(202)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"job_id": job_id}).encode())

 

        threading.Thread(
            target=generate_docs,
            args=(job_id, run_id, sha, repo),
            daemon=True
        ).start()

 

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    print(f"Doc server listening on :{port}")
    HTTPServer(("0.0.0.0", port), Handler).serve_forever()
"""
file_collector.py
─────────────────
Walks the repository and assembles relevant source files into a single
context string that can be sent to the language model.
"""

from pathlib import Path


# File extensions considered "source" for documentation purposes.
DEFAULT_EXTENSIONS: frozenset[str] = frozenset({
    ".ts", ".tsx", ".js", ".jsx",
    ".py", ".go", ".java",
    ".prisma", ".graphql", ".sql",
})

# Directories that should never be included (generated code, deps, etc.)
DEFAULT_IGNORE_DIRS: frozenset[str] = frozenset({
    "node_modules", ".git", "dist", "build",
    ".next", "coverage", "__pycache__", ".venv",
})


class FileCollector:
    """
    Collects source files from a repository into a single context string.

    The output format is:

        ### FILE: src/server/index.ts
        <file contents>

        ### FILE: src/models/user.ts
        <file contents>
        ...

    Files are sorted alphabetically and collection stops once the total
    character budget (`max_chars`) is reached.
    """

    def __init__(
        self,
        repo_path: str,
        extensions: frozenset[str] = DEFAULT_EXTENSIONS,
        ignore_dirs: frozenset[str] = DEFAULT_IGNORE_DIRS,
        max_chars: int = 80_000,
    ) -> None:
        """
        Parameters
        ----------
        repo_path : str
            Absolute path to the repository root.
        extensions : frozenset[str]
            File extensions to include (e.g. {".ts", ".py"}).
        ignore_dirs : frozenset[str]
            Directory names to skip at any depth.
        max_chars : int
            Hard cap on the total number of characters collected.
            Maps to roughly 20–25k tokens for a 9B model.
        """
        self.repo_path = Path(repo_path)
        self.extensions = extensions
        self.ignore_dirs = ignore_dirs
        self.max_chars = max_chars

    def collect(self) -> str:
        """
        Walk the repository and return a concatenated source string.

        Returns
        -------
        str
            All collected file contents joined with `### FILE:` headers,
            or an empty string if no files matched.

        Raises
        ------
        FileNotFoundError
            If `repo_path` does not exist.
        """
        if not self.repo_path.exists():
            raise FileNotFoundError(
                f"Repository path not found: {self.repo_path}"
            )

        parts: list[str] = []
        total_chars = 0

        for path in sorted(self.repo_path.rglob("*")):
            if self._should_skip(path):
                continue

            try:
                content = path.read_text(errors="replace")
            except OSError:
                continue

            chunk = f"\n\n### FILE: {path.relative_to(self.repo_path)}\n{content}"

            if total_chars + len(chunk) > self.max_chars:
                break

            parts.append(chunk)
            total_chars += len(chunk)

        return "".join(parts)

    def _should_skip(self, path: Path) -> bool:
        """Return True if this path should be excluded from collection."""
        if not path.is_file():
            return True
        if path.suffix not in self.extensions:
            return True
        if any(part in self.ignore_dirs for part in path.parts):
            return True
        return False
"""Tree-sitter-based structural code index with regex fallback."""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from observibot.core.code_intelligence.code_index import (
    CodeChunk,
    CodeIndex,
    CodeSymbol,
)

log = logging.getLogger(__name__)

LANGUAGE_EXTENSIONS = {
    "python": {".py"},
    "javascript": {".js", ".jsx", ".mjs"},
    "typescript": {".ts", ".tsx"},
}

HIGH_SIGNAL_NAME_PARTS = {
    "model", "schema", "entity", "type", "service", "handler",
    "controller", "route", "api", "middleware", "config",
    "migration", "seed", "index", "main", "app", "server",
}

# Universal directory exclusion set — framework-agnostic
EXCLUDED_DIRS = {
    "node_modules", "vendor", "dist", "build", ".git", "__pycache__",
    ".dart_tool", ".next", ".nuxt", "coverage", ".cache", ".venv", "venv",
    "env", ".tox", "target", "out", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", "egg-info",
}

# Max file size in bytes — files larger are almost always generated/vendored
MAX_FILE_SIZE_BYTES = 50_000

# Generated/compiled file patterns to skip
GENERATED_FILE_PATTERNS = {
    ".min.js", ".min.css", ".map", ".lock", ".sum",
    ".g.dart", ".freezed.dart", ".mocks.dart", ".pb.go", ".pb.dart",
}
GENERATED_GLOB_RE = re.compile(r"\.generated\.\w+$")

# Binary extensions to skip
BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".webp", ".bmp",
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".zip", ".tar", ".gz", ".bz2", ".7z", ".rar",
    ".exe", ".dll", ".so", ".dylib", ".o", ".a", ".pyc", ".pyo",
    ".wasm", ".class", ".jar",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx",
    ".mp3", ".mp4", ".avi", ".mov", ".wav",
    ".sqlite", ".db",
}

# Cap total code bytes sent to LLM
MAX_TOTAL_CODE_BYTES = 100_000
MAX_SINGLE_FILE_BYTES = 8_000

ROUTE_DECORATOR_RE = re.compile(
    r"@(?:app|router)\.\s*(?:get|post|put|delete|patch|route|api_route)\b"
)

_ts_available = False
try:
    import tree_sitter  # noqa: F401
    import tree_sitter_python  # noqa: F401

    _ts_available = True
except ImportError:
    log.info("tree-sitter not available, using regex fallback for code indexing")


def _ext_to_language(ext: str) -> str | None:
    for lang, exts in LANGUAGE_EXTENSIONS.items():
        if ext in exts:
            return lang
    return None


def _should_skip_dir(path: Path) -> bool:
    """Check if a directory should be excluded based on universal exclusion rules."""
    return path.name in EXCLUDED_DIRS


def _should_skip_file(fpath: Path) -> bool:
    """Check if a file should be excluded based on size, extension, or generated patterns."""
    suffix = fpath.suffix.lower()
    if suffix in BINARY_EXTENSIONS:
        return True
    fname = fpath.name.lower()
    for pattern in GENERATED_FILE_PATTERNS:
        if fname.endswith(pattern):
            return True
    if GENERATED_GLOB_RE.search(fname):
        return True
    try:
        if fpath.stat().st_size > MAX_FILE_SIZE_BYTES:
            return True
    except OSError:
        return True
    return False


def _score_file(fpath_str: str, symbols: list[CodeSymbol]) -> float:
    """Score a file by universal structural signals. Higher = more business logic."""
    fpath = Path(fpath_str)
    score = 0.0

    # Symbol density: functions/classes per KB of code
    try:
        size_kb = max(fpath.stat().st_size / 1024, 0.1)
    except OSError:
        size_kb = 1.0
    if symbols:
        score += min(len(symbols) / size_kb, 10.0) * 2.0

    # Import/dependency count (files importing many local modules are integration points)
    try:
        content = fpath.read_text(encoding="utf-8", errors="replace")
        import_lines = [
            ln for ln in content.splitlines()
            if ln.strip().startswith(("import ", "from "))
            and "node_modules" not in ln
        ]
        score += min(len(import_lines) * 0.3, 5.0)
    except OSError:
        content = ""

    # File depth: 2-4 levels deep is typical source code
    depth = len(fpath.parts)
    if 3 <= depth <= 5:
        score += 2.0
    elif depth <= 2 or depth >= 8:
        score += 0.0
    else:
        score += 1.0

    # Has exported/public symbols
    public_syms = [s for s in symbols if not s.name.startswith("_")]
    if public_syms:
        score += min(len(public_syms) * 0.5, 3.0)

    # Route handlers are high-signal
    routes = [s for s in symbols if s.kind == "route"]
    if routes:
        score += len(routes) * 3.0

    # Classes with methods indicate business logic
    classes = [s for s in symbols if s.kind == "class"]
    if classes:
        score += len(classes) * 1.5

    # Name signals (generic, not framework-specific)
    stem = fpath.stem.lower()
    for part in HIGH_SIGNAL_NAME_PARTS:
        if part in stem:
            score += 2.0
            break

    # Documentation files
    if fpath.name.upper() in ("README.MD", "ARCHITECTURE.MD", "CHANGELOG.MD"):
        score += 3.0

    return score


class TreeSitterIndex(CodeIndex):
    """Structural code index using tree-sitter with regex fallback."""

    def __init__(self) -> None:
        self._symbols: list[CodeSymbol] = []
        self._files: list[str] = []
        self._indexed_root: str | None = None

    async def index_directory(
        self, path: str, languages: list[str] | None = None,
    ) -> int:
        self._symbols = []
        self._files = []
        self._indexed_root = path
        root = Path(path)
        allowed_langs = set(languages) if languages else set(LANGUAGE_EXTENSIONS.keys())

        count = 0
        for dirpath, dirnames, filenames in os.walk(root):
            # Prune excluded directories in-place so os.walk doesn't descend
            dirnames[:] = [d for d in dirnames if not _should_skip_dir(Path(d))]

            dp = Path(dirpath)
            for fname in filenames:
                fpath = dp / fname
                if _should_skip_file(fpath):
                    continue
                lang = _ext_to_language(fpath.suffix)
                if lang is None or lang not in allowed_langs:
                    continue

                try:
                    symbols = await self._index_file(str(fpath), lang)
                    self._symbols.extend(symbols)
                    self._files.append(str(fpath))
                    count += 1
                except Exception as exc:
                    log.debug("Failed to index %s: %s", fpath, exc)

        log.info("Indexed %d files, extracted %d symbols", count, len(self._symbols))
        return count

    async def _index_file(self, file_path: str, language: str) -> list[CodeSymbol]:
        try:
            content = Path(file_path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []

        if _ts_available and language == "python":
            return self._ts_index_python(file_path, content)

        return self._regex_index(file_path, content, language)

    def _ts_index_python(self, file_path: str, content: str) -> list[CodeSymbol]:
        import tree_sitter
        import tree_sitter_python

        lang = tree_sitter.Language(tree_sitter_python.language())
        parser = tree_sitter.Parser(lang)
        tree = parser.parse(content.encode("utf-8"))
        symbols: list[CodeSymbol] = []
        # Track function nodes handled via decorated_definition to avoid duplicates
        handled_funcs: set[int] = set()

        for node in self._walk_tree(tree.root_node):
            if node.type == "decorated_definition":
                func_node = None
                for child in node.children:
                    if child.type in ("function_definition", "class_definition"):
                        func_node = child
                        break
                if func_node is None:
                    continue

                # Check decorators for route patterns
                is_route = False
                for child in node.children:
                    if child.type == "decorator":
                        dec_text = content[child.start_byte:child.end_byte]
                        if ROUTE_DECORATOR_RE.search(dec_text):
                            is_route = True
                            break

                if is_route and func_node.type == "function_definition":
                    fn_name = func_node.child_by_field_name("name")
                    if fn_name:
                        sig = self._extract_signature(func_node, content)
                        symbols.append(CodeSymbol(
                            name=content[fn_name.start_byte:fn_name.end_byte],
                            kind="route",
                            file_path=file_path,
                            start_line=node.start_point.row + 1,
                            end_line=node.end_point.row + 1,
                            language="python",
                            signature=sig,
                        ))
                    handled_funcs.add(func_node.id)
                # Non-route decorated functions/classes: let the inner node
                # be processed normally by the function/class handlers below.

            elif node.type == "function_definition" and node.id not in handled_funcs:
                name_node = node.child_by_field_name("name")
                if name_node:
                    name = content[name_node.start_byte:name_node.end_byte]
                    parent_cls = self._find_parent_class(node, content)
                    kind = "method" if parent_cls else "function"
                    docstring = self._extract_docstring(node, content)
                    sig = self._extract_signature(node, content)
                    symbols.append(CodeSymbol(
                        name=name, kind=kind, file_path=file_path,
                        start_line=node.start_point.row + 1,
                        end_line=node.end_point.row + 1,
                        language="python", parent=parent_cls,
                        docstring=docstring, signature=sig,
                    ))
            elif node.type == "class_definition":
                name_node = node.child_by_field_name("name")
                if name_node:
                    name = content[name_node.start_byte:name_node.end_byte]
                    docstring = self._extract_docstring(node, content)
                    symbols.append(CodeSymbol(
                        name=name, kind="class", file_path=file_path,
                        start_line=node.start_point.row + 1,
                        end_line=node.end_point.row + 1,
                        language="python", docstring=docstring,
                    ))

        return symbols

    def _walk_tree(self, node):
        yield node
        for child in node.children:
            yield from self._walk_tree(child)

    def _find_parent_class(self, node, content: str) -> str | None:
        parent = node.parent
        while parent:
            if parent.type == "class_definition":
                name_node = parent.child_by_field_name("name")
                if name_node:
                    return content[name_node.start_byte:name_node.end_byte]
            parent = parent.parent
        return None

    def _extract_docstring(self, node, content: str) -> str | None:
        body = node.child_by_field_name("body")
        if body and body.children:
            first = body.children[0]
            if first.type == "expression_statement" and first.children:
                child = first.children[0]
                if child.type == "string":
                    raw = content[child.start_byte:child.end_byte]
                    return raw.strip("\"'").strip()
        return None

    def _extract_signature(self, node, content: str) -> str | None:
        params = node.child_by_field_name("parameters")
        name_node = node.child_by_field_name("name")
        if params and name_node:
            name = content[name_node.start_byte:name_node.end_byte]
            p = content[params.start_byte:params.end_byte]
            return f"{name}{p}"
        return None

    def _regex_index(
        self, file_path: str, content: str, language: str,
    ) -> list[CodeSymbol]:
        symbols: list[CodeSymbol] = []
        lines = content.splitlines()

        if language == "python":
            patterns = [
                (re.compile(r"^class\s+(\w+)"), "class"),
                (re.compile(r"^def\s+(\w+)"), "function"),
                (re.compile(r"^\s+def\s+(\w+)"), "method"),
            ]
        elif language in ("javascript", "typescript"):
            patterns = [
                (re.compile(r"^class\s+(\w+)"), "class"),
                (re.compile(r"^(?:export\s+)?(?:async\s+)?function\s+(\w+)"), "function"),
                (re.compile(r"^(?:export\s+)?const\s+(\w+)\s*=\s*(?:async\s+)?\("), "function"),
            ]
        else:
            return symbols

        for i, line in enumerate(lines):
            for pattern, kind in patterns:
                m = pattern.match(line)
                if m:
                    end = self._find_block_end(lines, i, language)
                    symbols.append(CodeSymbol(
                        name=m.group(1), kind=kind, file_path=file_path,
                        start_line=i + 1, end_line=end + 1,
                        language=language,
                    ))

        return symbols

    def _find_block_end(
        self, lines: list[str], start: int, language: str,
    ) -> int:
        if language == "python":
            if start >= len(lines):
                return start
            base_indent = len(lines[start]) - len(lines[start].lstrip())
            for i in range(start + 1, min(start + 500, len(lines))):
                stripped = lines[i].strip()
                if not stripped:
                    continue
                indent = len(lines[i]) - len(lines[i].lstrip())
                if indent <= base_indent:
                    return i - 1
            return min(start + 499, len(lines) - 1)
        else:
            brace_count = 0
            for i in range(start, min(start + 500, len(lines))):
                brace_count += lines[i].count("{") - lines[i].count("}")
                if brace_count <= 0 and i > start:
                    return i
            return min(start + 499, len(lines) - 1)

    async def get_symbols(
        self, file_path: str | None = None, kind: str | None = None,
    ) -> list[CodeSymbol]:
        result = self._symbols
        if file_path:
            result = [s for s in result if s.file_path == file_path]
        if kind:
            result = [s for s in result if s.kind == kind]
        return result

    async def get_chunks_for_file(
        self, file_path: str, max_chunk_lines: int = 100,
    ) -> list[CodeChunk]:
        try:
            content = Path(file_path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []

        lines = content.splitlines()
        file_symbols = [s for s in self._symbols if s.file_path == file_path]

        if not file_symbols:
            chunk_content = "\n".join(lines[:max_chunk_lines])
            lang = _ext_to_language(Path(file_path).suffix) or "unknown"
            return [CodeChunk(
                content=chunk_content, file_path=file_path,
                start_line=1, end_line=min(len(lines), max_chunk_lines),
                language=lang, symbols=[],
            )]

        file_symbols.sort(key=lambda s: s.start_line)
        chunks: list[CodeChunk] = []

        for sym in file_symbols:
            start = max(0, sym.start_line - 1)
            end = min(len(lines), sym.end_line)
            if end - start > max_chunk_lines:
                end = start + max_chunk_lines
            chunk_lines = lines[start:end]
            chunks.append(CodeChunk(
                content="\n".join(chunk_lines),
                file_path=file_path,
                start_line=sym.start_line,
                end_line=end,
                language=sym.language,
                symbols=[sym],
            ))

        return chunks

    async def get_high_signal_files(self, max_files: int = 30) -> list[str]:
        """Return top files ranked by structural signals, capped at ~100KB total."""
        scored: list[tuple[str, float]] = []
        for fpath in self._files:
            syms = [s for s in self._symbols if s.file_path == fpath]
            score = _score_file(fpath, syms)
            scored.append((fpath, score))

        scored.sort(key=lambda x: x[1], reverse=True)

        result: list[str] = []
        total_bytes = 0
        for fpath, _score in scored[:max_files]:
            try:
                fsize = Path(fpath).stat().st_size
            except OSError:
                continue
            if total_bytes + min(fsize, MAX_SINGLE_FILE_BYTES) > MAX_TOTAL_CODE_BYTES:
                break
            total_bytes += min(fsize, MAX_SINGLE_FILE_BYTES)
            result.append(fpath)

        return result

    async def get_entrypoints(self) -> list[CodeSymbol]:
        entrypoints: list[CodeSymbol] = []
        for sym in self._symbols:
            if sym.kind == "route" or sym.name in ("main", "app", "create_app"):
                entrypoints.append(sym)
        return entrypoints

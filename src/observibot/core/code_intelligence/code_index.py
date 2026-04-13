"""Code index interface — abstracts structural code analysis."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class CodeSymbol:
    name: str
    kind: str  # "class", "function", "method", "route", "model"
    file_path: str
    start_line: int
    end_line: int
    language: str
    parent: str | None = None
    docstring: str | None = None
    signature: str | None = None


@dataclass
class CodeChunk:
    """A bounded piece of code with metadata for LLM extraction."""
    content: str
    file_path: str
    start_line: int
    end_line: int
    language: str
    symbols: list[CodeSymbol] = field(default_factory=list)


class CodeIndex(ABC):
    """Interface for querying indexed code structure."""

    @abstractmethod
    async def index_directory(
        self, path: str, languages: list[str] | None = None,
    ) -> int:
        """Index a directory. Returns number of files indexed."""

    @abstractmethod
    async def get_symbols(
        self, file_path: str | None = None, kind: str | None = None,
    ) -> list[CodeSymbol]:
        """Get symbols, optionally filtered by file or kind."""

    @abstractmethod
    async def get_chunks_for_file(
        self, file_path: str, max_chunk_lines: int = 100,
    ) -> list[CodeChunk]:
        """Get function/class-bounded chunks for a file."""

    @abstractmethod
    async def get_high_signal_files(self) -> list[str]:
        """Identify files likely containing business logic."""

    @abstractmethod
    async def get_entrypoints(self) -> list[CodeSymbol]:
        """Find main entrypoints, route handlers, API endpoints."""

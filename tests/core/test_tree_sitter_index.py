"""Tests for tree-sitter structural code analysis."""
from __future__ import annotations

from pathlib import Path

import pytest

from observibot.core.code_intelligence.tree_sitter_index import TreeSitterIndex

SAMPLE_PYTHON = '''\
"""Sample module for testing."""

class UserService:
    """Manages user accounts."""

    def create_user(self, email: str, name: str) -> dict:
        """Create a new user."""
        return {"email": email, "name": name}

    def get_user(self, user_id: int) -> dict:
        return {"id": user_id}


def standalone_function(x: int) -> int:
    return x * 2


class PaymentProcessor:
    def charge(self, amount: int) -> bool:
        return amount > 0
'''

SAMPLE_PYTHON_ROUTES = '''\
from fastapi import APIRouter

router = APIRouter()

@router.get("/users")
async def list_users():
    return []

@router.post("/users")
async def create_user(data: dict):
    return data

def helper():
    pass
'''

SAMPLE_TYPESCRIPT = '''\
export class TaskManager {
  async createTask(title: string): Promise<Task> {
    return { title };
  }
}

export async function deleteTask(id: string) {
  return true;
}

const processQueue = async (items: string[]) => {
  return items;
};
'''


@pytest.fixture
def sample_repo(tmp_path: Path) -> Path:
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "user.py").write_text(SAMPLE_PYTHON)

    (tmp_path / "api").mkdir()
    (tmp_path / "api" / "routes.py").write_text(SAMPLE_PYTHON_ROUTES)

    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "task.ts").write_text(SAMPLE_TYPESCRIPT)

    (tmp_path / "utils.py").write_text("def helper():\n    pass\n")

    return tmp_path


class TestTreeSitterIndex:
    async def test_index_directory_counts_files(self, sample_repo: Path):
        idx = TreeSitterIndex()
        count = await idx.index_directory(str(sample_repo))
        assert count >= 3

    async def test_extract_python_classes(self, sample_repo: Path):
        idx = TreeSitterIndex()
        await idx.index_directory(str(sample_repo))
        classes = await idx.get_symbols(kind="class")
        class_names = {s.name for s in classes}
        assert "UserService" in class_names
        assert "PaymentProcessor" in class_names

    async def test_extract_python_functions(self, sample_repo: Path):
        idx = TreeSitterIndex()
        await idx.index_directory(str(sample_repo))
        funcs = await idx.get_symbols(kind="function")
        func_names = {s.name for s in funcs}
        assert "standalone_function" in func_names

    async def test_extract_python_methods(self, sample_repo: Path):
        idx = TreeSitterIndex()
        await idx.index_directory(str(sample_repo))
        methods = await idx.get_symbols(kind="method")
        method_names = {s.name for s in methods}
        assert "create_user" in method_names
        assert "get_user" in method_names

    async def test_extract_route_handlers(self, sample_repo: Path):
        idx = TreeSitterIndex()
        await idx.index_directory(str(sample_repo))
        routes = await idx.get_symbols(kind="route")
        route_names = {s.name for s in routes}
        assert "list_users" in route_names or "create_user" in route_names

    async def test_filter_by_file(self, sample_repo: Path):
        idx = TreeSitterIndex()
        await idx.index_directory(str(sample_repo))
        user_file = str(sample_repo / "models" / "user.py")
        symbols = await idx.get_symbols(file_path=user_file)
        assert all(s.file_path == user_file for s in symbols)
        assert len(symbols) >= 3

    async def test_chunk_boundaries(self, sample_repo: Path):
        idx = TreeSitterIndex()
        await idx.index_directory(str(sample_repo))
        user_file = str(sample_repo / "models" / "user.py")
        chunks = await idx.get_chunks_for_file(user_file)
        assert len(chunks) >= 2
        for chunk in chunks:
            assert chunk.start_line >= 1
            assert chunk.end_line >= chunk.start_line
            assert len(chunk.content) > 0

    async def test_high_signal_files(self, sample_repo: Path):
        idx = TreeSitterIndex()
        await idx.index_directory(str(sample_repo))
        high_signal = await idx.get_high_signal_files()
        paths = [Path(p).name for p in high_signal]
        assert "user.py" in paths or "routes.py" in paths

    async def test_get_entrypoints(self, sample_repo: Path):
        idx = TreeSitterIndex()
        await idx.index_directory(str(sample_repo))
        entrypoints = await idx.get_entrypoints()
        assert len(entrypoints) >= 1

    async def test_skips_pycache(self, tmp_path: Path):
        cache_dir = tmp_path / "__pycache__"
        cache_dir.mkdir()
        (cache_dir / "test.py").write_text("x = 1")
        (tmp_path / "real.py").write_text("def foo(): pass")

        idx = TreeSitterIndex()
        await idx.index_directory(str(tmp_path))
        symbols = await idx.get_symbols()
        file_paths = {s.file_path for s in symbols}
        assert not any("__pycache__" in p for p in file_paths)


class TestRegexFallback:
    async def test_javascript_regex_parsing(self, sample_repo: Path):
        idx = TreeSitterIndex()
        await idx.index_directory(str(sample_repo), languages=["typescript"])
        symbols = await idx.get_symbols()
        names = {s.name for s in symbols}
        assert "TaskManager" in names or "deleteTask" in names

    async def test_empty_directory(self, tmp_path: Path):
        idx = TreeSitterIndex()
        count = await idx.index_directory(str(tmp_path))
        assert count == 0
        symbols = await idx.get_symbols()
        assert len(symbols) == 0


class TestDocstringExtraction:
    async def test_python_docstring(self, sample_repo: Path):
        idx = TreeSitterIndex()
        await idx.index_directory(str(sample_repo))
        classes = await idx.get_symbols(kind="class")
        user_service = [s for s in classes if s.name == "UserService"]
        if user_service:
            assert user_service[0].docstring is not None
            assert "user accounts" in user_service[0].docstring.lower()

"""Structural checks on the Alembic revision chain.

CI has no Postgres to run migrations against (see ROADMAP.md section 3a), so these tests
cannot prove a migration's SQL works. What they catch is a broken *chain*: a new revision whose
down_revision points at the wrong parent, or two revisions claiming the same parent, which
leaves Alembic with two heads and makes `alembic upgrade head` refuse to run.

The migration files are loaded by path rather than imported, because this repo's alembic/
directory is not a package, and `import alembic` resolves to the installed Alembic library.
"""

import importlib.util
from pathlib import Path

VERSIONS = Path(__file__).resolve().parent.parent / "alembic" / "versions"


def _load(path):
    """Loads one migration file as a module, so its revision identifiers can be read."""
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _all_revisions():
    return [_load(p) for p in sorted(VERSIONS.glob("*.py"))]


def test_created_at_revises_the_baseline():
    baseline = _load(VERSIONS / "0001_create_transactions_table.py")
    created_at = _load(VERSIONS / "0002_add_created_at.py")
    assert created_at.down_revision == baseline.revision


def test_revision_chain_has_exactly_one_head():
    revisions = _all_revisions()
    ids = {m.revision for m in revisions}
    parents = {m.down_revision for m in revisions if m.down_revision is not None}

    # Every parent must be a revision that actually exists...
    assert parents <= ids
    # ...and exactly one revision is nobody's parent. Two heads means two migrations were
    # written against the same parent, e.g. a future 0003 that also revises 0001.
    assert len(ids - parents) == 1


def test_exactly_one_revision_is_the_root():
    roots = [m.revision for m in _all_revisions() if m.down_revision is None]
    assert roots == ["0001_create_transactions"]


def test_entered_by_revises_created_at():
    created_at = _load(VERSIONS / "0002_add_created_at.py")
    entered_by = _load(VERSIONS / "0003_add_entered_by.py")
    assert entered_by.down_revision == created_at.revision

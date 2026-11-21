"""Structural checks on the Alembic revision chain.

CI has no Postgres to run migrations against (see the "Migrations applied from scratch" item in ROADMAP.md's Phase 1), so these tests
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
    assert ids - parents == {"0012_trip_sheet_id"}


def test_soft_delete_revises_settlements():
    settlements = _load(VERSIONS / "0009_add_settlements.py")
    soft_delete = _load(VERSIONS / "0010_soft_delete_behind_a_view.py")
    assert soft_delete.down_revision == settlements.revision


def test_the_view_shows_every_column_the_writers_name():
    """Every INSERT INTO transactions now goes through the view (ADR-0050). A column a writer
    names that the view leaves out would fail every save, so check the lists agree."""
    import re

    view = set(_load(VERSIONS / "0010_soft_delete_behind_a_view.py").LIVE_COLUMNS)
    root = VERSIONS.parent.parent
    for path in ("app/add_expense.py", "app/main.py"):
        source = (root / path).read_text(encoding="utf-8")
        inserts = re.findall(r"INSERT INTO transactions \((.*?)\)", source, re.DOTALL)
        assert inserts, path
        for columns in inserts:
            named = {c.strip() for c in columns.split(",")}
            assert named <= view, (path, named - view)
    assert "deleted_at" not in view and "deleted_by_participant_id" not in view


def test_exactly_one_revision_is_the_root():
    roots = [m.revision for m in _all_revisions() if m.down_revision is None]
    assert roots == ["0001_create_transactions"]


def test_entered_by_revises_created_at():
    created_at = _load(VERSIONS / "0002_add_created_at.py")
    entered_by = _load(VERSIONS / "0003_add_entered_by.py")
    assert entered_by.down_revision == created_at.revision


def test_participants_accounts_revises_entered_by():
    entered_by = _load(VERSIONS / "0003_add_entered_by.py")
    directory = _load(VERSIONS / "0004_add_participants_and_accounts.py")
    assert directory.down_revision == entered_by.revision


def test_contraction_revises_participants_accounts():
    directory = _load(VERSIONS / "0004_add_participants_and_accounts.py")
    contraction = _load(VERSIONS / "0005_drop_text_account_columns.py")
    assert contraction.down_revision == directory.revision


def test_trips_revises_the_contraction():
    contraction = _load(VERSIONS / "0005_drop_text_account_columns.py")
    trips = _load(VERSIONS / "0006_add_trips.py")
    assert trips.down_revision == contraction.revision


def test_paid_by_revises_trips():
    trips = _load(VERSIONS / "0006_add_trips.py")
    paid_by = _load(VERSIONS / "0007_add_paid_by.py")
    assert paid_by.down_revision == trips.revision

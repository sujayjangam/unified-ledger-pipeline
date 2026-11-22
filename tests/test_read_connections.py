"""Read checkout ownership, with fake connections only."""
from contextlib import nullcontext
from datetime import date
from unittest.mock import MagicMock, call

import pytest

from app import bot_core as bot, database
from app.services import ledger_queries as queries
from app.services.trips import Trip


def test_read_connection_uses_module_engine_and_autocommit(monkeypatch):
    engine = MagicMock()
    get_engine = MagicMock(return_value=engine)
    monkeypatch.setattr(database, 'get_engine', get_engine)
    result = database.get_read_connection()
    get_engine.assert_called_once_with()
    engine.connect.assert_called_once_with()
    engine.connect.return_value.execution_options.assert_called_once_with(
        isolation_level='AUTOCOMMIT')
    assert result is engine.connect.return_value.execution_options.return_value


def test_read_connection_is_blocked_by_database_fixture():
    with pytest.raises(RuntimeError, match='Tests must not open'):
        database.get_read_connection()


@pytest.mark.parametrize('active', [True, False])
def test_snapshot_checks_out_once_for_all_scopes(monkeypatch, directory, active):
    day = date(2026, 11, 22)
    trips = [Trip(i, f'Trip {i}', day, day) for i in (7, 8)]
    conn = MagicMock()
    conn.execute.return_value.fetchall.return_value = []
    conn.execute.return_value.mappings.return_value.all.return_value = []
    connect = MagicMock(side_effect=lambda: nullcontext(conn))
    monkeypatch.setattr(bot, 'get_read_connection', connect)
    # The real ledger loaders must use the passed connection, never check out their own.
    query_connect = MagicMock(side_effect=AssertionError('Extra checkout'))
    monkeypatch.setattr(queries, 'get_read_connection', query_connect)
    load = MagicMock(return_value=trips[0] if active else None)
    member = MagicMock(side_effect=lambda connection, pid, tid: trips[tid - 7])
    pages = MagicMock(side_effect=[([trips[0]], True), ([trips[1]], False)])
    families = MagicMock(return_value=((1, 'A'), (2, 'B')))
    monkeypatch.setattr(bot, 'load_active_trip', load)
    monkeypatch.setattr(bot, 'member_trip', member)
    monkeypatch.setattr(bot, 'member_trips', pages)
    monkeypatch.setattr(bot, 'trip_families', families)

    snapshot, _, _ = bot._load_settle_snapshot(directory.participants[0], directory)

    connect.assert_called_once_with()
    query_connect.assert_not_called()
    load.assert_called_once_with(conn, 1)
    assert snapshot['trip_id'] == (7 if active else None)
    assert snapshot['bills'] == ()
    assert conn.execute.call_count == (6 if active else 9)
    assert all(c.args[0] is conn for c in families.call_args_list)
    if active:
        pages.assert_not_called()
        member.assert_not_called()
    else:
        assert pages.call_args_list == [call(conn, 1, 0, bot.TRIP_PAGE_SIZE),
                                       call(conn, 1, bot.TRIP_PAGE_SIZE, bot.TRIP_PAGE_SIZE)]
        assert member.call_args_list == [call(conn, 1, 7), call(conn, 1, 8)]
    conn.commit.assert_not_called()
    conn.close.assert_not_called()


@pytest.mark.parametrize('loader', [queries.get_trip_split_entries, queries.get_trip_settlements])
def test_query_borrows_connection_and_keeps_failure_contract(monkeypatch, capsys, loader):
    connect = MagicMock(side_effect=AssertionError('Extra checkout'))
    monkeypatch.setattr(queries, 'get_read_connection', connect)
    conn = MagicMock()
    conn.execute.return_value.fetchall.return_value = []
    conn.execute.return_value.mappings.return_value.all.return_value = []
    assert loader(7, conn=conn) == []
    conn.execute.side_effect = RuntimeError('read failed')
    assert loader(7, conn=conn) is None
    assert 'read failed' in capsys.readouterr().out
    connect.assert_not_called()
    conn.__enter__.assert_not_called()
    conn.__exit__.assert_not_called()
    conn.commit.assert_not_called()


def test_choices_borrows_connection(monkeypatch):
    conn = object()
    connect = MagicMock(side_effect=AssertionError('Extra checkout'))
    choices = MagicMock(return_value=([], False))
    monkeypatch.setattr(bot, 'get_read_connection', connect)
    monkeypatch.setattr(bot, 'member_trips', choices)
    assert bot._load_bals_choices(1, 3, conn=conn) == ([], False)
    choices.assert_called_once_with(conn, 1, 3, bot.TRIP_PAGE_SIZE)
    connect.assert_not_called()

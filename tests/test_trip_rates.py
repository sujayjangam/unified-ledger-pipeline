"""Rate parsing, provenance and additive migration checks without external services."""
import importlib.util
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.services import trip_rates as rates

ROW = dict(rate=Decimal(14080), rate_date=date(2026, 6, 5), source='frankfurter',
           set_by_participant_id=None)


def test_display_directions_and_sources():
    assert rates.rate_display('IDR', ROW, {}) == '1 SGD = 14,080 IDR (ECB, 5 Jun)'
    assert rates.rate_display('USD', dict(ROW, rate=Decimal(1) / Decimal('1.2834'),
                                         source='typed', set_by_participant_id=1), {1: 'Alice'}) == (
        '1 USD = 1.2834 SGD (typed by Alice)')


def test_parse_direction_and_inclusive_ecb_bounds():
    prompt = 'Reply with how many IDR 1 SGD bought, e.g. 14100'
    assert rates.parse_rate('14,100', 'IDR', prompt, ROW) == Decimal(14100)
    assert rates.parse_rate('7040', 'IDR', prompt, ROW) == Decimal(7040)
    assert rates.parse_rate('28160', 'IDR', prompt, ROW) == Decimal(28160)
    assert rates.parse_rate('1.2834', 'USD', 'Reply with how many SGD 1 USD cost') == Decimal(1) / Decimal('1.2834')
    with pytest.raises(ValueError, match='far from the ECB rate 1 SGD = 14,080 IDR'):
        rates.parse_rate('2', 'IDR', prompt, ROW)
    assert rates.parse_rate('2', 'IDR', prompt, dict(ROW, source='typed')) == Decimal(2)


@pytest.mark.parametrize('value', ['0', '-1', '1e3', 'NaN', 'Infinity', '1 SGD', '1,2', '2 3', '', '+2'])
def test_refuse_anything_but_a_positive_number(value):
    with pytest.raises(ValueError):
        rates.parse_rate(value, 'IDR', 'how many IDR 1 SGD bought')


def test_failed_queries_are_explicit(monkeypatch):
    monkeypatch.setattr(rates, 'get_connection', MagicMock(side_effect=RuntimeError))
    assert rates.get_trip_rates(7) is None
    assert rates.upsert_trip_rate(7, 'IDR', Decimal(14080), ROW['rate_date'], 'typed', 1) is False


def test_query_shape_and_upsert_commit(monkeypatch):
    connection = MagicMock()
    connection.execute.return_value.mappings.return_value.all.return_value = [dict(ROW, currency='IDR')]
    monkeypatch.setattr(rates, 'get_connection', MagicMock(return_value=connection))
    connection.__enter__.return_value = connection
    assert rates.get_trip_rates(7)['IDR']['rate'] == Decimal(14080)
    assert rates.upsert_trip_rate(7, 'IDR', Decimal(14080), ROW['rate_date'], 'typed', 1)
    sql, args = connection.execute.call_args.args
    assert 'ON CONFLICT (trip_id, paid_currency, currency) DO UPDATE' in str(sql)
    assert "WHERE EXCLUDED.source = 'typed'" in str(sql)
    assert args['setter'] == 1
    connection.commit.assert_called_once()


def test_migration_import_and_additive_table(monkeypatch):
    spec = importlib.util.spec_from_file_location('trip_fx_migration', Path('alembic/versions/0013_trip_fx_rates.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    fake = MagicMock()
    monkeypatch.setattr(module, 'op', fake)
    module.upgrade()
    assert module.down_revision == '0012_trip_sheet_id'
    assert fake.create_table.call_args.args[0] == 'trip_fx_rates'
    assert [call[0] for call in fake.mock_calls] == ['create_table']

"""Fictional bill layouts and stubbed Sheets traffic; no Google or database access."""
import asyncio
from contextlib import nullcontext
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.add_expense import format_cents
from app.services import trip_sheet as sheet
from app.services.trips import trip_sheet_target

NOW = datetime(2026, 11, 22, 21, 5, tzinfo=timezone(timedelta(hours=8)))
NAMES = {1: "Alice", 2: "Bob", 3: "Carol"}
SHEET_ID = "fictional_sheet_id_1234567890"


def bill(key="one", **changes):
    """Use precisely the split-entry query's shape."""
    result = dict(transaction_id=key, date="2026-11-22", description="dinner\n  with friends",
                  amount=300000, currency="SGD", paid_by=1, splits={1: 100000, 2: 100000, 3: 100000})
    return {**result, **changes}


def payback(cents, **changes):
    """An old rowless payback defaults to Bob paying Alice."""
    return dict(from_id=2, to_id=1, cents=cents, currency="SGD", **changes)


def test_layout_paid_partial_unpaid_and_currencies():
    """Gross shares omit the payer and preserve order, cents and currency boundaries."""
    entries = [bill(), bill("two", currency="AUD", date=date(2026, 11, 23), description="=1+1")]
    rows = sheet.sheet_rows("Beach", entries, [payback(100000), payback(12345, for_id=3)], {}, NAMES, NOW)
    assert all(len(row) == 10 for row in rows)
    assert rows[0][0] == "Beach: every split bill, one row each, before netting"
    assert rows[1][0] == ("Updated 22 Nov 2026 21:05 SGT. /trip_bals nets these into fewer payments, "
                          "so its lines differ.")
    assert rows[4] == ["2026-11-22", "dinner with friends", "SGD", 3000.0, "Alice",
                       "Bob 1,000.00 ✅\nCarol 1,000.00 (876.55 left)",
                       2000.0, 1123.45, 876.55, "⏳ Carol"]
    assert rows[5] == ["2026-11-23", "=1+1", "AUD", 3000.0, "Alice",
                       "Bob 1,000.00\nCarol 1,000.00", 2000.0, 0.0, 2000.0, "⏳ Bob, Carol"]
    assert rows[4][8] == float(format_cents(87655))


def test_old_payback_clears_oldest_first():
    """Rowless payments use open_bill_shares' allocation, never display-side netting."""
    rows = sheet.sheet_rows("Beach", [bill("old", splits={2: 100}), bill("new", splits={2: 200})],
                            [payback(150)], {}, NAMES, NOW)
    assert rows[4][5:] == ["Bob 1.00 ✅", 1.0, 1.0, 0.0, "✅ Paid"]
    assert rows[5][5:] == ["Bob 2.00 (1.50 left)", 2.0, 0.5, 1.5, "⏳ Bob"]


def test_no_payer_and_empty_trip():
    """Unknown payers are visibly excluded; an empty trip still has headers."""
    description = "raw words " * 400
    rows = sheet.sheet_rows("Beach", [bill(paid_by=None, description=description)], [], {}, NAMES, NOW)
    assert rows[4][1] == description.strip()
    assert rows[4][4:] == ["⚠️ no payer recorded", "Alice 1,000.00\nBob 1,000.00\nCarol 1,000.00",
                          "", "", "", "⚠️ Not counted in balances"]
    rows = sheet.sheet_rows("Beach", [], [], {}, NAMES, NOW)
    assert rows[3][0] == "Date"
    assert rows[4] == ["No split bills on this trip yet."] + [""] * 9
    assert all(len(row) == 10 for row in rows)


@pytest.fixture
def writing(monkeypatch):
    """Replace every read and token request before a writer can reach external I/O."""
    conn = MagicMock()
    conn.execute.return_value.all.return_value = list(NAMES.items())
    monkeypatch.setattr(sheet, "get_read_connection", lambda: nullcontext(conn))
    monkeypatch.setattr(sheet, "trip_sheet_target", MagicMock(return_value=("Beach", SHEET_ID)))
    monkeypatch.setattr(sheet, "trip_families", lambda *_: ())
    monkeypatch.setattr(sheet, "get_trip_split_entries", MagicMock(return_value=[bill(description="=1+1")]))
    monkeypatch.setattr(sheet, "get_trip_settlements", MagicMock(return_value=[]))
    monkeypatch.setattr(sheet, "get_sgt_now", lambda: NOW)
    monkeypatch.setattr(sheet, "_access_token", AsyncMock(return_value="fictional-token"))
    monkeypatch.setattr(sheet, "_trip_locks", {})
    monkeypatch.setattr(sheet, "_off_printed", False)
    return conn


def transport(monkeypatch, handler):
    """Use real httpx request construction with an entirely in-memory transport."""
    client = httpx.AsyncClient
    factory = MagicMock(side_effect=lambda **kw: client(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(sheet.httpx, "AsyncClient", factory)
    return factory


async def test_no_target_skips_token_and_http(writing, monkeypatch):
    """Unconfigured trips make no identity or network request."""
    sheet.trip_sheet_target.return_value = None
    client = transport(monkeypatch, lambda _: pytest.fail("unexpected HTTP"))
    await sheet.refresh_trip_sheet(7)
    sheet._access_token.assert_not_awaited()
    client.assert_not_called()
    writing.execute.assert_not_called()


@pytest.mark.parametrize("query", ["get_trip_split_entries", "get_trip_settlements"])
@pytest.mark.parametrize("raises", [False, True])
async def test_failed_read_never_writes(writing, monkeypatch, capsys, query, raises):
    """None and raised read failures must never publish an apparently settled trip."""
    monkeypatch.setattr(sheet, query, MagicMock(return_value=None, side_effect=RuntimeError("read failed") if raises else None))
    client = transport(monkeypatch, lambda _: pytest.fail("unexpected HTTP"))
    await sheet.refresh_trip_sheet(7)
    assert "Trip sheet not updated for trip 7:" in capsys.readouterr().out
    client.assert_not_called()


async def test_write_then_clear_raw_ranges(writing, monkeypatch):
    """The new contents land before stale rows are cleared, using RAW for formula text."""
    import json
    requests = []
    transport(monkeypatch, lambda request: requests.append(request) or httpx.Response(200, json={}))
    await sheet.refresh_trip_sheet(7)
    assert [r.method for r in requests] == ["PUT", "POST"]
    assert requests[0].url.path == f"/v4/spreadsheets/{SHEET_ID}/values/A1:J5"
    assert dict(requests[0].url.params) == {"valueInputOption": "RAW"}
    assert requests[1].url.path.endswith("/values/A6:J:clear")
    assert json.loads(requests[0].content)["values"][4][1] == "=1+1"
    assert requests[0].headers["Authorization"] == "Bearer fictional-token"


@pytest.mark.parametrize("failed_method", ["PUT", "POST"])
async def test_http_error_is_bounded_and_never_raised(writing, monkeypatch, capsys, failed_method):
    """A failed PUT never clears, and either failure only prints a short diagnostic."""
    requests = []
    def respond(request):
        """Fail the selected stage of the write."""
        requests.append(request)
        return httpx.Response(403 if request.method == failed_method else 200, text="x" * 500)
    transport(monkeypatch, respond)
    await sheet.refresh_trip_sheet(7)
    output = capsys.readouterr().out
    assert output == "Trip sheet not updated for trip 7: HTTP 403: " + "x" * 300 + "\n"
    assert len(requests) == (1 if failed_method == "PUT" else 2)


@pytest.fixture
def identity(monkeypatch):
    """Clear the module's cache and both identity selectors for each token test."""
    monkeypatch.delenv("K_SERVICE", raising=False)
    monkeypatch.delenv("SHEETS_IMPERSONATE_SA", raising=False)
    monkeypatch.setattr(sheet, "_token", None)
    monkeypatch.setattr(sheet, "_token_until", 0)


async def test_metadata_token_cached_and_expires(identity, monkeypatch):
    """Cloud Run's scoped token is reused only until its refresh margin."""
    monkeypatch.setenv("K_SERVICE", "fictional-service")
    clock = MagicMock(return_value=100)
    monkeypatch.setattr(sheet.time, "monotonic", clock)
    requests = []
    transport(monkeypatch, lambda request: requests.append(request) or
              httpx.Response(200, json={"access_token": "fictional-token", "expires_in": 3600}))
    assert await sheet._access_token() == "fictional-token"
    assert await sheet._access_token() == "fictional-token"
    assert len(requests) == 1
    assert str(requests[0].url).startswith(sheet._METADATA)
    assert requests[0].headers["Metadata-Flavor"] == "Google"
    assert requests[0].url.params["scopes"] == sheet._SCOPE
    clock.return_value = 3640
    await sheet._access_token()
    assert len(requests) == 2


async def test_local_impersonation_cached(identity, monkeypatch):
    """Local identity requires explicit impersonation and uses the final stdout line."""
    monkeypatch.setenv("SHEETS_IMPERSONATE_SA", "fictional@example.invalid")
    monkeypatch.setattr(sheet.shutil, "which", lambda _: "gcloud")
    run = MagicMock(return_value=MagicMock(returncode=0, stdout="notice\nfictional-token\n"))
    monkeypatch.setattr(sheet.subprocess, "run", run)
    assert await sheet._access_token() == await sheet._access_token() == "fictional-token"
    run.assert_called_once_with(["gcloud", "auth", "print-access-token",
                                 "--impersonate-service-account=fictional@example.invalid",
                                 f"--scopes={sheet._SCOPE}"], capture_output=True, text=True)


async def test_no_identity_prints_once(writing, monkeypatch, capsys):
    """An off writer explains itself once per process, regardless of trip count."""
    sheet._access_token.return_value = None
    client = transport(monkeypatch, lambda _: pytest.fail("unexpected HTTP"))
    await sheet.refresh_trip_sheet(7)
    await sheet.refresh_trip_sheet(8)
    assert capsys.readouterr().out == (
        "Trip sheet writes are off here (no Cloud Run identity, no SHEETS_IMPERSONATE_SA).\n")
    client.assert_not_called()


async def test_no_identity_returns_none(identity):
    """Without an explicit identity the token helper cannot invoke any provider."""
    assert await sheet._access_token() is None


async def test_trip_lock_covers_reads_through_clear(writing, monkeypatch):
    """A second refresh waits to read until the first has finished both requests."""
    started, release = asyncio.Event(), asyncio.Event()
    calls = []
    async def respond(request):
        """Hold the first PUT so the second refresh has time to queue."""
        calls.append(request.method)
        if len(calls) == 1:
            started.set()
            await release.wait()
        return httpx.Response(200)
    transport(monkeypatch, respond)
    first = asyncio.create_task(sheet.refresh_trip_sheet(7))
    await started.wait()
    second = asyncio.create_task(sheet.refresh_trip_sheet(7))
    await asyncio.sleep(0)
    assert sheet.get_trip_split_entries.call_count == 1
    release.set()
    await asyncio.gather(first, second)
    assert calls == ["PUT", "POST", "PUT", "POST"]
    assert sheet.get_trip_split_entries.call_count == 2


@pytest.mark.parametrize("row, expected", [(None, None), (("Beach", SHEET_ID), ("Beach", SHEET_ID))])
def test_target_query(row, expected):
    """Only configured destinations come back, with a bound trip id."""
    conn = MagicMock()
    conn.execute.return_value.first.return_value = row
    assert trip_sheet_target(conn, 7) == expected
    assert "sheet_id IS NOT NULL" in str(conn.execute.call_args.args[0])
    assert conn.execute.call_args.args[1] == {"trip_id": 7}

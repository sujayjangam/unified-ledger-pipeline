"""Write-only trip workbooks, using the same gross bill shares as the settle card."""

import asyncio
import os
import shutil
import subprocess
import time

import httpx
from sqlalchemy import text

from app.add_expense import format_cents, format_money
from app.database import get_read_connection
from app.services.balances import (
    balance_currencies, behalf_totals, open_bill_shares, trip_payments, unit_name,
)
from app.services.ledger_queries import get_trip_settlements, get_trip_split_entries
from app.services.trips import trip_families, trip_sheet_target
from app.services.utils import get_sgt_now

_trip_locks: dict[int, asyncio.Lock] = {}
_token: str | None = None
_token_until = 0.0
_off_printed = False
_SCOPE = "https://www.googleapis.com/auth/spreadsheets"
_METADATA = "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token"


def _number(cents: int) -> float:
    """Convert canonical decimal money to a Sheets numeric cell."""
    # The shortest float repr round-trips to the same two-decimal text; all money
    # calculations happened in integer cents before this JSON boundary.
    return float(format_cents(cents))


def _amount(cents: int, currency: str) -> str:
    """Reuse the project's grouped money text without repeating the currency."""
    return format_money(cents, currency).removeprefix(currency + " ")


def offset_open_shares(entries, opened) -> tuple[dict, dict]:
    """Return display-only open shares and cents cleared by each opposite pair.

    Never mutate the payback allocation: these offsets are not stored settlements.
    """
    reduced, offsets, directions = dict(opened), {}, {}
    for entry in entries:
        payer = entry['paid_by']
        if payer is None:
            continue
        for person in entry['splits']:
            key = (entry['transaction_id'], person)
            if person != payer and opened.get(key, 0) > 0:
                directions.setdefault((person, payer, entry['currency']), []).append(key)
    for (person, payer, currency), keys in directions.items():
        if person >= payer:
            continue  # Each unordered pair is cleared once, in both directions.
        reverse = directions.get((payer, person, currency), [])
        amount = min(sum(opened[k] for k in keys), sum(opened[k] for k in reverse))
        for shares in (keys, reverse):
            remaining = amount
            # Entries retain query order: oldest date first, ties in received order.
            for key in shares:
                cleared = min(reduced[key], remaining)
                if cleared:
                    reduced[key] -= cleared
                    offsets[key] = cleared
                    remaining -= cleared
                if not remaining:
                    break
    return reduced, offsets


def sheet_rows(trip_name, entries, settlements, families, names, now) -> list[list]:
    """Lay out bills in query order with display-only opposite-share offsets.

    Pure: the same bills and paybacks always give the same rows, so the tests check the
    layout with no network. open_bill_shares replays every payback the way /trip_bals does
    and returns what is still open on each person's share of each bill; a share missing
    from it is fully paid. Gross shares stay visible, but opposite open shares between
    two people in one currency count as cleared on this tab only.
    """
    entries = tuple(entries)
    opened = open_bill_shares(entries, families, settlements)
    opened, offsets = offset_open_shares(entries, opened)

    def name_of(person):
        # Every participant is read, so this is a safety net: one odd id must never stop
        # the whole sheet from being written.
        return names.get(person, f"Person {person}")
    rows = [
        [f"{trip_name}: every split bill, one row each, before netting"],
        [f"Updated {now.day} {now:%b %Y %H:%M} SGT. /trip_bals nets these into fewer payments, so its lines differ."],
        [],
        ["Date", "Bill", "Cur", "Bill total", "Owed to", "Owed by", "Share", "Paid back", "Still owed", "Status"],
    ]
    for entry in entries:
        payer, currency = entry["paid_by"], entry["currency"]
        owing, lines, total, left_total = [], [], 0, 0
        for person, cents in entry["splits"].items():
            if person == payer:
                continue  # A payer's own share is spending, never a debt to themselves.
            name = name_of(person)
            line = f"{name} {_amount(cents, currency)}"
            # With no payer the entry counts in no balance (unit_balances leaves it out), so
            # its shares are listed but never added up as owed.
            if payer is not None:
                left = opened.get((entry["transaction_id"], person), 0)
                total += cents
                left_total += left
                if not left:
                    line += " ✅"
                    if (entry['transaction_id'], person) in offsets:
                        line += " (offset)"
                else:
                    owing.append(name)
                    if left < cents:
                        line += f" ({_amount(left, currency)} left)"
            lines.append(line)
        rows.append([
            # The date is stored as text (YYYY-MM-DD), which also sorts correctly in the sheet.
            # The description is the raw message (ADR-0008), kept whole, on one line.
            str(entry["date"]), " ".join((entry.get("description") or "").split()), currency,
            _number(entry["amount"]),
            name_of(payer) if payer is not None else "⚠️ no payer recorded", "\n".join(lines),
            _number(total) if payer is not None else "",
            _number(total - left_total) if payer is not None else "",
            _number(left_total) if payer is not None else "",
            ("✅ Paid" if not left_total else "⏳ " + ", ".join(owing))
            if payer is not None else "⚠️ Not counted in balances",
        ])
    if not entries:
        rows.append(["No split bills on this trip yet."])
    # Every row is exactly 10 cells (A..J), so a shorter row this time overwrites the
    # longer one from the last write instead of leaving its old cells behind.
    return [row + [""] * (10 - len(row)) for row in rows]


def summary_rows(trip_name, entries, settlements, families, names, now) -> list[list]:
    """Lay out netted totals and informational help from the same snapshot."""
    entries, settlements = tuple(entries), tuple(settlements)
    payments = trip_payments(entries, families, settlements)
    currencies = balance_currencies(entries, settlements, payments)

    def name_of(person):
        return names.get(person, f"Person {person}")

    def for_name(unit):
        return name_of(unit[1]) if unit[0] == 'person' else unit_name(unit, names)

    rows = [
        [f"{trip_name} · summary"],
        [f"Updated {now.day} {now:%b %Y %H:%M} SGT. Totals match /trip_bals; the first tab lists bills before netting."],
        [],
    ]
    for currency in currencies:
        done = [s['cents'] for s in settlements if s['currency'] == currency]
        remaining = [p.cents for cur, p in payments if cur == currency]
        rows.extend([[currency], ['Paybacks done', len(done), _number(sum(done))],
                     ['Still to settle', len(remaining), _number(sum(remaining))], []])
        other, own = behalf_totals(entries, families, settlements, currency)
        sections = [
            ('Paid on behalf of another family', [(name_of(g), for_name(u), c) for (g, u), c in other.items()]),
            ('Paid within own family', [(name_of(g), name_of(p), c) for (g, p), c in own.items()]),
        ]
        for title, items in sections:
            rows.append([title])
            if items:
                rows.append(['Paid by', 'Paid for', 'Amount'])
                rows.extend([giver, beneficiary, _number(cents)] for giver, beneficiary, cents
                            in sorted(items, key=lambda item: (-item[2], item[0], item[1])))
            else:
                rows.append(['None yet.'])
            rows.append([])
    if not currencies:
        rows.append(['No split bills on this trip yet.'])
    # Pad blank rows too so shorter sections cannot leave old values beside them.
    return [row + [''] * (5 - len(row)) for row in rows]


async def _access_token() -> str | None:
    """Use the runtime identity, or explicit local impersonation; never log tokens."""
    global _token, _token_until
    cloud = os.environ.get("K_SERVICE")
    service_account = os.environ.get("SHEETS_IMPERSONATE_SA")
    if not cloud and not service_account:
        return None
    if _token and time.monotonic() < _token_until:
        return _token
    if cloud:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(_METADATA, params={"scopes": _SCOPE},
                                        headers={"Metadata-Flavor": "Google"})
            # Token endpoint bodies could contain credentials; don't print their content.
            if response.is_error:
                raise RuntimeError(f"Metadata token HTTP {response.status_code}")
            response.raise_for_status()
            data = response.json()
        token, lifetime = data["access_token"], float(data["expires_in"]) - 60
    else:
        executable = shutil.which("gcloud")
        if not executable:
            raise RuntimeError("gcloud not found for Sheets impersonation")
        result = await asyncio.to_thread(subprocess.run, [
            executable, "auth", "print-access-token",
            f"--impersonate-service-account={service_account}", f"--scopes={_SCOPE}",
        ], capture_output=True, text=True)
        if result.returncode or not result.stdout.strip():
            raise RuntimeError("gcloud Sheets impersonation failed")
        token, lifetime = result.stdout.strip().splitlines()[-1], 50 * 60
    if not isinstance(token, str) or not token:
        raise RuntimeError("Sheets identity returned no access token")
    _token, _token_until = token, time.monotonic() + lifetime
    return _token


async def refresh_trip_sheet(trip_id: int) -> None:
    """Refresh a configured sheet without ever turning a saved bill into a failure."""
    global _off_printed
    try:
        # Serialize from the reads onward, so a waiting save writes fresh data. Separate
        # Cloud Run instances can still race; the next save reads fresh and corrects it.
        async with _trip_locks.setdefault(trip_id, asyncio.Lock()):
            with get_read_connection() as conn:
                target = trip_sheet_target(conn, trip_id)
                if target is None:
                    return
                families = dict(trip_families(conn, trip_id))
                names = dict(conn.execute(text("SELECT id, display_name FROM participants")).all())
            entries = get_trip_split_entries(trip_id)
            settlements = get_trip_settlements(trip_id)
            if entries is None or settlements is None:
                raise RuntimeError("Could not read trip bills or paybacks")
            token = await _access_token()
            if token is None:
                if not _off_printed:
                    print("Trip sheet writes are off here (no Cloud Run identity, no SHEETS_IMPERSONATE_SA).")
                    _off_printed = True
                return
            trip_name, sheet_id = target
            now = get_sgt_now()
            rows = sheet_rows(trip_name, entries, settlements, families, names, now)
            url = f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values"
            async with httpx.AsyncClient(timeout=20, headers={"Authorization": f"Bearer {token}"}) as client:
                # RAW keeps user text beginning with '=' as text, never executable formulas.
                response = await client.put(f"{url}/A1:J{len(rows)}", params={"valueInputOption": "RAW"},
                                            json={"values": rows})
                response.raise_for_status()
                # Write first, then clear stale trailing rows: a failed write cannot blank the sheet.
                # No tab name selects the first tab, even when its owner renames it.
                response = await client.post(f"{url}/A{len(rows) + 1}:J:clear")
                response.raise_for_status()
                # Summary failures (including a missing tab) cannot block the bill write.
                rows = summary_rows(trip_name, entries, settlements, families, names, now)
                response = await client.put(f"{url}/'Summary'!A1:E{len(rows)}",
                                            params={"valueInputOption": "RAW"}, json={"values": rows})
                response.raise_for_status()
                response = await client.post(f"{url}/'Summary'!A{len(rows) + 1}:E:clear")
                response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        detail = " ".join(exc.response.text.split())[:300]
        print(f"Trip sheet not updated for trip {trip_id}: HTTP {exc.response.status_code}: {detail}")
    except Exception as exc:
        print(f"Trip sheet not updated for trip {trip_id}: {exc}")

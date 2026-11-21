"""Plain-text settle screens from an immutable ledger snapshot; no Telegram or I/O.

Every redraw starts at the original reading. Derived option lists are stored by the
bot only after a successful edit, so an index always means what the user saw.
"""

from app.add_expense import format_money
from app.services.settle import (
    SettleChoices, bill_share_options, card_people, match_bills, receiver_options, settle_up,
)


def settle_card_state(card: dict) -> dict:
    """Return a copy with recomputed result and callback-index lists."""
    changed = dict(card)
    reading, bills = card['reading'], card['bills']
    choices = card.get('choices', SettleChoices())
    result = settle_up(reading, bills, trip_id=card['trip_id'], names=card['names'],
                       choices=choices, clears_cents=card.get('clears_cents'),
                       scope_families=card.get('scope_families'))
    shares = bill_share_options(reading, bills, result, choices)
    changed.update(result=result, choices=choices,
                   share_options=shares,
                   receiver_options=result.receiver_options or receiver_options(reading, bills, result, choices),
                   giver_options=tuple(p for p in card.get('scope_people', {}).get(result.trip_id, card['people'])
                                       if p != result.receiver_id),
                   pick_options=tuple((i, bid) for i, part in enumerate(result.parts) for bid in part.matched),
                   oldest_options=tuple(i for i, part in enumerate(result.parts) if part.kind == 'close'))
    return changed


# Telegram refuses a message over 4096 characters, and a card that can't be drawn can't be
# confirmed or fixed. Bill lines stop well short of that; the rest are counted on one line
# (Change bills still lists every share).
_TEXT_BUDGET = 3500
BILLS_PAGE_SIZE = 8
_LINE_DESCRIPTION = 40


def _bounded(lines, bill_lines, tail):
    """Join the header, as many bill lines as fit, and the tail, under _TEXT_BUDGET."""
    kept = []
    used = sum(len(x) + 1 for x in lines + tail)
    for i, line in enumerate(bill_lines):
        more = len(bill_lines) - i - 1
        reserve = 40 if more else 0
        if used + len(line) + 1 + reserve > _TEXT_BUDGET:
            kept.append(f' • … and {len(bill_lines) - i} more shares (see Change bills)')
            break
        kept.append(line)
        used += len(line) + 1
    return lines + kept + tail


def _date(value):
    """"19 Oct" from a date; anything else (say, unparsed ISO text) is shown as it is."""
    return f"{value.day} {value:%b}" if hasattr(value, "day") else str(value)


def _everyday_currency(card: dict) -> str:
    """The currency of the everyday bill an everyday-only part names.

    That question has no selected trip bill yet, so the card shows the matching
    everyday bill's currency without making it a writable allocation. Only bills
    the people concerned owe count, and the one the words name comes first: the
    first everyday bill of all once showed a USD bill nobody named (test bot).
    """
    reading, result = card['reading'], card['result']
    people = card_people(reading, card['choices'])
    owed = [b for b in card['bills'] if b.trip_id is None
            and any(b.open.get(p, 0) > 0 for p in people)]
    words = next((part.words for part, found in zip(reading.parts, result.parts)
                  if found.kind == 'everyday_only'), ())
    named = match_bills(words, owed, reading.cents, people).ticked if words else ()
    return (next((b.currency for b in owed if b.transaction_id in named), None)
            or next((b.currency for b in owed), 'SGD'))


def build_settle_card(card: dict) -> tuple[str, list[list[tuple[str, str]]]]:
    """Render the original reading plus choices, returning text and label/data rows.

    This function never mutates its argument. The bot uses settle_card_state to retain
    the identical option lists after an edit succeeds. Names never enter callback data.
    """
    card = settle_card_state(card)
    result, reading, names = card['result'], card['reading'], card['names']
    choices = card['choices']
    bills = {b.transaction_id: b for b in card['bills']}
    currency = result.currency or reading.currency
    if currency is None:
        currency = _everyday_currency(card)
    paid = format_money(reading.cents, result.paid_currency or currency)
    giver = names[result.giver_id]
    scope = card['scope_labels'].get(result.trip_id, 'Everyday')
    cancel = [('❌ Cancel', 'su:cancel')]
    back = [('⬅️ Back to card', 'su:back')]
    view = card.get('view', 'card')
    if view == 'bills':
        options = card['share_options']
        # Ticked rows first. The order is frozen when the menu opens (bills_order), so a
        # tick never makes a row jump to another page under the person's thumb.
        order = [i for i in card.get('bills_order', ()) if i < len(options)]
        order += [i for i in sorted(range(len(options)), key=lambda i: not options[i][3]) if i not in order]
        # Telegram allows at most 100 buttons per keyboard; a short page also reads better
        # on a phone. Every share stays reachable through the page buttons.
        pages = max(1, -(-len(order) // BILLS_PAGE_SIZE))
        page = min(max(card.get('bills_page', 0), 0), pages - 1)
        rows = []
        for i in order[page * BILLS_PAGE_SIZE:(page + 1) * BILLS_PAGE_SIZE]:
            bid, p, cents, ticked = options[i]
            rows.append([(f"{'✅ ' if ticked else ''}{bills[bid].description[:24]} · {names[p]} "
                          f"{format_money(cents, bills[bid].currency)}", f'su:share:{i}')])
        nav = ([('⬅️ Earlier', f'su:page:{page - 1}')] if page > 0 else []) + (
            [('More ➡️', f'su:page:{page + 1}')] if page < pages - 1 else [])
        title = f"Which bills does {giver}'s {paid} pay?" + (f" ({page + 1}/{pages})" if pages > 1 else '')
        return title, rows + ([nav] if nav else []) + [back]
    if view == 'giver':
        buttons = [(names[p], f'su:person:{i}') for i, p in enumerate(card['giver_options'])]
        return f"Who handed over the {paid}?", [buttons[i:i + 2] for i in range(0, len(buttons), 2)] + [back]
    if view == 'receiver':
        rows = [[(f'{names[p]} {format_money(cents, currency)}', f'su:receiver:{i}')]
                for i, (p, cents) in enumerate(card['receiver_options'])]
        return f"Who got {giver}'s {paid}?", rows + [back]

    lines = [f'{"👪 Family settle" if result.family else "🤝 Settle up"} · {scope}']
    if result.trip_id is None and (reading.family if choices.family is None else choices.family):
        lines.append(f"⚠️ Families are only on a trip: only {giver}'s shares.")
    head, bill_lines = None, []  # set on the bill card, where the text can grow long
    if result.receiver_options:
        if result.question == 'owes_nothing':
            suffix = f' on the {scope}' if result.trip_id is not None else ''
            who = ' and '.join(names[p] for p in card_people(reading, choices))
            lines.append(f'{who} owes {names[result.receiver_id]} nothing{suffix}. Did you mean this?')
        elif result.question == 'paid_by_other':
            payer = names[result.question_payer]
            lines.append(f'The {result.question_said} was paid by {payer}, not {names[result.receiver_id]}. Did you mean {payer}?')
        else:
            who = ' and '.join(names[p] for p in card_people(reading, choices))
            owed = ' and '.join(names[p] for p, _ in result.receiver_options)
            lines.append(f'{giver} pays {paid}. {who} owes {owed}. Who got it?')
        rows = [[(f'{names[p]} {format_money(cents, currency)}', f'su:receiver:{i}')]
                for i, (p, cents) in enumerate(card['receiver_options'])] + [cancel]
    else:
        receiver = names.get(result.receiver_id, '')
        lines.append(f'{giver} pays {receiver} {paid}'.replace('  ', ' '))
        rows = []
        head, lines = lines, []
        allocated = {(r.transaction_id, r.for_id): r.cents for r in result.rows}
        close_ids = {bid for p in result.parts if p.kind == 'close' for bid in p.matched}
        for bid, person, owed, ticked in bill_share_options(reading, card['bills'], result, choices):
            if not ticked or result.needs_rate:
                continue
            bill = bills[bid]
            description = (bill.description if len(bill.description) <= _LINE_DESCRIPTION
                           else bill.description[:_LINE_DESCRIPTION - 1] + '…')
            title = description + (f' ({_date(bill.date)})' if bid in close_ids else '')
            cents = allocated.get((bid, person), 0)
            state = ('cleared' if cents == owed else
                     f'{format_money(cents, currency)} paid, {format_money(owed - cents, currency)} still owed')
            behalf = (f" (paid on {names[person]}'s behalf)"
                      if person != result.giver_id and person not in result.family_members else '')
            lines.append(f" • {title}: {names[person]}'s share {format_money(owed, currency)}, {state}{behalf}")
        bill_lines, lines = lines, []
        if result.clears_all:
            lines.append('Clears all in full.')
        lines.extend(p.warning for p in result.parts if p.warning)
        for i, (part_index, bid) in enumerate(card['pick_options']):
            bill = bills[bid]
            tick = '✅ ' if bid in result.parts[part_index].ticked else ''
            rows.append([(f'{tick}{bill.description[:24]} · {_date(bill.date)}', f'su:pick:{i}')])
        for i, _ in enumerate(card['oldest_options']):
            rows.append([('Pay oldest first', f'su:oldest:{i}')])
        blocked = any(p.kind == 'everyday_only' for p in result.parts)
        if blocked:
            rows = [[('Settle everyday bills instead', 'su:everyday')], [('Change bills', 'su:bills')] + cancel]
        elif result.needs_rate:
            paid_code, bill_code = result.needs_rate
            lines.append(f"⚠️ Couldn't get an {paid_code} to {bill_code} rate. Reply with how much {bill_code} this clears.")
            rows = [cancel]
        else:
            if result.overpaid_cents and result.can_confirm:
                lines.append(f'⚠️ {format_money(result.overpaid_cents, currency)} more than the shares named. '
                             f'{receiver} will owe it back to {giver}.')
            foreign = result.paid_currency != result.currency and result.currency is not None
            if foreign:
                cleared = format_money(card['clears_cents'], currency)
                conversion = (f'You set {paid} = {cleared}.' if card.get('fx_source') == 'typed' else
                              f'{paid} ≈ {cleared} at the rate of {_date(card["fx_rate_date"])}.')
                lines.append(f'⚠️ Paid in {result.paid_currency}, the bill is in {currency}. '
                             f'{conversion} Check against the statement.')
            confirm = '⚠️ Confirm overpayment' if result.overpaid_cents else '✅ Confirm'
            if foreign:
                confirm = ('⚠️ Confirm overpayment' if result.overpaid_cents else '⚠️ Confirm') + f' in {result.paid_currency}'
            rows.append(([(confirm, 'su:confirm')] if result.can_confirm else []) + cancel)
            rows.append([('Change bills', 'su:bills'), ('Change who paid', 'su:giver'), ('Change receiver', 'su:receivers')])
            if result.trip_id is not None and len(result.family_members) > 1:
                label = f"👤 Only {giver}'s shares" if result.family else '👪 Whole family'
                rows.append([(label, 'su:family')])
            if foreign:
                rows.append([('Change amount cleared', 'su:amount')])
    if head is not None:
        lines = _bounded(head, bill_lines, lines)
    if card.get('outcome'):
        lines.append('✅ Payback saved.' if card['outcome'] == 'saved' else '❌ Cancelled.')
        rows = []
    if card.get('saving'):
        rows = [[('⏳ Saving...', 'su:saving')]]
    return '\n'.join(lines), rows

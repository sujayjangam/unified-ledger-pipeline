"""Fictional, in-memory examples from ADR-0052 and its implementation plan."""

from dataclasses import replace
from datetime import date

import pytest

from app.services.participants import Participant
from app.services.settle import (
    Bill, SettleChoices, SettlePart, SettleReading, SettleRefused, SettleRow,
    is_settle, match_bills, normalise, read_settle, settle_up,
)

NAMES = dict(enumerate(("Alice", "Bob", "Carol", "Dave", "Erin", "Frank"), 1))
PEOPLE = tuple(Participant(i, name, None, None) for i, name in NAMES.items())
FAMILIES = {1: "Tan", 2: "Tan", 3: "Lee", 4: "Lee", 5: "Ng", 6: "Ng"}
NEEDS = 'Settle needs who paid and how much, e.g. "Settle Carol paid 381".'


def read(text):
    return read_settle(text, PEOPLE, PEOPLE[0], FAMILIES)


def bill(id="saree", description="saree pre-pleating 381", *, trip=10,
         payer=1, currency="SGD", amount=38100, shares=None):
    return Bill(id, trip, date(2026, 11, 12), description, amount, currency,
                payer, {3: 12000} if shares is None else shares)


def settle(text, bills, *, trip=10, **kwargs):
    return settle_up(read(text), bills, trip_id=trip, names=NAMES, **kwargs)


@pytest.mark.parametrize("text, expected", [
    ("Settle, Carol paid 381.", True), (" SETTLE: Carol paid 381", True),
    ("settle!", True), ("settle", True), ("settle—Carol paid 381", True),
    ("Settlement 50", False), ("settled dinner 50", False),
    ("please settle Carol paid 381", False), ("", False), ("settle_up", False),
])
def test_routing(text, expected):
    assert is_settle(text) is expected


@pytest.mark.parametrize("text, giver, receiver, cents, parts", [
    ("Settle, Carol paid 381.", 3, None, 38100, ()),
    ("Settle Carol paid 381 for saree pleating and for Erin airbnb", 3, None, 38100,
     (SettlePart((), ("saree", "pleating"), "saree pleating"),
      SettlePart((5,), ("airbnb",), "Erin airbnb"))),
    ("Settle Dave paid 1942", 4, None, 194200, ()),
    ("Settle Dave paid 1,942", 4, None, 194200, ()),
    ("Settle Carol paid Alice 381", 3, 1, 38100, ()),
    ("Settle Carol paid to Alice 381", 3, 1, 38100, ()),
    ("Settle Carol paid back to Alice 381", 3, 1, 38100, ()),
    ("Settle Carol paid Alice back 381.50", 3, 1, 38150, ()),
    ("Settle Carol sent 381 to me", 3, 1, 38100, ()),
    ("Settle I gave Carol 50", 1, 3, 5000, ()),
    ("Settle me transferred 50 to Carol", 1, 3, 5000, ()),
    ("Settle Carol pays me 50", 3, 1, 5000, ()),
    ("Settle Carol paid 261 for airbnb booking", 3, None, 26100,
     (SettlePart((), ("airbnb", "booking"), "airbnb booking"),)),
    ("Settle Bob paid 330 for bed", 2, None, 33000,
     (SettlePart((), ("bed",), "bed"),)),
    ("Settle Dave paid 1000 for airbnb for the Ngs", 4, None, 100000,
     (SettlePart((5, 6), ("airbnb",), "airbnb"),)),
    ("Settle Carol paid 381 for airbnb for the Ngs and Carol", 3, None, 38100,
     (SettlePart((5, 6, 3), ("airbnb",), "airbnb"),)),
    ("Settle Carol paid 381 for the Ngs", 3, None, 38100,
     (SettlePart((5, 6), (), "the Ngs"),)),
])
def test_documented_readings(text, giver, receiver, cents, parts):
    assert read(text) == SettleReading(giver, receiver, cents, None, parts)


@pytest.mark.parametrize("amount, currency", [
    ("100sgd", "SGD"), ("100 SGD", "SGD"), ("SGD 100", "SGD"),
    ("SGD100", "SGD"), ("$100", None), ("S$100", None),
    ("100 AUD", "AUD"), ("INR 100", "INR"),
])
def test_currency_forms(amount, currency):
    result = read(f"Settle Erin paid {amount} for diwali deco")
    assert (result.cents, result.currency, result.parts[0].words) == (10000, currency, ("diwali", "deco"))


@pytest.mark.parametrize("phrase, people", [
    ("Erin's share of the airbnb", (5,)), ("Erin’s portion of airbnb", (5,)),
    ("Erin and Frank shares of our airbnb", (5, 6)),
    ("Erin, Frank & Carol part of airbnb", (5, 6, 3)),
    ("the Ngs airbnb", (5, 6)), ("Ng airbnb", (5, 6)),
    ("the Lees airbnb", (3, 4)), ("Lee family airbnb", (3, 4)),
    ("the Tans airbnb", (1, 2)), ("I airbnb", (1,)), ("me airbnb", (1,)),
])
def test_names_families_and_fillers(phrase, people):
    part = read(f"Settle Carol paid 100 for {phrase}").parts[0]
    assert part.people == people
    assert part.words == ("airbnb",)


def test_longest_name_and_person_before_family():
    candidates = (replace(PEOPLE[4], display_name="Erin Ng"), PEOPLE[5])
    part = read_settle("Settle Erin paid 100 for Erin Ng airbnb", candidates,
                       PEOPLE[0], FAMILIES).parts[0]
    assert part.people == (5,)
    assert part.words == ("airbnb",)


@pytest.mark.parametrize("text, message", [
    ("Settle paid 381", NEEDS), ("Settle Carol 381", NEEDS),
    ("Settle Carol paid", NEEDS), ("Settle Carol paid fifty", NEEDS),
    ("Settle Carol paid 0", NEEDS), ("Settle Carol paid 100 XYZ", NEEDS),
    ("Settle Carol paid 1,94", NEEDS), ("Settle Carol paid 100 for", NEEDS),
    ("Carol paid 381", NEEDS), ("Settle Carol paid 100 SGD AUD", NEEDS),
    ("Settle Zed paid 381", 'Couldn\'t match "Zed" as who paid.'),
    ("Settle Carol paid Zed 381", 'Couldn\'t match "Zed" as who got it.'),
    ("Settle Carol paid 381 to Zed", 'Couldn\'t match "Zed" as who got it.'),
    ("Settle Carol paid Carol 381", "Who paid and who got it must be two different people."),
])
def test_read_refusals(text, message):
    with pytest.raises(SettleRefused) as error:
        read(text)
    assert str(error.value) == message


# Each literal description and phrase in the ADR matching table is retained here.
@pytest.mark.parametrize("says, descriptions, amount, share, kind, ticks", [
    ("for the airbnb", ["Airbnb 2300 on my Visa, 5 nights"], 100, 200, "full", ("0",)),
    ("for saree pleating", ["saree pre-pleating 381"], 100, 200, "full", ("0",)),
    ("for beds", ["bed frame 330"], 100, 200, "full", ("0",)),
    ("for car", ["car rental 300", "Grab car 20"], 100, 200, "full", ("0", "1")),
    ("paid 120 for saree draping", ["saree pre-pleating 381"], 12000, 12000, "close", ("0",)),
    ("for car", ["carpark 5"], 100, 100, "none", ()),
    ("for air bnb", ["Airbnb 2300"], 100, 100, "none", ()),
    ("for hotel", ["Airbnb 2300"], 100, 100, "none", ()),
    ("for dinner yesterday", ["Dinner 480 cash"], 100, 200, "close", ("0",)),
    ("for dinner yesterday", ["Dinner 480 cash"], 48000, 200, "close", ("0",)),
    ("for dinner yesterday", ["Dinner 480 cash"], 200, 200, "close", ("0",)),
    ("for airbnd", ["Airbnb 2300"], 100, 100, "none", ()),
])
def test_adr_matching_table(says, descriptions, amount, share, kind, ticks):
    bills = [bill(str(i), d, amount=48000 if d == "Dinner 480 cash" else 38100,
                  shares={3: share}) for i, d in enumerate(descriptions)]
    words = normalise(says.split("for ", 1)[1])
    result = match_bills(words, bills, amount, (3,))
    assert (result.kind, result.ticked) == (kind, ticks)


def test_normalise_is_shared_and_whole_word_only():
    assert normalise("THE beds, a pre-pleating; carpark / his SHARES of our portion") == (
        "bed", "pre", "pleating", "carpark")
    assert match_bills((), [bill()], 12000, (3,)).kind == "none"
    assert match_bills(("saree", "draping"), [bill(shares={5: 12000})], 12000, (3,)).kind == "none"


@pytest.mark.parametrize("airbnb_share, cleared", [(26100, True), (28000, False)])
def test_all_cleared_and_short_screens(airbnb_share, cleared):
    bills = [bill(), bill("airbnb", "Airbnb 2300", shares={5: airbnb_share})]
    result = settle("Settle Carol paid 381 for saree pleating and for Erin airbnb", bills)
    assert result.rows == (SettleRow("saree", 3, 12000), SettleRow("airbnb", 5, 26100))
    assert result.clears_all is cleared
    assert result.can_confirm and result.overpaid_cents == 0
    assert (result.receiver_id, result.trip_id, result.currency, result.paid_currency) == (1, 10, "SGD", "SGD")


def test_close_pick_and_oldest_first_screens():
    bills = [bill("dinner", "Dinner", shares={5: 10000}),
             bill("first", "Airbnb 2300", shares={5: 26100}),
             bill("second", "Airbnb 900", shares={5: 26100})]
    text = "Settle Carol paid 261 for Erin airbnb booking"
    result = settle(text, bills)
    assert result.rows == (SettleRow("first", 5, 26100),)
    assert result.parts[0].matched == ("first", "second")
    assert result.parts[0].warning == "❓ Did you mean this bill? 2 bills could fit."
    picked = settle(text, bills, choices=SettleChoices(picks={0: "second"}))
    assert picked.rows == (SettleRow("second", 5, 26100),)
    assert settle(text, bills, choices=SettleChoices(picks={0: "stale"})).rows == result.rows
    oldest = settle(text, bills, choices=SettleChoices(oldest_first=frozenset({0})))
    assert oldest.rows == (SettleRow("dinner", 5, 10000), SettleRow("first", 5, 16100))
    assert oldest.parts[0].kind == "none"


def test_everyday_only_switch_and_trip_off_screens():
    bills = [bill("trip", "Dinner", shares={2: 40000}),
             bill("bed", "Bed frame 330", trip=None, shares={2: 33000})]
    text = "Settle Bob paid 330 for bed"
    result = settle(text, bills)
    assert result.rows == () and not result.can_confirm
    assert result.parts[0].kind == "everyday_only"
    assert result.parts[0].warning == '❓ "bed" matched an everyday bill, not a trip bill.'
    for kwargs in ({"choices": SettleChoices(everyday=True)}, {"trip": None}):
        result = settle(text, bills, **kwargs)
        assert result.rows == (SettleRow("bed", 2, 33000),)
        assert result.trip_id is None and result.clears_all and result.can_confirm


def test_no_match_warning_and_multiple_full_matches():
    bills = [bill("first", "car rental 300"), bill("second", "Grab car 20")]
    result = settle("Settle Carol paid 240 for car", bills)
    assert result.parts[0].ticked == ("first", "second")
    assert result.parts[0].warning == "⚠️ 2 bills matched"
    result = settle("Settle Carol paid 120 for pleating", bills)
    assert result.parts[0].warning == '⚠️ No bill matched "pleating". Used Carol\'s oldest bills. Check.'
    assert not result.clears_all


def test_receiver_options_then_choice_screen():
    bills = [bill("alice", shares={4: 150000}), bill("bob", payer=2, shares={4: 70000})]
    result = settle("Settle Dave paid 1942", bills)
    assert result.receiver_options == ((1, 150000), (2, 70000))
    assert result.rows == () and not result.can_confirm
    result = settle("Settle Dave paid 1942", bills, choices=SettleChoices(receiver_id=1))
    assert result.rows == (SettleRow("alice", 4, 150000),)
    assert result.overpaid_cents == 44200 and result.can_confirm
    tied = settle("Settle Dave paid 1942", [replace(b, open={4: 10000}) for b in reversed(bills)])
    assert tied.receiver_options == ((1, 10000), (2, 10000))


def test_overpaid_screen():
    result = settle("Settle Carol paid 160 for saree pleating", [bill()])
    assert result.overpaid_cents == 4000 and result.can_confirm and not result.clears_all


def test_named_receiver_narrows_and_no_debt_refusals():
    bills = [bill("alice"), bill("bob", payer=2)]
    result = settle("Settle Carol paid Alice 120 for saree pleating", bills)
    assert result.rows == (SettleRow("alice", 3, 12000),)
    question = settle("Settle Carol paid Alice 120", [bill(payer=2)])
    assert question.question == 'owes_nothing'
    assert question.receiver_options == ((2, 12000),)
    assert not question.rows and not question.can_confirm
    with pytest.raises(SettleRefused, match="No match\\."):
        settle("Settle Carol paid Alice 120 for Erin", [bill()])


@pytest.mark.parametrize("text, bills, trip, message", [
    ("Settle Carol paid 120 for car", [bill("a", "car", payer=1), bill("b", "car", payer=2)], 10,
     "These bills were paid by Alice and Bob. Send one settle message for each."),
    ("Settle Carol paid 120 for car", [bill("a", "car"), bill("b", "car", trip=None)], None,
     "These bills are on a trip and outside it. Send one settle message for each."),
    ("Settle Carol paid 120 for car", [bill("a", "car"), bill("b", "car", trip=20)], None,
     "These bills are on two trips. Send one settle message for each."),
    ("Settle Carol paid 120", [bill("a", currency="SGD"), bill("b", currency="AUD")], 10,
     'Carol owes Alice in AUD and SGD. Say which, e.g. "Settle Carol paid 50 AUD".'),
])
def test_allocation_refusals(text, bills, trip, message):
    with pytest.raises(SettleRefused) as error:
        settle(text, bills, trip=trip)
    assert str(error.value) == message


def test_currency_selection_and_rate_question():
    bills = [bill("aud", currency="AUD"), bill("sgd")]
    result = settle("Settle Carol paid 120 AUD", bills)
    assert result.rows == (SettleRow("aud", 3, 12000),)
    bills = [bill("deco", "diwali deco", currency="INR", shares={5: 600000})]
    result = settle("Settle Erin paid 100 SGD for diwali deco", bills)
    assert result.needs_rate == ("SGD", "INR") and result.rows == ()
    assert not result.can_confirm
    result = settle("Settle Erin paid 100 SGD for diwali deco", bills, clears_cents=620000)
    assert (result.currency, result.paid_currency) == ("INR", "SGD")
    assert result.rows == (SettleRow("deco", 5, 600000),)
    assert result.overpaid_cents == 20000 and result.can_confirm


def test_scope_people_and_sender_are_independent():
    bills = [bill("airbnb", "Airbnb", payer=3, shares={5: 60000, 6: 40000}),
             bill("everyday", "dinner", trip=None, shares={4: 10000})]
    result = settle("Settle Dave paid 1000 for airbnb for the Ngs", bills, trip=None)
    assert result.receiver_id == 3 and result.trip_id == 10
    assert result.rows == (SettleRow("airbnb", 5, 60000), SettleRow("airbnb", 6, 40000))
    for text in ("Settle Dave paid 100", "Settle Dave paid 100 for unknown"):
        result = settle(text, bills, trip=None)
        assert result.rows == (SettleRow("everyday", 4, 10000),)
        assert result.trip_id is None


def test_later_close_match_uses_remaining_money():
    bills = [bill(), bill("airbnb", "Airbnb", shares={3: 26100})]
    result = settle("Settle Carol paid 381 for saree pleating and for airbnb booking", bills)
    assert tuple(p.kind for p in result.parts) == ("full", "close")
    assert result.clears_all


@pytest.mark.parametrize("amount", [1, 11999, 12000, 12001, 24000, 50000])
def test_conservation_overlap_order_and_input_unchanged(amount):
    bills = [bill("first", "car", shares={5: 12000, 6: 12000}),
             bill("second", "car", shares={5: 5000, 6: 5000})]
    original = [dict(b.open) for b in bills]
    reading = SettleReading(3, None, amount, None, (
        SettlePart((6, 5), ("car",), "car"), SettlePart((5, 6), ("car",), "car")))
    result = settle_up(reading, bills, trip_id=10, names=NAMES)
    assert sum(row.cents for row in result.rows) + result.overpaid_cents == amount
    assert len({(r.transaction_id, r.for_id) for r in result.rows}) == len(result.rows)
    for row in result.rows:
        original_bill = next(b for b in bills if b.transaction_id == row.transaction_id)
        assert 0 < row.cents <= original_bill.open[row.for_id]
    assert result.rows[0].for_id == 6
    assert [dict(b.open) for b in bills] == original


def test_repeated_share_is_merged_not_paid_twice():
    result = settle("Settle Carol paid 200 for saree and for saree", [bill()])
    assert result.rows == (SettleRow("saree", 3, 12000),)
    assert result.overpaid_cents == 8000


def test_owing_nobody_is_refused_not_a_dead_card():
    """No receiver, no rows and no Confirm would be a dead end: say why instead."""
    with pytest.raises(SettleRefused, match=r"No match\."):
        settle("Settle Carol paid 50", [])


def test_names_then_bill_words_pair_up():
    """"for Erin for the airbnb" is Erin's airbnb share, like "for airbnb for Erin"."""
    expected = (SettlePart((5,), ("airbnb",), "the airbnb"),)
    assert read("Settle Carol paid 50 for Erin for the airbnb").parts == expected
    assert read("Settle Carol paid 50 for Erin and for the airbnb").parts != expected


def test_possessive_words_still_match():
    """A possessive leaves no empty word behind, so "mum's cake" can fully match."""
    assert normalise("mum's cake") == ("mum", "cake")
    assert match_bills(normalise("mum's cake"), [bill(description="Mum's cake 40")], 1, (3,)).kind == "full"


@pytest.mark.parametrize("suffix", ["Erin", "Erin unknown"])
def test_later_names_or_fallback_part_cannot_hide_another_currency(suffix):
    bills = [bill(), bill("dinner", "Dinner", currency="AUD", shares={5: 20000})]
    with pytest.raises(SettleRefused, match="Carol owes Alice in AUD and SGD"):
        settle(f"Settle Carol paid 120 for saree and for {suffix}", bills)


def test_unselected_currency_does_not_block_a_named_bill():
    bills = [bill(), bill("dinner", "Dinner", currency="AUD")]
    result = settle("Settle Carol paid 120 for saree", bills)
    assert result.currency == "SGD" and result.clears_all


def test_inferred_receiver_cannot_be_the_giver():
    with pytest.raises(SettleRefused, match="two different people"):
        settle("Settle Alice paid 120 for Carol saree", [bill()])


def test_trip_mode_does_not_use_other_trip_or_everyday_debts():
    bills = [bill("other", trip=20), bill("everyday", trip=None), bill("active")]
    assert settle("Settle Carol paid 120", bills).rows == (SettleRow("active", 3, 12000),)


def test_everyday_only_blocks_even_when_other_parts_allocate():
    bills = [bill(), bill("bed", "bed", trip=None)]
    result = settle("Settle Carol paid 240 for saree and for bed", bills)
    assert result.rows == (SettleRow("saree", 3, 12000),)
    assert not result.can_confirm and not result.clears_all
    assert result.overpaid_cents == 12000


def test_full_match_beats_close_and_oldest_override():
    bills = [bill("close", "saree", shares={3: 12000}), bill("full", "saree pleating")]
    result = settle("Settle Carol paid 120 for saree pleating", bills,
                    choices=SettleChoices(oldest_first=frozenset({0})))
    assert result.parts[0].kind == "full"
    assert result.rows == (SettleRow("full", 3, 12000),)


def test_named_bill_in_another_currency_asks_for_a_rate():
    """An unrelated debt in the paid currency never takes a payback meant for a named bill."""
    bills = [bill("deco", "Diwali deco", currency="INR", shares={5: 726000}),
             bill("dinner", "Dinner", currency="SGD", shares={5: 5000})]
    result = settle("Settle Erin paid 100 SGD for diwali deco", bills)
    assert result.needs_rate == ("SGD", "INR") and result.rows == ()
    result = settle("Settle Erin paid 100 SGD for diwali deco", bills, clears_cents=600000)
    assert result.rows == (SettleRow("deco", 5, 600000),)


def test_everyday_bill_still_blocks_when_the_receiver_was_only_guessed():
    """Bob owes Alice for a trip dinner and Carol for an everyday bed: "for bed" is not Alice's dinner."""
    bills = [bill("dinner", "Dinner", payer=1, shares={2: 33000}),
             bill("bed", "Bed", trip=None, payer=3, shares={2: 33000})]
    result = settle("Settle Bob paid 330 for bed", bills)
    assert result.parts[0].kind == "everyday_only"
    assert not result.can_confirm and result.rows == ()


def test_trip_debt_in_the_paid_currency_never_hides_an_everyday_bill():
    """Trip mode off, no bill named: scope is everyday before the paid currency filters."""
    bills = [bill("rent", "Rent", trip=None, currency="INR", shares={3: 500000}),
             bill("trip", "Dinner", trip=10, currency="SGD")]
    result = settle("Settle Carol paid 100 SGD", bills, trip=None)
    assert result.trip_id is None and result.needs_rate == ("SGD", "INR")


def test_foreign_payback_reads_the_same_before_and_after_the_rate():
    """Shared words suggest the same INR bill before and after conversion."""
    bills = [bill("old", "Dinner", currency="INR", shares={3: 10000}),
             bill("airbnb", "Airbnb", currency="INR", shares={3: 10000})]
    first = settle("Settle Carol paid 100 SGD for airbnb booking", bills)
    assert first.needs_rate == ("SGD", "INR") and first.parts[0].kind == "close"
    second = settle("Settle Carol paid 100 SGD for airbnb booking", bills, clears_cents=600000)
    assert second.parts[0].kind == first.parts[0].kind
    assert second.parts[0].ticked == first.parts[0].ticked


def test_two_named_bills_in_different_currencies_are_refused_not_dropped():
    bills = [bill(), bill("airbnb", "Airbnb", currency="AUD", shares={5: 12000})]
    with pytest.raises(SettleRefused, match="in AUD and SGD"):
        settle("Settle Carol paid 240 SGD for saree and for Erin airbnb", bills)


def test_everyday_only_wins_over_trip_debts_in_two_currencies():
    bills = [bill("sgd", "Dinner", currency="SGD"), bill("aud", "Taxi", currency="AUD"),
             bill("bed", "Bed", trip=None)]
    result = settle("Settle Carol paid 120 for bed", bills)
    assert result.parts[0].kind == "everyday_only" and not result.can_confirm


def test_paid_by_other_uses_the_named_bills_currency():
    """Found on the test bot: Carol owes Alice in SGD and INR, and the menu picks Bob.

    The saree bill is Alice's, so the card asks "Did you mean Alice?". It must ask
    in the saree's currency (SGD), not refuse because Carol's other Alice debt is INR.
    """
    bills = [bill(), bill("deco", "diwali deco", currency="INR", amount=726000),
             bill("car", "car rental", payer=2)]
    result = settle("Settle Carol paid 90 for saree pleating", bills,
                    choices=SettleChoices(receiver_id=2))
    assert result.question == 'paid_by_other' and result.question_payer == 1
    assert result.currency == 'SGD'
    assert result.receiver_options == ((1, 12000),)
    assert not result.rows and not result.can_confirm

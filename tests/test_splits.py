"""Whole-cent arithmetic and name resolution, with fictional people only."""

import pytest

from app.services.participants import Participant
from app.services.splits import resolve_split, share_out, split_shares


@pytest.mark.parametrize("amount,weights,expected", [
    (555, {1: 1, 2: 1}, {1: 278, 2: 277}),
    (100, {3: 1, 1: 1, 2: 1}, {1: 34, 2: 33, 3: 33}),
    (230000, {1: 5, 2: 5, 3: 4, 4: 4}, {1: 63889, 2: 63889, 3: 51111, 4: 51111}),
    (2, {9: 1, 2: 1, 5: 1}, {2: 1, 5: 1, 9: 0}),
    (7, {1: 1, 2: 2}, {1: 2, 2: 5}),
])
def test_share_out(amount, weights, expected):
    """Remainders decide spare cents, with participant ids breaking ties."""
    assert share_out(amount, weights) == expected
    assert list(share_out(amount, weights)) == sorted(weights)
    assert share_out(amount, dict(reversed(list(weights.items())))) == expected


def test_shares_always_add_up():
    """Small amounts and large groups may give zero shares but never lose cents."""
    for amount in range(1, 300):
        for size in range(1, 20):
            shares = share_out(amount, {pid: pid + 1 for pid in range(size)})
            assert sum(shares.values()) == amount
            assert min(shares.values()) >= 0


@pytest.mark.parametrize("amount,weights", [(0, {1: 1}), (-1, {1: 1}), (1, {}), (1, {1: 0}), (1, {1: -1})])
def test_invalid_share_inputs(amount, weights):
    """An empty group, nonpositive amount or nonpositive weight has no split."""
    with pytest.raises(ValueError):
        share_out(amount, weights)


def _resolve(directory, reference, trip_on=False, families=None):
    """Resolve a fictional message for Alice with optional trip families."""
    return resolve_split(reference, directory.participants, directory.participant(1), families or {}, trip_on)


@pytest.mark.parametrize("reference", [None, {}, {"kind": "none"}, {"kind": "none", "uneven": True}, {"people": []}])
def test_old_or_no_split(directory, reference):
    """Old pending shapes and explicit none remain personal entries."""
    result = _resolve(directory, reference)
    assert result.people == result.warnings == ()


@pytest.mark.parametrize("include_sender,ids", [(True, [1, 3]), (False, [3])])
def test_with_and_between(directory, include_sender, ids):
    """With includes Alice; between names only the explicit people."""
    result = _resolve(directory, {"kind": "named", "include_sender": include_sender,
                                  "people": [{"name": "Carol", "is_family": False}]})
    assert [p.id for p in result.people] == ids
    assert not result.warnings


@pytest.mark.parametrize("trip_on,ids", [(True, [2, 3]), (False, [3])])
def test_family_expansion(directory, trip_on, ids):
    """A family flag expands only when trip labels are available."""
    result = _resolve(directory, {"kind": "named", "people": [{"name": "Carol's family", "is_family": True}]},
                      trip_on, {2: "Carol family", 3: "Carol family"} if trip_on else {})
    assert [p.id for p in result.people] == ids


def test_everyone_except_one(directory):
    """A plain except entry removes that one person, even from inside a family."""
    result = _resolve(directory, {"kind": "everyone", "except_people": [{"name": "Carol", "is_family": False}]},
                      True, {2: "Carol family", 3: "Carol family"})
    assert [p.id for p in result.people] == [1, 2]


def test_everyone_except_a_family(directory):
    """"Everyone except Carol's family" leaves out everyone with Carol's family label."""
    result = _resolve(directory, {"kind": "everyone", "except_people": [{"name": "Carol's family", "is_family": True}]},
                      True, {2: "Carol family", 3: "Carol family"})
    assert [p.id for p in result.people] == [1]


@pytest.mark.parametrize("name", ["me", "I", "MYSELF"])
def test_sender_aliases_and_deduplication(directory, name):
    """Sender aliases work in both lists and cannot create duplicate shares."""
    result = _resolve(directory, {"kind": "named", "include_sender": True,
                                  "people": [{"name": name}, {"name": "Carol"}, {"name": "Carol"}],
                                  "except_people": [name]})
    assert [p.id for p in result.people] == [3]


def test_unknown_name_keeps_known_people(directory):
    """An unmatched inclusion or exclusion warns without discarding Carol's share."""
    result = _resolve(directory, {"kind": "named", "people": [{"name": "Erin"}, {"name": "Carol"}],
                                  "except_people": ["Dave"]})
    assert [p.id for p in result.people] == [3]
    assert result.warnings == ("⚠️ Couldn't match 'Erin'", "⚠️ Couldn't match 'Dave'")


@pytest.mark.parametrize("reference,trip_on,warning", [
    ({"kind": "everyone"}, False, '⚠️ "Everyone" needs a trip switched on. No split recorded.'),
    ({"kind": "named", "uneven": True}, True, "⚠️ This asks for uneven shares, which the bot can't do yet. No split recorded."),
    ({"kind": "named"}, False, "⚠️ Couldn't tell who shares this. No split recorded."),
    ({"kind": "named", "include_sender": True, "except_people": ["me"]}, True, "⚠️ Couldn't tell who shares this. No split recorded."),
])
def test_refusals(directory, reference, trip_on, warning):
    """No guessed group is returned when the requested split cannot be resolved."""
    result = _resolve(directory, reference, trip_on)
    assert not result.people
    assert result.warnings == (warning,)


def test_ambiguous_first_name(directory):
    """A shared first name cannot select either candidate."""
    people = (Participant(4, "Erin Lee", None, None), Participant(5, "Erin Kim", None, None))
    result = resolve_split({"kind": "named", "people": [{"name": "Erin"}]}, people, directory.participant(1), {}, False)
    assert not result.people
    assert result.warnings == ("⚠️ Couldn't match 'Erin'",)


def test_pending_rows(directory):
    """Rows are stable by id and carry the exact cents the save will receive."""
    assert split_shares(555, reversed(directory.participants[:2])) == (
        {"participant_id": 1, "name": "Alice", "share_cents": 278},
        {"participant_id": 2, "name": "Bob", "share_cents": 277},
    )


# --- What Python reads from the message text itself (ADR-0039, like "last week" in ADR-0031) ---

@pytest.mark.parametrize("message,expected", [
    ("dinner 80 split with Carol", True),
    ("taxi 30 shared with Dave", True),
    ("Petrol 90, split between Alice's and Bob's families", True),
    ("go dutch 40", True),
    ("dinner with Carol 70", False),
    ("bought Carol a coffee 6", False),
    ("splitter cable 12", False),
])
def test_asks_for_split(message, expected):
    """Only a whole split word counts; "with" alone and "splitter" don't."""
    from app.services.splits import asks_for_split
    assert asks_for_split(message) is expected


@pytest.mark.parametrize("message,expected", [
    ("lunch 60 split between Carol and Dave", False),
    ("lunch 60 split between me, Carol and Dave", True),
    ("lunch 60 I paid, split between Carol and Dave", False),
    ("dinner 80 split with Carol", True),
    ("Carol and Dave split 60", None),
    ("Dinner 60 paid by me, split between Carol and Bob", False),
    ("Dinner 60 split between Carol and Bob, paid by me", False),
    ("Dinner 60 split between Carol and me", True),
    ("split the bill evenly with Carol 80", True),
    ("Carol and Dave split 60, paid with Card A", None),
])
def test_sender_shares(message, expected):
    """"Between" lists the sharers in full; "with" adds people to the sender."""
    from app.services.splits import sender_shares
    assert sender_shares(message) is expected


def test_message_words_overrule_the_model(directory):
    """The model said the sender shares, but "between Carol and Bob" doesn't name them."""
    reference = {"kind": "named", "include_sender": True,
                 "people": [{"name": "Carol", "is_family": False}, {"name": "Bob", "is_family": False}]}
    result = resolve_split(reference, directory.participants, directory.participants[0], {}, False,
                           message="lunch 60 split between Carol and Bob")
    assert [p.id for p in result.people] == [2, 3]


def test_a_split_the_message_never_asked_for_is_dropped(directory):
    """No split word in the message: a personal entry, with no warning."""
    reference = {"kind": "named", "include_sender": True, "people": [{"name": "Carol", "is_family": False}]}
    result = resolve_split(reference, directory.participants, directory.participants[0], {}, False,
                           message="dinner with Carol 70")
    assert result.people == () and result.warnings == ()

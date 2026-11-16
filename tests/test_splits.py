"""Whole-cent arithmetic and name resolution, with fictional people only."""

import pytest

from app.services.participants import Participant
from app.services.splits import resolve_split, share_out, split_shares


@pytest.mark.parametrize('word', ['EXCEPT', 'excluding', 'but not', 'apart from', 'other than'])
def test_dropped_exclusion_refuses_split(directory, word):
    from app.services.splits import EXCEPT_UNREAD_WARNING
    result = resolve_split({'kind': 'everyone'}, directory.participants,
                           directory.participant(1), {}, True, f'split everyone {word} Carol')
    assert not result.people and result.warnings == (EXCEPT_UNREAD_WARNING,)


def test_plain_family_exclusion_and_whole_word_backstop(directory):
    reference = {'kind': 'everyone', 'except_people': [{'name': 'Carol', 'is_family': True}]}
    result = resolve_split(reference, directory.participants, directory.participant(1),
                           {2: 'Carol', 3: 'Carol'}, True, "split everyone except Carol's family")
    assert [p.id for p in result.people] == [1] and not result.counts and not result.warnings
    result = resolve_split({'kind': 'everyone'}, directory.participants, directory.participant(1),
                           {}, True, 'split everyone for an exceptional dinner')
    assert len(result.people) == 3 and not result.warnings


def test_everyone_without_entries_keeps_existing_behavior(directory):
    result = resolve_split({'kind': 'everyone'}, directory.participants, directory.participant(1),
                           {}, True, 'split dinner 80')
    assert len(result.people) == 3 and not result.warnings


@pytest.mark.parametrize('count', [None, 0, 1000, 7])
def test_exception_without_valid_count_excludes(directory, count):
    result = resolve_split({'kind': 'everyone', 'default_count': 5,
                            'except_people': [{'name': 'Carol', 'count': count}]},
                           directory.participants, directory.participant(1), {}, True,
                           'split everyone 5 nights except Carol')
    assert result.counts == {1: 5, 2: 5}


def test_counted_exception_overrides_inclusion(directory):
    result = _resolve(directory, {'kind': 'named', 'people': [{'name': 'Carol', 'count': 5}],
                                  'except_people': [{'name': 'Carol', 'count': 4}]})
    assert result.counts == {3: 4}


@pytest.mark.parametrize('name', ['everyone', 'EVERYBODY', 'all', 'the rest', 'rest', 'others', 'everyone else'])
def test_collective_name_supplies_default(directory, name):
    result = resolve_split({'kind': 'everyone', 'people': [{'name': name, 'count': 5}],
                            'except_people': [{'name': 'Carol', 'count': 4}]},
                           directory.participants, directory.participant(1), {}, True,
                           '5 nights everyone except Carol 4')
    assert result.counts == {1: 5, 2: 5, 3: 4} and not result.warnings


@pytest.mark.parametrize('default', [5, 0, 1000, 'bad'])
def test_unused_default_is_dropped(directory, default):
    result = resolve_split({'kind': 'everyone', 'default_count': default,
                            'include_sender': True,
                            'people': [{'name': 'me', 'count': 3}, {'name': 'Carol', 'count': 2}]},
                           directory.participants, directory.participant(1), {}, False,
                           'Hotel 600 split by nights, me 3 and Carol 2')
    assert result.counts == {1: 3, 3: 2}
    assert result.default_count is None and not result.warnings


def test_everyone_downgrade_obeys_between(directory):
    result = resolve_split({'kind': 'everyone', 'include_sender': True, 'people': ['Carol']},
                           directory.participants, directory.participant(1), {}, True,
                           'split between Carol')
    assert [p.id for p in result.people] == [3]


@pytest.mark.parametrize('word', ['everyone', 'everybody', 'all', 'all of us', 'whole group', 'the group'])
def test_count_shorthand_requires_everyone_and_valid_counts(directory, word):
    reference = {'kind': 'everyone', 'default_count': 5}
    valid = resolve_split(reference, directory.participants, directory.participant(1), {}, True,
                          f'Hotel 600, {word}, 5 nights')
    assert valid.counts == {1: 5, 2: 5, 3: 5}
    invalid = resolve_split(reference, directory.participants, directory.participant(1), {}, True,
                            f'Hotel 600, {word}, 4 nights')
    from app.services.splits import COUNT_UNREAD_WARNING
    assert not invalid.people and invalid.warnings == (COUNT_UNREAD_WARNING,)


@pytest.mark.parametrize('message', ['Dinner with everyone 80', 'Dinner with everybody 80',
                                     'Hotel 600 Carol 5 nights', 'Dinner with alligator 80'])
def test_no_split_word_traps(directory, message):
    reference = {'kind': 'everyone'}
    if 'nights' in message:
        reference.update(kind='named', people=[{'name': 'Carol', 'count': 5}])
    result = resolve_split(reference, directory.participants, directory.participant(1), {}, True, message)
    assert not result.people and not result.warnings


def test_missing_sender_count_warns_even_without_uneven_flag(directory):
    from app.services.splits import COUNT_UNREAD_WARNING
    result = _resolve(directory, {'kind': 'named', 'include_sender': True,
                                  'people': [{'name': 'Carol', 'count': 2}]})
    assert not result.people and result.warnings == (COUNT_UNREAD_WARNING,)


def test_invalid_inclusion_refuses_even_if_excluded(directory):
    from app.services.splits import COUNT_UNREAD_WARNING
    result = _resolve(directory, {'kind': 'everyone', 'default_count': 5,
                                  'people': [{'name': 'Carol', 'count': 0}], 'except_people': ['Carol']}, True)
    assert not result.people and result.warnings == (COUNT_UNREAD_WARNING,)


@pytest.mark.parametrize('message,count,valid', [
    ('split Carol 5 nights', 5, True), ('split Carol FIVE nights', 5, True),
    ('split Carol twenty-nine nights', 29, True), ('split Carol thirty nights', 30, True),
    ('split Carol 999 nights', 999, True), ('split Carol 2300', 3, False),
    ('split Carol 5.', 5, True),
    ('split Carol 5.50', 5, False), ('split Carol 4 nights', 5, False),
    ('split Carol 0 nights', 0, False), ('split Carol 1000 nights', 1000, False),
    (None, 5, True), (None, -1, False),
])
def test_counts_must_be_said(directory, message, count, valid):
    """Numbers are checked against raw text, including dictated English number words."""
    from app.services.splits import COUNT_UNREAD_WARNING
    result = resolve_split({'kind': 'named', 'uneven': True,
                            'people': [{'name': 'Carol', 'count': count}]},
                           directory.participants, directory.participant(1), {}, False, message)
    assert bool(result.people) is valid
    assert result.counts == ({3: count} if valid else {})
    assert result.warnings == (() if valid else (COUNT_UNREAD_WARNING,))


def test_everyone_with_family_duration_and_exclusion(directory):
    """A duration exception expands to each family member; a real exclusion still wins."""
    reference = {'kind': 'everyone', 'default_count': 5, 'count_unit': 'nights',
                 'except_people': [{'name': 'Carol', 'is_family': True, 'count': 4}]}
    result = resolve_split(reference, directory.participants, directory.participant(1),
                           {2: 'family', 3: 'family'}, True,
                           "Airbnb 2300, 5 nights, everyone except Carol's family who stayed 4")
    assert result.counts == {1: 5, 2: 4, 3: 4}
    assert result.default_count == 5 and result.unit == 'nights'
    rows = split_shares(230000, result.people, result.counts)
    assert sum(r['share_cents'] for r in rows) == 230000
    assert [r['count'] for r in rows] == [5, 4, 4]
    excluded = _resolve(directory, {**reference, 'except_people': ['Carol']}, True)
    assert excluded.counts == {1: 5, 2: 5}


def test_sender_count_and_missing_count(directory):
    """A me entry supplies the sender's count; include_sender alone cannot invent one."""
    from app.services.splits import COUNT_UNREAD_WARNING
    reference = {'kind': 'named', 'include_sender': True, 'uneven': True,
                 'people': [{'name': 'me', 'count': 3}, {'name': 'Carol', 'count': 2}]}
    result = resolve_split(reference, directory.participants, directory.participant(1), {}, False,
                           'Hotel 600 split by nights, me 3 and Carol 2')
    assert result.counts == {1: 3, 3: 2}
    missing = _resolve(directory, {**reference, 'people': [{'name': 'Carol', 'count': 2}]})
    assert missing.people == () and missing.warnings == (COUNT_UNREAD_WARNING,)


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


@pytest.mark.parametrize("name", ["Carol's family", "Carol’s family", "Carol family"])
def test_family_wording_left_in_the_name_still_matches(directory, name):
    """The model sometimes returns "Carol's family" as the name; it still means Carol's family."""
    reference = {'kind': 'everyone', 'default_count': 5, 'count_unit': 'nights',
                 'people': [{'name': name, 'is_family': False, 'count': 4}]}
    result = resolve_split(reference, directory.participants, directory.participant(1),
                           {2: 'family', 3: 'family'}, True,
                           "Airbnb 2300, 5 nights, everyone except Carol's family who only stayed 4")
    assert result.warnings == ()
    assert result.counts == {1: 5, 2: 4, 3: 4}


def test_except_read_as_counted_sharer_passes_the_backstop(directory):
    """"Except Carol's family who stayed 4" may come back under people with a count rather
    than under except_people; that is a counted sharer, not a dropped exclusion."""
    reference = {'kind': 'everyone', 'default_count': 5,
                 'people': [{'name': 'Carol', 'is_family': True, 'count': 4}], 'except_people': []}
    result = resolve_split(reference, directory.participants, directory.participant(1),
                           {2: 'family', 3: 'family'}, True,
                           "Airbnb 2300, 5 nights, everyone except Carol's family who only stayed 4")
    assert result.counts == {1: 5, 2: 4, 3: 4}


@pytest.mark.parametrize("message", [
    "Airbnb 2300, 5 nights, everyone except Carol's family who only stayed 4",
    "Airbnb 2300, five nights, everyone except Carol's family who only stayed four",
])
def test_uncounted_exception_with_a_number_after_except_refuses(directory, message):
    """Carol under except with her count lost would drop her family from the bill; refuse."""
    from app.services.splits import COUNT_UNREAD_WARNING
    reference = {'kind': 'everyone', 'default_count': 5,
                 'except_people': [{'name': 'Carol', 'is_family': True}]}
    result = resolve_split(reference, directory.participants, directory.participant(1),
                           {2: 'family', 3: 'family'}, True, message)
    assert not result.people and result.warnings == (COUNT_UNREAD_WARNING,)


def test_plain_exclusion_in_a_count_split_still_excludes(directory):
    """"5 nights, everyone except Carol" has no number after except: Carol is left out."""
    reference = {'kind': 'everyone', 'default_count': 5, 'except_people': [{'name': 'Carol'}]}
    result = resolve_split(reference, directory.participants, directory.participant(1),
                           {}, True, "Airbnb 2300, 5 nights, everyone except Carol")
    assert result.counts == {1: 5, 2: 5}

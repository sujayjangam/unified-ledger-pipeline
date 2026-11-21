"""Whole-cent arithmetic and name resolution, with fictional people only."""

import pytest

from app.services.participants import Participant
from app.services.splits import resolve_split, share_out, split_shares


# --- says_uneven: uneven shares read from the words, not the model (#120) --------------------

@pytest.mark.parametrize('message,uneven', [
    ('dinner 90 split unevenly with Carol', True),
    ('dinner 90 split uneven with Carol', True),
    ('taxi 30 split unequally between Carol and Dave', True),
    ('dinner 90 not split equally with Carol', True),
    ('dinner 90 split with Carol, not evenly', True),
    ('dinner 90, uneven split with Carol', True),
    ('taxi 40, unequal shares with Dave', True),
    ('Airbnb 900 split by nights with Carol', True),
    ('Hotel 600 split by number of nights with Carol', True),
    ('hotel 400 split by the night with Carol', True),
    ('dinner 100 split 60/40 with Carol', True),
    ('dinner 100 split 70:30 with Carol', True),
    ('Dinner 90 split 2:1 with Carol', True),
    # Even, or not a ratio at all.
    ('dinner 90 split evenly with Carol', False),
    ('dinner 90 split equally with Carol', False),
    ('dinner 100 split 50/50 with Carol', False),
    ('dinner 90 split 1:1 with Carol', False),
    ('Taxi 40 over uneven roads, go halves with Carol', False),  # "uneven" not about the split
    ('Paris by night tour 80 split with Carol', False),
    ('dinner 40 on 2/1 split with Carol', False),       # a date, not 2:1
    ('dinner 40 on 3/7 split with Carol', False),       # a date
    ('dinner 40 at 10:30 split with Carol', False),     # a time
    ('hotel 200 per night split with Carol', False),    # a price per night is still even
    ('grab 12 to Bythenights Road', False),             # not the word "by"
    ('dinner 80 split with Carol', False),
])
def test_says_uneven(message, uneven):
    from app.services.splits import says_uneven
    assert says_uneven(message) is uneven


def test_uneven_words_tick_evenly_with_the_set_amounts_warning(directory):
    """'Unevenly' with no count read: names ticked evenly, warning points to Set amounts."""
    from app.services.splits import UNEVEN_EVEN_SPLIT_WARNING
    reference = {'kind': 'named', 'include_sender': True, 'people': [{'name': 'Carol'}]}
    result = resolve_split(reference, directory.participants, directory.participant(1), {}, False,
                           'dinner 90 split unevenly with Carol')
    assert [p.id for p in result.people] == [1, 3]
    assert result.warnings == (UNEVEN_EVEN_SPLIT_WARNING,)
    # The same message without the word is a plain even split, with no warning.
    result = resolve_split(reference, directory.participants, directory.participant(1), {}, False,
                           'dinner 90 split with Carol')
    assert [p.id for p in result.people] == [1, 3] and result.warnings == ()


def test_uneven_words_never_drop_counts_that_were_read(directory):
    """'Split by nights' with the nights said is a split by count, not an unread uneven one."""
    reference = {'kind': 'named', 'include_sender': True,
                 'people': [{'name': 'me', 'count': 3}, {'name': 'Carol', 'count': 2}]}
    result = resolve_split(reference, directory.participants, directory.participant(1), {}, False,
                           'Hotel 600 split by nights, me 3 and Carol 2')
    assert result.counts == {1: 3, 3: 2} and result.warnings == ()


def test_a_stale_uneven_flag_from_the_model_is_ignored(directory):
    """Only the words decide now: an old-shape reference carrying uneven=True adds nothing."""
    reference = {'kind': 'named', 'include_sender': True, 'people': [{'name': 'Carol'}], 'uneven': True}
    result = resolve_split(reference, directory.participants, directory.participant(1), {}, False,
                           'dinner 90 split with Carol')
    assert [p.id for p in result.people] == [1, 3] and result.warnings == ()


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
    assert [p.id for p in result.people] == [1, 2] and result.counts == {}  # equal counts: even


def test_counted_exception_overrides_inclusion(directory):
    result = _resolve(directory, {'kind': 'named', 'people': [{'name': 'Bob', 'count': 5}, {'name': 'Carol', 'count': 5}],
                                  'except_people': [{'name': 'Carol', 'count': 4}]})
    assert result.counts == {2: 5, 3: 4}


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
    assert [p.id for p in valid.people] == [1, 2, 3] and valid.counts == {}  # equal counts: even
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
    # Bob at 1000 is never valid, so give him "1", said at the end: a count needs two sharers.
    message = None if message is None else f"{message}, Bob 1"
    result = resolve_split({'kind': 'named',
                            'people': [{'name': 'Carol', 'count': count}, {'name': 'Bob', 'count': 1}]},
                           directory.participants, directory.participant(1), {}, False, message)
    assert bool(result.people) is valid
    assert result.counts == ({2: 1, 3: count} if valid else {})
    assert result.warnings == (() if valid else (COUNT_UNREAD_WARNING,))


@pytest.mark.parametrize("message", [
    "Groceries 150, Carol's share is 50, rest split evenly",
    "Dinner 90 split with Carol, she owes 30",
    "Taxi 40 split 60% me and 40% Carol",
])
def test_fixed_share_wording_ticks_names_evenly(directory, message):
    """A fixed share or a percentage ticks the names read, evenly, with the Set amounts warning
    first (ADR-0048). The model's count (50 here, from "share is 50") is never used."""
    from app.services.splits import UNEVEN_EVEN_SPLIT_WARNING
    reference = {'kind': 'named', 'people': [{'name': 'Carol', 'count': 50}]}
    result = resolve_split(reference, directory.participants, directory.participant(1), {}, False, message)
    assert 3 in {p.id for p in result.people}
    assert result.counts == {} and result.default_count is None
    assert result.warnings[0] == UNEVEN_EVEN_SPLIT_WARNING


def test_fixed_share_with_nobody_matched_records_no_split(directory):
    """With no name matched there is nothing to tick: the 'no split recorded' wording, and the
    generic 'couldn't tell who shares' line is not repeated."""
    from app.services.splits import NOBODY_SHARES_WARNING, UNEVEN_NOT_BUILT_WARNING
    reference = {'kind': 'named', 'people': [{'name': 'Zed', 'count': 50}]}
    result = resolve_split(reference, directory.participants, directory.participant(1), {}, False,
                           "Dinner 90 split between Zed, Zed's share is 50")
    assert not result.people
    assert result.warnings[0] == UNEVEN_NOT_BUILT_WARNING
    assert NOBODY_SHARES_WARNING not in result.warnings


def test_one_sharer_with_a_count_refuses(directory):
    """A count only means something next to another; alone it was a misread number."""
    from app.services.splits import COUNT_UNREAD_WARNING
    reference = {'kind': 'named', 'people': [{'name': 'Carol', 'count': 50}]}
    result = resolve_split(reference, directory.participants, directory.participant(1), {}, False,
                           "Groceries 150, Carol 50, split")
    assert not result.people and result.warnings == (COUNT_UNREAD_WARNING,)


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
    assert [p.id for p in excluded.people] == [1, 2] and excluded.counts == {}  # equal counts: even


def test_sender_count_and_missing_count(directory):
    """A me entry supplies the sender's count; include_sender alone cannot invent one."""
    from app.services.splits import COUNT_UNREAD_WARNING
    reference = {'kind': 'named', 'include_sender': True,
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


def _resolve(directory, reference, trip_on=False, families=None, message=None):
    """Resolve a fictional message for Alice with optional trip families."""
    return resolve_split(reference, directory.participants, directory.participant(1), families or {},
                         trip_on, message)


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


@pytest.mark.parametrize("name", ["Reds", "reds", "Reds family", "the Reds family", "Reds'"])
def test_family_label_names_the_whole_family(directory, name):
    """A trip family's label, said on its own, stands for every member of that family."""
    result = _resolve(directory, {"kind": "named", "people": [{"name": name, "is_family": False}]},
                      True, {2: "Reds", 3: "Reds"})
    assert [p.id for p in result.people] == [2, 3]
    assert not result.warnings


def test_family_label_carries_its_count(directory):
    """"Reds only 5 nights, rest all 6": the label's count goes to each member of the family."""
    reference = {"kind": "everyone", "default_count": 6, "count_unit": "nights",
                 "people": [{"name": "Reds", "is_family": False, "count": 5}]}
    result = resolve_split(reference, directory.participants, directory.participant(1),
                           {2: "Reds", 3: "Reds"}, True,
                           "Airbnb 600 for 6 nights. Reds only staying 5 nights. Rest all stay 6 nights.")
    assert result.counts == {1: 6, 2: 5, 3: 5}
    assert not result.warnings


def test_a_person_is_matched_before_a_family_label(directory):
    """A label that is also somebody's name still means that one person, as it did before."""
    result = _resolve(directory, {"kind": "named", "people": [{"name": "Carol", "is_family": False}]},
                      True, {1: "Carol", 2: "Carol"})
    assert [p.id for p in result.people] == [3]


def test_family_label_needs_a_trip(directory):
    """With no trip on there are no labels, so a label is an unmatched name, never a guess."""
    result = _resolve(directory, {"kind": "named", "people": [{"name": "Reds"}, {"name": "Carol"}]})
    assert [p.id for p in result.people] == [3]
    assert result.warnings == ("⚠️ Couldn't match 'Reds'",)


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


@pytest.mark.parametrize("reference,trip_on,message,warning", [
    ({"kind": "everyone"}, False, None, '⚠️ "Everyone" needs a trip switched on. No split recorded.'),
    ({"kind": "named"}, True, "dinner 90 split unevenly",
     "⚠️ Uneven shares: tap 👥 Add split, tick who shares, then Set amounts. No split recorded."),
    ({"kind": "named"}, False, None, "⚠️ Couldn't tell who shares this. No split recorded."),
    ({"kind": "named", "include_sender": True, "except_people": ["me"]}, True, None,
     "⚠️ Couldn't tell who shares this. No split recorded."),
])
def test_refusals(directory, reference, trip_on, message, warning):
    """No guessed group is returned when the requested split cannot be resolved."""
    result = _resolve(directory, reference, trip_on, message=message)
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
    assert [p.id for p in result.people] == [1, 2] and result.counts == {}  # equal counts: even


def test_count_left_out_by_the_model_is_read_after_the_name(directory):
    """"Carol and Bob 4": the model gave 4 to Carol only; Bob's 4 is the next number after him."""
    reference = {'kind': 'named', 'people': [{'name': 'Alice', 'count': 5}, {'name': 'Carol', 'count': 4},
                                             {'name': 'Bob', 'count': None}]}
    result = resolve_split(reference, directory.participants, directory.participant(1), {}, True,
                           "Airbnb 2300, split by nights: Alice 5, Carol and Bob 4")
    assert result.counts == {1: 5, 2: 4, 3: 4} and not result.warnings


def test_unnamed_sender_is_not_added_to_a_count_split(directory):
    """The model's include_sender is ignored in a count split the sender isn't named in."""
    reference = {'kind': 'named', 'include_sender': True,
                 'people': [{'name': 'Bob', 'count': 5}, {'name': 'Carol', 'count': 4}]}
    result = resolve_split(reference, directory.participants, directory.participant(1), {}, True,
                           "Airbnb 2300, split by nights: Bob 5, Carol 4")
    assert result.counts == {2: 5, 3: 4} and not result.warnings


def test_sender_count_read_after_me(directory):
    """"me 3 and Carol 2" with no "me" entry from the model: the sender's 3 follows "me"."""
    reference = {'kind': 'named', 'include_sender': True, 'people': [{'name': 'Carol', 'count': 2}]}
    result = resolve_split(reference, directory.participants, directory.participant(1), {}, False,
                           "Hotel 600 split by nights, me 3 and Carol 2")
    assert result.counts == {1: 3, 3: 2}


def test_counted_exception_without_a_default_refuses(directory):
    """An except entry with a count, and nobody else given one, can't fall through to even."""
    reference = {'kind': 'everyone', 'except_people': [{'name': 'Carol', 'count': 4}]}
    result = resolve_split(reference, directory.participants, directory.participant(1), {}, True,
                           "Airbnb 2300, everyone except Carol who stayed 4")
    assert not result.people and result.warnings


@pytest.mark.parametrize("text,expected", [
    ("Airbnb 2300, 5 nights, Carol 4", [2300, 5, 4]),
    ("twenty one nights, twenty-two, Thirty", [21, 22, 30]),
    ("3.50 and 3,000 and 7.", [7]),
])
def test_numbers_reads_whole_numbers_in_order(text, expected):
    from app.services.splits import _numbers
    assert _numbers(text) == expected


# ---- The payer's clause and "the rest" (2026-11-17) ----
# Six fictional trip members; Carol and Dave are the Reds, Erin is the Blues.
_TRIP = tuple(Participant(i, n, None, None) for i, n in
              enumerate(["Alice", "Bob", "Carol", "Dave", "Erin", "Fay"], start=1))
_TRIP_FAMILIES = {3: "Reds", 4: "Reds", 5: "Blues"}
_STAY = ("Carol paid 2717.84 for airbnb for 6 nights. Reds and Blues only staying 5 nights. "
         "Rest all stay 6 nights.")
_STAY_COUNTS = {1: 6, 2: 6, 3: 5, 4: 5, 5: 5, 6: 6}


def _trip(reference, message):
    return resolve_split(reference, _TRIP, _TRIP[0], _TRIP_FAMILIES, True, message)


@pytest.mark.parametrize("people", [
    [{"name": "Carol", "count": 6}, {"name": "Reds", "count": 5}, {"name": "Blues", "count": 5}],
    [{"name": "Reds", "count": 5}, {"name": "Blues", "count": 5}, {"name": "Carol", "count": 6}],
    [{"name": "Reds", "count": 5}, {"name": "Blues", "count": 5}, {"name": "Carol"}],
])
def test_payer_clause_gives_no_count(people):
    """"Carol paid ... for 6 nights": the 6 is the bill's length, so Carol shares at her
    family's 5, whichever order the model lists the entries in."""
    result = _trip({"kind": "everyone", "default_count": 6, "count_unit": "nights", "people": people}, _STAY)
    assert result.counts == _STAY_COUNTS and not result.warnings


@pytest.mark.parametrize("kind", ["everyone", "named"])
def test_two_labels_read_as_one_name_are_both_matched(kind):
    """"Reds and Blues" came back as one name about half the time (logprobs, 2026-11-18, #120)
    and matched nobody, so the whole split was lost. Each part matches, so it is both families,
    whichever way the model also read "everyone" or "named" (a 49%/49% tie)."""
    result = _trip({"kind": kind, "include_sender": True, "default_count": 6, "count_unit": "nights",
                    "people": [{"name": "Carol", "is_family": False, "count": None},
                               {"name": "Reds and Blues", "is_family": True, "count": 5}]}, _STAY)
    assert result.counts == _STAY_COUNTS and not result.warnings


@pytest.mark.parametrize("kind", ["everyone", "named"])
def test_a_label_with_its_own_count_is_added_when_the_model_left_it_out(kind):
    """The /help example: the model returned no people, 94% sure (logprobs, 2026-11-18,
    #120), so the Reds' 5 was lost. "Reds 5 nights" is a label and one number in its clause."""
    result = _trip({"kind": kind, "include_sender": False, "people": [], "except_people": [],
                    "default_count": 6, "count_unit": "nights"},
                   "Airbnb 900 split by nights. Reds 5 nights, rest all 6 nights.")
    assert result.counts == {1: 6, 2: 6, 3: 5, 4: 5, 5: 6, 6: 6} and not result.warnings


def test_a_family_said_with_its_count_is_added_as_the_family():
    result = _trip({"kind": "named", "include_sender": False, "default_count": None, "count_unit": "nights",
                    "people": [{"name": "Bob", "count": 4}]},
                   "Airbnb 900 split by nights: Carol's family 5, Bob 4")
    assert result.counts == {2: 4, 3: 5, 4: 5}


@pytest.mark.parametrize("message, reference", [
    # An even split never gains a name: no count read and no "by nights".
    ("dinner 80 split with Carol and Dave 80", {"kind": "named", "include_sender": True,
                                               "people": [{"name": "Carol"}]}),
    # Two numbers after the name in its clause: either could be the count.
    ("Airbnb 900 split by nights. Reds 5 or 6 nights, rest all 6",
     {"kind": "everyone", "include_sender": True, "people": [], "default_count": 6}),
    # Said only as the payer: "Carol paid 600" is the bill, not her count.
    ("Carol paid 600 split by nights, Dave 4, Erin 6",
     {"kind": "named", "include_sender": False, "people": [{"name": "Dave", "count": 4},
                                                           {"name": "Erin", "count": 6}]}),
])
def test_nobody_is_added_without_one_count_of_their_own(message, reference):
    from app.services.splits import _add_unlisted
    assert _add_unlisted({"except_people": [], **reference}, message, _TRIP, _TRIP_FAMILIES) == \
        {"except_people": [], **reference}


@pytest.mark.parametrize("joined", ["Carol & Dave", "Carol, Dave", "Carol and Dave"])
def test_two_people_read_as_one_name_are_both_matched(joined):
    result = _trip({"kind": "named", "include_sender": False, "people": [{"name": joined}]},
                   "lunch 60 split between Carol and Dave")
    assert [p.id for p in result.people] == [3, 4] and not result.warnings


def test_a_joined_name_with_an_unmatched_part_matches_nobody():
    """One unknown part: nothing is guessed, and the warning names what was said."""
    result = _trip({"kind": "named", "include_sender": False, "people": [{"name": "Reds and Zed"}]},
                   "lunch 60 split between Reds and Zed")
    assert result.people == ()
    assert result.warnings == ("⚠️ Couldn't match 'Reds and Zed'",)


@pytest.mark.parametrize("reference", [
    {"kind": "everyone", "people": [{"name": "Carol", "count": 6}, {"name": "Reds", "count": 5},
                                    {"name": "Blues", "count": 5}]},
    {"kind": "named", "include_sender": True, "people": [{"name": "Reds", "count": 5}, {"name": "Blues", "count": 5}]},
    {"kind": "everyone", "default_count": 5,
     "people": [{"name": "Reds", "count": 5}, {"name": "Blues", "count": 5}, {"name": "rest", "count": 5}]},
])
def test_rest_count_read_from_the_words(reference):
    """The model left out the rest's 6, kept the split "named", or read the wrong default:
    the rest word's own clause decides, and with a trip on "the rest" is everyone else."""
    result = _trip(reference, _STAY)
    assert result.counts == _STAY_COUNTS and result.default_count == 6 and not result.warnings


def test_rest_without_all_or_a_split_word():
    """"Others stay 6 nights" has no split word and no "all", and is still a split."""
    result = _trip({"kind": "named", "people": [{"name": "Reds", "count": 5}]},
                   "Carol paid 600 for 6 nights. Reds only staying 5 nights. Others stay 6 nights.")
    assert result.counts == {1: 6, 2: 6, 3: 5, 4: 5, 5: 6, 6: 6}


def test_rest_count_comes_from_its_own_clause():
    """"6 nights for the rest, Reds 5": the rest get 6, never the 5 from the next clause."""
    result = _trip({"kind": "everyone", "default_count": 5, "people": [{"name": "Reds", "count": 5}]},
                   "Airbnb 600, split: 6 nights for the rest, Reds 5")
    assert result.counts == {1: 6, 2: 6, 3: 5, 4: 5, 5: 6, 6: 6}


def test_payer_named_again_as_a_sharer_keeps_that_count():
    """A count said after the payer's name somewhere else in the message is theirs."""
    message = "Carol paid 600 for 6 nights, split by nights between Carol 4 and Dave 6"
    for carol in ({"name": "Carol", "count": 4}, {"name": "Carol"}):
        result = _trip({"kind": "named", "people": [carol, {"name": "Dave", "count": 6}]}, message)
        assert result.counts == {3: 4, 4: 6}


def test_two_numbers_by_the_rest_keep_the_models_default():
    """"6 nights for the rest and Carol 4 nights": Python can't tell which number is the rest's,
    so the model's default stands and Carol keeps her 4."""
    result = _trip({"kind": "everyone", "default_count": 6, "people": [{"name": "Carol", "count": 4}]},
                   "Airbnb 600 split 6 nights for the rest and Carol 4 nights")
    assert result.counts == {1: 6, 2: 6, 3: 4, 4: 6, 5: 6, 6: 6}


def test_payer_clause_keeps_her_own_count():
    """"Carol paid for her 4 nights": the 4 is said to be hers, so it stays."""
    result = _trip({"kind": "named", "people": [{"name": "Carol", "count": 4}, {"name": "Bob", "count": 6}]},
                   "Airbnb 600 split by nights: Carol paid for her 4 nights and Bob stayed 6 nights")
    assert result.counts == {2: 6, 3: 4}
    dropped = _trip({"kind": "named", "people": [{"name": "Carol"}, {"name": "Bob", "count": 6}]},
                    "Airbnb 600 split by nights: Carol paid for her 4 nights and Bob stayed 6 nights")
    assert dropped.counts == {2: 6, 3: 4}  # the model left Carol's 4 out; read back from "her 4"


def test_amount_with_a_thousands_comma_is_not_the_rests_count():
    """"The rest paid 2,717.84": the comma inside the amount doesn't end the clause."""
    from app.services.splits import _rest_count
    assert _rest_count("Split by nights: everyone 6 nights, Carol 4 nights. The rest paid 2,717.84") is None


def test_their_airbnb_is_not_their_count():
    """"Their" before a word, not a number, doesn't make the bill's 6 nights Carol's."""
    result = _trip({"kind": "everyone", "default_count": 6,
                    "people": [{"name": "Carol", "count": 6}, {"name": "Reds", "count": 5}]},
                   "Carol paid 600 for their airbnb for 6 nights. Reds 5 nights. Rest all stay 6 nights.")
    assert result.counts == {1: 6, 2: 6, 3: 5, 4: 5, 5: 6, 6: 6}


@pytest.mark.parametrize("reference", [
    {"kind": "everyone"},
    {"kind": "everyone", "default_count": 80},
    {"kind": "named", "people": [{"name": "others", "count": 80}]},
])
def test_others_without_a_split_is_no_split(reference):
    """"Dinner with the others 80": no split word and nobody's count, so a personal entry."""
    result = _trip(reference, "Dinner with the others 80")
    assert result.people == () and result.warnings == ()


def test_rest_inside_a_between_list_stays_in_the_list():
    """"Split between Carol, Dave and Erin ... the rest 6": the rest of those three, not the trip."""
    result = _trip({"kind": "named", "people": [{"name": "Carol", "count": 4}, {"name": "Dave", "count": 6},
                                                {"name": "Erin", "count": 6}]},
                   "Airbnb 600 split between Carol, Dave and Erin by nights. Carol 4 nights, the rest 6")
    assert result.counts == {3: 4, 4: 6, 5: 6}


def test_split_with_the_others_alone_is_unchanged():
    """No named count: the rest word reads nothing, so no new warning appears."""
    result = resolve_split({"kind": "named", "include_sender": True, "people": [{"name": "others"}]},
                           _TRIP, _TRIP[0], {}, False, "Dinner 80 split with the others")
    assert result.counts == {} and result.default_count is None


def test_a_number_of_others_is_not_a_count():
    """"Split with Carol and 2 others" counts people: no whole-trip split at 2 each."""
    result = _trip({"kind": "named", "people": [{"name": "Carol"}]}, "Dinner 80, split with Carol and 2 others")
    assert [p.id for p in result.people] == [1, 3] and result.counts == {}


def test_payer_in_an_even_split_still_shares():
    """Only the count is dropped: who shares is decided as before."""
    result = _trip({"kind": "named", "people": [{"name": "Carol"}, {"name": "Bob"}]},
                   "Carol paid 90 for dinner, split with Bob")
    assert [p.id for p in result.people] == [1, 2, 3] and result.counts == {}


@pytest.mark.parametrize("message,count", [
    ("Rest all stay 6 nights", 6),
    ("6 nights for the rest, Reds 5", 6),
    ("Reds 5 and 6 for everyone else", None),  # two numbers in one clause: not read
    ("6 nights for the rest and Carol 4 nights", None),
    ("Reds 5, rest 6", 6),
    ("Reds 5. Rest 6.", 6),
    ("Reds 5, the rest of us 6", 6),
    ("Paid the rest of the airbnb 450. Reds 5 nights, Blues 6 nights", None),
    ("Carol and two others 6 nights", None),
    ("the rest paid 2717.84", None),
    ("Reds 5. The rest stay", None),
    ("Reds 5", None),
])
def test_rest_count(message, count):
    from app.services.splits import _rest_count
    assert _rest_count(message) == count


def test_amount_after_a_name_is_not_a_count():
    """"Carol 2717.84" has no count: neither 2717 nor 84 stands alone."""
    from app.services.splits import _count_after
    assert _count_after("Carol", "Carol 2717.84 for the airbnb") is None


# ---- "⚠️ Check …" when a count was read loosely ----

def test_payer_count_outside_the_paying_clause_asks_for_a_check():
    """"Carol paid 600 for the villa, stayed 4 nights": the 4 isn't in the paying clause, so it
    may be Carol's. It is still dropped (the rule stands), but the card asks for a check."""
    result = _trip({"kind": "everyone", "count_unit": "nights",
                    "people": [{"name": "Carol", "count": 4}]},
                   "Carol paid 600 for the villa, stayed 4 nights. Rest all 6 nights.")
    assert result.warnings == ("⚠️ Check Carol's nights.",)


def test_payer_count_inside_the_paying_clause_is_dropped_quietly():
    """The real message: "Carol paid ... for 6 nights" is the bill's length, so no check line."""
    result = _trip({"kind": "everyone", "count_unit": "nights",
                    "people": [{"name": "Carol", "count": 6}, {"name": "Reds", "count": 5},
                               {"name": "Blues", "count": 5}]}, _STAY)
    assert result.counts == _STAY_COUNTS and not result.warnings


def test_count_borrowed_from_another_clause_asks_for_a_check():
    """"Split between Bob, Dave and Fay. Bob 2, the rest 3": the first number after Dave and
    Fay is Bob's 2, from another clause, so each of them gets a check line."""
    result = _trip({"kind": "named", "count_unit": "nights",
                    "people": [{"name": "Bob", "count": 2}, {"name": "Dave"}, {"name": "Fay"}]},
                   "Villa 600 split between Bob, Dave and Fay. Bob 2, the rest 3.")
    assert result.warnings == ("⚠️ Check Dave's nights.", "⚠️ Check Fay's nights.")


def test_count_from_the_same_clause_needs_no_check():
    """"Carol and Erin 4": Erin's 4 is in her own clause, so filling it in is safe."""
    result = _trip({"kind": "named", "count_unit": "nights",
                    "people": [{"name": "Bob", "count": 5}, {"name": "Carol", "count": 4}, {"name": "Erin"}]},
                   "Airbnb 2300, split by nights: Bob 5, Carol and Erin 4")
    assert result.counts == {2: 5, 3: 4, 5: 4} and not result.warnings


def test_check_line_without_a_unit_says_count():
    result = _trip({"kind": "everyone", "people": [{"name": "Carol", "count": 4}]},
                   "Carol paid 600 for the villa, stayed 4. Rest all 6.")
    assert result.warnings == ("⚠️ Check Carol's count.",)


# Two "rest" wordings (one is a /help example) that only this branch's rules read.
@pytest.mark.parametrize("message, reference, counts", [
    ("Airbnb 900 split by nights. Reds 5 nights, rest all 6 nights.",
     {"kind": "everyone", "people": [{"name": "Reds", "count": 5}]},
     {1: 6, 2: 6, 3: 5, 4: 5, 5: 6, 6: 6}),
    # Replaced "Carol paid 600 for her 4 nights. Rest all stay 6 nights.", which could fairly
    # mean the 600 was Carol's own stay (#120). This wording says the 600 is for everyone.
    ("Carol paid 600 for all. Carol stayed 4 nights, rest all 6 nights",
     {"kind": "everyone", "people": [{"name": "Carol", "count": 4}]},
     {1: 6, 2: 6, 3: 4, 4: 6, 5: 6, 6: 6}),
])
def test_help_examples_split_as_the_help_says(message, reference, counts):
    result = _trip({**reference, "count_unit": "nights"}, message)
    assert result.counts == counts and not result.warnings


def test_payer_count_that_ends_up_the_same_needs_no_check():
    """The model gave the payer her family's 5, outside her paying clause; she shares at 5
    anyway, so nothing was lost and the card stays quiet."""
    result = _trip({"kind": "everyone", "count_unit": "nights",
                    "people": [{"name": "Carol", "count": 5}, {"name": "Reds", "count": 5},
                               {"name": "Blues", "count": 5}]}, _STAY)
    assert result.counts == _STAY_COUNTS and not result.warnings


# --- "for <person>": paid for someone, who owes it back (#154) -------------------------------

FOR_PEOPLE = tuple(Participant(i, name, None, None) for i, name in enumerate(
    ('Alice', 'Bob', 'Carol', 'Dave', 'Erin', 'Frank', 'Ann Lee', 'Ann Kim'), start=1))
FOR_FAMILIES = {1: 'Alice', 2: 'Reds', 6: 'Reds', 3: 'Blues', 4: 'Blues'}


def _for(message, reference=None):
    """Alice sends, on a trip with every FOR_PEOPLE member; the model read no split."""
    return resolve_split(reference or {'kind': 'none'}, FOR_PEOPLE, FOR_PEOPLE[0], FOR_FAMILIES, True, message)


@pytest.mark.parametrize('message,names', [
    ("Carol paid 50 for Dave's ticket", ['Dave']),
    ('Carol paid $365.68 for Dave’s Bali flight', ['Dave']),           # a curly apostrophe
    ('Spent 153.46usd for Dave on bed from Alibaba', ['Dave']),
    ('Carol paid 90 for me', ['Alice']),
    ('Dave paid 35myr for my haircut', ['Alice']),                     # "my" is the sender
    ('Carol paid $58.26 on my behalf for travel insurance', ['Alice']),
    ('flights 400 on behalf of Dave', ['Dave']),
    ("Carol paid 120 for Dave and Erin's tickets", ['Dave', 'Erin']),
    ('taxi 30 for Dave, Erin & Frank', ['Dave', 'Erin', 'Frank']),
    ('Carol paid 90 for Dave, Erin, and Frank', ['Dave', 'Erin', 'Frank']),   # an Oxford comma
    ('Taxi for Dave, 30', ['Dave']),                                   # the comma ends the list
    ("Carol paid 50 for Ann Lee's ticket", ['Ann Lee']),               # a two-word name
    ('Carol paid 300 for the Reds', ['Bob', 'Frank']),                 # a trip family label
    ("Carol paid 300 for Bob's family", ['Bob', 'Frank']),
    ("Carol paid 120 for Dave's ticket and Erin's ticket", ['Dave', 'Erin']),
    ('Carol paid 120 for Dave and for Erin', ['Dave', 'Erin']),
    ("Carol paid 900 for Dave's flight and hotel", ['Dave']),         # the hotel is Dave's too
    ("Carol paid 120 for Dave's ticket and my ticket", ['Alice', 'Dave']),
    ("Carol paid 50 for Dave's ticket and Erin booked it", ['Dave']),  # Erin starts a new clause
    ('Carol paid 30 for Dave and Erin on the bus', ['Dave', 'Erin']),
    ('Taxi for Dave and Erin 30', ['Dave', 'Erin']),                   # an amount, not a new clause
    ('Taxi for Dave and Erin sgd 30', ['Dave', 'Erin']),
    ('Carol paid 252 for Scoot flights for Dave and Erin.', ['Dave', 'Erin']),  # #156
    ('Carol paid 90 for hotel for Dave, Erin & Frank', ['Dave', 'Erin', 'Frank']),
    ("Carol paid 60 for Dave's ticket for the show", ['Dave']),
])
def test_for_a_person_means_they_owe(message, names):
    result = _for(message)
    assert [p.display_name for p in result.people] == names
    assert result.warnings == ()


@pytest.mark.parametrize('message', [
    'gift for Dave 40',
    "Carol paid 50 for Dave's birthday cake",
    'lunch 45 for Dave, my treat',
    "Carol bought cake for Dave's mum on 3 Sep for $34.80",            # a relative, not Dave
    "I paid 85myr for my mum's earrings",
    'dinner for 4 people 120',
    'Carol paid for dinner 80',
    'dinner 80 paid for by Carol',                                     # the payer, not a sharer
    'ticket 50 for Zed',                                               # nobody called Zed
    "Taxi 30 for Ann's bag",                                           # two people called Ann
    'Carol paid 82.60 for all 4 pax on 31 Aug',
    'Carol paid 816.04 for SIN-SYD tickets for 2 (SJ & L)',
    'Carol paid 2717.84 for airbnb for 6 nights',
])
def test_for_without_a_known_person_or_with_a_gift_word_is_personal(message):
    result = _for(message)
    assert result.people == () and result.warnings == ()


@pytest.mark.parametrize('message,unknown', [
    ('Carol paid 120 for her and Dave', 'her'),
    ('Carol paid 120 for Dave and the team', 'the team'),
    ("Carol paid 120 for Dave's ticket and Zed's ticket", "Zed's ticket"),
    ("Carol paid 120 for Dave's ticket and her ticket", 'her ticket'),
    ("Carol paid 120 for Dave's ticket and my mum's ticket", "my mum's ticket"),
    ("Carol paid 120 for Dave's ticket and for Zed", 'Zed'),          # a second "for" names a person
    ('Carol paid 120 for flights for Dave and Zed', 'Zed'),           # the second list still checks
    ("Carol paid 120 for Dave's ticket for the show and Zed's ticket", "Zed's ticket"),
])
def test_for_with_a_person_and_an_unknown_name_records_nothing(message, unknown):
    """Half a list is never charged: nobody owes, and the card says which name failed."""
    from app.services.splits import FOR_NO_MATCH_WARNING
    result = _for(message)
    assert result.people == ()
    assert result.warnings == (FOR_NO_MATCH_WARNING.format(name=unknown),)


def test_a_split_word_overrides_for():
    """With a split word the split rules decide, even when they find nobody."""
    assert _for('dinner 80 for Dave, split').people == ()
    with_carol = {'kind': 'named', 'include_sender': True, 'people': [{'name': 'Carol', 'is_family': False}]}
    assert [p.display_name for p in _for('dinner 80 for Dave, split with Carol', with_carol).people] == ['Alice', 'Carol']


def test_for_ignores_a_split_the_model_read_without_a_split_word():
    """No split word: the model's names are dropped as before, and "for" decides."""
    erin = {'kind': 'named', 'include_sender': True, 'people': [{'name': 'Erin', 'is_family': False}]}
    assert [p.display_name for p in _for("Carol paid 50 for Dave's ticket", erin).people] == ['Dave']


def test_for_is_read_only_with_the_message():
    """No message, no text checks: the model's reference alone, as for every other reading."""
    result = resolve_split({'kind': 'none'}, FOR_PEOPLE, FOR_PEOPLE[0], FOR_FAMILIES, True, None)
    assert result.people == ()


def test_for_matches_only_the_candidates():
    """Off a trip's member list, a name is nobody, as in any split."""
    result = resolve_split({'kind': 'none'}, FOR_PEOPLE[:3], FOR_PEOPLE[0], {}, True, "Carol paid 50 for Dave's ticket")
    assert result.people == () and result.warnings == ()

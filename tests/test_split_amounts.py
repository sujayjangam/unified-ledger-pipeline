"""Exact reply shares, with no Telegram, model or database connections."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram import ForceReply

import app.bot_core as bot_core
from app.services.participants import Participant
from app.services.splits import (
    AMOUNTS_CLEARED_WARNING, AMOUNTS_PROMPT_LINE, COUNT_UNREAD_WARNING, COUNT_EVEN_SPLIT_WARNING,
    UNEVEN_NOT_BUILT_WARNING, UNEVEN_EVEN_SPLIT_WARNING, _FIXED_SHARE, _set_split, amounts_prompt,
    parse_split_amounts, split_rows, toggle_split_person,
)
from test_bot_core import _tap
from test_paid_by_button import _data
from test_splits_card import WITH_CAROL, _card, _confirm


@pytest.fixture
def pending(directory):
    """Alice and Bob ticked, with Carol available but not selected."""
    entry = {'split_menu': {'amount_cents': 13500, 'everyone': directory.participants,
                           'groups': (), 'families': {}, 'counts': {1: 2, 2: 1}},
             'split': (), 'split_warnings': (), 'entered_by_participant_id': 1}
    entry['split'] = split_rows(entry, (1, 2))
    return entry


@pytest.mark.parametrize('text,cents', [
    ('Alice 45, Bob 90', 4500), ('Alice 45\nBob rest', 4500),
    ('me: $45, Bob: rest', 4500), ('i 45, Bob rest', 4500),
    ('myself 45, Bob rest', 4500), ('Alice 45.5, Bob rest', 4550),
    ('Alice 45.555, Bob rest', 4556),
])
def test_amounts_exact_and_pure(pending, text, cents):
    before = deepcopy(pending)
    changed = parse_split_amounts(pending, text, 1)
    assert pending == before
    assert changed['split_menu']['amounts'] == ({1: cents, 2: 9000} if text == 'Alice 45, Bob 90' else {1: cents})
    assert [r['share_cents'] for r in split_rows(changed, (1, 2))] == [cents, 13500 - cents]
    assert all('count' not in r for r in changed['split'])


@pytest.mark.parametrize('text,error', [
    ('Alice 45, Bob 50', '40.00 short and nobody left'), ('Alice 85, Bob 90', '175.00, more than 135.00'),
    ('Zed 45, Bob rest', "Couldn't match 'Zed'."),
    ('Carol 45, Bob rest', "Couldn't match 'Carol'."),
    ('Alice 45, me 45, Bob rest', 'More than one'), ('Alice 0, Bob rest', 'untick them instead'),
    ('Alice -1, Bob rest', 'positive amount'), ('Alice nope', "Couldn't read"),
    ('Alice NaN', "Couldn't read"), ('Alice 135, Bob rest', 'Nothing left for Bob'),
    ('Alice 140, Bob rest', '140.00, more than 135.00'), ('Alice', "Couldn't read"),
    ('90', "Couldn't read '90'"), ('Bob 90 Alice', "Couldn't read 'Alice'"),
    ('Bob 4x', 'Invalid amount'),
])
def test_errors_leave_argument_alone(pending, text, error):
    before = deepcopy(pending)
    with pytest.raises(ValueError) as raised:
        parse_split_amounts(pending, text, 1)
    assert str(raised.value).startswith('⚠️ ')
    assert error in str(raised.value)
    assert pending == before


def test_ambiguous_name(pending):
    pending['split_menu']['everyone'] = (Participant(1, 'Ann Lee', None, None),
                                         Participant(2, 'Bob Lee', None, None))
    with pytest.raises(ValueError, match="Couldn't match 'Lee'"):
        parse_split_amounts(pending, 'Lee 45, Bob rest', 1)


@pytest.mark.parametrize('text', ['Bob 90', 'Bob 90 me rest', 'Bob 90, me rest',
                                  'Bob 90\nme rest', 'Bob:$90 me:REST', 'Bob 90,'])
def test_one_person_and_optional_separators(pending, text):
    changed = parse_split_amounts(pending, text, 1)
    assert changed['split_menu']['amounts'] == {2: 9000}
    assert [(r['share_cents'], r['set']) for r in changed['split']] == [(4500, False), (9000, True)]
    prompt, placeholder = amounts_prompt(changed, '⚠️ Check this')
    assert prompt.startswith(AMOUNTS_PROMPT_LINE + '\n⚠️ Check this\nTotal 135.00.')
    assert "Alice 45.00 (what's left)\nBob 90.00 ✏️" in prompt
    assert placeholder == 'Alice 50'


def test_multiword_name_and_placeholder_limit(pending):
    pending['split_menu']['everyone'] = (Participant(1, 'Alice', None, None),
                                         Participant(2, 'Bob Lee', None, None))
    changed = parse_split_amounts(pending, 'Bob Lee 90 me rest', 1)
    assert changed['split_menu']['amounts'] == {2: 9000}
    changed['split'][0]['name'] = 'A' * 100
    assert len(amounts_prompt(changed)[1]) == 64


@pytest.mark.parametrize('counts,expected', [({}, [2251, 9000, 2250]),
                                           ({1: 2, 2: 1, 3: 1}, [3001, 9000, 1500])])
def test_remainder_whole_cents_and_counts(pending, counts, expected):
    pending['split_menu'].update(counts=counts, amount_cents=13501)
    pending['split'] = split_rows(pending, (1, 2, 3))
    changed = parse_split_amounts(pending, 'Bob 90', 1)
    assert [r['share_cents'] for r in changed['split']] == expected
    assert sum(r['share_cents'] for r in changed['split']) == 13501
    assert all('count' not in r for r in changed['split'])


def test_replace_and_multiple_rest(pending):
    first = parse_split_amounts(pending, 'Bob 90', 1)
    before = deepcopy(first)
    second = parse_split_amounts(first, 'Bob 80', 1)
    assert first == before
    assert second['split_menu']['amounts'] == {2: 8000}
    with pytest.raises(ValueError, match='more than'):
        parse_split_amounts(first, 'me 50', 1)
    assert first == before
    reset = parse_split_amounts(second, 'Bob rest me rest', 1)
    assert reset['split_menu']['amounts'] == {}
    assert [r['share_cents'] for r in reset['split']] == [9000, 4500]
    assert all('set' not in r for r in reset['split'])


def test_tiny_remainder_rejected_without_mutation(pending):
    pending['split_menu']['counts'] = {}
    pending['split'] = split_rows(pending, (1, 2, 3))
    before = deepcopy(pending)
    with pytest.raises(ValueError, match='Nothing left for Carol'):
        parse_split_amounts(pending, 'Bob 134.99', 1)
    assert pending == before


def test_ticks_keep_amounts_until_they_misfit(pending):
    pending['split_menu']['counts'] = {}
    exact = parse_split_amounts(pending, 'Bob 90', 1)
    added = toggle_split_person(exact, 3)
    assert added['split_menu']['amounts'] == {2: 9000}
    assert [r['share_cents'] for r in added['split']] == [2250, 9000, 2250]
    assert added['split_warnings'] == ()
    removed = toggle_split_person(added, 2)
    assert removed['split_menu']['amounts'] == {}
    assert [r['share_cents'] for r in removed['split']] == [6750, 6750]
    assert removed['split_warnings'] == ()
    # Unticking the only unset person leaves nobody to take the remainder.
    misfit = toggle_split_person(exact, 1)
    assert 'amounts' not in misfit['split_menu']
    assert misfit['split_menu']['cleared_amounts'] == {2: 9000}
    assert AMOUNTS_CLEARED_WARNING in misfit['split_warnings']


def test_card_lines_unchanged_without_amounts(pending):
    assert bot_core._split_lines(pending) == (
        '👥 **Split by count between 2:**\n • Alice: 2, 90.00\n • Bob: 1, 45.00\n')
    pending['split_menu']['counts'] = {}
    pending['split'] = split_rows(pending, (1, 2))
    assert bot_core._split_lines(pending) == (
        '👥 **Split between 2:**\n • Alice: 67.50\n • Bob: 67.50\n')
    changed = parse_split_amounts(pending, 'Bob 90', 1)
    changed['split'][1]['name'] = 'Bob_Lee'
    assert ' • Bob\\_Lee: 90.00 ✏️' in bot_core._split_lines(changed)
    only_bob = {**changed, 'split': ({**changed['split'][1], 'share_cents': 13500},)}
    assert ' • Bob\\_Lee: 135.00 ✏️' in bot_core._split_lines(only_bob)


def test_tick_change_and_prompt_hints(pending):
    exact = parse_split_amounts(pending, 'Alice 45, Bob 90', 1)
    unchanged = _set_split(exact, (1, 2))
    assert unchanged['split_menu']['amounts'] == exact['split_menu']['amounts']
    assert unchanged['split_warnings'] == ()
    assert 'cleared_amounts' not in unchanged['split_menu']
    before = deepcopy(exact)
    changed = toggle_split_person(exact, 3)
    assert exact == before
    assert 'amounts' not in changed['split_menu']
    assert changed['split_menu']['cleared_amounts'] == {1: 4500, 2: 9000}
    assert AMOUNTS_CLEARED_WARNING in changed['split_warnings']
    assert AMOUNTS_CLEARED_WARNING in toggle_split_person(changed, 3)['split_warnings']
    text, placeholder = amounts_prompt(changed)
    assert text.startswith(AMOUNTS_PROMPT_LINE + '\nTotal 135.00.')
    assert 'Alice (was 45.00)\nBob (was 90.00)\nCarol' in text
    assert placeholder == 'Alice 50'
    assert 'Alice 45.00' in amounts_prompt(exact)[0]


def test_remove_split_drops_cleared_warning(pending):
    """After "Remove split" nobody is ticked, so "set them again" would point at nothing."""
    from app.services.splits import remove_split
    removed = remove_split(parse_split_amounts(pending, 'Alice 45, Bob rest', 1))
    assert removed['split'] == ()
    assert AMOUNTS_CLEARED_WARNING not in removed['split_warnings']
    assert removed['split_menu']['cleared_amounts'] == {1: 4500}


def test_success_clears_obsolete_warnings(pending):
    added = '⚠️ Bob added at 2 nights. Check.'
    pending['split_menu'].update(added_counts={2: added}, cleared_amounts={1: 1})
    pending['split_warnings'] = (UNEVEN_NOT_BUILT_WARNING, UNEVEN_EVEN_SPLIT_WARNING,
                                 COUNT_UNREAD_WARNING, COUNT_EVEN_SPLIT_WARNING,
                                 AMOUNTS_CLEARED_WARNING, added, 'Keep this')
    partial = parse_split_amounts(pending, 'Alice 45', 1)
    assert partial['split_warnings'] == (COUNT_UNREAD_WARNING, COUNT_EVEN_SPLIT_WARNING, added, 'Keep this')
    changed = parse_split_amounts(partial, 'Bob 90', 1)
    assert changed['split_warnings'] == ('Keep this',)
    assert 'cleared_amounts' not in changed['split_menu']


@pytest.mark.parametrize('text,matched', [
    ("Bob's split is 90", True), ("Bob's split was 90", True),
    ("Bob's split = 90", True), ('split of 3', False), ('split with Bob 90', False),
])
def test_fixed_share_wording(text, matched):
    assert bool(_FIXED_SHARE.search(text)) is matched


def _reply(context, text='me 45, Carol rest', prompt_id=600):
    """A real-looking reply envelope; bot edits are always async stubs."""
    context.bot = SimpleNamespace(id=99, edit_message_text=AsyncMock(), delete_message=AsyncMock())
    message = SimpleNamespace(text=text, chat_id=123, message_id=601,
                              reply_text=AsyncMock(return_value=SimpleNamespace(message_id=602)),
                              reply_to_message=SimpleNamespace(message_id=prompt_id,
                                  from_user=SimpleNamespace(id=99), text=AMOUNTS_PROMPT_LINE + '\nTotal 135.00.'))
    return SimpleNamespace(message=message)


@pytest.mark.parametrize('count', [0, 1, 2])
def test_amounts_button_only_with_two(pending, count):
    pending['split'] = split_rows(pending, range(1, count + 1))
    assert (['split:amounts'] in _data(bot_core._split_keyboard(pending))) == (count >= 2)


@pytest.mark.parametrize('count', [0, 1, 2])
def test_amounts_button_on_card_and_top_of_menu(pending, count):
    """Set amounts sits beside Change split on the card, and first in the menu (ADR-0048)."""
    pending['split'] = split_rows(pending, range(1, count + 1))
    on_card = ['split:menu', 'split:amounts'] in _data(bot_core._keyboard_for(pending))
    assert on_card == (count >= 2)
    first_row = _data(bot_core._split_keyboard(pending))[0]
    assert (first_row == ['split:amounts']) == (count >= 2)


async def test_tick_change_refreshes_open_prompt(monkeypatch, directory):
    """Ticking or unticking replaces the open prompt (a ForceReply message can't be edited);
    under two ticked, it goes."""
    context, entry, _ = await _card(monkeypatch, directory, WITH_CAROL, amount=135)
    sent = iter(range(700, 710))
    context.bot = SimpleNamespace(
        edit_message_text=AsyncMock(), delete_message=AsyncMock(),
        send_message=AsyncMock(side_effect=lambda **kw: SimpleNamespace(message_id=next(sent))))
    context.user_data.update(split_prompts={600: 500}, split_prompt_of_card={500: 600})

    update, query = _tap('split:p:2')  # Tick Bob: a fresh prompt lists him, the old one goes.
    await bot_core.handle_split_button(update, context)
    send = context.bot.send_message.await_args.kwargs
    assert '\nBob' in send['text'] and isinstance(send['reply_markup'], ForceReply)
    context.bot.delete_message.assert_awaited_with(chat_id=query.message.chat_id, message_id=600)
    context.bot.edit_message_text.assert_not_awaited()  # Telegram refuses that edit.
    assert context.user_data['split_prompt_of_card'] == {500: 700}
    assert context.user_data['split_prompts'] == {700: 500}

    update, query = _tap('split:p:2')  # Untick Bob: the next prompt drops him.
    await bot_core.handle_split_button(update, context)
    assert '\nBob' not in context.bot.send_message.await_args.kwargs['text']
    assert context.user_data['split_prompt_of_card'] == {500: 701}

    context.bot.send_message.side_effect = RuntimeError('offline')
    update, query = _tap('split:p:2')  # A failed send never undoes the tick or the old link.
    await bot_core.handle_split_button(update, context)
    assert 2 in {row['participant_id'] for row in entry['split']}
    assert context.user_data['split_prompt_of_card'] == {500: 701}
    query.answer.assert_awaited_once()

    update, query = _tap('split:none')  # Nobody ticked: nothing to set, so the prompt goes.
    await bot_core.handle_split_button(update, context)
    context.bot.delete_message.assert_awaited_with(chat_id=query.message.chat_id, message_id=701)
    assert context.user_data['split_prompts'] == {} and context.user_data['split_prompt_of_card'] == {}


@pytest.mark.parametrize('state', ['live', 'saved', 'cancelled', 'one', 'failed', 'delete_failed'])
async def test_prompt_tap(monkeypatch, directory, state):
    context, entry, _ = await _card(monkeypatch, directory, WITH_CAROL, amount=135)
    context.bot = SimpleNamespace(delete_message=AsyncMock())
    if state in ('live', 'delete_failed'):
        context.user_data.update(split_prompts={599: 500}, split_prompt_of_card={500: 599})
    if state == 'delete_failed':
        context.bot.delete_message.side_effect = RuntimeError('cannot delete')
    before = deepcopy(entry)
    update, query = _tap('split:amounts')
    query.message.reply_text = AsyncMock(return_value=SimpleNamespace(message_id=600))
    if state in ('saved', 'cancelled'):
        context.user_data['resolved_cards'] = {500: state}
    if state == 'one':
        entry['split'] = entry['split'][:1]
        before = deepcopy(entry)
    if state == 'failed':
        query.message.reply_text.side_effect = RuntimeError('offline')
    await bot_core.handle_split_button(update, context)
    query.answer.assert_awaited_once()
    query.edit_message_text.assert_not_awaited()
    assert entry == before
    if state in ('live', 'delete_failed'):
        markup = query.message.reply_text.await_args.kwargs['reply_markup']
        assert isinstance(markup, ForceReply) and markup.selective
        assert markup.input_field_placeholder == 'Alice 50'
        assert context.user_data['split_prompts'] == {600: 500}
        assert context.user_data['split_prompt_of_card'] == {500: 600}
        context.bot.delete_message.assert_awaited_once_with(chat_id=query.message.chat_id, message_id=599)
        query.answer.assert_awaited_once_with("Reply with a name and amount")
    else:
        assert not context.user_data.get('split_prompts')
        if state != 'failed':
            query.message.reply_text.assert_not_awaited()


@pytest.mark.parametrize('state', ['live', 'invalid', 'failed', 'gone', 'saved', 'replaced'])
async def test_reply_redraw_and_confirm(monkeypatch, directory, state):
    context, entry, _ = await _card(monkeypatch, directory, WITH_CAROL, amount=135)
    context.user_data['split_prompts'] = {600: 500}
    context.user_data['split_prompt_of_card'] = {500: 600}
    update = _reply(context, 'me 45, Carol 50' if state == 'invalid' else 'me 45, Carol rest')
    before = deepcopy(entry)
    if state == 'failed':
        context.bot.edit_message_text.side_effect = RuntimeError('offline')
    if state == 'gone':
        context.user_data['pending_cards'].clear()
    if state == 'saved':
        context.user_data['resolved_cards'] = {500: 'saved'}
    if state == 'replaced':
        async def replace(**kwargs):
            context.user_data['pending_cards'][500] = {'replacement': True}
        context.bot.edit_message_text.side_effect = replace
    assert await bot_core._split_amounts_reply(update, context, directory)
    assert context.bot.delete_message.await_args_list[-1].kwargs == {'chat_id': 123, 'message_id': 601}
    assert any(call.kwargs['message_id'] == 600 for call in context.bot.delete_message.await_args_list)
    if state != 'live':
        assert entry == before
        update.message.reply_text.assert_awaited_once()
        if state in ('invalid', 'gone', 'saved'):
            context.bot.edit_message_text.assert_not_awaited()
        if state in ('invalid', 'failed'):
            prompt = update.message.reply_text.await_args.args[0]
            assert prompt.startswith(AMOUNTS_PROMPT_LINE + '\n⚠️ ')
            assert context.user_data['split_prompts'] == {602: 500}
        return
    edit = context.bot.edit_message_text.await_args.kwargs
    assert edit['parse_mode'] == 'Markdown' and edit['message_id'] == 500
    assert _data(edit['reply_markup']) == _data(bot_core._keyboard_for(entry))
    assert 'Alice: 45.00' in edit['text'] and 'Carol: 90.00' in edit['text']
    assert '45.00 ✏️' in edit['text'] and "90.00 (what's left)" in edit['text']
    assert context.user_data['split_prompt_of_card'] == {500: 602}
    save, _ = await _confirm(monkeypatch, context)
    assert save.call_args.kwargs['splits'] == [(1, 4500), (3, 9000)]
    assert context.user_data['split_prompt_of_card'] == {}
    assert context.user_data['split_prompts'] == {}
    assert context.bot.delete_message.await_args.kwargs['message_id'] == 602


@pytest.mark.parametrize('own,prompt,extracted', [
    (True, True, False), (False, True, True), (True, False, True), (True, None, True),
])
async def test_expired_or_ordinary_reply(monkeypatch, directory, own, prompt, extracted):
    context = SimpleNamespace(user_data={})
    update = _reply(context)
    if not own:
        update.message.reply_to_message.from_user.id = 77
    if not prompt:
        update.message.reply_to_message.text = 'Something else'
    if prompt is None:
        update.message.reply_to_message = None
    monkeypatch.setattr(bot_core, 'authorize', AsyncMock(return_value=directory))
    extract = AsyncMock()
    monkeypatch.setattr(bot_core, 'process_expense_text', extract)

    async def answer(*args, **kwargs):
        # In a group chat the answer quotes the reply; Telegram refuses a deleted one.
        context.bot.delete_message.assert_not_awaited()
    update.message.reply_text.side_effect = answer
    await bot_core.handle_text(update, context)
    assert bool(extract.await_count) == extracted
    if not extracted:
        update.message.reply_text.assert_awaited_once_with('⚠️ This prompt has expired. Send the entry again.')
        assert [call.kwargs['message_id'] for call in context.bot.delete_message.await_args_list] == [601, 600]


@pytest.mark.parametrize('action', ['confirm_save', 'cancel_save', 'failed_save'])
@pytest.mark.parametrize('delete_fails', [False, True])
async def test_resolving_card_cleans_prompt_only_after_success(monkeypatch, directory, action, delete_fails):
    context, entry, _ = await _card(monkeypatch, directory, WITH_CAROL, amount=135)
    context.user_data.update(split_prompts={600: 500}, split_prompt_of_card={500: 600})
    context.bot = SimpleNamespace(delete_message=AsyncMock(
        side_effect=RuntimeError('cannot delete') if delete_fails else None))
    if action == 'cancel_save':
        update, _ = _tap(action)
        await bot_core.handle_button_click(update, context)
    else:
        await _confirm(monkeypatch, context, success=action != 'failed_save')
    if action == 'failed_save':
        assert context.user_data['pending_cards'][500] is entry
        assert context.user_data['split_prompts'] == {600: 500}
        assert context.user_data['split_prompt_of_card'] == {500: 600}
        context.bot.delete_message.assert_not_awaited()
    else:
        assert not context.user_data['split_prompts']
        assert not context.user_data['split_prompt_of_card']
        assert context.bot.delete_message.await_args.kwargs['message_id'] == 600


@pytest.mark.parametrize('failure', ['delete', 'send'])
async def test_reply_cleanup_failures_keep_card_state(monkeypatch, directory, failure):
    context, entry, _ = await _card(monkeypatch, directory, WITH_CAROL, amount=135)
    context.user_data.update(split_prompts={600: 500}, split_prompt_of_card={500: 600})
    update = _reply(context, 'Carol 90')
    if failure == 'delete':
        context.bot.delete_message.side_effect = RuntimeError('cannot delete')
    else:
        update.message.reply_text.side_effect = RuntimeError('cannot send')
    assert await bot_core._split_amounts_reply(update, context, directory)
    assert entry['split_menu']['amounts'] == {3: 9000}
    assert [r['share_cents'] for r in entry['split']] == [4500, 9000]
    # A failed send retains the old prompt as a route to retry; the redraw still stands.
    current = 600 if failure == 'send' else 602
    assert context.user_data['split_prompts'] == {current: 500}
    assert context.user_data['split_prompt_of_card'] == {500: current}
    save, _ = await _confirm(monkeypatch, context)
    assert save.call_args.kwargs['splits'] == [(1, 4500), (3, 9000)]


async def test_reply_order_and_lock(monkeypatch, directory):
    context, entry, _ = await _card(monkeypatch, directory, WITH_CAROL, amount=135)
    context.user_data.update(split_prompts={600: 500}, split_prompt_of_card={500: 600})
    update = _reply(context, 'Carol 90')
    events = []

    async def delete(**kwargs):
        assert bot_core._card_lock(context, 500).locked()
        events.append(('delete', kwargs['message_id']))

    async def redraw(**kwargs):
        assert 'amounts' not in entry['split_menu']
        events.append(('redraw', kwargs['message_id']))

    async def send(*args, **kwargs):
        assert entry['split_menu']['amounts'] == {3: 9000}
        assert isinstance(kwargs['reply_markup'], ForceReply)
        events.append(('send', 602))
        return SimpleNamespace(message_id=602)

    context.bot.delete_message.side_effect = delete
    context.bot.edit_message_text.side_effect = redraw
    update.message.reply_text.side_effect = send
    await bot_core._split_amounts_reply(update, context, directory)
    # The reply goes last: in a group chat the answer quotes it (bot_core._reply_deleted_last).
    assert events == [('redraw', 500), ('send', 602), ('delete', 600), ('delete', 601)]

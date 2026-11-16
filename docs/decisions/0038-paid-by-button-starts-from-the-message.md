# ADR-0038: A tapped payer starts from the account the message named, using the card's own directory snapshot

**Status:** Accepted
**Date:** 2026-11-16
**Plan:** `docs/superpowers/plans/2026-11-15-paid-by.md` (step 6)
**Code:** `app/services/participants.py::change_payer`, `::resolve_payment`, `app/bot_core.py::handle_payer_button`, `::_people_keyboard`, `::process_expense_text`

## Context

ADR-0037 has Python read the payer from the message. It misses some phrasings ("Carol covered
dinner") and some names (a voice transcript that misspells one), and until now the only fix was
Cancel and resend. The card needs a button to set the payer by hand.

The payer and the account must agree (ADR-0037), so changing the payer can change the account
too. Three things had to be decided: which account a tapped payer lands on, where a tap gets the
people and accounts from, and what happens when no account fits.

## Decision

- **The card gets a "Change who paid" button.** It swaps the card's buttons for a list of people,
  two to a row, with the current payer marked and a Back button. The people are the candidates
  the message was read against (the sender's trip members while a trip is on, otherwise the
  household), plus whoever is shown as paying. While the list is open there is no Confirm button.
- **A tapped payer follows the same rules as a named one, with one difference** (`change_payer`):
  where ADR-0037 lets an owned account decide the payer, here the tap decides, and the account
  moves instead. This refines ADR-0037; it doesn't replace it.
  - An outside payer goes on "Paid by someone else".
  - A household payer keeps the account the message named when it has no owner (Cash, the
    wallet) or is their own. Otherwise it is treated as if no account was named: the wallet for a
    foreign currency, then the payer's default. Neither is used if it is someone else's.
- **The rule starts from the account the message named, not the account on the card now.**
  `resolve_payment` records it as `base_account_id` on the pending entry (None when no account
  was named). So taps don't pile up: tapping Bob and then Alice gives back the card Alice started
  with, not Alice's default.
- **A tap never reads the database.** The directory `authorize()` loaded for the message is kept
  on the pending entry, with the list of people, and `change_payer` reads that snapshot.
- **Never guess an account.** A household payer with no usable default account (missing, or
  someone else's card), or an
  outside payer with no open "Paid by someone else" account, gets an alert and the card stays as
  it was.
- **A tap is a deliberate choice**, so the card's payer warning goes, even when the person tapped
  is the one already shown.
- **No button on a YouTrip top-up.** Its payer is the owner of the wallet's funding card.
- **The closed-card warning is worked out when the card is drawn**, not once when the entry is
  logged, because a tap can move the entry onto or off a closed card.
- **Taps on one card run one at a time.** Confirm, Cancel, the trip toggle and the payer menu
  all run inside a lock for that card (`_card_lock`). The webhook handles updates concurrently,
  so without it a Confirm landing while a payer change was still redrawing the card would save
  the old payer under a card showing the new one. The trip toggle had the same gap. The lock
  lives in process memory, like the pending entries (ADR-0023).
- The menu builder (`_people_keyboard`) takes its callback prefix and marked people as arguments,
  so the "Add split" menu can use it.

## Alternatives considered

- **Start from the account the card shows now.** Rejected: after tapping Bob the card is on Bob's
  default, and tapping Alice again would land on Alice's default, not the card her message named.
  The result would depend on the order of taps.
- **Copy only ids and names onto the pending entry**, as the plan first said. Rejected: the rule
  needs each person's default account, each account's owner, the wallet and the outside account.
  That is most of the directory, and a second copy of its lookups. The snapshot is already in
  memory and frozen.
- **Reload the directory on a tap.** Rejected: a tap must not depend on the database
  (ADR-0023), and the card would then show accounts from two different moments.
- **Owner wins on a tap**, as it does for a named payer (ADR-0037 rule 3). Rejected: the tap would
  do nothing whenever the message named someone else's card, with no way to fix the payer.

## Consequences

- A wrong or missing payer is fixed in two taps, with no resend.
- Each pending card holds a reference to one directory snapshot. At most 20 cards are kept per
  person, and the snapshots are small.
- A card edited in the accounts table after the message was sent isn't seen by a tap on that
  card. The entry is saved with the ids the card showed, as before.
- A household member who doesn't use the bot needs a default account in the seed file before they
  can be tapped as payer for an entry that names no account.
- Tapping a new payer on the "Database Error" card redraws it as a normal confirm card, as the
  trip toggle already does.

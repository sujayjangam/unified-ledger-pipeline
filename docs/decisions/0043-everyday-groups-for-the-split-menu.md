# ADR-0043: Everyday groups for the split menu, and no relationships between people

**Status:** Accepted, not built yet. Parked 2026-11-17 for the trip's essentials; build it to this record.
**Date:** 2026-11-17
**Plan:** `docs/superpowers/plans/2026-11-16-splits.md` (PR 2b)
**Code:** `alembic/versions/0009_add_participant_groups.py`, `app/services/participants.py::load_directory`, `app/services/splits.py`, `app/bot_core.py::_split_keyboard`, `scripts/seed_directory.py`

## Context

ADR-0041 gave the split menu family rows, but only on a card tagged to a trip: a family is a
label on `trip_members`. With no trip on, the menu is a flat list of every participant, and
sharing an everyday bill with one family means a tap per person.

Two wider questions came up while scoping it (2026-11-17):

- The list will grow past families: friends from work, a partner's friends. A family-only
  grouping would leave them as one long tail.
- Should the ledger store how people are related (a spouse, a friend of whom), the way a bank
  keeps a relationship table between customers?

## Decision

- **A person belongs to at most one everyday group.** Groups are rows in `participant_groups`;
  `participants.group_id` points at one, and NULL means no group. A group is any set of people
  the sender thinks of together ("Family", "Work friends"), not only a family.
- **The group shows only on a card that isn't tagged to a trip.** The split menu gets a toggle
  row per group, with its members under it, and the card's share lines are headed by group. A
  tagged card uses the trip's families and never an everyday group.
- **Order.** The sender's own group first, then the other groups A-Z, people A-Z inside a group.
  People with no group come last under no heading, in ADR-0041's order (household first, then
  A-Z). A database with no groups looks exactly as it did before.
- **Labels follow the card as it is now.** After "Not trip" the headings are everyday groups;
  tagged again, trip families. A group button sends `split:g:<index>` and a trip family
  `split:f:<index>`, so a stale button from the other list is refused.
- **Reading the text does not change.** With no trip on, "Carol's family" still means Carol
  alone, and "everyone" still warns. The first-opening tick-all stays trip-only, and the payer
  menu stays a flat list.
- **Relationships between people are not stored.**
- **Categories are a second, later step.** A group may belong to one category ("Direct
  family", "Extended family"); a group with none stands alone. A category only orders and heads
  groups; the group stays the unit that is ticked and that settles together. Stored as a
  nullable column on `participant_groups`, added by its own additive migration, together with
  collapsed groups. Not part of the first build.
- **The seed file keeps people and groups in separate sections**, so every list holds one kind
  of name:

  ```json
  "groups": {
    "Alice's family": ["Alice", "Bob"],
    "Carol's family": ["Carol", "Dave"],
    "Workplace 1 friends": ["Erin", "Frank"]
  },
  "group_categories": {
    "Direct family": ["Alice's family"],
    "Extended family": ["Carol's family"]
  }
  ```

  `groups` always lists people and is the first build. `group_categories` always lists groups
  and comes with the categories step; adding it changes nothing in `groups`.
- **Nothing about a group is stored on an entry.** The shares in `transaction_splits` are the
  record; the group is only how the menu and the card are laid out.

## Alternatives considered

- **A text label on `participants`**, like `trip_members.family`. Simpler, but a typo makes a
  second group, and renaming a group means updating every member. A trip's labels are typed once
  in one seed entry; everyday groups will be edited for years.
- **A person in several groups.** Splitwise allows it, but its groups are ledgers an expense
  belongs to, which is what a trip is here. In a menu, a person in two groups appears twice and
  a group's tick-all becomes unclear. One group is enough to order a menu.
- **A relationship table.** Splitwise's API has no relationship field on a friend ("couple" is
  only a kind of group), and the budget apps looked at (YNAB, Actual Budget, Tiller) report
  shared spending from who shares each entry. Every report planned here needs who paid, who
  shared and a group label. Relationships also go out of date, and a wrong link is harder to
  notice than a wrong label.
- **Expanding "Carol's family" with no trip on.** Carol's group may be "Work friends", and
  charging all of them would be a wrong split that looks right. Naming a group in the text
  ("split with work friends") is left for later.
- **One nested list**, `"Extended family": ["Carol's family"]` beside
  `"Workplace friends": ["Erin", "Frank"]`. The same list would hold groups in one entry and
  people in another, and a person and a group with the same name would be read wrongly with no
  error.
- **Categories in the first build.** With 10 people in about 4 groups a category level adds
  menu rows and saves no taps. Open for the categories step: whether a category row ticks
  everyone in it or is only a heading.
- **Collapsed groups**, opened one at a time, for a long list. Production has 10 participants,
  a five-row menu. Left until the list needs it (about 20 people).

## Consequences

- Sharing an everyday bill with a whole group is one tap.
- ADR-0041's "with no trip on there are no families in the menu" no longer holds; its order
  for people with no group does.
- Typing and tapping still differ with no trip on: a group can be ticked but not named in the
  message.
- The menu gets one more row per group. With 30 people it will be long; collapsing is the
  planned answer.
- People already in the database get a group from a seed rerun, which fills only an empty
  group. Moving someone to another group is a hand `UPDATE`.
- The split menu's state gains a second list of groups, and every reader must pick the list
  from the card's trip tag through one helper, or a button index points at the wrong people.

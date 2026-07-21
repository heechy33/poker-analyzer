# P1.10 canonical HUNL decision state

This document traces the canonical `hunl-decision-state/1` for the approved,
anonymized user-owned CoinPoker hand in
`backend/tests/fixtures/p1_10/real_hunl_flop_decision.txt`. It is hand
`93840400001`: true two-seat HUNL, `$0.02/$0.05`, 100 BB effective, a 2.4 BB
BTN/SB open followed by a BB call, and a hero flop decision. The canonical
wire payload is checked in at
`contracts/hunl-decision-state-v1.real-hunl-flop.json`.

The target action is Hero's flop bet (`event_index: 5`). The state is the
instant immediately before that action. It is not a solver input authorization,
solver result, or grade. Eligibility is checked separately and is `supported`
only for this narrowly configured cohort.

| Decision-state field | Canonical value | Provenance and derivation |
|---|---|---|
| `schema_version` | `hunl-decision-state/1` | Versioned decision-state policy in `backend/app/ledger/decision_state.py`. |
| `raw_hand_id` | `93840400001` | Raw header: `CoinPoker Hand #93840400001`. |
| `played_at` | `2026-07-21T00:19:28-07:00` | Raw header timestamp, normalized from PDT. |
| `game` | `NLHE` | Raw `NLH` game marker, normalized by the parser. |
| `table_marker` | `200601` | Raw table line: `Table '200601'`. |
| `table_format` | `hu_2max` | Raw `2-max` marker, normalized by table-format policy; never inferred from players left. |
| `button_seat` | `2` | Raw table line: `Seat #2 is the button`. |
| `small_blind` | `0.0200` | Raw header stakes and seat-2 `posts small blind` line. |
| `big_blind` | `0.0500` | Raw header stakes and Hero `posts big blind` line. |
| `action_event_index` | `5` | Canonical ordered ledger: flop bet is event 5. |
| `action_street_event_index` | `0` | Canonical street-local ledger index for the first flop decision. |
| `street` | `flop` | Ledger prefix at the raw `*** FLOP *** [As 5d 2d]` transition. |
| `players` | seats 1 Hero / 2 `88e1f1df` | Raw seat and dealt lines; positions come from button/blind reduction; opponent cards are excluded. |
| `hero_seat` | `1` | User-scoped Hero identity matched to the raw `Dealt to Hero` line. |
| `hero_position` | `BB` | Reducer derives it from Hero's raw big-blind post. |
| `hero_combo` | `Ac`, `Ts` | Raw `Dealt to Hero [Ac Ts]` line. |
| `active_seats` | `1`, `2` | Ledger snapshot immediately before event 5; neither player had folded or gone all-in. |
| `folded_seats` | empty | Ledger snapshot immediately before event 5; the later fold is outside the prefix. |
| `all_in_seats` | empty | Ledger snapshot immediately before event 5. |
| `players_reached_flop` | `1`, `2` | Reducer captures both active seats at the flop transition and never derives this from later streets. |
| `player_states` | each has `0` flop contribution, `0.1200` total contribution, `4.8800` stack | Reducer applies only raw blind, raise-to `0.12`, and call `0.07` prefix actions. |
| `amount_to_call` | `0` | Reducer snapshot after the preflop call and before any flop bet. |
| `last_full_raise` | `0.0500` | Reducer state at the new flop street. |
| `legal_raise_bounds` | min `0.0500`, max `4.8800`, reopened | Reducer legal-raise state using the prefix stacks and blind quantum. |
| `legal_actions` | `check`, `bet` | Versioned `_legal_actions` policy derives this from zero call amount and the legal raise bounds. |
| `player_contributed_pot` | `0.2400` | Reducer sum of prefix player contributions: `0.12 + 0.12`; it does not use the later total-pot summary. |
| `board_prefix` | `As`, `5d`, `2d` | Raw flop line, retained as the first-run board prefix only. |
| `rake_schedule_id` | `coinpoker-hu-nlhe-0.02-0.05-observed-2026-07-20/1` | `resolve_rake_schedule` selects the immutable P1.9 row from played-at date, stakes, game, table format, and two dealt players. The later summary `Rake ₮0.01` is only a reconciliation observation. |

The raw suffix after the decision — villain's fold, Hero's returned uncalled
bet, collection, total-pot/rake summary, and shown cards — is intentionally
absent from the canonical payload. P1.7's suffix-mutation tests and P1.11's
shared wire test prevent those outcome facts from becoming decision inputs.

## Why this shape

It makes the review state replayable and auditable while separating decision
facts from whole-hand eligibility and settlement facts. The alternate design
was to serialize the complete parsed hand or final ledger with each decision.
That would be simpler to display, but it would expose future board, action,
and outcome information to any future solver or grading consumer, violating
the decision-time boundary. The chosen payload keeps only the ledger prefix
and resolves rake from immutable policy facts, so an observed final rake cannot
silently influence a decision.

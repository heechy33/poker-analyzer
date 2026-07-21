from __future__ import annotations

import logging
import re
from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import delete, or_
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.ledger.models import CanonicalLedgerV1, LEDGER_SCHEMA_V1
from app.ledger.parsed import ParsedLedgerError, ledger_from_parsed
from app.models import Hand, HandAction, HandLedger, HandPlayer, Session, Upload
from app.parser.coinpoker import ParseError, parse_hand
from app.parser.models import ParsedHand

logger = logging.getLogger(__name__)

SESSION_GAP = timedelta(hours=2)
STREET_ORDER = {"preflop": 0, "flop": 1, "turn": 2, "river": 3, "showdown": 4}


def _uuid(value: str | UUID) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


def _json_safe(value: Any) -> Any:
    """Coerce values for PostgreSQL JSONB (asyncpg rejects Decimal, etc.)."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    return value


def hand_from_parsed(
    parsed: ParsedHand,
    user_id: UUID,
    upload_id: UUID,
    ledger: CanonicalLedgerV1 | None = None,
    ledger_error: str | None = None,
) -> Hand:
    hero_invested, hero_collected, hero_net, hero_net_bb = _financial_summary(
        parsed, ledger
    )
    flags = _json_safe(parsed.flags)
    if ledger_error is not None:
        flags = {**flags, "invalid_ledger": True}
    return Hand(
        user_id=user_id,
        upload_id=upload_id,
        coinpoker_hand_id=parsed.coinpoker_hand_id,
        played_at=parsed.played_at,
        table_name=parsed.table_name,
        table_size=parsed.table_size,
        stake_sb=parsed.stake_sb,
        stake_bb=parsed.stake_bb,
        button_seat=parsed.button_seat,
        hero_seat=parsed.hero_seat,
        hero_position=parsed.hero_position,
        hero_cards=parsed.hero_cards,
        flop=parsed.flop,
        turn=parsed.turn,
        river=parsed.river,
        total_pot=parsed.total_pot,
        rake=_observed_rake(parsed, ledger),
        splash_fee=_splash_fee(parsed, ledger),
        hero_invested=hero_invested,
        hero_collected=hero_collected,
        hero_net=hero_net,
        hero_net_bb=hero_net_bb,
        went_to_showdown=parsed.went_to_showdown,
        won_at_showdown=parsed.won_at_showdown,
        flags=flags,
        ledger_status="valid" if ledger is not None else "invalid_ledger" if ledger_error else "legacy_unbackfilled",
        ledger_version=LEDGER_SCHEMA_V1 if ledger is not None else None,
        ledger_hash=ledger.ledger_hash if ledger is not None else None,
        raw_text=parsed.raw_text,
    )


def players_from_parsed(
    parsed: ParsedHand, hand_id: UUID, user_id: UUID
) -> list[HandPlayer]:
    return [
        HandPlayer(
            hand_id=hand_id,
            user_id=user_id,
            seat=player.seat,
            screen_name=player.screen_name,
            position=player.position,
            starting_stack=player.starting_stack,
            is_hero=player.is_hero,
            final_cards=player.final_cards,
        )
        for player in parsed.players
    ]


def actions_from_parsed(
    parsed: ParsedHand, hand_id: UUID, user_id: UUID
) -> list[HandAction]:
    return [
        HandAction(
            hand_id=hand_id,
            user_id=user_id,
            street=action.street,
            action_order=action.action_order,
            seat=action.seat,
            screen_name=action.screen_name,
            action=action.action,
            amount=action.amount,
            raise_to=action.raise_to,
            is_all_in=action.is_all_in,
        )
        for action in parsed.actions
    ]


def actions_from_ledger(
    ledger: CanonicalLedgerV1, hand_id: UUID, user_id: UUID
) -> list[HandAction]:
    """Build the temporary replay/stats projection from canonical events only."""
    aliases = {player.seat: player.alias for player in ledger.hand.players}
    rows: list[HandAction] = []
    for event in ledger.events:
        if event.actor_seat is None or event.verb not in _PROJECTED_LEDGER_VERBS:
            continue
        rows.append(
            HandAction(
                hand_id=hand_id,
                user_id=user_id,
                street=event.street,
                action_order=event.street_event_index or 0,
                seat=event.actor_seat,
                screen_name=aliases[event.actor_seat],
                action=_PROJECTED_LEDGER_VERBS[event.verb],
                amount=event.action_amount,
                raise_to=event.raise_to,
                is_all_in=event.is_all_in,
                ledger_event_index=event.event_index,
                contribution_delta=event.contribution_delta,
                returned_delta=event.returned_delta,
                raise_increment=event.raise_increment,
            )
        )
    return rows


def ledger_record_from_result(
    *,
    hand_id: UUID,
    user_id: UUID,
    ledger: CanonicalLedgerV1 | None,
    failure_reason: str | None = None,
    summary_diff: dict[str, Any] | None = None,
) -> HandLedger:
    if ledger is not None:
        return HandLedger(
            hand_id=hand_id,
            user_id=user_id,
            status="valid",
            schema_version=ledger.schema_version,
            ledger_hash=ledger.ledger_hash,
            payload=ledger.model_dump(mode="json"),
            summary_diff=summary_diff or {},
        )
    assert failure_reason is not None
    return HandLedger(
        hand_id=hand_id,
        user_id=user_id,
        status="invalid_ledger",
        summary_diff=summary_diff or {},
        failure_reason=failure_reason,
    )


_PROJECTED_LEDGER_VERBS = {
    "post_small_blind": "post_sb",
    "post_big_blind": "post_bb",
    "post_ante": "post_ante",
    "post_dead_blind": "post_dead_blind",
    "post_straddle": "post_straddle",
    "fold": "fold",
    "check": "check",
    "call": "call",
    "bet": "bet",
    "raise": "raise",
    "return_uncalled": "return_uncalled",
    "collect": "collect",
}


def _financial_summary(
    parsed: ParsedHand, ledger: CanonicalLedgerV1 | None
) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    if ledger is None or not ledger.events:
        return (
            parsed.hero_invested,
            parsed.hero_collected,
            parsed.hero_net,
            parsed.hero_net_bb,
        )
    final_players = {state.seat: state for state in ledger.events[-1].state_after.player_states}
    hero_invested = final_players[parsed.hero_seat].total_contribution
    hero_collected = sum(
        (
            event.award_amount
            for event in ledger.events
            if event.verb == "collect" and event.actor_seat == parsed.hero_seat
        ),
        Decimal("0"),
    )
    hero_net = hero_collected - hero_invested
    hero_net_bb = hero_net / parsed.stake_bb
    return hero_invested, hero_collected, hero_net, hero_net_bb


def _observed_rake(parsed: ParsedHand, ledger: CanonicalLedgerV1 | None) -> Decimal:
    if ledger is None or not ledger.events:
        return parsed.rake
    return ledger.events[-1].state_after.fee_metadata.observed_rake


def _splash_fee(parsed: ParsedHand, ledger: CanonicalLedgerV1 | None) -> Decimal:
    if ledger is None or not ledger.events:
        return parsed.splash_fee
    return ledger.events[-1].state_after.fee_metadata.splash_fee


@dataclass(frozen=True, slots=True)
class LedgerBackfillResult:
    """Outcome counts for an idempotent raw-history ledger backfill."""

    scanned: int = 0
    valid: int = 0
    invalid: int = 0
    unchanged: int = 0


async def backfill_canonical_ledgers(
    db: AsyncSession,
    *,
    user_id: UUID | None = None,
    limit: int | None = None,
) -> LedgerBackfillResult:
    """Reparse legacy raw text and replace legacy action rows with a projection.

    A current valid ledger is left untouched.  The operation is therefore safe
    to repeat after interruption and never reconstructs accounting from the
    old ``hand_actions`` rows.
    """
    needs_backfill = or_(
        Hand.ledger_status != "valid",
        Hand.ledger_version != LEDGER_SCHEMA_V1,
        Hand.ledger_hash.is_(None),
    )
    stmt = select(Hand).where(needs_backfill).order_by(Hand.played_at, Hand.id)
    if user_id is not None:
        stmt = stmt.where(Hand.user_id == user_id)
    if limit is not None:
        if limit < 1:
            raise ValueError("limit must be positive")
        stmt = stmt.limit(limit)

    hands = list((await db.exec(stmt)).all())
    valid = invalid = 0
    for hand in hands:
        parsed, ledger, failure_reason = _backfill_ledger_input(hand)
        if ledger is None:
            await _persist_invalid_ledger(
                db,
                hand,
                failure_reason or "canonical ledger reconstruction failed",
            )
            invalid += 1
            continue

        assert parsed is not None
        summary_diff = _summary_diff(hand, parsed, ledger)
        _apply_canonical_hand_values(hand, parsed, ledger)
        await _replace_ledger_projection(db, hand, ledger)
        await _upsert_ledger_record(
            db,
            ledger_record_from_result(
                hand_id=hand.id,
                user_id=hand.user_id,
                ledger=ledger,
                summary_diff=summary_diff,
            ),
        )
        valid += 1

    await db.flush()
    return LedgerBackfillResult(scanned=len(hands), valid=valid, invalid=invalid)


def _backfill_ledger_input(
    hand: Hand,
) -> tuple[ParsedHand | None, CanonicalLedgerV1 | None, str | None]:
    if not hand.raw_text:
        return None, None, "raw hand history is missing"
    try:
        parsed = parse_hand(hand.raw_text.splitlines())
    except ParseError as exc:
        return None, None, f"raw hand history no longer parses: {exc}"
    if parsed.coinpoker_hand_id != hand.coinpoker_hand_id:
        return None, None, "raw hand id does not match the stored hand id"
    try:
        return parsed, ledger_from_parsed(parsed), None
    except (ParsedLedgerError, ValueError) as exc:
        return parsed, None, str(exc)


async def _persist_invalid_ledger(
    db: AsyncSession,
    hand: Hand,
    failure_reason: str,
) -> None:
    hand.ledger_status = "invalid_ledger"
    hand.ledger_version = None
    hand.ledger_hash = None
    hand.flags = {**(hand.flags or {}), "invalid_ledger": True}
    await _upsert_ledger_record(
        db,
        ledger_record_from_result(
            hand_id=hand.id,
            user_id=hand.user_id,
            ledger=None,
            failure_reason=failure_reason,
        ),
    )


async def _replace_ledger_projection(
    db: AsyncSession,
    hand: Hand,
    ledger: CanonicalLedgerV1,
) -> None:
    await db.exec(delete(HandAction).where(HandAction.hand_id == hand.id))
    for action in actions_from_ledger(ledger, hand.id, hand.user_id):
        db.add(action)


async def _upsert_ledger_record(db: AsyncSession, replacement: HandLedger) -> None:
    existing = await db.get(HandLedger, replacement.hand_id)
    if existing is None:
        db.add(replacement)
        return
    existing.status = replacement.status
    existing.schema_version = replacement.schema_version
    existing.ledger_hash = replacement.ledger_hash
    existing.payload = replacement.payload
    existing.summary_diff = replacement.summary_diff
    existing.failure_reason = replacement.failure_reason
    db.add(existing)


def _apply_canonical_hand_values(
    hand: Hand,
    parsed: ParsedHand,
    ledger: CanonicalLedgerV1,
) -> None:
    canonical = hand_from_parsed(parsed, hand.user_id, hand.upload_id, ledger=ledger)
    for field in _CANONICAL_HAND_FIELDS:
        setattr(hand, field, getattr(canonical, field))
    hand.flags = {
        key: value
        for key, value in {**(hand.flags or {}), **(canonical.flags or {})}.items()
        if key != "invalid_ledger"
    }


_CANONICAL_HAND_FIELDS = (
    "coinpoker_hand_id",
    "played_at",
    "table_name",
    "table_size",
    "stake_sb",
    "stake_bb",
    "button_seat",
    "hero_seat",
    "hero_position",
    "hero_cards",
    "flop",
    "turn",
    "river",
    "total_pot",
    "rake",
    "splash_fee",
    "hero_invested",
    "hero_collected",
    "hero_net",
    "hero_net_bb",
    "went_to_showdown",
    "won_at_showdown",
    "ledger_status",
    "ledger_version",
    "ledger_hash",
    "raw_text",
)
_SUMMARY_FIELDS = (
    "total_pot",
    "rake",
    "splash_fee",
    "hero_invested",
    "hero_collected",
    "hero_net",
    "hero_net_bb",
)


def _summary_diff(
    hand: Hand,
    parsed: ParsedHand,
    ledger: CanonicalLedgerV1,
) -> dict[str, dict[str, str]]:
    canonical = hand_from_parsed(parsed, hand.user_id, hand.upload_id, ledger=ledger)
    return {
        field: {"stored": str(getattr(hand, field)), "canonical": str(getattr(canonical, field))}
        for field in _SUMMARY_FIELDS
        if getattr(hand, field) != getattr(canonical, field)
    }


async def existing_coinpoker_ids(
    session: AsyncSession, user_id: UUID, hand_ids: Iterable[int]
) -> set[int]:
    ids = list(hand_ids)
    if not ids:
        return set()
    result = await session.exec(
        select(Hand.coinpoker_hand_id).where(
            Hand.user_id == user_id,
            Hand.coinpoker_hand_id.in_(ids),
        )
    )
    return set(result.all())


def cluster_hands(hands: list[Hand]) -> list[list[Hand]]:
    by_stake: dict[tuple[Decimal, int], list[Hand]] = defaultdict(list)
    for hand in hands:
        by_stake[(hand.stake_bb, hand.table_size)].append(hand)

    clusters: list[list[Hand]] = []
    for group in by_stake.values():
        group.sort(key=lambda hand: hand.played_at)
        current: list[Hand] = []
        for hand in group:
            if current and hand.played_at - current[-1].played_at > SESSION_GAP:
                clusters.append(current)
                current = []
            current.append(hand)
        if current:
            clusters.append(current)
    return clusters


async def find_mergeable_session(
    session: AsyncSession,
    user_id: UUID,
    stake_bb: Decimal,
    table_size: int,
    started_at: datetime,
    ended_at: datetime,
) -> Session | None:
    result = await session.exec(
        select(Session).where(
            Session.user_id == user_id,
            Session.stake_bb == stake_bb,
            Session.table_size == table_size,
            Session.ended_at >= started_at - SESSION_GAP,
            Session.started_at <= ended_at + SESSION_GAP,
        )
    )
    matches = result.all()
    if not matches:
        return None
    return min(matches, key=lambda row: row.started_at)


async def assign_sessions(
    db: AsyncSession, user_id: UUID, inserted_hands: list[Hand]
) -> None:
    for cluster in cluster_hands(inserted_hands):
        if not cluster:
            continue

        started_at = cluster[0].played_at
        ended_at = cluster[-1].played_at
        stake_bb = cluster[0].stake_bb
        table_size = cluster[0].table_size

        db_session = await find_mergeable_session(
            db, user_id, stake_bb, table_size, started_at, ended_at
        )
        if db_session is None:
            db_session = Session(
                user_id=user_id,
                started_at=started_at,
                ended_at=ended_at,
                stake_bb=stake_bb,
                table_size=table_size,
            )
            db.add(db_session)
            await db.flush()
        else:
            db_session.started_at = min(db_session.started_at, started_at)
            db_session.ended_at = max(db_session.ended_at, ended_at)

        cluster_net = sum((hand.hero_net for hand in cluster), Decimal("0"))
        cluster_net_bb = sum((hand.hero_net_bb for hand in cluster), Decimal("0"))
        db_session.hands_played += len(cluster)
        db_session.hero_net += cluster_net
        db_session.hero_net_bb += cluster_net_bb

        for hand in cluster:
            hand.session_id = db_session.id
            db.add(hand)

        db.add(db_session)


async def ingest_parsed_hands(
    db: AsyncSession,
    user_id: UUID,
    upload_id: UUID,
    parsed_hands: Iterator[ParsedHand],
) -> tuple[int, int]:
    """Insert parsed hands and children. Returns (inserted_count, skipped_duplicates)."""
    user_uuid = _uuid(user_id)
    upload_uuid = _uuid(upload_id)

    parsed_list = list(parsed_hands)
    if not parsed_list:
        return 0, 0

    existing = await existing_coinpoker_ids(
        db,
        user_uuid,
        [hand.coinpoker_hand_id for hand in parsed_list],
    )

    inserted_hands: list[Hand] = []
    skipped = 0

    for parsed in parsed_list:
        if parsed.coinpoker_hand_id in existing:
            skipped += 1
            continue

        try:
            ledger = ledger_from_parsed(parsed)
            ledger_error = None
        except (ParsedLedgerError, ValueError) as exc:
            ledger = None
            ledger_error = str(exc)
            logger.warning("hand %s has invalid canonical ledger: %s", parsed.coinpoker_hand_id, exc)

        hand_row = hand_from_parsed(
            parsed,
            user_uuid,
            upload_uuid,
            ledger=ledger,
            ledger_error=ledger_error,
        )
        db.add(hand_row)
        await db.flush()

        for player in players_from_parsed(parsed, hand_row.id, user_uuid):
            db.add(player)
        action_rows = (
            actions_from_ledger(ledger, hand_row.id, user_uuid)
            if ledger is not None
            else actions_from_parsed(parsed, hand_row.id, user_uuid)
        )
        for action in action_rows:
            db.add(action)
        db.add(
            ledger_record_from_result(
                hand_id=hand_row.id,
                user_id=user_uuid,
                ledger=ledger,
                failure_reason=ledger_error,
            )
        )

        inserted_hands.append(hand_row)
        existing.add(parsed.coinpoker_hand_id)

    if inserted_hands:
        await assign_sessions(db, user_uuid, inserted_hands)

    return len(inserted_hands), skipped


_HEADER_RE = re.compile(
    r"^CoinPoker Hand #(?P<hand_id>\d+):"
)


def _split_hand_blocks(text: str) -> list[list[str]]:
    """Split hand history text into per-hand line blocks.

    Replicates the header-detection logic from ``parse_hands()`` so we can
    call ``parse_hand()`` on each block with per-block error isolation.
    """
    blocks: list[list[str]] = []
    current: list[str] = []

    for line in text.splitlines(keepends=False):
        stripped = line.strip()
        if _HEADER_RE.match(stripped):
            if current:
                blocks.append(current)
            current = [line]
        elif current:
            current.append(line)

    if current:
        blocks.append(current)

    return blocks


def _build_parse_summary(
    inserted: int, skipped_duplicates: int, parse_errors: list[str]
) -> str | None:
    """Build a human-readable parse warnings string or None if clean."""
    parts: list[str] = []
    if parse_errors:
        parts.append(f"{len(parse_errors)} parse error{'s' if len(parse_errors) != 1 else ''}")
    if skipped_duplicates:
        parts.append(f"{skipped_duplicates} duplicate{'s' if skipped_duplicates != 1 else ''} skipped")
    if not parts:
        return None
    return f"Imported {inserted} hand{'s' if inserted != 1 else ''} ({', '.join(parts)})"


async def ingest_upload_content(
    db: AsyncSession,
    user_id: UUID,
    upload_id: UUID,
    content: str | bytes,
) -> int:
    user_uuid = _uuid(user_id)
    upload_uuid = _uuid(upload_id)

    if isinstance(content, bytes):
        text = content.decode("utf-8")
    else:
        text = content

    blocks = _split_hand_blocks(text)
    if not blocks:
        upload = await db.get(Upload, upload_uuid)
        if upload is not None:
            upload.error_message = "No hands found in file"
            upload.parse_warnings = None
            db.add(upload)
            await db.flush()
        return 0

    parsed_list: list[ParsedHand] = []
    parse_errors: list[str] = []

    for block in blocks:
        try:
            parsed_list.append(parse_hand(block))
        except ParseError as exc:
            msg = str(exc)
            logger.warning("skipping hand due to parse error: %s", msg)
            parse_errors.append(msg)

    inserted, skipped_duplicates = await ingest_parsed_hands(
        db,
        user_uuid,
        upload_uuid,
        iter(parsed_list),
    )

    # Persist parse summary on the Upload row.
    upload = await db.get(Upload, upload_uuid)
    if upload is not None:
        upload.error_message = None
        upload.parse_warnings = _build_parse_summary(
            inserted, skipped_duplicates, parse_errors
        )
        if not parsed_list and parse_errors:
            # All blocks failed to parse — still "parsed" but with zero inserted
            # and a descriptive error so the frontend can surface it.
            upload.error_message = parse_errors[0][:2000]
        # On re-upload, report how many hands were recognized even if all dupes.
        upload.hand_count = inserted if inserted > 0 else skipped_duplicates
        db.add(upload)
        await db.flush()

    return inserted


async def run_upload_ingest(
    db: AsyncSession,
    upload_id: UUID,
    content: str | bytes | None = None,
) -> None:
    upload = await db.get(Upload, upload_id)
    if upload is None:
        logger.error("upload %s not found for ingest", upload_id)
        return

    # Capture scalars before commit; expire_on_commit clears the instance.
    user_uuid = _uuid(upload.user_id)
    upload_uuid = _uuid(upload.id)
    storage_path = upload.storage_path

    upload.status = "parsing"
    upload.error_message = None
    upload.parse_warnings = None
    db.add(upload)
    await db.commit()

    try:
        if content is None:
            from app.services.storage import download_storage_object

            content = download_storage_object(storage_path)

        hand_count = await ingest_upload_content(db, user_uuid, upload_uuid, content)

        upload = await db.get(Upload, upload_uuid)
        if upload is None:
            logger.error("upload %s not found after ingest", upload_uuid)
            return

        # ingest_upload_content sets error_message for hard failures and may set
        # hand_count when everything was a duplicate re-upload.
        if upload.error_message:
            upload.status = "error"
        else:
            upload.status = "parsed"
        if upload.hand_count is None:
            upload.hand_count = hand_count
    except Exception as exc:
        logger.exception("failed to ingest upload %s", upload_id)
        await db.rollback()
        upload = await db.get(Upload, upload_id)
        if upload is not None:
            upload.status = "error"
            upload.error_message = str(exc)[:2000]
            db.add(upload)
            await db.commit()
        return

    db.add(upload)
    await db.commit()


def sort_actions(actions: list[HandAction]) -> list[HandAction]:
    return sorted(
        actions,
        key=lambda action: (
            STREET_ORDER.get(action.street, 99),
            action.action_order,
            action.id or 0,
        ),
    )


def parsed_to_summary_dict(parsed: ParsedHand) -> dict[str, Any]:
    """Expose mapping fields for unit tests without a database."""
    hand = hand_from_parsed(
        parsed,
        UUID("00000000-0000-0000-0000-000000000001"),
        UUID("00000000-0000-0000-0000-000000000002"),
    )
    return {
        "coinpoker_hand_id": hand.coinpoker_hand_id,
        "hero_net": hand.hero_net,
        "hero_net_bb": hand.hero_net_bb,
        "hero_cards": hand.hero_cards,
        "players_count": len(parsed.players),
        "actions_count": len(parsed.actions),
    }

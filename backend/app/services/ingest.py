from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Iterable, Iterator
from datetime import datetime, timedelta
from decimal import Decimal
from io import StringIO
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import Hand, HandAction, HandPlayer, Session, Upload
from app.parser.coinpoker import parse_hands
from app.parser.models import ParsedHand

logger = logging.getLogger(__name__)

SESSION_GAP = timedelta(hours=2)
STREET_ORDER = {"preflop": 0, "flop": 1, "turn": 2, "river": 3, "showdown": 4}


def _uuid(value: str | UUID) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


def hand_from_parsed(parsed: ParsedHand, user_id: UUID, upload_id: UUID) -> Hand:
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
        rake=parsed.rake,
        splash_fee=parsed.splash_fee,
        hero_invested=parsed.hero_invested,
        hero_collected=parsed.hero_collected,
        hero_net=parsed.hero_net,
        hero_net_bb=parsed.hero_net_bb,
        went_to_showdown=parsed.went_to_showdown,
        won_at_showdown=parsed.won_at_showdown,
        flags=parsed.flags,
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

        hand_row = hand_from_parsed(parsed, user_uuid, upload_uuid)
        db.add(hand_row)
        await db.flush()

        for player in players_from_parsed(parsed, hand_row.id, user_uuid):
            db.add(player)
        for action in actions_from_parsed(parsed, hand_row.id, user_uuid):
            db.add(action)

        inserted_hands.append(hand_row)
        existing.add(parsed.coinpoker_hand_id)

    if inserted_hands:
        await assign_sessions(db, user_uuid, inserted_hands)

    return len(inserted_hands), skipped


async def ingest_upload_content(
    db: AsyncSession,
    upload: Upload,
    content: str | bytes,
) -> int:
    user_uuid = _uuid(upload.user_id)
    upload_uuid = _uuid(upload.id)

    if isinstance(content, bytes):
        text = content.decode("utf-8")
    else:
        text = content

    source: StringIO = StringIO(text)
    inserted, _skipped = await ingest_parsed_hands(
        db,
        user_uuid,
        upload_uuid,
        parse_hands(source),
    )
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

    upload.status = "parsing"
    upload.error_message = None
    db.add(upload)
    await db.commit()

    try:
        if content is None:
            from app.services.storage import download_storage_object

            content = download_storage_object(upload.storage_path)

        hand_count = await ingest_upload_content(db, upload, content)
        upload.status = "parsed"
        upload.hand_count = hand_count
        upload.error_message = None
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

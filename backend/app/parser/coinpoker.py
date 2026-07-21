from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import IO, Iterable, Iterator, Literal
from zoneinfo import ZoneInfo

from app.parser.models import (
    Action,
    ParsedAction,
    ParsedHand,
    ParsedPlayer,
    ParsedReturn,
    Street,
    ParsedSplashDrop,
)


class ParseError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        hand_id: int | None = None,
        line_no: int | None = None,
        line: str | None = None,
    ) -> None:
        parts = [message]
        if hand_id is not None:
            parts.append(f"hand_id={hand_id}")
        if line_no is not None:
            parts.append(f"line={line_no}")
        if line:
            parts.append(f"text={line!r}")
        super().__init__("; ".join(parts))
        self.hand_id = hand_id
        self.line_no = line_no
        self.line = line


@dataclass(slots=True)
class _Seat:
    seat: int
    screen_name: str
    starting_stack: Decimal
    final_cards: list[str] | None = None


_HEADER_RE = re.compile(
    r"^CoinPoker Hand #(?P<hand_id>\d+):\s*"
    r"(?P<variant>[^()]+?)\s*"
    r"\((?P<stakes>[^)]+)\)\s*(?:-\s*)?"
    r"(?P<played_at>.+?)\s*$"
)
_TABLE_RE = re.compile(
    r"^Table '(?P<table_name>.+)'\s+"
    r"(?P<table_size>heads-up|2-max|6-max|9-max)\s+"
    r"Seat #(?P<button_seat>\d+) is the button$",
    re.IGNORECASE,
)
_SEAT_RE = re.compile(
    r"^Seat (?P<seat>\d+): (?P<screen_name>.+?) "
    r"\((?P<stack>.+?) in chips\)$"
)
_MARKER_RE = re.compile(
    r"^\*\*\* (?P<label>(?:(?:FIRST|SECOND) )?"
    r"(?:HOLE CARDS|FLOP|TURN|RIVER|SHOWDOWN|SUMMARY)) \*\*\*"
    r"(?P<rest>.*)$"
)
_DEALT_RE = re.compile(
    r"^Dealt to (?P<screen_name>.+?)"
    r"(?:\s+\[(?P<cards>[2-9TJQKA][cdhs] [2-9TJQKA][cdhs])\])?"
    r"\s*$"
)
_DEALT_CARDS_LINE_RE = re.compile(
    r"^\[(?P<cards>[2-9TJQKA][cdhs] [2-9TJQKA][cdhs])\]\s*$"
)
_POST_RE = re.compile(
    r"^(?P<screen_name>.+?): posts (?:the )?(?:auto )?"
    r"(?P<blind>small blind|big blind) (?P<body>.+)$"
)
_ANTE_POST_RE = re.compile(r"^(?P<screen_name>.+?): posts ante (?P<amount>.+)$")
_ACTION_RE = re.compile(r"^(?P<screen_name>.+?): (?P<body>.+)$")
_RAISE_RE = re.compile(r"^raises (?P<amount>.+?) to (?P<raise_to>.+)$")
_COLLECT_RE = re.compile(
    r"^(?P<screen_name>.+?) collected (?P<amount>.+?) from (?P<pot>.+)$"
)
_UNCALLED_RE = re.compile(
    r"^Uncalled bet \((?P<amount>.+?)\) returned to (?P<screen_name>.+)$"
)
_RETURN_RE = re.compile(r"^(?P<screen_name>.+?): RETURN (?P<amount>.+)$")
_CARD_GROUP_RE = re.compile(r"\[([2-9TJQKA][cdhs](?:\s+[2-9TJQKA][cdhs])*)\]")
_SPLASH_DROP_RE = re.compile(r"^SPLASH dropped (?P<amount>.+)$")

_MONEY_RE = re.compile(
    r"(?:\u20ae|\u00e2\u201a\u00ae|\u00c3\u00a2\u00e2\u20ac"
    r"\u0161\u00c2\u00ae)?\s*(?P<amount>\d[\d,]*(?:\.\d+)?)"
)
_ZERO = Decimal("0")
_State = Literal[
    "HEADER",
    "SEATS",
    "POSTS",
    "HOLE_CARDS",
    "PREFLOP",
    "FLOP",
    "TURN",
    "RIVER",
    "SHOWDOWN",
    "SUMMARY",
]


def parse_hands(source: IO[str] | Iterable[str]) -> Iterator[ParsedHand]:
    block: list[str] = []
    for line in source:
        if _HEADER_RE.match(line.strip()):
            if block:
                yield parse_hand(block)
            block = [line]
            continue
        if block:
            block.append(line)
        elif line.strip():
            raise ParseError("expected CoinPoker hand header", line=line.strip())

    if block:
        yield parse_hand(block)


def parse_hand(lines: list[str]) -> ParsedHand:
    if not lines:
        raise ParseError("empty hand block")

    raw_text = _join_raw_text(lines)
    hand_id: int | None = None
    played_at: datetime | None = None
    table_name: str | None = None
    table_size: int | None = None
    stake_sb: Decimal | None = None
    stake_bb: Decimal | None = None
    button_seat: int | None = None
    hero_screen_name: str | None = None
    hero_cards: list[str] | None = None
    awaiting_hero_cards = False
    flop: list[str] | None = None
    turn: str | None = None
    river: str | None = None
    total_pot: Decimal | None = None
    rake = _ZERO
    splash_fee = _ZERO
    state: _State = "HEADER"
    current_street: Street = "preflop"
    went_to_showdown = False
    uncalled_returns: list[ParsedReturn] = []
    splash_drops: list[ParsedSplashDrop] = []
    flags: dict[str, object] = {
        "all_in": False,
        "bomb_pot": False,
        "run_it_twice": False,
        "split_pot": False,
        "side_pots": False,
    }
    seats: dict[int, _Seat] = {}
    seats_by_name: dict[str, _Seat] = {}
    actions: list[ParsedAction] = []
    dealt_player_lines: dict[str, int] = {}
    action_orders: defaultdict[Street, int] = defaultdict(int)
    collect_winners_by_pot: defaultdict[str, set[str]] = defaultdict(set)

    def error(message: str, line_no: int, line: str) -> ParseError:
        return ParseError(message, hand_id=hand_id, line_no=line_no, line=line)

    def add_action(
        *,
        street: Street,
        screen_name: str,
        action: Action,
        line_no: int,
        line: str,
        amount: Decimal | None = None,
        raise_to: Decimal | None = None,
        is_all_in: bool = False,
        pot_award_id: str | None = None,
    ) -> None:
        seat = seats_by_name.get(screen_name)
        if seat is None:
            raise error("action references an unknown player", line_no, line)
        if is_all_in:
            flags["all_in"] = True
        order = action_orders[street]
        action_orders[street] += 1
        actions.append(
            ParsedAction(
                street=street,
                action_order=order,
                seat=seat.seat,
                screen_name=screen_name,
                action=action,
                amount=amount,
                raise_to=raise_to,
                is_all_in=is_all_in,
                line_number=line_no,
                pot_award_id=pot_award_id,
            )
        )

    for line_no, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line:
            continue

        if awaiting_hero_cards and hero_cards is None:
            cards_match = _DEALT_CARDS_LINE_RE.match(line)
            if cards_match:
                hero_cards = cards_match.group("cards").split()
                awaiting_hero_cards = False
                state = "PREFLOP"
                continue
            awaiting_hero_cards = False

        header_match = _HEADER_RE.match(line)
        if header_match:
            if hand_id is not None:
                raise error("unexpected hand header inside hand block", line_no, line)
            hand_id = int(header_match.group("hand_id"))
            variant = header_match.group("variant").strip()
            base_variant = variant.replace(" BombPot", "").strip()
            if base_variant != "NLH":
                raise error("unsupported or non-English hand variant", line_no, line)
            if "BombPot" in variant:
                flags["bomb_pot"] = True
            stake_parts = header_match.group("stakes").split("/")
            if len(stake_parts) < 2:
                raise error("stakes are missing sb/bb", line_no, line)
            stake_sb = _parse_money(stake_parts[0], line_no, line)
            stake_bb = _parse_money(stake_parts[1], line_no, line)
            if len(stake_parts) >= 3 and flags["bomb_pot"]:
                flags["bomb_pot_ante"] = _parse_money(stake_parts[2], line_no, line)
            played_at = _parse_played_at(
                header_match.group("played_at"), line_no, line
            )
            state = "SEATS"
            continue

        if hand_id is None:
            raise error("expected CoinPoker hand header", line_no, line)

        table_match = _TABLE_RE.match(line)
        if table_match:
            table_name = table_match.group("table_name")
            table_size = _parse_table_size(table_match.group("table_size"))
            button_seat = int(table_match.group("button_seat"))
            state = "SEATS"
            continue

        seat_match = _SEAT_RE.match(line)
        if seat_match and state != "SUMMARY":
            seat_no = int(seat_match.group("seat"))
            screen_name = seat_match.group("screen_name")
            seat = _Seat(
                seat=seat_no,
                screen_name=screen_name,
                starting_stack=_parse_money(seat_match.group("stack"), line_no, line),
            )
            seats[seat_no] = seat
            seats_by_name[screen_name] = seat
            if screen_name == "Hero":
                hero_screen_name = "Hero"
            state = "SEATS"
            continue

        marker_match = _MARKER_RE.match(line)
        if marker_match:
            label = marker_match.group("label")
            rest = marker_match.group("rest")
            run_label: str | None = None
            if label.startswith("FIRST "):
                flags["run_it_twice"] = True
                run_label = "first"
                label = label.removeprefix("FIRST ")
            elif label.startswith("SECOND "):
                flags["run_it_twice"] = True
                run_label = "second"
                label = label.removeprefix("SECOND ")

            if label == "HOLE CARDS":
                state = "HOLE_CARDS"
                current_street = "preflop"
            elif label in {"FLOP", "TURN", "RIVER"}:
                if label == "FLOP":
                    state = "FLOP"
                    current_street = "flop"
                elif label == "TURN":
                    state = "TURN"
                    current_street = "turn"
                else:
                    state = "RIVER"
                    current_street = "river"
                groups = _card_groups(rest)
                if not groups:
                    raise error("board marker is missing cards", line_no, line)
                if run_label == "second":
                    flags["second_board"] = _flatten_card_groups(groups)
                    continue
                if label == "FLOP":
                    flop = _extract_flop(groups, line_no, line)
                elif label == "TURN":
                    turn = groups[-1][-1]
                else:
                    river = groups[-1][-1]
            elif label == "SHOWDOWN":
                state = "SHOWDOWN"
                current_street = "showdown"
                went_to_showdown = True
            elif label == "SUMMARY":
                state = "SUMMARY"
            continue

        total_match = _parse_total_line(line, line_no)
        if total_match is not None:
            total_pot, rake, splash_fee = total_match
            continue

        if line.startswith("Board "):
            continue
        splash_drop_match = _SPLASH_DROP_RE.match(line)
        if splash_drop_match:
            splash_drops.append(
                ParsedSplashDrop(
                    amount=_parse_money(splash_drop_match.group("amount"), line_no, line),
                    line_number=line_no,
                )
            )
            flags["splash_drop"] = True
            continue


        if (
            line in {"Hand was run once", "Hand was run with two boards"}
            or line.startswith("Game ended:")
            or line.startswith(("FIRST Board ", "SECOND Board "))
        ):
            continue

        if state == "SUMMARY" and line.startswith("Seat "):
            _parse_summary_seat(line, seats)
            continue

        dealt_match = _DEALT_RE.match(line)
        if dealt_match:
            screen_name = dealt_match.group("screen_name").strip()
            cards = dealt_match.group("cards")
            dealt_player_lines.setdefault(screen_name, line_no)
            if hero_screen_name is None:
                if screen_name == "Hero":
                    hero_screen_name = "Hero"
                elif cards:
                    hero_screen_name = screen_name
                else:
                    hero_screen_name = screen_name
                    awaiting_hero_cards = True
                    state = "PREFLOP"
                    continue
            elif screen_name != hero_screen_name:
                if cards:
                    raise error(
                        "unexpected non-Hero dealt cards with hole cards",
                        line_no,
                        line,
                    )
                continue
            if cards:
                hero_cards = cards.split()
                awaiting_hero_cards = False
            else:
                awaiting_hero_cards = True
            state = "PREFLOP"
            continue

        uncalled_match = _UNCALLED_RE.match(line)
        if uncalled_match:
            amount = _parse_money(uncalled_match.group("amount"), line_no, line)
            screen_name = uncalled_match.group("screen_name")
            seat = seats_by_name.get(screen_name)
            if seat is None:
                raise error("return references an unknown player", line_no, line)
            uncalled_returns.append(
                ParsedReturn(
                    street=current_street,
                    seat=seat.seat,
                    screen_name=screen_name,
                    amount=amount,
                    line_number=line_no,
                )
            )
            continue

        return_match = _RETURN_RE.match(line)
        if return_match:
            amount = _parse_money(return_match.group("amount"), line_no, line)
            screen_name = return_match.group("screen_name")
            seat = seats_by_name.get(screen_name)
            if seat is None:
                raise error("return references an unknown player", line_no, line)
            uncalled_returns.append(
                ParsedReturn(
                    street=current_street,
                    seat=seat.seat,
                    screen_name=screen_name,
                    amount=amount,
                    line_number=line_no,
                )
            )
            continue

        collect_match = _COLLECT_RE.match(line)
        if collect_match:
            screen_name = collect_match.group("screen_name")
            amount = _parse_money(collect_match.group("amount"), line_no, line)
            pot_name = collect_match.group("pot").strip().lower()
            if "side pot" in pot_name:
                flags["side_pots"] = True
            collect_winners_by_pot[pot_name].add(screen_name)
            if len(collect_winners_by_pot[pot_name]) > 1:
                flags["split_pot"] = True
            add_action(
                street="showdown",
                screen_name=screen_name,
                action="collect",
                amount=amount,
                line_no=line_no,
                line=line,
                pot_award_id=pot_name,
            )
            continue

        ante_post_match = _ANTE_POST_RE.match(line)
        if ante_post_match:
            add_action(
                street="preflop",
                screen_name=ante_post_match.group("screen_name"),
                action="post_ante",
                amount=_parse_money(ante_post_match.group("amount"), line_no, line),
                line_no=line_no,
                line=line,
            )
            state = "POSTS"
            continue

        post_match = _POST_RE.match(line)
        if post_match:
            screen_name = post_match.group("screen_name")
            blind = post_match.group("blind")
            body, is_all_in = _strip_all_in(post_match.group("body"))
            add_action(
                street="preflop",
                screen_name=screen_name,
                action="post_sb" if blind == "small blind" else "post_bb",
                amount=_parse_money(body, line_no, line),
                is_all_in=is_all_in,
                line_no=line_no,
                line=line,
            )
            state = "POSTS"
            continue

        action_match = _ACTION_RE.match(line)
        if action_match:
            _parse_player_action(
                action_match=action_match,
                current_street=current_street,
                add_action=add_action,
                seats_by_name=seats_by_name,
                line_no=line_no,
                line=line,
                parse_money=_parse_money,
            )
            if _is_showdown_action(action_match.group("body")):
                went_to_showdown = True
            continue

        raise error(
            "unrecognized CoinPoker line; expected English hand history format",
            line_no,
            line,
        )

    if hand_id is None or played_at is None or stake_sb is None or stake_bb is None:
        raise ParseError("hand header is incomplete")
    if table_name is None or table_size is None or button_seat is None:
        raise ParseError("table metadata is missing", hand_id=hand_id)
    if total_pot is None:
        raise ParseError("summary total pot line is missing", hand_id=hand_id)
    if hero_cards is None and hero_screen_name is not None:
        hero_seat_obj = seats_by_name.get(hero_screen_name)
        if hero_seat_obj and hero_seat_obj.final_cards:
            hero_cards = hero_seat_obj.final_cards[:2]

    if hero_cards is None:
        raise ParseError("Hero hole cards are missing", hand_id=hand_id)
    if hero_screen_name is None:
        raise ParseError("Hero player is missing", hand_id=hand_id)

    hero_seat = _find_hero_seat(seats, hero_screen_name, hand_id)
    positions = _assign_positions(seats, table_size, button_seat, hand_id)
    hero_position = positions[hero_seat]
    hero_invested, hero_collected = _hero_amounts(
        hero_screen_name, actions, uncalled_returns
    )
    hero_net = hero_collected - hero_invested
    hero_net_bb = _ZERO if stake_bb == _ZERO else hero_net / stake_bb
    won_at_showdown = (hero_collected > _ZERO) if went_to_showdown else None

    players = [
        ParsedPlayer(
            seat=seat.seat,
            screen_name=seat.screen_name,
            starting_stack=seat.starting_stack,
            position=positions[seat.seat],
            is_hero=seat.screen_name == hero_screen_name,
            final_cards=seat.final_cards,
        )
        for seat in sorted(seats.values(), key=lambda item: item.seat)
    ]

    return ParsedHand(
        coinpoker_hand_id=hand_id,
        played_at=played_at,
        table_name=table_name,
        table_size=table_size,
        stake_sb=stake_sb,
        stake_bb=stake_bb,
        button_seat=button_seat,
        hero_seat=hero_seat,
        hero_position=hero_position,
        hero_cards=hero_cards,
        flop=flop,
        turn=turn,
        river=river,
        total_pot=total_pot,
        rake=rake,
        splash_fee=splash_fee,
        hero_invested=hero_invested,
        hero_collected=hero_collected,
        hero_net=hero_net,
        hero_net_bb=hero_net_bb,
        went_to_showdown=went_to_showdown,
        won_at_showdown=won_at_showdown,
        flags=flags,
        raw_text=raw_text,
        players=players,
        actions=actions,
        dealt_player_lines=dealt_player_lines,
        uncalled_returns=uncalled_returns,
        splash_drops=splash_drops,
    )


def _parse_player_action(
    *,
    action_match: re.Match[str],
    current_street: Street,
    add_action: Callable[..., None],
    seats_by_name: dict[str, _Seat],
    line_no: int,
    line: str,
    parse_money: Callable[[str, int, str], Decimal],
) -> None:
    screen_name = action_match.group("screen_name")
    body, is_all_in = _strip_all_in(action_match.group("body"))
    action_street: Street = current_street

    if body.startswith("AUTOBB "):
        add_action(
            street="preflop",
            screen_name=screen_name,
            action="post_bb",
            amount=parse_money(body.removeprefix("AUTOBB "), line_no, line),
            is_all_in=is_all_in,
            line_no=line_no,
            line=line,
        )
        return
    if body == "folds" or "has timed out" in body or "is sitting out" in body:
        add_action(
            street=action_street,
            screen_name=screen_name,
            action="fold",
            line_no=line_no,
            line=line,
        )
        return
    if body == "checks":
        add_action(
            street=action_street,
            screen_name=screen_name,
            action="check",
            line_no=line_no,
            line=line,
        )
        return
    if body.startswith("ALLIN "):
        amount = parse_money(body.removeprefix("ALLIN "), line_no, line)
        add_action(
            street=action_street,
            screen_name=screen_name,
            action="raise",
            amount=amount,
            raise_to=amount,
            is_all_in=True,
            line_no=line_no,
            line=line,
        )
        return
    if body.startswith("calls "):
        add_action(
            street=action_street,
            screen_name=screen_name,
            action="call",
            amount=parse_money(body.removeprefix("calls "), line_no, line),
            is_all_in=is_all_in,
            line_no=line_no,
            line=line,
        )
        return
    if body.startswith("bets "):
        add_action(
            street=action_street,
            screen_name=screen_name,
            action="bet",
            amount=parse_money(body.removeprefix("bets "), line_no, line),
            is_all_in=is_all_in,
            line_no=line_no,
            line=line,
        )
        return

    raise_match = _RAISE_RE.match(body)
    if raise_match:
        add_action(
            street=action_street,
            screen_name=screen_name,
            action="raise",
            amount=parse_money(raise_match.group("amount"), line_no, line),
            raise_to=parse_money(raise_match.group("raise_to"), line_no, line),
            is_all_in=is_all_in,
            line_no=line_no,
            line=line,
        )
        return

    if body in {"is all-in", "is all in"}:
        add_action(
            street=action_street,
            screen_name=screen_name,
            action="all_in",
            is_all_in=True,
            line_no=line_no,
            line=line,
        )
        return

    if body.startswith("shows "):
        groups = _card_groups(body)
        if groups:
            seats_by_name[screen_name].final_cards = groups[0]
        add_action(
            street="showdown",
            screen_name=screen_name,
            action="show",
            line_no=line_no,
            line=line,
        )
        return

    if body.startswith("mucks") or "doesn't show hand" in body:
        groups = _card_groups(body)
        if groups:
            seats_by_name[screen_name].final_cards = groups[0]
        add_action(
            street="showdown",
            screen_name=screen_name,
            action="muck",
            line_no=line_no,
            line=line,
        )
        return

    raise ParseError(
        "unrecognized CoinPoker line; expected English hand history format",
        line_no=line_no,
        line=line,
    )


def _join_raw_text(lines: list[str]) -> str:
    if any(line.endswith(("\n", "\r")) for line in lines):
        return "".join(lines)
    return "\n".join(lines)


def _parse_money(value: str, line_no: int, line: str) -> Decimal:
    match = _MONEY_RE.search(value)
    if match is None:
        raise ParseError("money amount is missing", line_no=line_no, line=line)
    return Decimal(match.group("amount").replace(",", ""))


def _parse_played_at(value: str, line_no: int, line: str) -> datetime:
    try:
        date_part, tz_part = value.rsplit(" ", 1)
    except ValueError as exc:
        raise ParseError("timestamp is missing timezone", line_no=line_no, line=line) from exc

    try:
        naive = datetime.strptime(date_part, "%Y/%m/%d %H:%M:%S")
    except ValueError as exc:
        raise ParseError("timestamp has unsupported format", line_no=line_no, line=line) from exc

    tzinfo = _timezone_for(tz_part, line_no, line)
    return naive.replace(tzinfo=tzinfo)


def _timezone_for(value: str, line_no: int, line: str) -> ZoneInfo | timezone:
    zones = {
        "PST": "America/Los_Angeles",
        "PDT": "America/Los_Angeles",
        "MST": "America/Denver",
        "MDT": "America/Denver",
        "CST": "America/Chicago",
        "CDT": "America/Chicago",
        "EST": "America/New_York",
        "EDT": "America/New_York",
    }
    if value in {"UTC", "GMT"}:
        return timezone.utc
    zone_name = zones.get(value)
    if zone_name is None:
        raise ParseError("unsupported timestamp timezone", line_no=line_no, line=line)
    return ZoneInfo(zone_name)


def _parse_table_size(value: str) -> int:
    normalized = value.lower()
    if normalized in {"heads-up", "2-max"}:
        return 2
    if normalized == "6-max":
        return 6
    if normalized == "9-max":
        return 9
    raise ParseError(f"unsupported table size: {value}")


def _card_groups(value: str) -> list[list[str]]:
    return [group.split() for group in _CARD_GROUP_RE.findall(value)]


def _flatten_card_groups(groups: list[list[str]]) -> list[str]:
    return [card for group in groups for card in group]


def _extract_flop(groups: list[list[str]], line_no: int, line: str) -> list[str]:
    cards = _flatten_card_groups(groups)
    if len(cards) < 3:
        raise ParseError("flop marker has fewer than 3 cards", line_no=line_no, line=line)
    return cards[:3]


def _parse_total_line(line: str, line_no: int) -> tuple[Decimal, Decimal, Decimal] | None:
    if not line.startswith("Total pot "):
        return None

    total_pot: Decimal | None = None
    rake = _ZERO
    splash_fee = _ZERO
    for part in (piece.strip() for piece in line.split("|")):
        if part.startswith("Total pot "):
            total_pot = _parse_money(part.removeprefix("Total pot "), line_no, line)
        elif part.startswith("Rake "):
            rake = _parse_money(part.removeprefix("Rake "), line_no, line)
        elif part.startswith("Splash Fee "):
            splash_fee = _parse_money(part.removeprefix("Splash Fee "), line_no, line)
    if total_pot is None:
        raise ParseError("total pot amount is missing", line_no=line_no, line=line)
    return total_pot, rake, splash_fee


def _parse_summary_seat(line: str, seats: dict[int, _Seat]) -> None:
    match = re.match(r"^Seat (?P<seat>\d+): .+$", line)
    if match is None:
        return
    seat = seats.get(int(match.group("seat")))
    if seat is None:
        return
    groups = _card_groups(line)
    if groups and ("showed" in line or "mucked" in line):
        seat.final_cards = groups[0]


def _strip_all_in(body: str) -> tuple[str, bool]:
    is_all_in = bool(re.search(r"\ball[- ]?in\b", body))
    body = re.sub(r"\s+and is all[- ]?in$", "", body)
    return body, is_all_in


def _is_showdown_action(body: str) -> bool:
    return body.startswith("shows ") or body.startswith("mucks")


def _hero_amounts(
    hero_screen_name: str,
    actions: list[ParsedAction],
    uncalled_returns: list[ParsedReturn],
) -> tuple[Decimal, Decimal]:
    invested = _ZERO
    collected = _ZERO
    invest_actions = {"post_sb", "post_bb", "call", "bet", "raise"}
    for action in actions:
        if action.screen_name != hero_screen_name:
            continue
        if action.action in invest_actions and action.amount is not None:
            invested += action.amount
        elif action.action == "collect" and action.amount is not None:
            collected += action.amount
    for returned in uncalled_returns:
        if returned.screen_name == hero_screen_name:
            invested -= returned.amount
    return invested, collected


def _find_hero_seat(seats: dict[int, _Seat], hero_screen_name: str, hand_id: int) -> int:
    for seat_no, seat in seats.items():
        if seat.screen_name == hero_screen_name:
            return seat_no
    raise ParseError("Hero seat is missing", hand_id=hand_id)


def _assign_positions(
    seats: dict[int, _Seat],
    table_size: int,
    button_seat: int,
    hand_id: int,
) -> dict[int, str]:
    if button_seat not in seats:
        raise ParseError("button seat is not occupied", hand_id=hand_id)

    seat_numbers = sorted(seats)
    button_index = seat_numbers.index(button_seat)
    ordered = seat_numbers[button_index:] + seat_numbers[:button_index]
    labels = _position_labels(table_size, len(ordered))
    return dict(zip(ordered, labels, strict=True))


def _position_labels(table_size: int, player_count: int) -> list[str]:
    if player_count < 2:
        raise ParseError("at least two seated players are required")
    if player_count == 2:
        return ["BTN/SB", "BB"]

    six_max = {
        3: ["BTN", "SB", "BB"],
        4: ["BTN", "SB", "BB", "CO"],
        5: ["BTN", "SB", "BB", "UTG", "CO"],
        6: ["BTN", "SB", "BB", "UTG", "HJ", "CO"],
    }
    nine_max = {
        3: ["BTN", "SB", "BB"],
        4: ["BTN", "SB", "BB", "CO"],
        5: ["BTN", "SB", "BB", "UTG", "CO"],
        6: ["BTN", "SB", "BB", "UTG", "HJ", "CO"],
        7: ["BTN", "SB", "BB", "UTG", "LJ", "HJ", "CO"],
        8: ["BTN", "SB", "BB", "UTG", "MP", "LJ", "HJ", "CO"],
        9: ["BTN", "SB", "BB", "UTG", "UTG+1", "MP", "LJ", "HJ", "CO"],
    }

    labels_by_count = nine_max if table_size == 9 else six_max
    labels = labels_by_count.get(player_count)
    if labels is None:
        raise ParseError("player count is incompatible with table size")
    return labels

"""
Stats Engine — single-round-trip SQL aggregation for hero poker statistics.

Stat definitions
----------------
VPIP%
    Numerator  : Hands where hero voluntarily put money in preflop via
                 call / raise / bet / all_in, excluding blind posts
                 (post_sb, post_bb do NOT count as VPIP).
    Denominator: All hands where hero was dealt in (every row in `hands`
                 for this user).

PFR%
    Numerator  : Hands where hero made an aggressive preflop action:
                 action IN ('raise', 'bet') or all_in with a raise_to value.
    Denominator: Same as VPIP.

3-bet%
    Numerator  : Hands where hero made an aggressive preflop action AFTER
                 at least one non-hero raise/bet had already appeared in
                 the preflop action sequence before hero acted.
    Denominator: Hands where hero faced at least one such prior raise/bet
                 (i.e. hero had a 3-bet *opportunity*).
    v1 edge-cases: squeeze, cold 4-bet, and standard 3-bet are all counted
    equally — any hero raise after facing any prior aggressive action counts.

WTSD%
    Numerator  : hands.went_to_showdown IS TRUE
    Denominator: Hands where a flop was dealt (hands.flop IS NOT NULL).
                 Do NOT divide by all hands.

W$SD%
    Numerator  : hands.won_at_showdown IS TRUE
    Denominator: hands.went_to_showdown IS TRUE

BB/100
    sum(hero_net_bb) / COUNT(*) * 100
    All hands in the filtered set.

Implementation notes
--------------------
* Every query filters on user_id — never touches another user's rows.
* One SQL round-trip per request: all aggregation happens inside a CTE
  chain resolved by PostgreSQL in a single execute().
* Timeframe filtering is injected as a literal WHERE clause fragment
  (values come from a Literal type, not user input — no injection risk).
* Position filtering uses a named bind parameter (:position).
* Money values stay as Decimal inside the engine; the router converts
  to float and rounds to 2 decimals before returning to the API.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlmodel.ext.asyncio.session import AsyncSession

# ---------------------------------------------------------------------------
# Timeframe helpers
# ---------------------------------------------------------------------------

_TIMEFRAME_CLAUSES: dict[str, str] = {
    "lifetime": "",
    "7d": "AND h.played_at >= NOW() - INTERVAL '7 days'",
    "30d": "AND h.played_at >= NOW() - INTERVAL '30 days'",
}


def _tf_clause(timeframe: str) -> str:
    return _TIMEFRAME_CLAUSES.get(timeframe, "")


# ---------------------------------------------------------------------------
# CTE core (shared between summary and by-position)
# ---------------------------------------------------------------------------

_CORE_CTES = """\
WITH
hero_seats AS (
    -- Identify the hero's seat number for each hand owned by this user.
    SELECT hand_id, seat AS hero_seat
    FROM   hand_players
    WHERE  user_id = :user_id
      AND  is_hero  = TRUE
),
preflop_hero_actions AS (
    -- All preflop actions taken BY the hero, annotated with whether
    -- the hero faced an aggressive action (raise/bet) from a non-hero
    -- player *before* this specific action in action_order sequence.
    SELECT
        ha.hand_id,
        ha.action,
        ha.action_order,
        ha.raise_to,
        EXISTS (
            SELECT 1
            FROM   hand_actions p
            WHERE  p.hand_id      = ha.hand_id
              AND  p.street       = 'preflop'
              AND  p.action_order < ha.action_order
              AND  (
                       p.action IN ('raise', 'bet')
                    OR (p.action = 'all_in' AND p.raise_to IS NOT NULL)
                   )
              AND  p.seat != hs.hero_seat
        ) AS faced_raise_before
    FROM  hand_actions ha
    JOIN  hero_seats   hs ON hs.hand_id = ha.hand_id
    WHERE ha.user_id  = :user_id
      AND ha.street   = 'preflop'
      AND ha.seat     = hs.hero_seat
),
hand_flags AS (
    -- Per-hand boolean flags derived from the hero's preflop action log.
    -- Uses GROUP BY h.id (primary key) so PostgreSQL resolves all other
    -- h.* columns via functional dependency without listing them explicitly.
    SELECT
        h.id              AS hand_id,
        h.hero_position,
        h.hero_net_bb,
        h.went_to_showdown,
        h.won_at_showdown,
        h.flop IS NOT NULL AS saw_flop,
        -- VPIP: any voluntary preflop money in (call/raise/bet/all_in)
        COALESCE(BOOL_OR(
            pha.action IN ('call', 'raise', 'bet', 'all_in')
        ), FALSE) AS vpip,
        -- PFR: preflop raise (open-raise, 3-bet, aggressive all-in)
        COALESCE(BOOL_OR(
            pha.action IN ('raise', 'bet')
            OR (pha.action = 'all_in' AND pha.raise_to IS NOT NULL)
        ), FALSE) AS pfr,
        -- 3-bet opportunity: hero faced a prior raise before any of their actions
        COALESCE(BOOL_OR(pha.faced_raise_before), FALSE) AS three_bet_opp,
        -- 3-bet: hero raised AFTER facing a prior raise
        COALESCE(BOOL_OR(
            pha.faced_raise_before
            AND (
                pha.action IN ('raise', 'bet')
                OR (pha.action = 'all_in' AND pha.raise_to IS NOT NULL)
            )
        ), FALSE) AS three_bet
    FROM       hands h
    LEFT JOIN  preflop_hero_actions pha ON pha.hand_id = h.id
    WHERE      h.user_id = :user_id
      {timeframe_clause}
      {position_clause}
    GROUP BY h.id
)"""

# ---------------------------------------------------------------------------
# Summary query (single aggregate row)
# ---------------------------------------------------------------------------

_SUMMARY_SELECT = """\
SELECT
    COUNT(*)                                                             AS hands_count,
    COALESCE(
        ROUND(100.0 * COUNT(*) FILTER (WHERE vpip)
              / NULLIF(COUNT(*), 0), 2), 0.0)                           AS vpip_pct,
    COALESCE(
        ROUND(100.0 * COUNT(*) FILTER (WHERE pfr)
              / NULLIF(COUNT(*), 0), 2), 0.0)                           AS pfr_pct,
    COALESCE(
        ROUND(100.0 * COUNT(*) FILTER (WHERE three_bet)
              / NULLIF(COUNT(*) FILTER (WHERE three_bet_opp), 0), 2),
        0.0)                                                             AS three_bet_pct,
    COALESCE(
        ROUND(100.0 * COUNT(*) FILTER (WHERE went_to_showdown)
              / NULLIF(COUNT(*) FILTER (WHERE saw_flop), 0), 2), 0.0)  AS wtsd_pct,
    COALESCE(
        ROUND(100.0 * COUNT(*) FILTER (WHERE won_at_showdown IS TRUE)
              / NULLIF(COUNT(*) FILTER (WHERE went_to_showdown), 0), 2),
        0.0)                                                             AS wsd_pct,
    COALESCE(
        ROUND(100.0 * SUM(hero_net_bb)
              / NULLIF(COUNT(*), 0), 2), 0.0)                          AS bb_per_100
FROM hand_flags"""

# ---------------------------------------------------------------------------
# By-position query (one row per position)
# ---------------------------------------------------------------------------

_BY_POSITION_SELECT = """\
SELECT
    hero_position                                                              AS position,
    COUNT(*)                                                                   AS hands,
    COALESCE(
        ROUND(100.0 * COUNT(*) FILTER (WHERE vpip)
              / NULLIF(COUNT(*), 0), 2), 0.0)                                 AS vpip_pct,
    COALESCE(
        ROUND(100.0 * COUNT(*) FILTER (WHERE pfr)
              / NULLIF(COUNT(*), 0), 2), 0.0)                                 AS pfr_pct,
    COALESCE(
        ROUND(100.0 * COUNT(*) FILTER (WHERE three_bet)
              / NULLIF(COUNT(*) FILTER (WHERE three_bet_opp), 0), 2),
        0.0)                                                                   AS three_bet_pct,
    COALESCE(
        ROUND(100.0 * COUNT(*) FILTER (WHERE went_to_showdown)
              / NULLIF(COUNT(*) FILTER (WHERE saw_flop), 0), 2), 0.0)        AS wtsd_pct,
    COALESCE(
        ROUND(100.0 * COUNT(*) FILTER (WHERE won_at_showdown IS TRUE)
              / NULLIF(COUNT(*) FILTER (WHERE went_to_showdown), 0), 2),
        0.0)                                                                   AS wsd_pct,
    COALESCE(
        ROUND(100.0 * SUM(hero_net_bb)
              / NULLIF(COUNT(*), 0), 2), 0.0)                                 AS bb_per_100
FROM  hand_flags
GROUP BY hero_position
ORDER BY hero_position"""


# ---------------------------------------------------------------------------
# Public query builders
# ---------------------------------------------------------------------------

def build_stats_query(
    user_id: UUID,
    timeframe: str = "lifetime",
    position: str | None = None,
) -> tuple[Any, dict[str, Any]]:
    """Return (SQLAlchemy text clause, bind params) for the summary stats query.

    The returned TextClause aggregates all stats in a single DB round-trip.
    Bind params always include ``user_id``; ``position`` is added when supplied.
    """
    tf = _tf_clause(timeframe)
    pos = "AND h.hero_position = :position" if position else ""
    sql = _CORE_CTES.format(timeframe_clause=tf, position_clause=pos)
    sql = f"{sql}\n{_SUMMARY_SELECT}"
    params: dict[str, Any] = {"user_id": user_id}
    if position:
        params["position"] = position
    return text(sql), params


def build_by_position_query(
    user_id: UUID,
    timeframe: str = "lifetime",
) -> tuple[Any, dict[str, Any]]:
    """Return (SQLAlchemy text clause, bind params) for the by-position query.

    Returns one aggregate row per ``hero_position`` value present in the
    filtered hand set, ordered alphabetically by position label.
    """
    tf = _tf_clause(timeframe)
    sql = _CORE_CTES.format(timeframe_clause=tf, position_clause="")
    sql = f"{sql}\n{_BY_POSITION_SELECT}"
    params: dict[str, Any] = {"user_id": user_id}
    return text(sql), params


# ---------------------------------------------------------------------------
# Async execution helpers
# ---------------------------------------------------------------------------

def _row_to_summary(row: Any) -> dict[str, Any]:
    """Convert a MappingResult row to a plain dict with Python types."""
    return {
        "hands_count": int(row.hands_count),
        "vpip_pct": float(row.vpip_pct),
        "pfr_pct": float(row.pfr_pct),
        "three_bet_pct": float(row.three_bet_pct),
        "wtsd_pct": float(row.wtsd_pct),
        "wsd_pct": float(row.wsd_pct),
        "bb_per_100": Decimal(row.bb_per_100),
    }


def _row_to_position(row: Any) -> dict[str, Any]:
    return {
        "position": str(row.position),
        "hands": int(row.hands),
        "vpip_pct": float(row.vpip_pct),
        "pfr_pct": float(row.pfr_pct),
        "three_bet_pct": float(row.three_bet_pct),
        "wtsd_pct": float(row.wtsd_pct),
        "wsd_pct": float(row.wsd_pct),
        "bb_per_100": Decimal(row.bb_per_100),
    }


async def compute_stats(
    session: AsyncSession,
    user_id: UUID,
    timeframe: str = "lifetime",
    position: str | None = None,
) -> dict[str, Any]:
    """Execute the summary stats query and return a plain dict.

    Returns zero values for all stats when the user has no hands.
    """
    stmt, params = build_stats_query(user_id, timeframe, position)
    result = await session.execute(stmt, params)
    row = result.mappings().one()
    return _row_to_summary(row)


async def compute_by_position(
    session: AsyncSession,
    user_id: UUID,
    timeframe: str = "lifetime",
) -> list[dict[str, Any]]:
    """Execute the by-position query and return a list of position dicts.

    Positions with zero hands in the filtered timeframe are omitted.
    """
    stmt, params = build_by_position_query(user_id, timeframe)
    result = await session.execute(stmt, params)
    return [_row_to_position(row) for row in result.mappings()]


# ---------------------------------------------------------------------------
# Leak tag aggregation
# ---------------------------------------------------------------------------

_LEAKS_TIMEFRAME_CLAUSES: dict[str, str] = {
    "lifetime": "",
    "7d": "AND la.created_at >= NOW() - INTERVAL '7 days'",
    "30d": "AND la.created_at >= NOW() - INTERVAL '30 days'",
}

_LEAKS_SQL = """\
WITH
total_analyses AS (
    SELECT COUNT(*) AS total
    FROM   llm_analyses la
    WHERE  la.user_id = :user_id
      {timeframe_clause}
),
tag_counts AS (
    SELECT
        tag,
        COUNT(*) AS cnt
    FROM   llm_analyses la,
           UNNEST(la.leak_tags) AS tag
    WHERE  la.user_id = :user_id
      {timeframe_clause}
    GROUP  BY tag
)
SELECT
    tc.tag,
    tc.cnt                                                  AS count,
    ROUND(
        100.0 * tc.cnt / NULLIF(ta.total, 0),
        1
    )::float                                                AS pct_of_analyses
FROM       tag_counts tc
CROSS JOIN total_analyses ta
ORDER BY   tc.cnt DESC, tc.tag ASC
"""


async def compute_leaks(
    session: AsyncSession,
    user_id: UUID,
    timeframe: str = "30d",
) -> list[dict[str, Any]]:
    """Aggregate leak tags from LLM analyses and return them ranked by frequency.

    Uses the GIN index ``idx_llm_analyses_user_tags`` for fast array filtering.
    Returns an empty list when the user has no analyses in the requested window.
    """
    tf = _LEAKS_TIMEFRAME_CLAUSES.get(timeframe, _LEAKS_TIMEFRAME_CLAUSES["30d"])
    sql = _LEAKS_SQL.format(timeframe_clause=tf)
    result = await session.execute(text(sql), {"user_id": user_id})
    return [
        {
            "tag": str(row.tag),
            "count": int(row.count),
            "pct_of_analyses": float(row.pct_of_analyses),
        }
        for row in result.mappings()
    ]

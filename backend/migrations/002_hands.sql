-- Migration 002: sessions, hands, hand_players, hand_actions
-- Sessions are auto-derived clusters of hands at the same stake within a 2-hour window.
-- hand_players and hand_actions denormalize user_id for RLS performance.

CREATE TABLE sessions (
  id            uuid          PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id       uuid          NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  started_at    timestamptz   NOT NULL,
  ended_at      timestamptz   NOT NULL,
  stake_bb      numeric(8,4)  NOT NULL,
  table_size    smallint      NOT NULL,
  hands_played  integer       NOT NULL DEFAULT 0,
  hero_net      numeric(12,4) NOT NULL DEFAULT 0,
  hero_net_bb   numeric(12,4) NOT NULL DEFAULT 0,
  created_at    timestamptz   NOT NULL DEFAULT now()
);

ALTER TABLE sessions ENABLE ROW LEVEL SECURITY;

CREATE POLICY sessions_rls ON sessions
  USING     (user_id = auth.uid())
  WITH CHECK (user_id = auth.uid());

-- ------------------------------------------------------------

CREATE TABLE hands (
  id                  uuid          PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id             uuid          NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  upload_id           uuid          NOT NULL REFERENCES uploads(id) ON DELETE CASCADE,
  session_id          uuid          REFERENCES sessions(id),
  coinpoker_hand_id   bigint        NOT NULL,
  played_at           timestamptz   NOT NULL,
  table_name          text          NOT NULL,
  table_size          smallint      NOT NULL,
  stake_sb            numeric(8,4)  NOT NULL,
  stake_bb            numeric(8,4)  NOT NULL,
  button_seat         smallint      NOT NULL,
  hero_seat           smallint      NOT NULL,
  hero_position       text          NOT NULL,       -- BTN, SB, BB, UTG, HJ, CO
  hero_cards          text[]        NOT NULL,       -- ['Kc','9d']
  flop                text[],                       -- ['Kd','9c','Td'] or NULL
  turn                text,
  river               text,
  total_pot           numeric(12,4) NOT NULL,
  rake                numeric(12,4) NOT NULL DEFAULT 0,
  splash_fee          numeric(12,4) NOT NULL DEFAULT 0,
  hero_invested       numeric(12,4) NOT NULL DEFAULT 0,
  hero_collected      numeric(12,4) NOT NULL DEFAULT 0,
  hero_net            numeric(12,4) NOT NULL,       -- hero_collected - hero_invested
  hero_net_bb         numeric(12,4) NOT NULL,       -- hero_net / stake_bb
  went_to_showdown    boolean       NOT NULL DEFAULT false,
  won_at_showdown     boolean,
  flags               jsonb         NOT NULL DEFAULT '{}',  -- {all_in, run_it_twice, split_pot, side_pots}
  raw_text            text,                                 -- original hand block, kept for re-parsing
  created_at          timestamptz   NOT NULL DEFAULT now()
);

ALTER TABLE hands ADD CONSTRAINT hands_user_coinpoker_id_unique UNIQUE (user_id, coinpoker_hand_id);

-- Query patterns: dashboard loads hands ordered by date, filtered by position, joined to session
CREATE INDEX idx_hands_user_played_at ON hands (user_id, played_at DESC);
CREATE INDEX idx_hands_user_position  ON hands (user_id, hero_position);
CREATE INDEX idx_hands_user_session   ON hands (user_id, session_id);
CREATE INDEX idx_hands_user_net       ON hands (user_id, hero_net);  -- biggest losers widget

ALTER TABLE hands ENABLE ROW LEVEL SECURITY;

CREATE POLICY hands_rls ON hands
  USING     (user_id = auth.uid())
  WITH CHECK (user_id = auth.uid());

-- ------------------------------------------------------------

-- one row per seat per hand
CREATE TABLE hand_players (
  id              bigserial     PRIMARY KEY,
  hand_id         uuid          NOT NULL REFERENCES hands(id) ON DELETE CASCADE,
  user_id         uuid          NOT NULL,           -- denormalized for RLS performance
  seat            smallint      NOT NULL,
  screen_name     text          NOT NULL,
  position        text,
  starting_stack  numeric(12,4) NOT NULL,
  is_hero         boolean       NOT NULL DEFAULT false,
  final_cards     text[],                           -- NULL unless shown at showdown
  created_at      timestamptz   NOT NULL DEFAULT now()
);

ALTER TABLE hand_players ENABLE ROW LEVEL SECURITY;

CREATE POLICY hand_players_rls ON hand_players
  USING     (user_id = auth.uid())
  WITH CHECK (user_id = auth.uid());

-- ------------------------------------------------------------

-- append-only event log powering the hand replayer and stats queries
CREATE TABLE hand_actions (
  id            bigserial     PRIMARY KEY,
  hand_id       uuid          NOT NULL REFERENCES hands(id) ON DELETE CASCADE,
  user_id       uuid          NOT NULL,             -- denormalized for RLS performance
  street        text          NOT NULL CHECK (street IN ('preflop','flop','turn','river','showdown')),
  action_order  smallint      NOT NULL,
  seat          smallint      NOT NULL,
  screen_name   text          NOT NULL,
  action        text          NOT NULL CHECK (action IN (
                  'post_sb','post_bb','fold','check','call',
                  'bet','raise','all_in','show','muck','collect'
                )),
  amount        numeric(12,4),                      -- for call/bet/collect; raise increment for raises
  raise_to      numeric(12,4),                      -- total to-call after a raise (not the same as amount)
  is_all_in     boolean       NOT NULL DEFAULT false,
  created_at    timestamptz   NOT NULL DEFAULT now()
);

CREATE INDEX idx_hand_actions_hand_street ON hand_actions (hand_id, street, action_order);

ALTER TABLE hand_actions ENABLE ROW LEVEL SECURITY;

CREATE POLICY hand_actions_rls ON hand_actions
  USING     (user_id = auth.uid())
  WITH CHECK (user_id = auth.uid());

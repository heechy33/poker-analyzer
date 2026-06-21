-- 009_multiway_ranges.sql
-- Seed tightened multiway-specific ranges for common postflop scenarios.
-- Multiway rows use action_sequence suffixes "_multiway" or "_mw" to
-- differentiate from the standard heads-up library rows.
-- These are approximations for the HU CFR engine — not true multiway GTO.

-- ============================================================================
-- Limped multiway (3+ players) — caller facing a limp
-- ============================================================================

-- 6-max, CO limper in multiway limped pot (tighter than standard limp)
INSERT INTO range_library (table_size, effective_stack_bb, position, action_sequence, range_string)
VALUES
    (6, 100, 'CO', 'limp_multiway',
     '22-88,A2s-A9s,K8s-KTs,Q9s-QJs,J9s-JTs,T9s,98s,87s,76s,A9o-ATo,KTo+,QJo')
ON CONFLICT (table_size, effective_stack_bb, position, action_sequence) DO NOTHING;

-- 6-max, HJ limper in multiway limped pot
INSERT INTO range_library (table_size, effective_stack_bb, position, action_sequence, range_string)
VALUES
    (6, 100, 'HJ', 'limp_multiway',
     '22-77,A2s-A8s,K7s-K9s,Q8s-QTs,J8s-JTs,T9s,98s,87s,A8o-A9o,KTo,QTo,JTo')
ON CONFLICT (table_size, effective_stack_bb, position, action_sequence) DO NOTHING;

-- 6-max, BTN limper in multiway limped pot
INSERT INTO range_library (table_size, effective_stack_bb, position, action_sequence, range_string)
VALUES
    (6, 100, 'BTN', 'limp_multiway',
     '22-66,A2s-A7s,K5s-K8s,Q6s-Q9s,J7s-J9s,T8s-T9s,98s,87s,A6o-A8o,K9o,Q9o,J9o,T9o')
ON CONFLICT (table_size, effective_stack_bb, position, action_sequence) DO NOTHING;

-- ============================================================================
-- Single-raised multiway (2+ callers of an open) — caller facing open + calls
-- ============================================================================

-- 6-max, CO calls BTN open with 2+ other players in (multiway cold call)
INSERT INTO range_library (table_size, effective_stack_bb, position, action_sequence, range_string)
VALUES
    (6, 100, 'CO', 'vs_BTN_open_call_multiway',
     '77-JJ,ATs-AQs,KJs-KQs,QJs,JTs,T9s,98s,87s,AJo-AQo,KQo')
ON CONFLICT (table_size, effective_stack_bb, position, action_sequence) DO NOTHING;

-- 6-max, HJ calls CO open in multiway
INSERT INTO range_library (table_size, effective_stack_bb, position, action_sequence, range_string)
VALUES
    (6, 100, 'HJ', 'vs_CO_open_call_multiway',
     '88-JJ,ATs-AJs,KJs-KQs,QJs,JTs,T9s,AQo')
ON CONFLICT (table_size, effective_stack_bb, position, action_sequence) DO NOTHING;

-- 6-max, BTN calls UTG open in multiway
INSERT INTO range_library (table_size, effective_stack_bb, position, action_sequence, range_string)
VALUES
    (6, 100, 'BTN', 'vs_UTG_open_call_multiway',
     '88-TT,ATs-AJs,KJs-KQs,QJs,JTs,AQo')
ON CONFLICT (table_size, effective_stack_bb, position, action_sequence) DO NOTHING;

-- 6-max, BB calls UTG open in multiway (defending wide but not trash)
INSERT INTO range_library (table_size, effective_stack_bb, position, action_sequence, range_string)
VALUES
    (6, 100, 'BB', 'vs_UTG_open_call_multiway',
     '66-JJ,A2s-AJs,K8s+,Q9s+,J9s+,T9s,98s,87s,76s,65s,A9o-AJo,KTo+,QJo')
ON CONFLICT (table_size, effective_stack_bb, position, action_sequence) DO NOTHING;

-- 2-max (HU), caller facing open multiway (SB vs BB with 3+ seated — rare but possible)
INSERT INTO range_library (table_size, effective_stack_bb, position, action_sequence, range_string)
VALUES
    (2, 100, 'BB', 'vs_BTN/SB_open_call_multiway',
     '22-TT,A2s-AJs,K8s+,Q9s+,J9s+,T9s,98s,87s,A2o-AJo,K9o+,Q9o+,JTo')
ON CONFLICT (table_size, effective_stack_bb, position, action_sequence) DO NOTHING;

-- ============================================================================
-- Squeeze spots (3bet into multiway pot) — caller facing squeeze
-- ============================================================================

-- 6-max, open raiser calls a squeeze from a later position (multiway)
INSERT INTO range_library (table_size, effective_stack_bb, position, action_sequence, range_string)
VALUES
    (6, 100, 'CO', 'vs_BTN_3bet_call_multiway',
     '88-JJ,ATs-AQs,KQs,AQo')
ON CONFLICT (table_size, effective_stack_bb, position, action_sequence) DO NOTHING;

INSERT INTO range_library (table_size, effective_stack_bb, position, action_sequence, range_string)
VALUES
    (6, 100, 'HJ', 'vs_CO_3bet_call_multiway',
     '99-JJ,AJs-AQs,KQs,AQo')
ON CONFLICT (table_size, effective_stack_bb, position, action_sequence) DO NOTHING;

-- ============================================================================
-- Aggressor multiway — tighter continuation against multiway fields
-- ============================================================================

-- 6-max, UTG open, 2 callers (multiway flop), UTG's opening range tightened
-- These are used when hero IS the preflop aggressor and faces multiway flop.
INSERT INTO range_library (table_size, effective_stack_bb, position, action_sequence, range_string)
VALUES
    (6, 100, 'UTG', 'open_multiway',
     '88+,ATs+,KJs+,QJs,AJo+,KQo')
ON CONFLICT (table_size, effective_stack_bb, position, action_sequence) DO NOTHING;

INSERT INTO range_library (table_size, effective_stack_bb, position, action_sequence, range_string)
VALUES
    (6, 100, 'CO', 'open_multiway',
     '66+,A8s+,KTs+,QTs+,JTs,T9s,98s,AJo+,KQo')
ON CONFLICT (table_size, effective_stack_bb, position, action_sequence) DO NOTHING;

INSERT INTO range_library (table_size, effective_stack_bb, position, action_sequence, range_string)
VALUES
    (6, 100, 'BTN', 'open_multiway',
     '22+,A2s+,K8s+,Q9s+,J9s+,T9s,98s,87s,76s,A9o+,KTo+,QJo')
ON CONFLICT (table_size, effective_stack_bb, position, action_sequence) DO NOTHING;

-- ============================================================================
-- Short "_mw" aliases for common spots (alternative suffix)
-- ============================================================================

INSERT INTO range_library (table_size, effective_stack_bb, position, action_sequence, range_string)
VALUES
    (6, 100, 'BTN', 'vs_CO_open_call_mw',
     '88-TT,ATs-AJs,KJs-KQs,QJs,AQo')
ON CONFLICT (table_size, effective_stack_bb, position, action_sequence) DO NOTHING;

INSERT INTO range_library (table_size, effective_stack_bb, position, action_sequence, range_string)
VALUES
    (6, 100, 'BB', 'vs_BTN_open_call_mw',
     '66-JJ,A2s-AJs,K8s+,Q9s+,J9s+,T9s,98s,87s,76s,A9o-AJo,KTo+,QJo')
ON CONFLICT (table_size, effective_stack_bb, position, action_sequence) DO NOTHING;
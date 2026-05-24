-- Migration 003 (seed): minimal range_library coverage for v1
-- 6-max NLHE, 100bb effective. Approximate GTO-baseline ranges sourced from
-- public 2025 charts (GTO Wizard free tier / RiverOdds preflop pages).
-- Numbers are intentionally rounded — the WASM solver re-solves from these
-- baselines, so the priors only need to be in the right neighbourhood.

INSERT INTO range_library (table_size, effective_stack_bb, position, action_sequence, range_string, source, version) VALUES
  -- ---------- 6-max opens (no preflop action so far) ----------
  (6, 100, 'UTG', 'open',
   '22+,A2s+,K9s+,Q9s+,J9s+,T9s,98s,87s,76s,65s,A9o+,KJo+,QJo',
   'GTOWizard-free-tier', 'v1'),
  (6, 100, 'HJ', 'open',
   '22+,A2s+,K7s+,Q9s+,J8s+,T8s+,97s+,86s+,75s+,64s+,54s,A8o+,KTo+,QTo+,JTo',
   'GTOWizard-free-tier', 'v1'),
  (6, 100, 'CO', 'open',
   '22+,A2s+,K5s+,Q8s+,J8s+,T8s+,97s+,86s+,75s+,64s+,54s,A5o+,K9o+,Q9o+,J9o+,T9o',
   'GTOWizard-free-tier', 'v1'),
  (6, 100, 'BTN', 'open',
   '22+,A2s+,K2s+,Q4s+,J7s+,T7s+,97s+,86s+,75s+,64s+,53s+,A2o+,K7o+,Q9o+,J9o+,T9o,98o',
   'GTOWizard-free-tier', 'v1'),
  (6, 100, 'SB', 'open',
   '22+,A2s+,K6s+,Q8s+,J8s+,T8s+,97s+,86s+,75s+,64s+,A7o+,K9o+,QTo+,JTo',
   'GTOWizard-free-tier', 'v1'),

  -- ---------- 6-max BB calls vs. open ----------
  (6, 100, 'BB', 'vs_BTN_open_call',
   '22-JJ,A2s-AJs,K5s-KQs,Q7s-QJs,J7s+,T7s+,96s+,85s+,75s+,64s+,53s+,A2o-AJo,K8o+,Q9o+,J9o+,T9o',
   'GTOWizard-free-tier', 'v1'),
  (6, 100, 'BB', 'vs_CO_open_call',
   '22-TT,A2s-AJs,K7s-KQs,Q8s+,J8s+,T8s+,97s+,86s+,75s+,64s+,A5o-A9o,KTo-KQo,QTo+,JTo',
   'GTOWizard-free-tier', 'v1'),
  (6, 100, 'BB', 'vs_HJ_open_call',
   '22-99,A2s-ATs,K9s-KQs,Q9s+,J9s+,T9s,98s,87s,76s,A9o-AJo,KJo-KQo,QJo',
   'GTOWizard-free-tier', 'v1'),
  (6, 100, 'BB', 'vs_UTG_open_call',
   '22-99,A2s-ATs,KTs-KQs,QTs+,J9s+,T9s,98s,87s,76s,AJo-AQo,KQo',
   'GTOWizard-free-tier', 'v1'),

  -- ---------- 6-max 3-bet pots ----------
  (6, 100, 'CO', 'vs_UTG_open_3bet',
   'TT+,AQs+,KQs,AKo,A5s:0.5,A4s:0.5',
   'GTOWizard-free-tier', 'v1'),
  (6, 100, 'BTN', 'vs_UTG_open_3bet',
   'JJ+,AQs+,KQs,AKo,A5s:0.5,A4s:0.5',
   'GTOWizard-free-tier', 'v1'),
  (6, 100, 'BTN', 'vs_CO_open_3bet',
   'TT+,AJs+,KQs,AQo+,A5s:0.5,A4s:0.5',
   'GTOWizard-free-tier', 'v1'),
  (6, 100, 'BB', 'vs_BTN_open_3bet',
   'TT+,AJs+,KTs+,QJs,AQo+,A5s:0.5,A4s:0.5',
   'GTOWizard-free-tier', 'v1'),
  (6, 100, 'UTG', 'vs_CO_3bet_call',
   '99-JJ,AJs-AQs,KQs,QJs,JTs,T9s,AQo',
   'GTOWizard-free-tier', 'v1'),
  (6, 100, 'UTG', 'vs_BTN_3bet_call',
   '99-JJ,AJs-AQs,KQs,QJs,JTs,T9s,AQo',
   'GTOWizard-free-tier', 'v1'),
  (6, 100, 'CO', 'vs_BTN_3bet_call',
   '88-JJ,ATs-AQs,KQs,QJs,JTs,T9s,98s,AJo+',
   'GTOWizard-free-tier', 'v1'),

  -- ---------- 6-max default fallbacks (low-confidence) ----------
  (6, 100, 'BB', 'default_call',
   '22-99,A2s-AJs,K9s+,Q9s+,J9s+,T9s,98s,87s,76s,A9o-AJo,KTo+,QTo+,JTo',
   'fallback', 'v1'),
  (6, 100, 'CO', 'default_call',
   '22-TT,A2s-AJs,K9s+,Q9s+,J9s+,T9s,98s,87s,76s,AJo-AQo,KQo',
   'fallback', 'v1'),

  -- ---------- Heads-up (table_size = 2) ----------
  (2, 100, 'BTN/SB', 'open',
   '22+,A2s+,K2s+,Q3s+,J6s+,T6s+,96s+,85s+,75s+,64s+,53s+,42s+,32s,A2o+,K3o+,Q7o+,J7o+,T7o+,97o+,87o,76o',
   'rivers-app', 'v1'),
  (2, 100, 'BB', 'vs_BTN/SB_open_call',
   '22-TT,A2s-AJs,K2s-KQs,Q5s+,J7s+,T7s+,96s+,85s+,75s+,64s+,53s+,A2o-AJo,K7o-KQo,Q9o+,J9o+,T9o,98o,87o',
   'rivers-app', 'v1'),
  (2, 100, 'BTN/SB', 'limp',
   '22-66,A2s-A5s,K2s-K8s,Q4s-Q9s,J6s-J9s,T6s-T9s,96s-98s,86s-87s,75s-76s,65s,54s,A7o-A2o,K7o-K9o,Q8o-Q9o,J8o-J9o,T8o-T9o,97o-98o,87o',
   'rivers-app', 'v1'),
  (2, 100, 'BB', 'limp_check',
   '22+,A2s+,K2s+,Q2s+,J2s+,T5s+,95s+,85s+,75s+,64s+,53s+,42s+,32s,A2o+,K2o+,Q4o+,J6o+,T6o+,96o+,86o+,75o+,65o,54o',
   'rivers-app', 'v1');

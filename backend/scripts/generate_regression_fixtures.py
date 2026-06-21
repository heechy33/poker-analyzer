import asyncio
import json
import os
import sys
from pathlib import Path
from decimal import Decimal
from dataclasses import dataclass
from typing import Sequence, Any, Callable

# Set python path so we can import app modules.
script_dir = Path(__file__).resolve().parent
backend_dir = script_dir.parent
sys.path.insert(0, str(backend_dir))

from app.parser.coinpoker import parse_hand
from app.scenario.builder import build_scenario
from app.services.ingest import (
    actions_from_parsed,
    hand_from_parsed,
    players_from_parsed,
)

# Load jsonschema if available, otherwise write a dummy validator.
try:
    import jsonschema
    HAS_JSONSCHEMA = True
except ImportError:
    HAS_JSONSCHEMA = False

USER_ID = "00000000-0000-0000-0000-000000000001"
UPLOAD_ID = "00000000-0000-0000-0000-000000000002"

RANGE_SEED = {
    (6, "CO", "vs_UTG_open_3bet"): "TT+,AQs+,KQs,AKo",
    (6, "CO", "vs_UTG_3bet_3bet"): "TT+,AQs+,KQs,AKo",
    (6, "UTG", "vs_CO_3bet_call"): "99-JJ,AJs-AQs,KQs,QJs,AQo",
    (2, "BTN/SB", "open"): "22+,A2s+,K7s+,Q9s+,J9s+,T9s,A2o+,K9o+,Q9o+,JTo",
    (2, "BB", "vs_BTN/SB_open_call"): "22-TT,A2s-AJs,K8s+,Q9s+,J9s+,T9s,98s,87s,A2o-AJo,K9o+,Q9o+,JTo",
    (2, "BTN/SB", "limp"): "22-99,A2s-A9s,K5s-K9s,Q7s-Q9s,J7s-J9s,T7s-T9s",
    (2, "BB", "limp_check"): "22+,A2s+,K2s+,Q2s+,J5s+,T5s+,A2o+,K8o+,Q8o+,J8o+,T8o+",
    (6, "CO", "vs_UTG_open_call"): "22-TT,A2s-AJs,K8s+,Q9s+,J9s+,T9s,98s,87s,A2o-AJo,K9o+,Q9o+,JTo",
    (6, "UTG", "open"): "TT+,AQs+,KQs,AKo",
    (6, "BB", "vs_UTG_open_call"): "22-TT,A2s-AJs,K8s+,Q9s+,J9s+,T9s,98s,87s,A2o-AJo,K9o+,Q9o+,JTo",
}

def _build_lookup(seed: dict):
    @dataclass
    class _Row:
        range_string: str
        combo_weights: dict[str, float] | None = None

    async def _lookup(table_size: int, position: str, action_sequence: str):
        val = seed.get((table_size, position, action_sequence))
        if val:
            return _Row(range_string=val)
        return None

    return _lookup

def _load_fixture(name: str):
    fixtures_dir = backend_dir / "tests" / "fixtures" / "coinpoker"
    text = (fixtures_dir / name).read_text(encoding="utf-8")
    parsed = parse_hand(text.splitlines())
    hand = hand_from_parsed(parsed, USER_ID, UPLOAD_ID)
    players = players_from_parsed(parsed, hand.id, USER_ID)
    actions = actions_from_parsed(parsed, hand.id, USER_ID)
    return hand, players, actions

# Define modifications helper functions
def mod_low_spr(env):
    env["pot_bb"] = 100.0
    env["effective_stack_bb"] = 80.0

def mod_deep_stack(env):
    env["pot_bb"] = 50.0
    env["effective_stack_bb"] = 450.0

def mod_large_pot(env):
    env["pot_bb"] = 600.0
    env["effective_stack_bb"] = 450.0

def mod_near_degen(env):
    env["pot_bb"] = 100.0
    env["effective_stack_bb"] = 60.0
    # At SPR 0.6, standard 33%/75% flop sizes collapse to all-in with allin_always;
    # keep the tree solvable while still exercising shallow SPR.
    env["bet_tree"]["allin_always"] = False

def mod_degen_spr(env):
    env["pot_bb"] = 100.0
    env["effective_stack_bb"] = 40.0

def mod_min_pot_1_0(env):
    env["pot_bb"] = 1.0
    env["effective_stack_bb"] = 100.0

def mod_min_pot_1_5(env):
    env["pot_bb"] = 1.5
    env["effective_stack_bb"] = 100.0

# Define scenarios to generate
SCENARIOS = [
    # --- HU High Confidence ---
    {"name": "hu_high_clean_flop_btn_vs_bb.json", "source": "hand_004.txt", "street": "flop", "seed": RANGE_SEED},
    {"name": "hu_high_clean_turn_co_vs_utg.json", "source": "multiway_flop_hu_turn.txt", "street": "turn", "seed": RANGE_SEED},
    {"name": "hu_high_clean_river_co_vs_utg.json", "source": "multiway_flop_hu_turn.txt", "street": "river", "seed": RANGE_SEED},
    {"name": "hu_high_clean_3bet_co_vs_utg.json", "source": "hand_003.txt", "street": "flop", "seed": RANGE_SEED},
    {"name": "hu_high_clean_limped_btn_vs_bb.json", "source": "hand_005.txt", "street": "flop", "seed": RANGE_SEED},
    
    # --- HU Medium (Fallback / Gap) ---
    {"name": "hu_medium_library_fallback_flop.json", "source": "hand_004.txt", "street": "flop", "seed": {}},
    {"name": "hu_medium_range_gap_turn.json", "source": "multiway_flop_hu_turn.txt", "street": "turn", "seed": {}},
    
    # --- HU Medium (Borderline Inputs) ---
    {"name": "hu_medium_borderline_low_spr.json", "source": "hand_004.txt", "street": "flop", "seed": RANGE_SEED, "mod": mod_low_spr},
    {"name": "hu_medium_borderline_deep_stack.json", "source": "hand_004.txt", "street": "flop", "seed": RANGE_SEED, "mod": mod_deep_stack},
    {"name": "hu_medium_borderline_large_pot.json", "source": "hand_004.txt", "street": "flop", "seed": RANGE_SEED, "mod": mod_large_pot},
    
    # --- Multiway Low Confidence ---
    {"name": "multiway_low_approx_flop.json", "source": "multiway_flop_hu_turn.txt", "street": "flop", "seed": RANGE_SEED},
    {"name": "multiway_low_approx_fallback.json", "source": "multiway_flop_hu_turn.txt", "street": "flop", "seed": {}},
    
    # --- Near-degenerate SPR ---
    {"name": "near_degenerate_spr_0_6.json", "source": "hand_004.txt", "street": "flop", "seed": RANGE_SEED, "mod": mod_near_degen},
    
    # --- Degenerate SPR / Tree Rejections ---
    {"name": "degenerate_allin_tree_0_4_spr.json", "source": "hand_004.txt", "street": "flop", "seed": RANGE_SEED, "mod": mod_degen_spr},
    
    # --- Min Pot Edge Cases ---
    {"name": "min_pot_1_0_bb.json", "source": "hand_004.txt", "street": "flop", "seed": RANGE_SEED, "mod": mod_min_pot_1_0},
    {"name": "min_pot_1_5_bb.json", "source": "hand_004.txt", "street": "flop", "seed": RANGE_SEED, "mod": mod_min_pot_1_5},
]

async def main():
    schema_path = backend_dir / "schemas" / "scenario_envelope.json"
    schema = json.loads(schema_path.read_text())
    
    out_dir = backend_dir.parent / "solver-wasm" / "tests" / "fixtures" / "regression"
    out_dir.mkdir(parents=True, exist_ok=True)
    
    print(f"Generating fixtures in: {out_dir.resolve()}")
    
    for item in SCENARIOS:
        name = item["name"]
        source = item["source"]
        street = item["street"]
        seed = item["seed"]
        mod = item.get("mod")
        
        print(f"Building scenario for {name} (source={source}, street={street})...")
        
        try:
            hand, players, actions = _load_fixture(source)
            lookup = _build_lookup(seed)
            res = await build_scenario(hand, players, actions, street, lookup)
            
            envelope = res["scenario"]
            envelope["hero_position"] = res["metadata"]["hero_position"]
            if mod:
                mod(envelope)
            envelope["max_iterations"] = 10
            envelope["target_exploitability_bb"] = 999.0
                
            # Validate against schema
            if HAS_JSONSCHEMA:
                jsonschema.validate(instance=envelope, schema=schema)
                print(f"  - Schema validation passed for {name}")
            else:
                print(f"  - [WARNING] jsonschema not installed, skipped validation for {name}")
                
            # Write to file
            out_file = out_dir / name
            out_file.write_text(json.dumps(envelope, indent=2), encoding="utf-8")
            print(f"  - Wrote {out_file.name}")
            
        except Exception as e:
            print(f"  - [ERROR] Failed to build {name}: {e}", file=sys.stderr)

if __name__ == "__main__":
    asyncio.run(main())

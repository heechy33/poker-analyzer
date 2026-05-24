//! Deserialisation for the backend `ScenarioEnvelope` JSON.
//!
//! The Python backend (`app/scenario/builder.py`) emits an envelope with
//! `hero_range` / `villain_range` keyed by *hand classes* (PIO-style strings
//! like `"AKs"`, `"QQ"`, `"AKo"`) and position labels in `oop_player` /
//! `ip_player`. The solver only cares about OOP vs IP, so we accept two
//! equivalent encodings of the same information:
//!
//! 1. **OOP/IP-keyed** (preferred, written by the frontend after consulting
//!    metadata): `oop_range` + `ip_range`. The solver uses these directly.
//! 2. **Hero/villain-keyed** (the raw backend envelope): `hero_range` +
//!    `villain_range`. To map hero onto OOP/IP we additionally require
//!    `hero_position` (which the frontend reads from the scenario metadata
//!    and merges into the envelope before calling `init_game`).
//!
//! If both forms are provided, the explicit OOP/IP form wins. If neither is
//! present, [`ScenarioEnvelope::resolve_ranges`] returns an error.
//!
//! No backend Python change is required to support form (2); the WASM caller
//! is expected to inject `hero_position` from the metadata sibling object.

use std::collections::BTreeMap;

use serde::Deserialize;

pub type HandClassWeights = BTreeMap<String, f32>;

#[derive(Debug, Clone, Deserialize)]
pub struct ScenarioEnvelope {
    /// 3 = flop, 4 = turn, 5 = river. Cards in the standard 2-char form
    /// (`"As"`, `"Td"`, `"9c"`).
    pub board: Vec<String>,

    /// Pot at the *start* of the street (before any postflop action).
    pub pot_bb: f64,

    /// Effective stack between OOP and IP at the start of the street.
    pub effective_stack_bb: f64,

    /// Position label of the OOP player (e.g. `"BB"`, `"UTG"`).
    pub oop_player: String,

    /// Position label of the IP player.
    pub ip_player: String,

    /// Hero's position label. Optional; required if only hero/villain ranges
    /// are provided.
    #[serde(default)]
    pub hero_position: Option<String>,

    #[serde(default)]
    pub oop_range: Option<HandClassWeights>,
    #[serde(default)]
    pub ip_range: Option<HandClassWeights>,

    #[serde(default)]
    pub hero_range: Option<HandClassWeights>,
    #[serde(default)]
    pub villain_range: Option<HandClassWeights>,

    pub bet_tree: BetTreeConfig,
}

#[derive(Debug, Clone, Deserialize)]
pub struct BetTreeConfig {
    /// Bet sizes available on the flop, as pot-percent strings (e.g. `"33%"`).
    pub flop: Vec<String>,
    /// Bet sizes available on the turn.
    pub turn: Vec<String>,
    /// Bet sizes available on the river.
    pub river: Vec<String>,
    /// If `true`, an all-in option is always appended to every bet list.
    #[serde(default)]
    pub allin_always: bool,
}

#[derive(Debug, Clone)]
pub struct ResolvedRanges {
    pub oop: HandClassWeights,
    pub ip: HandClassWeights,
}

impl ScenarioEnvelope {
    /// Resolve OOP / IP ranges from whichever form the caller provided.
    pub fn resolve_ranges(&self) -> Result<ResolvedRanges, String> {
        if let (Some(oop), Some(ip)) = (&self.oop_range, &self.ip_range) {
            return Ok(ResolvedRanges {
                oop: oop.clone(),
                ip: ip.clone(),
            });
        }

        let hero = self
            .hero_range
            .as_ref()
            .ok_or_else(|| "envelope missing hero_range / oop_range".to_string())?;
        let villain = self
            .villain_range
            .as_ref()
            .ok_or_else(|| "envelope missing villain_range / ip_range".to_string())?;
        let hero_pos = self.hero_position.as_deref().ok_or_else(|| {
            "envelope provides hero_range/villain_range but no hero_position; \
             frontend must inject the hero_position from scenario metadata"
                .to_string()
        })?;

        if hero_pos == self.oop_player {
            Ok(ResolvedRanges {
                oop: hero.clone(),
                ip: villain.clone(),
            })
        } else if hero_pos == self.ip_player {
            Ok(ResolvedRanges {
                oop: villain.clone(),
                ip: hero.clone(),
            })
        } else {
            Err(format!(
                "hero_position {hero_pos:?} matches neither oop_player {:?} \
                 nor ip_player {:?}",
                self.oop_player, self.ip_player
            ))
        }
    }
}

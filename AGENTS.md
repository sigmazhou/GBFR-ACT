# AGENTS.md

Guidance for AI coding sessions working in this repo. Read this before making changes.

## What this repo is

GBFR-ACT is a DPS meter / combat log tool for **Granblue Fantasy: Relink** (GBFR) — the
"ACT" (Advanced Combat Tracker) equivalent for this game. It injects into the running
game process, hooks internal game functions via memory-signature scanning, and streams
damage/party/death events over a local websocket to a browser-based overlay UI.

Two halves:
- **Python side** (`gbfr_act/`, `act_ws.py`): injected into `granblue_fantasy_relink.exe`,
  finds game functions via byte-pattern (AOB) scans, installs inline hooks, reads game
  memory through fixed-offset `ctypes` structs, and broadcasts parsed events over a
  websocket server.
- **Frontend** (`act_ws.html`): a single-file Vue.js app (no build step, plain
  `<script>` tags) that connects to the websocket and renders the live meter, per-actor
  skill breakdown, charts, and history.

## High-level structure

- `act_ws.py` — entry point. Finds the game process, injects, subclasses `Act` to wire
  hook callbacks (`on_damage`, `on_load_party`, `on_enter_area`, `on_inc_death_cnt`) to
  websocket broadcasts. Run this to install the tool into a live game.
- `gbfr_act/act/__init__.py` — the core `Act` class. Finds hook addresses via AOB
  signatures, installs/uninstalls hooks, parses raw hook args into `Actor`/damage data,
  and does **party attribution** (mapping a damage source to a party slot/name).
- `gbfr_act/act/structures.py` — `Actor`, `ProcessDamageSource`, `Weapon`, `Sigil`,
  `OverMastery`, `VBuffer`: `ctypes`-based views into game memory at fixed offsets.
- `gbfr_act/utils/` — process injection, the `Hook` wrapper (EasyHook-based inline
  hooking), the AOB pattern scanner (`pattern.py`), a websocket server, RPC/pipe IPC,
  and raw memory-read helpers (`size_t_from`, `u32_from`, etc.).
- `act_ws.html` — the actual meter UI. No bundler — edit the inline `<script>` directly
  and be careful with syntax; nothing lints it for you.
- `assets/` — i18n tables (`act_ws_texts.js`, `dump_texts.js`) mapping game hash IDs to
  translated actor/skill/sigil/item/weapon names.
- `CHANGELOG.md` — actively maintained fix log (see below — update this for real fixes).
- `test_act.py` — a live-injection smoke harness, not a unit test suite. There is no
  meaningful automated test coverage; changes to hook logic are validated by reasoning
  + live-game testing, not CI.

## gbfr-logs: the reference implementation

`onelittlechildawa/gbfr-logs` (a fork of `false-spring/gbfr-logs`, both on GitHub) is an
independent Rust/Tauri DPS meter for the same game, hooking the **same compiled game
code**. This repo has a standing practice of cross-referencing gbfr-logs whenever a game
update breaks something here, since both tools target identical binary offsets/signatures.

**When a signature/offset breaks after a game update, check gbfr-logs before re-deriving
from scratch.** You can shallow-clone it for comparison:
```
git clone --depth 1 https://github.com/onelittlechildawa/gbfr-logs.git /tmp/gbfr-logs-ref
```
Relevant files there: `src-hook/src/hooks/*.rs` (the hooking/parsing logic, equivalent to
`gbfr_act/act/__init__.py` + `structures.py`) and `src-tauri/src/parser/v1/mod.rs` (the
event-processing/attribution logic, equivalent to the party-attribution parts of `Act` +
what `act_ws.html` does with events).

**Important nuance, learned the hard way:** don't port a gbfr-logs snippet blindly — port
the *architecture*. Example: both codebases have identical logic for excluding placeholder
party identities (`is_online` / slot-0-only checks). That logic was never the bug. The bug
was that this codebase's frontend used *failure to resolve identity* as a reason to drop a
damage event entirely, whereas gbfr-logs never gates damage recording on identity
resolution — identity is joined in afterward, purely for display, and is allowed to be
missing. Skim the surrounding control flow in gbfr-logs, not just the matching snippet.

## CHANGELOG.md requirement

Update `CHANGELOG.md` for any real bug fix or behavioral change (not for pure refactors/
debug tooling unless it's what shipped). Format:
- New dated section `## YYYY-MM-DD` appended at the bottom (file is in ascending
  chronological order, oldest first).
- `### Fixed` (or `### Added` / `### Known limitations`) bullets.
- **Keep it concise**: motivation/symptom → root cause → high level idea → important
  implementation details, if any. Do not narrate the investigation or list every file
  touched — that's what `git diff`/`git log` are for. A few sentences per fix is the
  right length; do not write multi-paragraph essays.

## Things worth knowing before touching hook/attribution code

- **Signatures are fragile by design.** They're byte patterns (AOB) against compiled
  game code and break on every game update, especially major ones (comments reference a
  "2.0"/"DLC" update repeatedly). The established pattern: wrap each *optional* hook's
  setup in `try/except`, log the error, and continue with that one feature disabled.
  Only the core damage hook (`process_damage_evt_hook`) is required to start at all.
- **`Act._debug = True`** and `_dump_matches()` in `gbfr_act/act/__init__.py` exist for
  diagnosing broken signatures/offsets live (dumps raw struct bytes / all candidate match
  sites for a pattern) — reach for these instead of guessing when a hook stops firing.
- **Party attribution is cosmetic, not gating.** `party_index_of()` / `player_identities`
  answer "what slot/name/color does this actor have," and legitimately return `-1` when
  unresolved (e.g. AI-controlled solo teammates never get a cached identity, since they
  permanently look like an unfilled online slot). **Never use `party_idx === -1` as a
  reason to drop an event.** The correct gate for "is this a real player hit" is whether
  the source resolves to a player-character actor at all — in this codebase that's a
  `Pl`-prefixed type name (see `is_player_actor_type()` in `act_ws.html`); enemies are
  `Em`-prefixed. This exact confusion caused a real bug (AI teammates' damage silently
  dropped in solo play, fixed 2026-07-17) — check for the same mistake in any similar
  "should we record this" logic.
- **Pet/summon/child actors are already resolved to their owning player** server-side
  (Python `Actor.parent`, in `_on_process_damage_evt`) before events reach the frontend —
  the frontend generally shouldn't need to re-derive ownership.
- **No build step for `act_ws.html`.** It's one big inline `<script>` block; there's no
  bundler/linter to catch syntax mistakes. `node --check` is a decent sanity check if
  Node works in your environment (it may not — this environment's Node install had a
  broken `icu4c` dylib dependency; don't burn time fixing the environment for a quick
  syntax check, just review the diff carefully).

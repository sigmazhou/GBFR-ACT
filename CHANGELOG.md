# Changelog

## 2026-07-15

### Fixed
- Restored compatibility with the game's major DLC update, which shifted internal
  memory layouts and broke damage tracking. Offset fixes were cross-verified
  against [onelittlechildawa/gbfr-logs](https://github.com/onelittlechildawa/gbfr-logs)'s
  equivalent 2.0.2 compatibility fix, since both tools hook the same game function
  via an identical byte signature.
  - `ProcessDamageSource` struct offsets updated: `damage` (`0xD0`→`0xD4`),
    `flags` (`0xD8`→`0xE8`), `action_id` (`0x154`→`0x16C`), `dmg_cap`
    (`0x264`→`0x2BC`).
  - Removed `ProcessDamageSource.attack_rate`: its old offset (`0xD4`) now
    aliases the `damage` field and was unused elsewhere in the codebase.
  - `Actor.idx`: the old party/instance index at `+0x170` no longer exists in
    the updated game layout. Replaced with a synthetic per-instance ID cache
    keyed by actor pointer, so each actor still gets a stable, unique identifier.
  - `Actor.parent`: fixed the dragon-form (`Pl2000`) parent offset
    (`0xD488`→`0x1CA98`), and added parent-resolution offsets for `Wp2290` and
    `Pl0600PlantRose`.
  - `Actor.party_index` offset updated (`0x230`→`0x22C`).
- Hardened `Act.__init__` startup: a single unmatched byte signature used to
  crash the entire tool. DoT tracking, area-enter tracking, death-count
  tracking, and the party/member-info signature group are now each wrapped so
  a failed scan disables just that feature (with a logged error) instead of
  preventing the tool from starting. The core damage hook remains required.
- Fixed the core damage hook silently dropping almost all hits after the DLC
  update. `process_damage_evt`'s return value was declared as a full
  `c_size_t`, but the recompiled function now only guarantees its low byte
  (`AL`) as the "was this hit processed" flag — the upper bytes are leftover
  register garbage. Reading the full 8 bytes made the processed/not-processed
  gate essentially random. Declared the hook's return type as `c_uint8`
  instead, so only the meaningful byte is read. Diagnosed by dumping the raw
  event struct and cross-referencing an on-screen damage number, which also
  confirmed the `damage` offset fix above (`0xD4`) is correct.
- Fixed the damage meter showing fight time but no damage rows. The frontend
  (`act_ws.html`) drops any damage event whose source doesn't resolve to a
  party slot (`party_idx === -1`), and that resolution depended entirely on
  `build_team_map()`'s global party-table pointer walk, which still fails to
  find its signature (`p_qword_1467572B0`) post-DLC. Rather than keep
  re-deriving that broken pointer chain, ported gbfr-logs' replacement
  mechanism instead: their 2.0.2 fix abandoned the same party-table walk in
  favor of hooking the function that refreshes a player's identity snapshot
  and reading a stable per-actor `player_key` field. Added the equivalent
  here:
  - `Actor.player_key` (new): reads `+0x1AB40`, valid only on concrete player
    actors.
  - New hook on the identity-refresh function, found via a fully literal
    byte signature (no wildcards, reused directly from gbfr-logs since it's
    the same compiled game code): caches each party slot's `player_key`,
    `display_name`, `character_name`, and online status as identities are
    refreshed (`Act._on_refresh_player_identity`).
  - `Act.party_index_of()` (new): joins a damage source's `player_key`
    against the cached identities to recover its party slot, falling back to
    the (currently non-functional) old `team_map` first if that ever starts
    resolving again.
  - This restores party attribution for the damage meter; it does not restore
    the party-member equipment/sigil panel, which still depends on the
    separately-broken `Actor.Offsets` signature group.
- Fixed fights no longer separating into distinct records. The frontend only
  ever starts a new record on an `enter_area` event, which comes from
  `on_enter_area_hook` — one of the signatures that still fails to resolve
  post-DLC (`e8 * * * * c5 ? ? ? c5 f8 29 45 ? c7 45 ? ? ? ? ?` now matches 2+
  locations, ambiguous). Rather than keep hunting for a new unique signature,
  ported gbfr-logs' own documented fallback for this exact situation: they run
  a periodic inactivity check (`AUTO_SAVE_INACTIVITY_MS = 120_000`) that
  auto-finishes an in-progress encounter if it's had damage but seen no
  activity for 2 minutes, so encounters still get split even when their
  precise "battle end" hook is unavailable. Added the equivalent in
  `act_ws.html`'s existing 200ms update loop: `AUTO_SPLIT_INACTIVITY_MS`
  (2 minutes, same threshold) archives a record with damage once it's been
  idle that long, which is enough for the next hit to start a fresh record.
  This is a JS-only change; no game-side signature was fixed. `on_enter_area`
  remains broken, so fights won't split until ~2 minutes after the last hit,
  rather than the instant the next area loads.

### Known limitations
- `Actor.canceled_action` (`0xBFF8`) could not be cross-verified (no
  equivalent in gbfr-logs) and was left unchanged; may need re-derivation if
  canceled-action damage looks wrong.
- Character identity (name/type ID) is read live from the game's own RTTI, so
  new playable characters are attributed correctly without any table update.
  However, the friendly skill/move name table (`assets/act_ws_texts.js`) only
  covers the base roster through `PL1900` and has no entries for
  Sandalphon/Seofon/Tweyen or the newest DLC characters — their skills will
  display with raw IDs instead of names until that table is extended.

## 2026-07-16

### Fixed
- Confirmed `on_enter_area`'s signature is fully dead post-DLC, not just
  ambiguous. Added debug tooling to investigate (`Act._debug`): `_dump_matches()`
  logs every match for a pattern (call site + resolved call target), and a
  probe mode installs a passthrough+logging hook on every distinct candidate
  target so we can see which one, if any, fires on a live area transition.
  The pattern now matches 11 unrelated call sites (a generic "spill a float to
  a local" compiler idiom, not a specific function anymore); none of them
  fired when actually transitioning areas in-game. Also hardened `install()`/
  `uninstall()` to catch per-hook install/enable failures individually (one
  candidate hit `EasyHookException 0x1E7: Hooking near conditional jumps is
  not supported`), so a bad probe address can't crash startup.
- Replaced the dead `on_enter_area` signature with gbfr-logs' battle-end hook
  instead of continuing to chase a new area-enter signature. This targets a
  completely different, fully literal signature (no wildcards, same compiled
  game code as the identity-refresh hook) for the quest result-reward setup
  function, which fires once per finished fight — arguably a more precise
  "split the record now" signal than area-enter ever was, since it fires
  right when the fight ends rather than waiting for the next area to load.
  `Act._on_battle_end` replaces `Act._on_enter_area`; the public
  `Act.on_enter_area()` override point (used by `act_ws.py`) is unchanged, just
  now triggered by this hook. The 2-minute inactivity fallback added yesterday
  remains in place as a backstop in case this hook's signature breaks too.

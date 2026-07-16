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

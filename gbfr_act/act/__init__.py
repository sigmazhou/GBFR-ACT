import logging

from gbfr_act.utils.hook import Hook

from .utils import *
from .structures import *


def ensure_same(args):
    if len(s := set(args)) != 1: raise ValueError(f'not same {args=}')
    return s.pop()


def _dump_matches(name, pattern, scanner, enabled):
    if not enabled: return
    try:
        matches = list(scanner.search(pattern))
    except Exception as e:
        print(f'[debug] {name}: search error: {e!r}')
        return
    print(f'[debug] {name}: {len(matches)} match(es) for {pattern!r}')
    for call_addr, args in matches:
        print(f'[debug]   call_at={call_addr:#x} captured={[hex(a) for a in args]}')


class Act:
    _sys_key = '_act_'
    _debug = False  # set True to dump raw damage-hook hits to console for offset/ABI diagnosis
    _invalid_player_key = 0x887AE0B0

    def __init__(self):
        self.server = get_server()
        scanner = Process.current.base_scanner()

        p_process_damage_evt, = scanner.find_val('e8 * * * * 66 83 bc 24 ? ? ? ? ?')
        # Game 2.0 recompiled this function returning a bool: only the low byte (AL) is a
        # meaningful "was this a processed hit" flag, the rest of the register is leftover
        # garbage. Reading it as a full c_size_t used to work pre-DLC but now corrupts the gate.
        self.process_damage_evt_hook = Hook(p_process_damage_evt, self._on_process_damage_evt, ctypes.c_uint8, [
            ctypes.c_size_t,
            ctypes.c_size_t,
            ctypes.c_size_t,
            ctypes.c_uint8
        ])

        # The signatures below are more fragile across game updates than the core
        # damage hook. If one no longer matches, disable just that feature instead
        # of taking down the whole tool.
        self.process_dot_evt_hook = None
        try:
            PROCESS_DOT_EVT_SIG = '44 89 74 24 ? 48 ? ? ? ? 48 ? ? e8 * * * * 4c ? ? ? ? ? ?'
            _dump_matches('process_dot_evt', PROCESS_DOT_EVT_SIG, scanner, Act._debug)
            p_process_dot_evt, = ensure_same(map(tuple, scanner.find_vals(PROCESS_DOT_EVT_SIG)))
            self.process_dot_evt_hook = Hook(p_process_dot_evt, self._on_process_dot_evt, ctypes.c_size_t, [
                ctypes.c_size_t,
                ctypes.c_size_t
            ])
        except Exception:
            logging.error('failed to locate process_dot_evt; DoT damage tracking disabled', exc_info=True)

        # Confirmed dead post-DLC as of 2026-07-16: this pattern now matches 11 unrelated
        # call sites (a generic "spill a float to a local" compiler idiom, not a specific
        # function anymore), and none of them fired on a live area transition. Disabled
        # but kept for reference / in case a future game patch changes things back.
        # Superseded by the battle-end hook further down.
        self.on_enter_area_hook = None
        if False:
            try:
                ON_ENTER_AREA_SIG = 'e8 * * * * c5 ? ? ? c5 f8 29 45 ? c7 45 ? ? ? ? ?'
                _dump_matches('on_enter_area', ON_ENTER_AREA_SIG, scanner, Act._debug)
                # This pattern's `*` capture resolves to the call's target function address,
                # not the call site. It used to match 2+ call sites; tolerate that (like
                # process_dot_evt above) as long as they all target the same function,
                # instead of requiring the raw match position to be unique.
                p_on_enter_area, = ensure_same(map(tuple, scanner.find_vals(ON_ENTER_AREA_SIG)))
                self.on_enter_area_hook = Hook(p_on_enter_area, self._on_enter_area, ctypes.c_uint64, [
                    ctypes.c_uint,
                    ctypes.c_uint64,
                    ctypes.c_uint64,
                    ctypes.c_uint64,
                ])
            except Exception:
                logging.error('failed to locate on_enter_area; area-enter tracking disabled', exc_info=True)

        # gbfr-logs' battle-end hook: a completely different, fully literal signature (no
        # wildcards, same compiled game code as the identity-refresh hook) for the quest
        # result-reward setup function, which fires once per finished fight - a more
        # precise "split the record now" signal than area-enter ever was.
        self.on_battle_end_hook = None
        try:
            p_on_battle_end = scanner.find_address(
                "41 56 56 57 53 48 83 ec 38 48 89 ce 48 8b 0d ? ? ? ? 48 8d 54 24 30 41 b8 ab 4e f1 51 e8 ? ? ? ? 48 8b 44 24 30 48 85 c0 0f 84 ? ? ? ? 48 8b 58 18 4c 8b 70 20 4c 39 f3 0f 84 ? ? ? ? 48 8d 7c 24 2c"
            )
            self.on_battle_end_hook = Hook(p_on_battle_end, self._on_battle_end, None, [
                ctypes.c_size_t
            ])
        except Exception:
            logging.error('failed to locate on_battle_end; fights will only split after the inactivity timeout', exc_info=True)

        self.on_inc_death_cnt_hook = None
        try:
            p_on_inc_death_cnt, = scanner.find_val('e8 * * * * 49 ? ? 48 ? ? ? ? ? ? 83 78 ? ?')
            self.on_inc_death_cnt_hook = Hook(p_on_inc_death_cnt, self._on_inc_death_cnt, None, [
                ctypes.c_void_p
            ])
        except Exception:
            logging.error('failed to locate on_inc_death_cnt; death-count tracking disabled', exc_info=True)

        self.p_qword_1467572B0 = None
        try:
            self.p_qword_1467572B0, = scanner.find_val("48 ? ? * * * * 83 49 ? ? e8 ? ? ? ?")
            self._ui_actor_off, = scanner.find_val("48 ? ? <? ? ? ?> 48 ? ? ? ? ? ? 75 ? 48 ? ? ? ? ? ? 48 39 86")
            Actor.Offsets.p_data_off, = scanner.find_val("48 ? ? <? ? ? ?> 89 86 ? ? ? ? 44 89 96")
            Actor.Offsets.p_data_sigil_off, = scanner.find_val("49 89 84 24 <? ? ? ?> 48 ? ? 74 ? 49 ? ? ? ? ? ? ? 48 89 43 ? 48 89 8b ? ? ? ?")
            Actor.Offsets.p_data_weapon_off, = scanner.find_val("48 ? ? <?> 48 ? ? ? 48 ? ? e8 ? ? ? ? 31 ? 83 bf ? ? ? ? ?")
            Actor.Offsets.p_data_over_mastery_off = scanner.find_val(
                "49 ? ? <? ? ? ?> 49 ? ? ? ? ? ? ? e8 ? ? ? ? 49 ? ? ? ? ? ? 49 ? ? ? ? ? ? ? 41"
            )[0] + scanner.find_val(
                "49 ? ? ? <? ? ? ?> e8 ? ? ? ? 41 ? ? e9"
            )[0]
        except Exception:
            logging.error('failed to locate party/member-info signatures; party tracking disabled', exc_info=True)
            self.p_qword_1467572B0 = None

        # Fallback party attribution: the party-table walk above no longer resolves in
        # game 2.0. This hooks the function that refreshes a player's identity snapshot
        # (fully literal signature, no wildcards) and reads a stable per-actor player key
        # to join damage-event sources against cached identities, independent of the
        # broken global party-table pointer.
        self.refresh_player_identity_hook = None
        try:
            p_refresh_player_identity = scanner.find_address(
                "55 41 57 41 56 41 54 56 57 53 48 83 ec 70 48 8d 6c 24 70 48 c7 45 f8 fe ff ff ff 80 b9 bc 5e 00 00 00"
            )
            self.refresh_player_identity_hook = Hook(p_refresh_player_identity, self._on_refresh_player_identity, None, [
                ctypes.c_size_t
            ])
        except Exception:
            logging.error('failed to locate refresh_player_identity; party attribution disabled', exc_info=True)

        self.i_ui_comp_name = ctypes.CFUNCTYPE(ctypes.c_char_p, ctypes.c_size_t)
        self.team_map = None
        self.member_info = None
        self.player_identities = {}

    def actor_data(self, actor: Actor):
        return actor.type_name, actor.idx, actor.type_id, self.party_index_of(actor)

    def party_index_of(self, actor: Actor):
        if self.team_map and actor.address in self.team_map:
            return self.team_map[actor.address]
        try:
            player_key = actor.player_key
        except Exception:
            player_key = 0
        if player_key and player_key != self._invalid_player_key:
            for party_index, identity in self.player_identities.items():
                if identity['player_key'] == player_key:
                    return party_index
        return -1

    def build_team_map(self):
        if self.team_map is not None: return
        if self.p_qword_1467572B0 is None: return
        self.team_map = {}
        qword_1467572B0 = size_t_from(self.p_qword_1467572B0)
        p_party_base = size_t_from(qword_1467572B0 + 0x20)
        p_party_tbl = size_t_from(p_party_base + 0x10 * (size_t_from(qword_1467572B0 + 0x38) & 0x6C4F1B4D) + 8)
        if p_party_tbl != size_t_from(qword_1467572B0 + 0x10) and (p_party_data := size_t_from(p_party_tbl + 0x30)):
            party_start = size_t_from(p_party_data + 0x18)
            party_end = size_t_from(p_party_data + 0x20)
            for i, p_data in enumerate(range(party_start, party_end, 0x10)):
                a1 = size_t_from(p_data + 8)
                if (self.i_ui_comp_name(v_func(a1, 0x8))(a1) == b'ui::component::ControllerPlParameter01' and
                        (p_actor := size_t_from(a1 + self._ui_actor_off))):
                    p_actor_data = size_t_from(p_actor + 0x70)
                    self.team_map[p_actor_data] = i
                    print(f'[{i}] {p_actor_data=:#x}')

        self.member_info = [None, None, None, None, None, ]
        for p_member, i in self.team_map.items():
            try:
                actor = Actor(p_member)
                self.member_info[i] = actor.member_info() | {
                    'common_info': self.actor_data(actor)
                }
            except:
                logging.error(f'build_team_map {i}', exc_info=True)
        self.on_load_party(self.member_info)

    def _on_process_damage_evt(self, hook, p_target_evt, p_source_evt, a3, a4):
        source_evt = ProcessDamageSource(p_source_evt)
        target = source = None
        try:
            self.build_team_map()
            target = Actor(size_t_from(size_t_from(p_target_evt + 8)))
            source = source_evt.actor
        except:
            logging.error('on_process_damage_evt', exc_info=True)
        res = hook.original(p_target_evt, p_source_evt, a3, a4)  # return 0 if it is non processed damage event
        if Act._debug:
            try:
                dump = bytes_from(p_source_evt, 0x2d0).hex()
            except Exception as e:
                dump = f'<read failed: {e}>'
            print(f'[debug] damage_evt hit: res={res!r} p_source_evt={p_source_evt:#x} dump={dump}')
        if not (res and target and source): return res  # or if get target or source failed
        try:
            flags_ = source_evt.flags
            if source.type_id == 0x2af678e8:  # 菲莉宝宝 # Pl0700Ghost
                source = source.parent
                action_id = -0x10  # summon attack
            else:
                source = source.parent or source
                if (1 << 7 | 1 << 50) & flags_:
                    action_id = -1  # link attack
                elif (1 << 13 | 1 << 14) & flags_:
                    action_id = -2  # limit break
                else:
                    action_id = source_evt.action_id
                    if action_id == 0xFFFFFFFF:
                        action_id = source.canceled_action
            self._on_damage(source, target, source_evt.damage, flags_, action_id)
        except:
            logging.error('on_process_damage_evt', exc_info=True)
        return res

    def _on_process_dot_evt(self, hook, a1, a2):
        res = hook.original(a1, a2)
        try:
            dmg = i32_from(a2)
            target = Actor(size_t_from(size_t_from(a1 + 0x18) + 0x70))
            source = Actor(size_t_from(size_t_from(a1 + 0x30) + 0x70))
            source = source.parent or source
            self._on_damage(source, target, dmg, 0, -0x100)
        except:
            logging.error('on_process_dot_evt', exc_info=True)
        return res

    # Unused (see the disabled `if False:` block in __init__): kept for reference in
    # case ON_ENTER_AREA_SIG is ever revisited.
    def _on_enter_area(self, hook, *a):
        res = hook.original(*a)
        try:
            self.team_map = None
            self.member_info = None
            self.player_identities = {}
            self.on_enter_area()
        except:
            logging.error('on_enter_area', exc_info=True)
        return res

    def _on_refresh_player_identity(self, hook, p_record):
        hook.original(p_record)
        try:
            if not p_record: return
            player_key = u32_from(p_record + 0x5ea8)
            if player_key == 0 or player_key == self._invalid_player_key: return
            p_snapshot = size_t_from(p_record + 0x5e60)
            if not p_snapshot: return
            is_online = u32_from(p_snapshot + 0x1c8)
            party_index = u32_from(p_snapshot + 0x22c)
            if is_online > 1 or party_index > 3: return
            # Before an online party is fully populated, the game creates placeholder
            # records for slots 1-3 using the local profile name; skip those.
            if party_index != 0 and not is_online: return
            display_name = VBuffer(p_snapshot + 0x208).raw.decode('utf-8', 'ignore')
            if not display_name: return
            character_name = VBuffer(p_snapshot + 0x1e8).raw.decode('utf-8', 'ignore')
            self.player_identities[party_index] = {
                'player_key': player_key,
                'character_name': character_name,
                'display_name': display_name,
                'is_online': bool(is_online),
            }
        except:
            logging.error('on_refresh_player_identity', exc_info=True)

    def _on_battle_end(self, hook, a1):
        hook.original(a1)
        try:
            self.team_map = None
            self.member_info = None
            self.on_enter_area()
        except:
            logging.error('on_battle_end', exc_info=True)

    def _on_inc_death_cnt(self, hook, a1):
        hook.original(a1)
        try:
            actor = Actor(size_t_from(a1 + 0x10))
            death_cnt = u32_from(a1 + 0xEC)
            self.on_inc_death_cnt(self.actor_data(actor), death_cnt)
        except:
            logging.error('on_inc_death_cnt', exc_info=True)

    def _on_damage(self, source, target, damage, flags, action_id):
        return self.on_damage(self.actor_data(source), self.actor_data(target), damage, flags, action_id)

    def on_damage(self, source, target, damage, flags, action_id):
        pass

    def on_load_party(self, datas):
        pass

    def on_enter_area(self):
        pass

    def on_inc_death_cnt(self, actor, death_cnt):
        pass

    def install(self):
        assert not hasattr(sys, self._sys_key), 'Act already installed'
        self.process_damage_evt_hook.install_and_enable()
        for hook in (self.process_dot_evt_hook, self.on_enter_area_hook, self.on_battle_end_hook, self.on_inc_death_cnt_hook,
                     self.refresh_player_identity_hook):
            if not hook: continue
            try:
                hook.install_and_enable()
            except Exception:
                logging.error(f'failed to install hook at {hook.at:#x}', exc_info=True)
        setattr(sys, self._sys_key, self)
        return self

    def uninstall(self):
        assert getattr(sys, self._sys_key, None) is self, 'Act not installed'
        self.process_damage_evt_hook.uninstall()
        for hook in (self.process_dot_evt_hook, self.on_enter_area_hook, self.on_battle_end_hook, self.on_inc_death_cnt_hook,
                     self.refresh_player_identity_hook):
            if not hook: continue
            try:
                hook.uninstall()
            except Exception:
                logging.error(f'failed to uninstall hook at {hook.at:#x}', exc_info=True)
        delattr(sys, self._sys_key)
        return self

    @classmethod
    def get_or_create(cls):
        if hasattr(sys, cls._sys_key):
            return getattr(sys, cls._sys_key)
        return cls().install()

    @classmethod
    def remove(cls):
        if hasattr(sys, cls._sys_key):
            getattr(sys, cls._sys_key).uninstall()

    @classmethod
    def reload(cls):
        cls.remove()
        return cls.get_or_create()

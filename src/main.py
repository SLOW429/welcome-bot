import asyncio
import time
import traceback
from typing import Any

from highrise import BaseBot
from highrise.__main__ import BotDefinition, main
from highrise.models import Position, AnchorPosition

from config import BOT_TOKEN, ROOM_ID, CONFIG_PATH
from storage import load_json, save_json
from welcomes import build_welcome
from commands import handle_command
from permissions import normalize_username
from dances import DanceManager


DEFAULT_CONFIG = {
    'owner_username': 'Wegza',
    'mods': [],
    'vip_position': {
        'x': 10.0,
        'y': 0.0,
        'z': 10.0,
        'facing': 'FrontRight',
    },
    'floors': {
        'z': {'x': 10.0, 'y': 0.0, 'z': 10.0, 'facing': 'FrontRight'},
        'f1': {'x': 10.0, 'y': 8.0, 'z': 10.0, 'facing': 'FrontRight'},
        'f2': {'x': 10.0, 'y': 16.0, 'z': 10.0, 'facing': 'FrontRight'},
    },
    'vip_users': [],
    'known_users': {},
    'points': {},
    'role_expiries': {},
    'last_daily': {},
    'settings': {
        'message_cooldown': 120,
        'message_points': 4,
        'presence_interval': 600,
        'presence_points': 25,
        'activity_timeout': 600,
        'daily_reward': 30,
        'shop_prices': {
            'vip': 3000,
            'plus': 6500,
            'elite': 12000,
        },
        'role_days': {
            'vip': 7,
            'plus': 7,
            'elite': 7,
        },
    },
}

BOT_SPAWN = Position(17.5, 0.0, 4.0, 'FrontRight')


class WelcomeBot(BaseBot):
    def __init__(self) -> None:
        super().__init__()
        self.runtime_config = load_json(CONFIG_PATH, DEFAULT_CONFIG)
        self._apply_defaults()
        self.user_positions: dict[str, dict[str, Any]] = {}
        self.room_users_by_id: dict[str, Any] = {}
        self.room_users_by_name: dict[str, Any] = {}
        self.dance_manager: DanceManager | None = None
        self.last_message_points: dict[str, float] = {}
        self.last_active: dict[str, float] = {}
        self.presence_task: asyncio.Task | None = None

    def _apply_defaults(self):
        for key, value in DEFAULT_CONFIG.items():
            if key not in self.runtime_config:
                self.runtime_config[key] = value
        self.runtime_config.setdefault('known_users', {})
        self.runtime_config.setdefault('points', {})
        self.runtime_config.setdefault('role_expiries', {})
        self.runtime_config.setdefault('last_daily', {})
        settings = self.runtime_config.setdefault('settings', {})
        default_settings = DEFAULT_CONFIG['settings']
        for key, value in default_settings.items():
            if key not in settings:
                settings[key] = value
            elif isinstance(value, dict):
                settings[key] = {**value, **settings.get(key, {})}

    def log_error(self, where: str, error: Exception):
        print(f'[{where}] {error}')
        traceback.print_exc()

    def save_runtime_config(self) -> None:
        save_json(CONFIG_PATH, self.runtime_config)

    def remember_user(self, username: str, user_id: str) -> None:
        self.runtime_config.setdefault('known_users', {})[normalize_username(username)] = user_id
        self.save_runtime_config()

    def get_known_user_id(self, username: str) -> str | None:
        return self.runtime_config.get('known_users', {}).get(normalize_username(username))

    def get_points(self, user_id: str) -> int:
        return int(self.runtime_config.setdefault('points', {}).get(user_id, 0))

    def add_points(self, user_id: str, amount: int) -> int:
        points = self.runtime_config.setdefault('points', {})
        points[user_id] = max(0, int(points.get(user_id, 0)) + int(amount))
        self.save_runtime_config()
        return points[user_id]

    def spend_points(self, user_id: str, amount: int) -> bool:
        current = self.get_points(user_id)
        if current < amount:
            return False
        self.add_points(user_id, -amount)
        return True

    def grant_role(self, username: str, role_name: str, days: int):
        role_key = role_name.casefold()
        expiries = self.runtime_config.setdefault('role_expiries', {})
        user_key = normalize_username(username)
        user_roles = expiries.setdefault(user_key, {})
        now = int(time.time())
        current_expiry = int(user_roles.get(role_key, 0))
        base = current_expiry if current_expiry > now else now
        user_roles[role_key] = base + (days * 86400)
        self.save_runtime_config()

    def has_role(self, username: str, role_name: str) -> bool:
        self.prune_expired_roles(save=False)
        user_roles = self.runtime_config.get('role_expiries', {}).get(normalize_username(username), {})
        return int(user_roles.get(role_name.casefold(), 0)) > int(time.time())

    def get_active_roles(self, username: str) -> dict[str, int]:
        self.prune_expired_roles(save=False)
        return self.runtime_config.get('role_expiries', {}).get(normalize_username(username), {})

    def prune_expired_roles(self, save: bool = True):
        changed = False
        now = int(time.time())
        expiries = self.runtime_config.setdefault('role_expiries', {})
        for user_key in list(expiries.keys()):
            roles = expiries[user_key]
            for role_name in list(roles.keys()):
                if int(roles[role_name]) <= now:
                    del roles[role_name]
                    changed = True
            if not roles:
                del expiries[user_key]
                changed = True
        if changed and save:
            self.save_runtime_config()

    def mark_active(self, user_id: str):
        self.last_active[user_id] = time.time()

    async def reward_presence_loop(self):
        while True:
            try:
                settings = self.runtime_config.get('settings', {})
                interval = int(settings.get('presence_interval', 600))
                reward = int(settings.get('presence_points', 25))
                timeout = int(settings.get('activity_timeout', 600))
                now = time.time()
                for user_id in list(self.room_users_by_id.keys()):
                    last_seen = self.last_active.get(user_id, 0)
                    if now - last_seen <= timeout:
                        self.add_points(user_id, reward)
                self.prune_expired_roles(save=True)
            except Exception as e:
                self.log_error('reward_presence_loop', e)
            await asyncio.sleep(int(self.runtime_config.get('settings', {}).get('presence_interval', 600)))

    def cache_user(self, user) -> None:
        if not user or not getattr(user, 'id', None) or not getattr(user, 'username', None):
            return
        self.room_users_by_id[user.id] = user
        self.room_users_by_name[normalize_username(user.username)] = user
        self.remember_user(user.username, user.id)

    def remove_user(self, user) -> None:
        if not user:
            return
        self.room_users_by_id.pop(getattr(user, 'id', None), None)
        username = getattr(user, 'username', None)
        if username:
            self.room_users_by_name.pop(normalize_username(username), None)
        uid = getattr(user, 'id', None)
        self.user_positions.pop(uid, None)
        self.last_active.pop(uid, None)
        self.last_message_points.pop(uid, None)

    def find_user_by_username(self, username: str):
        return self.room_users_by_name.get(normalize_username(username))

    async def safe_whisper(self, user_id: str, message: str):
        try:
            await self.highrise.send_whisper(user_id, message)
        except Exception as e:
            self.log_error('safe_whisper', e)

    async def refresh_room_users(self):
        try:
            response = await self.highrise.get_room_users()
            content = getattr(response, 'content', [])
            self.room_users_by_id.clear()
            self.room_users_by_name.clear()
            for entry in content:
                room_user, pos = entry
                self.cache_user(room_user)
                self.mark_active(room_user.id)
                if isinstance(pos, Position):
                    self.user_positions[room_user.id] = {
                        'x': pos.x,
                        'y': pos.y,
                        'z': pos.z,
                        'facing': pos.facing,
                    }
        except Exception as e:
            self.log_error('refresh_room_users', e)

    async def on_start(self, session_metadata):
        print('Welcome bot started.')
        try:
            self.dance_manager = DanceManager(self.highrise)
            await self.refresh_room_users()
            await self.highrise.walk_to(BOT_SPAWN)
            if self.presence_task is None or self.presence_task.done():
                self.presence_task = asyncio.create_task(self.reward_presence_loop())
        except Exception as e:
            self.log_error('on_start', e)

    async def on_user_join(self, user, position):
        try:
            self.cache_user(user)
            self.mark_active(user.id)
            if isinstance(position, Position):
                self.user_positions[user.id] = {
                    'x': position.x,
                    'y': position.y,
                    'z': position.z,
                    'facing': position.facing,
                }
            msg = build_welcome(user.username)
            await self.highrise.chat(msg)
        except Exception as e:
            self.log_error('on_user_join', e)

    async def on_user_leave(self, user):
        try:
            self.remove_user(user)
            if self.dance_manager is not None:
                await self.dance_manager.stop(user.id)
        except Exception as e:
            self.log_error('on_user_leave', e)

    async def on_user_move(self, user, pos: Position | AnchorPosition):
        try:
            self.cache_user(user)
            self.mark_active(user.id)
            if isinstance(pos, Position):
                self.user_positions[user.id] = {
                    'x': pos.x,
                    'y': pos.y,
                    'z': pos.z,
                    'facing': pos.facing,
                }
        except Exception as e:
            self.log_error('on_user_move', e)

    async def on_chat(self, user, message: str):
        try:
            self.cache_user(user)
            self.mark_active(user.id)
            settings = self.runtime_config.get('settings', {})
            cooldown = int(settings.get('message_cooldown', 120))
            reward = int(settings.get('message_points', 4))
            now = time.time()
            last = self.last_message_points.get(user.id, 0)
            if now - last >= cooldown:
                self.add_points(user.id, reward)
                self.last_message_points[user.id] = now
            if self.dance_manager is None:
                self.dance_manager = DanceManager(self.highrise)
            await handle_command(self, user, message)
        except Exception as e:
            self.log_error('on_chat', e)
            try:
                await self.highrise.send_whisper(user.id, 'حصل خطأ أثناء تنفيذ الأمر.')
            except Exception:
                pass


async def run_bot_forever():
    while True:
        try:
            await main([BotDefinition(WelcomeBot(), ROOM_ID, BOT_TOKEN)])
        except Exception as e:
            print('Welcome bot crashed, restarting in 5 seconds...', e)
            traceback.print_exc()
            await asyncio.sleep(5)


if not BOT_TOKEN or not ROOM_ID:
    print('BOT_TOKEN أو ROOM_ID ناقصين في .env')
else:
    asyncio.run(run_bot_forever())

import asyncio
import json
from pathlib import Path


class DanceManager:
    def __init__(self, highrise):
        self.highrise = highrise
        self.user_dance_tasks: dict[str, asyncio.Task] = {}
        self.dances = self._load_dances()
        self.dance_keys = list(self.dances.keys())

    def _load_dances(self):
        path = Path(__file__).with_name('dance_list.json')
        with path.open('r', encoding='utf-8') as f:
            return json.load(f)

    def get_dance_by_number(self, value: str):
        if not value.isdigit():
            return None
        index = int(value) - 1
        if not (0 <= index < len(self.dance_keys)):
            return None
        key = self.dance_keys[index]
        data = self.dances[key]
        return {
            'number': index + 1,
            'key': key,
            'id': data['id'],
            'duration': float(data['duration']),
        }

    async def play_once(self, user_id: str, dance_number: str) -> tuple[bool, str | None]:
        dance = self.get_dance_by_number(dance_number)
        if not dance:
            return False, 'مفيش رقصة بهذا الرقم.'
        try:
            await self.highrise.send_emote(dance['id'], user_id)
            return True, None
        except Exception as e:
            return False, str(e)

    async def start_loop(self, user_id: str, dance_number: str) -> tuple[bool, str | None]:
        dance = self.get_dance_by_number(dance_number)
        if not dance:
            return False, 'مفيش رقصة بهذا الرقم.'

        await self.stop(user_id)

        async def dance_loop():
            try:
                while True:
                    await self.highrise.send_emote(dance['id'], user_id)
                    await asyncio.sleep(max(0.5, dance['duration']))
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

        self.user_dance_tasks[user_id] = asyncio.create_task(dance_loop())
        return True, None

    async def stop(self, user_id: str) -> bool:
        task = self.user_dance_tasks.pop(user_id, None)
        if task is None:
            return False
        task.cancel()
        return True

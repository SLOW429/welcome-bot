import asyncio
import time
from highrise import ResponseError
from permissions import (
    is_owner,
    is_staff,
    is_mod,
    normalize_username,
    can_use_floors,
    can_use_to,
    can_use_vip,
)
from movement import make_position


REACTIONS = {
    'h': 'heart',
    'w': 'wink',
    'c': 'clap',
    'wv': 'wave',
    't': 'thumbs',
}

AR_FLOORS = {'تحت': 'z', 'وسط': 'f1', 'فوق': 'f2'}
SHOP_KEYWORDS = {'shop', 'المتجر'}
POINTS_KEYWORDS = {'points', 'رصيدي', 'فلوسي'}
MYROLES_KEYWORDS = {'myroles', 'رتبي'}
TOP_KEYWORDS = {'top', 'leaderboard', 'توب'}
DAILY_KEYWORDS = {'daily', 'يومي'}


def _parse_count(value: str | None, default: int = 25, maximum: int = 50) -> int:
    if not value:
        return default
    try:
        num = int(value)
    except Exception:
        return default
    return max(1, min(maximum, num))


def _format_remaining(expiry: int) -> str:
    remaining = max(0, int(expiry - time.time()))
    days = remaining // 86400
    hours = (remaining % 86400) // 3600
    if days > 0:
        return f'{days} يوم و {hours} ساعة'
    return f'{hours} ساعة'


async def _burst_reaction(bot, actor_id: str, target_id: str, reaction: str, count: int):
    async def runner():
        for _ in range(count):
            try:
                await bot.highrise.react(reaction, target_id)
                await asyncio.sleep(0.25)
            except Exception as e:
                bot.log_error(f'react:{reaction}', e)
                try:
                    await bot.safe_whisper(actor_id, f'فشل إرسال الريأكشن: {e}')
                except Exception:
                    pass
                break
    asyncio.create_task(runner())


async def _resolve_target(bot, requester, username: str, refresh: bool = True):
    target = bot.find_user_by_username(username)
    if target or not refresh:
        return target
    try:
        await bot.refresh_room_users()
    except Exception as e:
        bot.log_error('refresh_room_users', e)
    return bot.find_user_by_username(username)


async def _moderate(bot, actor, target_name: str, action: str, action_length: int | None = None):
    config = bot.runtime_config
    owner = config['owner_username']
    mods = config['mods']
    target_name = normalize_username(target_name)

    if is_owner(target_name, owner) or is_mod(target_name, mods):
        await bot.safe_whisper(actor.id, 'لا يمكن تنفيذ هذا الأمر على Owner أو Moderator.')
        return True

    target = await _resolve_target(bot, actor, target_name)
    target_id = None

    if target is not None:
        target_id = target.id
        bot.remember_user(target.username, target.id)
    else:
        target_id = bot.get_known_user_id(target_name)

    if not target_id:
        await bot.safe_whisper(actor.id, 'المستخدم غير موجود في الروم ولم يسبق أن حفظه البوت.')
        return True

    try:
        await bot.highrise.moderate_room(target_id, action, action_length)
        suffix = f' لمدة {action_length} دقيقة' if action in ('ban', 'mute') and action_length else ''
        await bot.safe_whisper(actor.id, f'تم تنفيذ {action} على @{target_name}{suffix}')
    except ResponseError as e:
        await bot.safe_whisper(actor.id, f'فشل تنفيذ {action}: {e}')
    except Exception as e:
        await bot.safe_whisper(actor.id, f'خطأ غير متوقع في {action}: {e}')
        bot.log_error(f'moderate:{action}', e)
    return True


async def _shop_message(bot, user_id: str):
    prices = bot.runtime_config.get('settings', {}).get('shop_prices', {})
    lines = ['المتجر:']
    for role in ('vip', 'plus', 'elite'):
        lines.append(f'- {role.upper()}: {prices.get(role, 0)} نقطة / 7 أيام')
    await bot.safe_whisper(user_id, '\n'.join(lines))


async def _top_message(bot, user_id: str):
    points = bot.runtime_config.get('points', {})
    if not points:
        await bot.safe_whisper(user_id, 'لا يوجد ترتيب بعد.')
        return
    ranked = sorted(points.items(), key=lambda x: int(x[1]), reverse=True)[:10]
    lines = ['أفضل اللاعبين:']
    for idx, (uid, score) in enumerate(ranked, start=1):
        room_user = bot.room_users_by_id.get(uid)
        username = room_user.username if room_user else uid
        lines.append(f'{idx}. {username} - {score}')
    await bot.safe_whisper(user_id, '\n'.join(lines))


async def handle_command(bot, user, message: str) -> bool:
    text = message.strip()
    if not text:
        return False

    config = bot.runtime_config
    owner = config['owner_username']
    mods = config['mods']
    lower = text.casefold()
    parts = text.split()
    vip_users = config.get('vip_users', [])

    if lower in POINTS_KEYWORDS:
        await bot.safe_whisper(user.id, f'رصيدك: {bot.get_points(user.id)} نقطة')
        return True

    if lower in DAILY_KEYWORDS:
        last_daily = bot.runtime_config.setdefault('last_daily', {}).get(user.id, 0)
        now = int(time.time())
        if now - int(last_daily) < 86400:
            remaining = 86400 - (now - int(last_daily))
            hours = remaining // 3600
            minutes = (remaining % 3600) // 60
            await bot.safe_whisper(user.id, f'اليومي أخذته بالفعل. ارجع بعد {hours} ساعة و {minutes} دقيقة')
            return True
        reward = int(bot.runtime_config.get('settings', {}).get('daily_reward', 30))
        bot.runtime_config.setdefault('last_daily', {})[user.id] = now
        total = bot.add_points(user.id, reward)
        await bot.safe_whisper(user.id, f'أخذت {reward} نقطة يومية. رصيدك الآن: {total}')
        return True

    if lower in SHOP_KEYWORDS:
        await _shop_message(bot, user.id)
        return True

    if lower.startswith('buy ') or lower.startswith('شراء '):
        role_name = normalize_username(parts[1]) if len(parts) >= 2 else ''
        prices = bot.runtime_config.get('settings', {}).get('shop_prices', {})
        days = int(bot.runtime_config.get('settings', {}).get('role_days', {}).get(role_name, 7))
        price = int(prices.get(role_name, 0))
        if role_name not in prices:
            await bot.safe_whisper(user.id, 'العنصر غير موجود في المتجر.')
            return True
        if not bot.spend_points(user.id, price):
            await bot.safe_whisper(user.id, f'نقاطك غير كافية. السعر: {price} | رصيدك: {bot.get_points(user.id)}')
            return True
        bot.grant_role(user.username, role_name, days)
        await bot.safe_whisper(user.id, f'تم شراء {role_name.upper()} لمدة {days} أيام.')
        return True

    if lower in MYROLES_KEYWORDS:
        roles = bot.get_active_roles(user.username)
        if not roles:
            await bot.safe_whisper(user.id, 'لا تملك أي رتب مؤقتة حالياً.')
            return True
        lines = ['رتبك الحالية:']
        for role_name, expiry in roles.items():
            lines.append(f'- {role_name.upper()}: {_format_remaining(int(expiry))}')
        await bot.safe_whisper(user.id, '\n'.join(lines))
        return True

    if lower in TOP_KEYWORDS:
        await _top_message(bot, user.id)
        return True

    if lower.startswith('givepoints ') and len(parts) >= 3:
        if not is_owner(user.username, owner):
            await bot.safe_whisper(user.id, 'فقط الأونر يستطيع استخدام الأمر.')
            return True
        target = await _resolve_target(bot, user, parts[1])
        if not target:
            await bot.safe_whisper(user.id, 'المستخدم غير موجود في الروم')
            return True
        try:
            amount = int(parts[2])
        except Exception:
            await bot.safe_whisper(user.id, 'اكتب عدد صحيح.')
            return True
        total = bot.add_points(target.id, amount)
        await bot.safe_whisper(user.id, f'تم إعطاء @{target.username} عدد {amount} نقطة. رصيده الآن: {total}')
        await bot.safe_whisper(target.id, f'تم إضافة {amount} نقطة إلى حسابك. رصيدك الآن: {total}')
        return True

    if lower == 'pos':
        pos = bot.user_positions.get(user.id)
        if not pos:
            await bot.safe_whisper(user.id, 'ما عنديش إحداثياتك لسه. اتحرك خطوة واحدة ثم اكتب pos')
            return True
        await bot.safe_whisper(user.id, f"X={pos['x']} | Y={pos['y']} | Z={pos['z']} | Facing={pos['facing']}")
        return True

    floor_key = AR_FLOORS.get(lower, lower)
    if floor_key in ('z', 'f1', 'f2'):
        if not can_use_floors(bot, user.username, owner, mods):
            await bot.safe_whisper(user.id, 'هذه الأوامر تحتاج PLUS أو ELITE أو صلاحية مود.')
            return True
        try:
            floor_pos = make_position(config['floors'][floor_key])
            await bot.highrise.teleport(user.id, floor_pos)
        except Exception as e:
            await bot.safe_whisper(user.id, f'فشل النقل: {e}')
        return True

    if floor_key in ('z set', 'f1 set', 'f2 set'):
        if not is_staff(user.username, owner, mods):
            await bot.safe_whisper(user.id, 'ليس لديك صلاحية.')
            return True
        raw_key = floor_key.split()[0]
        pos = bot.user_positions.get(user.id)
        if not pos:
            await bot.safe_whisper(user.id, 'ما عنديش إحداثياتك لسه. اتحرك خطوة واحدة ثم اكتب الأمر مرة ثانية.')
            return True
        config['floors'][raw_key] = {'x': pos['x'], 'y': pos['y'], 'z': pos['z'], 'facing': pos['facing']}
        bot.save_runtime_config()
        await bot.safe_whisper(user.id, f'تم حفظ موقع {raw_key.upper()}')
        return True

    if lower == 'vip':
        if not can_use_vip(bot, user.username, owner, mods, vip_users):
            await bot.safe_whisper(user.id, 'أنت غير مسموح لك بدخول منطقة VIP')
            return True
        try:
            vip_pos = make_position(config['vip_position'])
            await bot.highrise.teleport(user.id, vip_pos)
        except Exception as e:
            await bot.safe_whisper(user.id, f'فشل النقل للـ VIP: {e}')
        return True

    if lower in ('stop', '0'):
        stopped = await bot.dance_manager.stop(user.id)
        await bot.safe_whisper(user.id, 'تم إيقاف اللوب.' if stopped else 'مافيش لوب شغال عليك.')
        return True

    if parts and parts[0].casefold() == 'stop' and len(parts) >= 2:
        if not is_staff(user.username, owner, mods):
            await bot.safe_whisper(user.id, 'ليس لديك صلاحية.')
            return True
        target_name = normalize_username(parts[1])
        target = await _resolve_target(bot, user, target_name)
        if not target:
            await bot.safe_whisper(user.id, 'المستخدم غير موجود في الروم')
            return True
        stopped = await bot.dance_manager.stop(target.id)
        await bot.safe_whisper(user.id, 'تم إيقاف اللوب.' if stopped else 'مافيش لوب شغال على اللاعب.')
        return True

    if parts and parts[0].casefold() == 'loop':
        target_user = user
        dance_number = None
        if len(parts) == 2:
            dance_number = parts[1]
        elif len(parts) >= 3:
            if parts[1].startswith('@'):
                if not is_staff(user.username, owner, mods):
                    await bot.safe_whisper(user.id, 'ليس لديك صلاحية.')
                    return True
                target_user = await _resolve_target(bot, user, parts[1])
                dance_number = parts[2]
            elif parts[2].startswith('@'):
                if not is_staff(user.username, owner, mods):
                    await bot.safe_whisper(user.id, 'ليس لديك صلاحية.')
                    return True
                target_user = await _resolve_target(bot, user, parts[2])
                dance_number = parts[1]
        if not target_user:
            await bot.safe_whisper(user.id, 'المستخدم غير موجود في الروم')
            return True
        ok, err = await bot.dance_manager.start_loop(target_user.id, str(dance_number or ''))
        if not ok:
            await bot.safe_whisper(user.id, err or 'فشل تشغيل اللوب')
        return True

    if parts and parts[0].isdigit():
        target_user = user
        dance_number = parts[0]
        if len(parts) >= 2:
            if not is_staff(user.username, owner, mods):
                await bot.safe_whisper(user.id, 'ليس لديك صلاحية.')
                return True
            target_user = await _resolve_target(bot, user, parts[1])
            if not target_user:
                await bot.safe_whisper(user.id, 'المستخدم غير موجود في الروم')
                return True
        ok, err = await bot.dance_manager.play_once(target_user.id, dance_number)
        if not ok:
            await bot.safe_whisper(user.id, err or 'فشل تشغيل الرقصة')
        return True

    if parts and parts[0].casefold() in REACTIONS:
        if len(parts) < 2:
            await bot.safe_whisper(user.id, f"استخدام صحيح: {parts[0]} @user [count]")
            return True
        target_name = normalize_username(parts[1])
        target = await _resolve_target(bot, user, target_name)
        if not target:
            await bot.safe_whisper(user.id, 'المستخدم غير موجود في الروم')
            return True
        count = _parse_count(parts[2] if len(parts) >= 3 else None)
        await _burst_reaction(bot, user.id, target.id, REACTIONS[parts[0].casefold()], count)
        await bot.safe_whisper(user.id, f"تم إرسال {REACTIONS[parts[0].casefold()]} × {count} إلى @{target_name}")
        return True

    if lower == 'vip set':
        if not is_staff(user.username, owner, mods):
            await bot.safe_whisper(user.id, 'ليس لديك صلاحية.')
            return True
        pos = bot.user_positions.get(user.id)
        if not pos:
            await bot.safe_whisper(user.id, 'ما عنديش إحداثياتك لسه. اتحرك خطوة واحدة ثم اكتب الأمر مرة ثانية.')
            return True
        config['vip_position'] = {'x': pos['x'], 'y': pos['y'], 'z': pos['z'], 'facing': pos['facing']}
        bot.save_runtime_config()
        await bot.safe_whisper(user.id, 'تم حفظ موقع VIP')
        return True

    if not is_staff(user.username, owner, mods) and not can_use_to(bot, user.username, owner, mods):
        pass

    cmd = parts[0].casefold() if parts else ''

    if cmd == 'mod' and len(parts) >= 3:
        if not is_owner(user.username, owner):
            await bot.safe_whisper(user.id, 'فقط المالك يستطيع تعديل المودز.')
            return True
        action = parts[1].casefold()
        target = normalize_username(parts[2])
        if action == 'add':
            if target not in {normalize_username(x) for x in config['mods']}:
                config['mods'].append(target)
                bot.save_runtime_config()
            await bot.safe_whisper(user.id, f'تمت إضافة @{target} كمود')
            return True
        if action == 'del':
            config['mods'] = [m for m in config['mods'] if normalize_username(m) != target]
            bot.save_runtime_config()
            await bot.safe_whisper(user.id, f'تم حذف @{target} من المودز')
            return True

    if cmd == 'mods':
        mods_text = ', '.join(config['mods']) if config['mods'] else 'لا يوجد مودز'
        await bot.safe_whisper(user.id, f'المودز: {mods_text}')
        return True

    if lower.startswith('vip add ') and len(parts) >= 3:
        if not is_staff(user.username, owner, mods):
            await bot.safe_whisper(user.id, 'ليس لديك صلاحية.')
            return True
        target = normalize_username(parts[2])
        if target not in {normalize_username(x) for x in config['vip_users']}:
            config['vip_users'].append(target)
            bot.save_runtime_config()
        await bot.safe_whisper(user.id, f'تمت إضافة @{target} إلى VIP')
        return True

    if lower.startswith('vip del ') and len(parts) >= 3:
        if not is_staff(user.username, owner, mods):
            await bot.safe_whisper(user.id, 'ليس لديك صلاحية.')
            return True
        target = normalize_username(parts[2])
        config['vip_users'] = [x for x in config['vip_users'] if normalize_username(x) != target]
        bot.save_runtime_config()
        await bot.safe_whisper(user.id, f'تم حذف @{target} من VIP')
        return True

    if lower == 'vip list':
        vip_text = ', '.join(config['vip_users']) if config['vip_users'] else 'لا يوجد VIP'
        await bot.safe_whisper(user.id, f'VIP: {vip_text}')
        return True

    if parts and parts[0] == 'VIP' and len(parts) >= 2:
        if not is_staff(user.username, owner, mods):
            await bot.safe_whisper(user.id, 'ليس لديك صلاحية.')
            return True
        target_name = normalize_username(parts[1])
        target_user = await _resolve_target(bot, user, target_name)
        if not target_user:
            await bot.safe_whisper(user.id, 'المستخدم غير موجود في الروم')
            return True
        try:
            vip_pos = make_position(config['vip_position'])
            await bot.highrise.teleport(target_user.id, vip_pos)
            await bot.safe_whisper(user.id, f'تم نقل @{target_name} إلى VIP')
        except Exception as e:
            await bot.safe_whisper(user.id, f'فشل نقل المستخدم: {e}')
        return True

    if cmd in ('br', 'هات') and len(parts) >= 2:
        if not is_staff(user.username, owner, mods):
            await bot.safe_whisper(user.id, 'الأمر للمودز فقط.')
            return True
        target_name = normalize_username(parts[1])
        target_user = await _resolve_target(bot, user, target_name)
        if not target_user:
            await bot.safe_whisper(user.id, 'المستخدم غير موجود في الروم')
            return True
        pos = bot.user_positions.get(user.id)
        if not pos:
            await bot.safe_whisper(user.id, 'ما عنديش إحداثياتك الحالية.')
            return True
        try:
            await bot.highrise.teleport(target_user.id, make_position(pos))
            await bot.safe_whisper(user.id, f'تم سحب @{target_name} إليك')
        except Exception as e:
            await bot.safe_whisper(user.id, f'فشل السحب: {e}')
        return True

    if cmd == 'to' and len(parts) >= 2:
        if not can_use_to(bot, user.username, owner, mods):
            await bot.safe_whisper(user.id, 'هذا الأمر يحتاج ELITE أو صلاحية مود.')
            return True
        target_name = normalize_username(parts[1])
        target_user = await _resolve_target(bot, user, target_name)
        if not target_user:
            await bot.safe_whisper(user.id, 'المستخدم غير موجود في الروم')
            return True
        target_pos = bot.user_positions.get(target_user.id)
        if not target_pos:
            await bot.safe_whisper(user.id, 'لا توجد إحداثيات محفوظة لهذا اللاعب.')
            return True
        try:
            await bot.highrise.teleport(user.id, make_position(target_pos))
            await bot.safe_whisper(user.id, f'تم نقلك إلى @{target_name}')
        except Exception as e:
            await bot.safe_whisper(user.id, f'فشل النقل: {e}')
        return True

    if cmd == 'kick' and len(parts) >= 2:
        if not is_staff(user.username, owner, mods):
            return True
        return await _moderate(bot, user, parts[1], 'kick')

    if cmd == 'ban' and len(parts) >= 2:
        if not is_staff(user.username, owner, mods):
            return True
        minutes = 60
        if len(parts) >= 3 and parts[2].isdigit():
            minutes = max(1, min(10080, int(parts[2])))
        return await _moderate(bot, user, parts[1], 'ban', minutes)

    if cmd == 'unban' and len(parts) >= 2:
        if not is_staff(user.username, owner, mods):
            return True
        return await _moderate(bot, user, parts[1], 'unban')

    if cmd == 'mute' and len(parts) >= 2:
        if not is_staff(user.username, owner, mods):
            return True
        minutes = 10
        if len(parts) >= 3 and parts[2].isdigit():
            minutes = max(1, min(1440, int(parts[2])))
        return await _moderate(bot, user, parts[1], 'mute', minutes)

    if cmd == 'unmute' and len(parts) >= 2:
        await bot.safe_whisper(user.id, 'أمر unmute غير مدعوم من highrise-bot-sdk الحالي، لذلك عطّلته حتى لا يوقع البوت.')
        return True

    return False

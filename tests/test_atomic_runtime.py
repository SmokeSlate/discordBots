import asyncio
import copy
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch
try:
    import discord
except ModuleNotFoundError:
    raise unittest.SkipTest('Install tests/requirements.txt for Discord runtime regressions')
_TEMP=tempfile.TemporaryDirectory();os.environ['SMOKEBOT_DB_PATH']=str(Path(_TEMP.name)/'test.sqlite3')
from SmokeBot import main as m

class AtomicRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        for name in ('giveaway_completion_lock','pin_lock','auto_update_lock'):
            patcher=patch.object(m,name,asyncio.Lock());patcher.start();self.addCleanup(patcher.stop)
        patcher=patch.object(m,'autoreply_pending',set());patcher.start();self.addCleanup(patcher.stop)

    async def test_duplicate_giveaway_completions_announce_once(self):
        async def fetch(_):
            await asyncio.sleep(0)
            return NS(edit=AsyncMock(),jump_url='https://example.com/synthetic')
        channel=NS(fetch_message=fetch,send=AsyncMock())
        with patch.object(m,'giveaways',{'1':{'channel_id':2,'participants':['3'],'winner_count':1}}),patch.object(m.bot,'get_channel',return_value=channel),patch.object(m,'save_giveaways') as save:
            await asyncio.gather(m.conclude_giveaway('1'),m.conclude_giveaway('1'))
            channel.send.assert_awaited_once();save.assert_called_once()

    async def test_concurrent_pin_reposts_leave_one_tracked_live_pin(self):
        store={'1':{'channel_id':'2','content':'synthetic'}};live={'1'};counter=1
        async def fetch(mid):
            await asyncio.sleep(0)
            async def delete():live.discard(str(mid))
            return NS(delete=delete)
        async def send(_):
            nonlocal counter
            await asyncio.sleep(0);counter+=1;live.add(str(counter));return NS(id=counter)
        def save(_,data):store.clear();store.update(copy.deepcopy(data))
        channel=NS(id=2,fetch_message=fetch,send=send)
        with patch.object(m,'load_pinned_messages',side_effect=lambda:copy.deepcopy(store)),patch.object(m,'write_json',side_effect=save):
            await asyncio.gather(m.handle_pin_repost(NS(channel=channel)),m.handle_pin_repost(NS(channel=channel)))
        self.assertEqual(live,set(store));self.assertEqual(len(live),1)

    async def test_autoreply_bucket_reserved_before_sending(self):
        sends=[]
        async def send(*_,**__):await asyncio.sleep(0);sends.append(True)
        message=NS(guild=NS(id=1),channel=NS(id=2,send=send),author=NS(id=3,roles=[]),reference=None)
        entry={'response':'synthetic','cooldown_seconds':60,'cooldown_scope':'guild'}
        with patch.object(m,'autoreply_cooldowns',{}),patch.object(m,'build_reply_reference',return_value=None):
            await asyncio.gather(m.dispatch_auto_reply(message,'x',entry,None),m.dispatch_auto_reply(message,'x',entry,None))
        self.assertEqual(len(sends),1)

    async def test_role_cooldown_uses_highest_non_default_role(self):
        message=lambda top:NS(guild=NS(id=1),channel=NS(id=2),author=NS(id=3,roles=[NS(id=1),NS(id=top)],top_role=NS(id=top)))
        entry={'cooldown_seconds':60,'cooldown_scope':'role'}
        self.assertNotEqual(m.determine_autoreply_cooldown_bucket(entry,message(7)),m.determine_autoreply_cooldown_bucket(entry,message(8)))

    async def test_rejected_autoreply_options_preserve_existing(self):
        entry={'include_roles':['old']};before=copy.deepcopy(entry)
        inter=NS(guild=NS(id=1),user=NS(guild_permissions=NS(administrator=True)),response=NS(send_message=AsyncMock()))
        with patch.object(m,'auto_replies',{'1':{'rule':entry}}),patch.object(m,'parse_role_input',side_effect=[(['20'],[]),([],['bad'])]),patch.object(m,'save_auto_replies') as save:
            await m.autoreply_options.callback(inter,'rule',include_roles='20',exclude_roles='bad')
            self.assertEqual(entry,before);save.assert_not_called()

    async def test_malformed_durations_rejected_as_whole_input(self):
        for value in ['-1h','1.5h','junk1h','1h??','1h -2m']:
            self.assertIsNotNone(m.parse_duration_string(value)[1],value)
        self.assertEqual(m.parse_duration_string('1h 30m'),(5400,None))

    async def test_failed_autoreply_does_not_consume_cooldown(self):
        channel=NS(id=2,send=AsyncMock(side_effect=[discord.Forbidden(NS(status=403,reason='Forbidden'),'synthetic'),None]))
        message=NS(guild=NS(id=1),channel=channel,author=NS(id=3,roles=[]),reference=None)
        entry={'response':'synthetic','cooldown_seconds':60}
        with patch.object(m,'autoreply_cooldowns',{}),patch.object(m,'build_reply_reference',return_value=None):
            self.assertFalse(await m.dispatch_auto_reply(message,'x',entry,None))
            self.assertTrue(await m.dispatch_auto_reply(message,'x',entry,None))
            self.assertEqual(channel.send.await_count,2)

    async def test_giveaway_timer_is_not_duplicated_on_reconnect(self):
        gate=asyncio.Event()
        async def schedule(_): await gate.wait()
        with patch.object(m,'giveaway_tasks',{}),patch.object(m,'schedule_giveaway_end',new=schedule):
            first=m.queue_giveaway_end('1');second=m.queue_giveaway_end('1')
            self.assertIs(first,second)
            gate.set();await first;await asyncio.sleep(0)
            self.assertNotIn('1',m.giveaway_tasks)

    async def test_no_restart_for_noop_update_or_diverged_history(self):
        for status,result in [({'ok':True,'up_to_date':False,'can_fast_forward':True},{'ok':True,'changed':False}),({'ok':True,'up_to_date':False,'can_fast_forward':False},{'ok':True,'changed':True})]:
            with patch.object(m,'get_git_update_status',return_value=status),patch.object(m,'apply_git_update',return_value=result) as update,patch.object(m.os,'execv') as restart:
                await m.perform_auto_update_check()
                restart.assert_not_called()
                if not status['can_fast_forward']:update.assert_not_called()

    async def test_pin_reposts_in_separate_channels_preserve_both_records(self):
        store={'1':{'channel_id':'10','content':'A'},'2':{'channel_id':'20','content':'B'}}
        async def fetch(_):await asyncio.sleep(0);return NS(delete=AsyncMock())
        async def send_a(_):await asyncio.sleep(0);return NS(id=3)
        async def send_b(_):await asyncio.sleep(0);return NS(id=4)
        def save(_,data):store.clear();store.update(copy.deepcopy(data))
        with patch.object(m,'load_pinned_messages',side_effect=lambda:copy.deepcopy(store)),patch.object(m,'write_json',side_effect=save):
            await asyncio.gather(m.handle_pin_repost(NS(channel=NS(id=10,fetch_message=fetch,send=send_a))),m.handle_pin_repost(NS(channel=NS(id=20,fetch_message=fetch,send=send_b))))
        self.assertEqual(set(store),{'3','4'})

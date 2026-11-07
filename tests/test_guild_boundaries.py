import asyncio
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch
try:
    import discord
except ModuleNotFoundError:
    raise unittest.SkipTest('Install tests/requirements.txt for Discord runtime regressions')
_TEMP=tempfile.TemporaryDirectory()
os.environ['SMOKEBOT_DB_PATH']=str(Path(_TEMP.name)/'test.sqlite3')
from SmokeBot import main as m

def interaction(guild_id=1):
    return NS(guild=NS(id=guild_id),user=NS(id=99,guild_permissions=NS(administrator=True,manage_messages=True,moderate_members=True)),response=NS(send_message=AsyncMock(),defer=AsyncMock()),followup=NS(send=AsyncMock()))

class GuildBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_reaction_role_removal_cannot_cross_guild(self):
        data={'123_x':{'guild_id':2}}
        with patch.object(m,'reaction_roles',data),patch.object(m,'save_reaction_roles') as save:
            await m.remove_reaction_role.callback(interaction(1),'123','x')
        self.assertIn('123_x',data);save.assert_not_called()

    async def test_ticket_category_edit_is_guild_local(self):
        original={'version':2,'defaults':{},'guilds':{'1':{},'2':{'other':{'label':'Other','desc':'x'}}}}
        with patch.object(m,'ticket_categories',original),patch.object(m,'save_ticket_categories'):
            await m.add_ticket_category.callback(interaction(1),'new','New','Description')
            self.assertEqual(m.get_ticket_categories(2),original['guilds']['2'])
            self.assertIn('new',m.get_ticket_categories(1))
            self.assertNotIn('new',m.get_ticket_categories(2))

    async def test_ticket_category_limits_reject_without_mutation(self):
        store={'version':2,'defaults':{},'guilds':{'1':{str(i):{'label':'X','desc':'x'} for i in range(25)}}}
        for key,label,desc in [('extra','Extra','x'),('x'*101,'X','x'),('valid','x'*101,'x'),('valid','X','x'*101)]:
            with patch.object(m,'ticket_categories',store),patch.object(m,'save_ticket_categories') as save:
                await m.add_ticket_category.callback(interaction(1),key,label,desc)
                save.assert_not_called()
        self.assertEqual(len(store['guilds']['1']),25)

    async def test_timeout_deadline_aware_and_invalid_range_rejected(self):
        target=NS(timeout=AsyncMock(),mention='<@1>')
        await m.timeout_member.callback(interaction(),target,5)
        until=target.timeout.call_args.args[0]
        self.assertIsNotNone(until.tzinfo)
        for duration in (0,-1,40321):
            target.timeout.reset_mock()
            await m.timeout_member.callback(interaction(),target,duration)
            target.timeout.assert_not_called()

    async def test_guild_commands_reject_dm_and_register_guild_only(self):
        dm=interaction();dm.guild=None
        await m.list_snippets.callback(dm)
        dm.response.send_message.assert_awaited_once()
        for command in m.bot.tree.get_commands():
            if command.name!='help': self.assertTrue(command.guild_only,command.name)

    async def test_large_snippet_list_is_split_to_valid_embeds(self):
        inter=interaction()
        with patch.object(m,'snippets',{'1':{('name'+str(i))*100:{'content':'text'*400} for i in range(60)}}):
            await m.list_snippets.callback(inter)
        calls=inter.response.send_message.call_args_list+inter.followup.send.call_args_list
        self.assertGreater(len(calls),1)
        fields=[]
        for call in calls:
            embed=call.kwargs['embed'];self.assertLessEqual(len(embed.fields),25);self.assertLessEqual(len(embed),6000)
            for f in embed.fields:
                self.assertLessEqual(len(f.name),256);self.assertLessEqual(len(f.value),1024)
            fields+=list(embed.fields)
        self.assertEqual(len(fields),60)

    async def test_archived_ticket_fetch_still_blocks_duplicate(self):
        inter=interaction();thread=NS(mention='<#5>')
        inter.guild.get_thread=Mock(return_value=None);inter.guild.fetch_channel=AsyncMock(return_value=thread)
        inter.channel=NS(create_thread=AsyncMock())
        select=m.TicketCategorySelect();select._values=['other']
        with patch.object(m,'ticket_data',{'1':{'5':{'user_id':'99','status':'open'}}}):
            await select.callback(inter)
        inter.guild.fetch_channel.assert_awaited_once_with(5)
        inter.channel.create_thread.assert_not_called()
        self.assertIn('<#5>',inter.response.send_message.call_args.args[0])

    async def test_legacy_categories_copy_without_shared_mutation(self):
        legacy={'old':{'label':'Old','desc':'Legacy'}}
        with patch.object(m,'ticket_categories',m.migrate_ticket_categories(legacy)):
            m.get_ticket_categories(1)['old']['label']='Changed'
            self.assertEqual(m.get_ticket_categories(2)['old']['label'],'Old')
            self.assertEqual(legacy['old']['label'],'Old')

    async def test_empty_menu_disabled_and_post_rejected(self):
        with patch.object(m,'ticket_categories',{'version':2,'defaults':{},'guilds':{'1':{}}}):
            select=m.TicketCategorySelect(1)
            self.assertTrue(select.disabled);self.assertEqual(len(select.options),1)
            inter=interaction();inter.channel=NS(send=AsyncMock())
            await m.create_ticket_menu.callback(inter)
            inter.channel.send.assert_not_called()

    async def test_old_utc_values_have_correct_epoch_in_non_utc_timezone(self):
        import time
        previous=os.environ.get('TZ')
        try:
            os.environ['TZ']='America/New_York';time.tzset()
            dt=m.parse_utc_timestamp('2026-01-01T12:00:00')
            self.assertEqual(dt.timestamp(),datetime(2026,1,1,12,tzinfo=timezone.utc).timestamp())
            embed=m.build_giveaway_embed({'end_time':'2026-01-01T12:00:00'})
            self.assertIn(str(int(dt.timestamp())),embed.description)
        finally:
            if previous is None: os.environ.pop('TZ',None)
            else: os.environ['TZ']=previous
            time.tzset()

    async def test_script_timeout_helper_uses_same_validated_deadline(self):
        target=NS(timeout=AsyncMock())
        guild=NS(id=1,get_member=Mock(return_value=target))
        entry={'code':'__script_async_entry__ = timeout_member(1, 5)','operator_id':str(m.TRUSTED_SCRIPT_USER_ID)}
        self.assertTrue(await m.run_script_trigger('timeout',entry,guild=guild,user=NS(id=m.TRUSTED_SCRIPT_USER_ID)))
        self.assertIsNotNone(target.timeout.call_args.args[0].tzinfo)

    async def test_raw_reactions_resolve_uncached_members_and_run_once(self):
        member=NS(bot=False,add_roles=AsyncMock(),remove_roles=AsyncMock())
        role=NS(id=7)
        message=NS(id=123)
        channel=NS(fetch_message=AsyncMock(return_value=message))
        guild=NS(id=1,get_member=Mock(return_value=None),fetch_member=AsyncMock(return_value=member),get_role=Mock(return_value=role),get_channel_or_thread=Mock(return_value=channel))
        payload=NS(guild_id=1,user_id=99,message_id=123,channel_id=10,emoji='x')
        with patch.object(m.bot,'get_guild',return_value=guild),patch.object(m,'reaction_roles',{'123_x':{'guild_id':1,'role_id':7}}),patch.object(m,'script_triggers',{'1':{'rule':{'event':'reaction_add'}}}),patch.object(m,'dispatch_script_triggers_for_event',AsyncMock()) as dispatch:
            await m.on_raw_reaction_add(payload)
            member.add_roles.assert_awaited_once_with(role)
            dispatch.assert_awaited_once()
            await m.on_raw_reaction_remove(payload)
            member.remove_roles.assert_awaited_once_with(role)
        self.assertFalse(hasattr(m,'on_reaction_add'),'cached and raw handlers must not double-dispatch')

    async def test_missing_thread_reconciles_but_forbidden_does_not(self):
        response=NS(status=404,reason='Not Found')
        guild=NS(get_thread=Mock(return_value=None),fetch_channel=AsyncMock(side_effect=discord.NotFound(response,'missing')))
        data={'status':'open'}
        with patch.object(m,'save_ticket_data') as save:
            self.assertIsNone(await m.resolve_ticket_thread(guild,'5',data))
            self.assertEqual(data['status'],'deleted');save.assert_called_once()
        data={'status':'open'}
        guild.fetch_channel.side_effect=discord.Forbidden(NS(status=403,reason='Forbidden'),'blocked')
        with self.assertRaises(discord.Forbidden): await m.resolve_ticket_thread(guild,'5',data)
        self.assertEqual(data['status'],'open')

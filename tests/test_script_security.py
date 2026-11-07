"""Actual runtime/HTTP handlers with synthetic inputs; never connects the bot."""
import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

_TEMP = tempfile.TemporaryDirectory()
os.environ['SMOKEBOT_DB_PATH'] = str(Path(_TEMP.name) / 'test.sqlite3')
try:
    import discord
except ModuleNotFoundError:
    raise unittest.SkipTest("Install tests/requirements.txt to run actual Discord runtime regressions")
from SmokeBot import main as m
from aiohttp import web


def request(query=None, cookies=None, payload=None):
    return NS(query=query or {}, cookies=cookies or {}, headers={}, match_info={'guild_id':'1','name':'rule'}, json=AsyncMock(return_value=payload))


class ScriptSecurityTests(unittest.IsolatedAsyncioTestCase):
    async def test_unapproved_script_never_executes(self):
        self.assertFalse(await m.run_script_trigger('rule', {'code':"pass"}, guild=NS(id=1)))

    async def test_operator_approval_not_event_author_selects_runtime(self):
        entry={'code':"import hashlib\nassert bot is not None",'operator_id':str(m.TRUSTED_SCRIPT_USER_ID)}
        self.assertTrue(await m.run_script_trigger('rule',entry,guild=NS(id=1),user=NS(id=99)))

    async def test_other_guild_manager_cannot_upload_host_python(self):
        with patch.object(m,'_script_api_bot_has_guild',return_value=True), patch.object(m,'_script_api_guild_allowed',return_value=True), patch.object(m,'_script_api_authorize_request',AsyncMock(return_value=(True,None,{'id':'99'}))), patch.object(m,'_script_api_get_member_for_guild',AsyncMock(return_value=NS(guild_permissions=NS(administrator=True,manage_guild=True)))):
            allowed,_=await m._script_api_can_manage_guild(request(),'1')
        self.assertFalse(allowed)

    async def test_invalid_payloads_preserve_existing_trigger(self):
        for payload in [None,[],{'code':'x','event':'unknown'},{'code':'x','event':'message','pattern':'hi','channel_ids':['bad']},{'code':'x','event':'message','pattern':'hi','enabled':'false'}]:
            with self.subTest(payload=payload), patch.object(m,'script_triggers',{'1':{'rule':{'code':'original'}}}), patch.object(m,'_script_api_can_manage_guild',AsyncMock(return_value=(True,None))), patch.object(m,'save_script_triggers'):
                response=await m.script_api_upsert_trigger(request(payload=payload))
                self.assertEqual(response.status,400)
                self.assertEqual(m.script_triggers['1']['rule']['code'],'original')

    async def test_channel_ids_round_trip_as_decimal_strings(self):
        entry=m._sanitize_script_entry({'code':'pass','event':'message','pattern':'hi','channel_ids':['123456789012345678']})
        self.assertEqual(entry['channel_ids'],['123456789012345678'])

    async def test_missing_state_rejected_before_token_exchange(self):
        with patch.object(m.urllib.request,'urlopen') as exchange:
            try:
                response=await m.script_auth_callback(request({'code':'synthetic'}))
            except web.HTTPException as exc:
                response=exc
            self.assertEqual(response.status,400)
            exchange.assert_not_called()

    async def test_login_state_is_cookie_bound_and_consumed(self):
        with self.assertRaises(web.HTTPFound) as login:
            await m.script_auth_login(request())
        response=login.exception
        state=m.urllib.parse.parse_qs(m.urllib.parse.urlparse(response.location).query)['state'][0]
        cookie=response.cookies['smokebot_oauth_state']
        self.assertEqual(cookie.value,state)
        self.assertTrue(cookie['httponly'])
        with patch.object(m,'_script_manager_discord_client_secret',return_value='synthetic'), patch.object(m,'_exchange_discord_code',return_value={'access_token':'synthetic'}) as exchange:
            wrong=await m.script_auth_callback(request({'code':'synthetic','state':state},{'smokebot_oauth_state':'wrong'}))
            self.assertEqual(wrong.status,400)
            exchange.assert_not_called()
            with self.assertRaises(web.HTTPFound):
                await m.script_auth_callback(request({'code':'synthetic','state':state},{'smokebot_oauth_state':state}))
            replay=await m.script_auth_callback(request({'code':'synthetic','state':state},{'smokebot_oauth_state':state}))
            self.assertEqual(replay.status,400)
            exchange.assert_called_once()

    async def test_multiline_trigger_matching(self):
        for fn in (m.trigger_match_text,m.message_trigger_match):
            for match_type in ('contains','exact'):
                text='hello\nworld'
                pattern='hello' if match_type=='contains' else text
                self.assertIsNotNone(fn({'pattern':pattern,'match_type':match_type},NS(content=text) if fn is m.message_trigger_match else text))

    async def test_token_exchange_does_not_block_event_loop(self):
        import time
        state='synthetic-state'
        m.oauth_states[state]=(time.monotonic()+60,m._script_manager_default_return_url())
        heartbeat=[]
        async def beat():
            await asyncio.sleep(0.005)
            heartbeat.append(True)
        def slow_exchange(_):
            time.sleep(0.06)
            return {'access_token':'synthetic'}
        task=asyncio.create_task(beat())
        with patch.object(m,'_script_manager_discord_client_secret',return_value='synthetic'), patch.object(m,'_exchange_discord_code',side_effect=slow_exchange):
            with self.assertRaises(web.HTTPFound):
                await m.script_auth_callback(request({'code':'synthetic','state':state},{m.OAUTH_STATE_COOKIE:state}))
        self.assertTrue(heartbeat)
        await task

    async def test_expired_oauth_state_never_exchanges(self):
        m.oauth_states['expired']=(0,m._script_manager_default_return_url())
        with patch.object(m,'_exchange_discord_code') as exchange:
            response=await m.script_auth_callback(request({'code':'synthetic','state':'expired'},{m.OAUTH_STATE_COOKIE:'expired'}))
            self.assertEqual(response.status,400)
            exchange.assert_not_called()

    async def test_bundled_detector_runs_for_ordinary_member(self):
        message=NS(id=1,content='',attachments=[],reference=None,channel=NS(id=1),author=NS(id=99,bot=False,guild_permissions=NS(manage_messages=False)))
        entry={'code':Path('SmokeBot/scripts/cross_channel_spam.py').read_text(),'operator_id':str(m.TRUSTED_SCRIPT_USER_ID)}
        self.assertTrue(await m.run_script_trigger('detector',entry,guild=NS(id=1),message=message,event_name='message_all'))

    async def test_default_message_log_omits_content(self):
        message=NS(id=1,content='synthetic-private-marker',guild=None,channel=NS(id=1),reference=None,author=NS(id=99,bot=True))
        with patch.object(m.bot,'process_commands',AsyncMock()), self.assertLogs('smokebot',level='INFO') as logs:
            await m.on_message(message)
        self.assertNotIn(message.content,' '.join(logs.output))

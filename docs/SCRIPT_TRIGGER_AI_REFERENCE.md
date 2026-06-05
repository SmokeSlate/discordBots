# SmokeBot Script Trigger AI Reference

This document is for AIs that need to generate valid SmokeBot script triggers for `SmokeBot/main.py`.

## Goal

Generate Python code that runs inside SmokeBot's hosted script trigger runtime, plus the trigger metadata needed to save it.

Source of truth:

- Trigger defaults and validation live in `ensure_script_trigger_defaults()`.
- Trigger execution lives in `run_script_trigger()`.
- The built-in `/script docs` command lists the main helper functions.

## Trigger Object Shape

Each saved trigger is a dictionary with these fields:

```json
{
  "event": "message",
  "pattern": "!hello",
  "match_type": "exact",
  "channel_ids": [123456789012345678],
  "code": "async def main():\n    await reply('hi')\n\n__script_async_entry__ = main()",
  "enabled": true
}
```

Field rules:

- `event`: one of `message`, `message_all`, `reply`, `reaction_add`, `reaction_remove`, `member_join`, `member_leave`
- `pattern`: used for `message`, `reply`, `reaction_add`, and `reaction_remove`
- `match_type`: one of `contains`, `exact`, `regex`
- `channel_ids`: list of integer channel IDs; leave empty for all channels
- `code`: Python source code executed by the bot
- `enabled`: boolean

Defaults if omitted:

- `event = "message"`
- `pattern = ""`
- `match_type = "contains"`
- `channel_ids = []`
- `code = ""`
- `enabled = true`

## What An AI Should Output

When generating a script body:

- Prefer an `async def main(): ...` entrypoint.
- End with `__script_async_entry__ = main()`.
- `await` helper calls when order matters.
- Use SmokeBot helpers instead of direct Discord API access.
- Do not use imports unless you know the trusted-user override applies.
- Do not assume full Python builtins are available.

Recommended code template:

```python
async def main():
    await reply("Hello from SmokeBot.")


__script_async_entry__ = main()
```

## Event Semantics

### `message`

- Runs only when the message text matches `pattern` according to `match_type`.

### `message_all`

- Runs for every non-self message in an allowed guild.
- `pattern` is ignored.

### `reply`

- Runs only if the new message is a reply and the new message text matches `pattern`.
- `referenced_message` is populated when the replied-to message can be resolved.

### `reaction_add` and `reaction_remove`

- Pattern matching is applied to `str(reaction.emoji)`.

### `member_join` and `member_leave`

- Member lifecycle events.
- `pattern` is not used.
- `member` and `user` are the main actor objects.

## Context Variables

These names are injected into the script runtime:

- `message`: the trigger message when applicable
- `referenced_message`: the replied-to message for `reply` events when available
- `author`: the message author, or the event user when applicable
- `channel`: the source channel when applicable
- `guild`: the guild object
- `content`: the trigger message content, or `""` for non-message events
- `reaction`: the reaction object for reaction events
- `user`: the event user for reaction/member events
- `member`: the member for join/leave events
- `event`: the current event name string
- `match`: the regex/search match object for matched events, else `None`
- `random`, `re`, `asyncio`: utility modules

## Helper API

All helpers below return an `asyncio.Task` because the runtime wraps them with `asyncio.create_task(...)`. In normal generated scripts, `await` them.

### Message helpers

- `send(content, channel_id=None)`: send a message
- `reply(content)`: reply to the trigger message
- `react(emoji)`: add a reaction to the trigger message
- `remove_reaction(emoji, user_id=None)`: remove a reaction
- `send_embed(title=None, description=None, color=None, channel_id=None, fields=None, footer=None)`: send an embed
- `dm(user_id, content)`: send a DM
- `edit_message(message_id, content=None)`: edit a message in the source channel
- `delete_message(message_id=None)`: delete a message; defaults to the trigger message
- `pin_message(message_id=None)`: pin a message; defaults to the trigger message
- `unpin_message(message_id=None)`: unpin a message; defaults to the trigger message
- `search_messages(query=None, limit=50, from_user_id=None, include_bots=True, attachments_only=False)`: search recent source-channel history
- `http_request(url, method='GET', headers=None, body=None, json_body=None, timeout=15)`: make an HTTP request

### Thread helpers

- `send_in_thread(content, thread_name=None)`: send in a thread for the trigger message
- `send_to_thread(thread_id, content)`: send to an existing thread by ID
- `find_thread_by_name(thread_name)`: return a matching thread if found
- `create_thread(name, message_id=None, archive_minutes=60)`: create a public thread on a message
- `lock_thread(reason='')`: lock the current thread
- `unlock_thread(reason='')`: unlock the current thread
- `archive_thread(reason='')`: archive the current thread
- `add_user_to_thread(user_id)`: add a user to the current thread
- `remove_user_from_thread(user_id)`: remove a user from the current thread
- `rename_thread(new_name)`: rename the current thread

### Channel and member helpers

- `get_channel_info()`: return a dict with channel metadata
- `fetch_member_info(user_id)`: return a dict with member metadata, or `None`
- `set_channel_topic(topic)`: set the current text channel topic
- `resolve_snippet(text)`: resolve snippet content through the bot's snippet system

### Moderation helpers

- `kick_member(member_id, reason='No reason provided')`
- `ban_member(user_id, reason='No reason provided', delete_message_seconds=0)`
- `unban_user(user_id, reason='No reason provided')`
- `timeout_member(member_id, minutes, reason='No reason provided')`
- `set_slowmode(seconds, reason='No reason provided')`
- `add_role(member_id, role_id, reason='No reason provided')`
- `remove_role(member_id, role_id, reason='No reason provided')`
- `clear_messages(amount, from_user_id=None, contains=None, starts_after=None, ends_before=None, include_bots=True, only_bots=False, attachments_only=False, role_id=None, scan_limit=None)`

## Builtin Constraints

For normal script authors, the runtime only exposes a restricted builtin set. Safe assumptions:

- Basic types and functions like `str`, `int`, `len`, `range`, `min`, `max`, `sum`, `sorted`, `list`, `dict`, `set`, `tuple`, `print`, `enumerate`, `zip`, `map`, `filter`, `abs`, `round`, `any`, `all`
- Common exceptions like `Exception`, `ValueError`, `TypeError`, `KeyError`, `AttributeError`, `IndexError`, `RuntimeError`

Do not assume these are available for non-trusted scripts:

- Arbitrary imports
- File I/O helpers like `open`
- Direct access to `bot`, `discord`, `os`, `datetime`, `timedelta`

## Trusted User Override

One trusted user ID gets a wider runtime:

- Full Python builtins
- Extra globals: `bot`, `discord`, `os`, `datetime`, `timedelta`

Unless explicitly told the script runs as that trusted user, generate for the restricted runtime.

## AI Generation Rules

Use these rules when producing scripts:

- Prefer helper calls over raw object methods.
- Guard against missing context on non-message events.
- Use integer Discord IDs, not mentions, inside code.
- Keep scripts self-contained.
- Handle failures with simple `if result is None` checks.
- For regex triggers, use `match.group(...)` only after verifying `match` exists.
- For `message_all`, avoid loops or replies that can create spam.

## Minimal Examples

### Reply trigger

Trigger metadata:

- `event: message`
- `pattern: !ping`
- `match_type: exact`

Code:

```python
async def main():
    await reply("pong")


__script_async_entry__ = main()
```

### Welcome on member join

Trigger metadata:

- `event: member_join`

Code:

```python
async def main():
    if member is None:
        return
    await send(f"Welcome {member.mention}!")


__script_async_entry__ = main()
```

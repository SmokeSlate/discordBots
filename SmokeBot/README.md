# SmokeBot maintenance

Ticket categories are stored in a version 2 document with a preserved legacy default template and separate guild maps. Existing shared categories are copied when each guild is first accessed. A guild's edits then affect only that guild. Back up the SQLite database before rollout. Existing ticket menus resolve selections against the interacting guild's current configuration; staff should re-post menus after category changes. Empty configurations cannot post a menu, and category creation enforces Discord's option limits.

New timestamps use aware UTC. Previously stored naive ISO timestamps are interpreted as UTC when read, matching the old writer's UTC convention. Existing records are not destructively rewritten.

Persistent reaction roles use raw gateway events and fetch missing members. Reaction scripts fetch message context only when configured. Deleted ticket threads are reconciled only after Discord returns NotFound; permission and transient failures keep the ticket open and block duplicate creation.

For isolated tests, install `tests/requirements.txt` then run `python -m unittest discover -s tests -p 'test_*.py'`. Tests use temporary SQLite and synthetic Discord calls. The existing CI workflow does not install this runtime, so runtime test modules explicitly skip if Discord is absent. No test starts or logs in the bot.

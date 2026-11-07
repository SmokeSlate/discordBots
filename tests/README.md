# Isolated regressions

Run from the repository root, without starting the bot:

```sh
python -m pip install -r tests/requirements.txt
python -m unittest discover -s tests -p "test_*.py"
node tests/test_script_manager.js
```

Runtime tests use discord.py objects, temporary SQLite storage and mocked HTTP/Discord calls. They never log in or connect the bot. The script manager test executes the actual frontend in a minimal synthetic DOM. It does not verify browser styling or real Discord OAuth.

The existing CI workflow does not install the optional Discord test runtime. Those tests report an explicit skip when it is absent. Install the pinned requirements to run them. The Node regression runs through unittest when Node is available.

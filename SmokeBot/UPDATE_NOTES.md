# Update and concurrency behavior

The shell updater downloads all required modules into a temporary directory and validates Python syntax before stopping the tracked PID or replacing files. It keeps backups and restores them on replacement or startup failure. It no longer stops unrelated processes through a broad `pkill` pattern. The startup check only verifies one second of process liveness; it does not prove a successful Discord connection. Prefer a commit-pinned `SMOKEBOT_UPDATE_BASE` for coordinated releases. `SMOKEBOT_BOT_DIR` can select an alternate installation directory.

Automatic Git checks distinguish equal, ahead, behind and diverged history. They only fast-forward a behind branch and restart after HEAD actually changes. Ahead/diverged installations require operator review rather than a restart loop.

Giveaway completion and pin storage updates are serialized within one bot process. Reconnects reuse outstanding giveaway timers. An ended giveaway persists its winner selection before announcement, so another task cannot redraw or announce it again. This does not promise exactly-once network delivery across crashes or Discord failures. Run only one bot process per database.

Auto replies reserve pending cooldown buckets before sending. Failed delivery releases the reservation; successful delivery starts the cooldown. Primary-role buckets use the member's highest role rather than the shared default role. Invalid option edits leave the previous configuration intact, and duration strings must match the complete supported grammar.

Isolated runtime tests require `python -m pip install -r tests/requirements.txt`, then `python -m unittest discover -s tests -p 'test_*.py'`. Existing CI omits the optional runtime and reports an explicit module skip. Local Git-history and staged-updater tests require no Discord dependency, contact no network and use disposable files plus mocked process/download commands.

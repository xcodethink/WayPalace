# systemd templates (Linux)

These templates are the Linux equivalents of the macOS `launchd/` plist files.
They have **not been tested on a real Linux deployment yet** — PRs from Linux
users to fix bugs are very welcome.

## Files

| Template | Purpose |
|---|---|
| `waypalace-memory.service.tpl` | Long-running memory daemon (ChromaDB + bge-m3). One systemd unit. |
| `waypalace-refresh.service.tpl` | One-shot refresh batch (sparse sync, hourly classification, etc.) — triggered by timer below. |
| `waypalace-refresh.timer.tpl` | systemd timer that fires the refresh service every hour. |

## Installation

```bash
# Activate your venv first so env vars are set
source $WAYPALACE_VENV/bin/activate

# Install the memory daemon
sed "s|\${WAYPALACE_HOME}|$WAYPALACE_HOME|g; s|\${WAYPALACE_VENV}|$WAYPALACE_VENV|g; s|\${WAYPALACE_USER}|$USER|g" \
    templates/systemd/waypalace-memory.service.tpl \
    | sudo tee /etc/systemd/system/waypalace-memory.service

# Install the refresh batch (service + timer)
sed "s|\${WAYPALACE_HOME}|$WAYPALACE_HOME|g; s|\${WAYPALACE_USER}|$USER|g" \
    templates/systemd/waypalace-refresh.service.tpl \
    | sudo tee /etc/systemd/system/waypalace-refresh.service
sudo cp templates/systemd/waypalace-refresh.timer.tpl /etc/systemd/system/waypalace-refresh.timer

# Reload + enable
sudo systemctl daemon-reload
sudo systemctl enable --now waypalace-memory.service
sudo systemctl enable --now waypalace-refresh.timer

# Verify
systemctl status waypalace-memory.service
systemctl list-timers | grep waypalace
journalctl -u waypalace-memory.service -f
```

## User vs system services

The templates above install as **system services** (under `/etc/systemd/system/`).

If you prefer user-level systemd (no sudo), put them under
`~/.config/systemd/user/` and use `systemctl --user` for everything. You will
need `loginctl enable-linger $USER` to keep them running when you log out.

## Known caveats (please report)

- `MemoryMax=8G` is a guess. Tune based on your hardware.
- `CPUQuota=400%` assumes a multi-core machine. Adjust.
- Hardening directives (`ProtectSystem`, `ProtectHome`, `ReadWritePaths`) are
  conservative; if your data path is outside `$WAYPALACE_HOME/data`, add it
  to `ReadWritePaths`.
- No SELinux / AppArmor profile shipped. PRs welcome.

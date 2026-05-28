# waypalace hourly refresh batch — systemd timer template
#
# Installation:
#   # First install the service file (waypalace-refresh.service.tpl)
#   sed "s|\${WAYPALACE_HOME}|$WAYPALACE_HOME|g; s|\${WAYPALACE_USER}|$USER|g" \
#       templates/systemd/waypalace-refresh.service.tpl \
#       | sudo tee /etc/systemd/system/waypalace-refresh.service
#   # Then install the timer
#   cp templates/systemd/waypalace-refresh.timer.tpl \
#       /etc/systemd/system/waypalace-refresh.timer
#   sudo systemctl daemon-reload
#   sudo systemctl enable --now waypalace-refresh.timer
#
# Verify:
#   systemctl list-timers | grep waypalace
#   journalctl -u waypalace-refresh.service -f

[Unit]
Description=Hourly WayPalace refresh batch (Tier 1 indexing + reconcilers)
Documentation=https://github.com/xcodethink/WayPalace/blob/main/docs/INSTALL.md

[Timer]
OnBootSec=10min
OnUnitActiveSec=1h
Unit=waypalace-refresh.service
Persistent=true

[Install]
WantedBy=timers.target

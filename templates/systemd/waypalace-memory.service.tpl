# waypalace memory daemon — systemd unit template
#
# Installation:
#   sed "s|\${WAYPALACE_HOME}|$WAYPALACE_HOME|g; s|\${WAYPALACE_VENV}|$WAYPALACE_VENV|g; s|\${WAYPALACE_USER}|$USER|g" \
#       templates/systemd/waypalace-memory.service.tpl \
#       | sudo tee /etc/systemd/system/waypalace-memory.service
#   sudo systemctl daemon-reload
#   sudo systemctl enable --now waypalace-memory.service
#
# Verify:
#   systemctl status waypalace-memory.service
#   journalctl -u waypalace-memory.service -f

[Unit]
Description=WayPalace memory daemon (long-running ChromaDB + bge-m3 server)
Documentation=https://github.com/xcodethink/WayPalace/blob/main/docs/INSTALL.md
After=network.target

[Service]
Type=simple
User=${WAYPALACE_USER}
Environment=WAYPALACE_HOME=${WAYPALACE_HOME}
Environment=WAYPALACE_DATA=${WAYPALACE_HOME}/data
ExecStart=${WAYPALACE_VENV}/bin/python ${WAYPALACE_HOME}/waypalace/memory_daemon.py

# Reliability
Restart=on-failure
RestartSec=10
StartLimitInterval=300
StartLimitBurst=5

# Resource limits (adjust to your hardware)
MemoryMax=8G
CPUQuota=400%
Nice=5

# Hardening (optional but recommended)
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=${WAYPALACE_HOME}/data

[Install]
WantedBy=default.target

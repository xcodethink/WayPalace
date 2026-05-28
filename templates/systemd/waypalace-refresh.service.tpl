# waypalace refresh batch — systemd service (oneshot, triggered by timer)
# See waypalace-refresh.timer.tpl for installation.

[Unit]
Description=WayPalace refresh batch (one-shot, triggered hourly by timer)

[Service]
Type=oneshot
User=${WAYPALACE_USER}
Environment=WAYPALACE_HOME=${WAYPALACE_HOME}
Environment=WAYPALACE_DATA=${WAYPALACE_HOME}/data
ExecStart=/bin/bash ${WAYPALACE_HOME}/refresh-batch.sh

# Hardening
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=${WAYPALACE_HOME}/data

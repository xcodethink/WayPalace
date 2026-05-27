<?xml version="1.0" encoding="UTF-8"?>
<!--
 WayPalace memory daemon — launchd template.
 Substitute ${WAYPALACE_HOME} and ${WAYPALACE_VENV} before installing.

 Install:
   sed "s|\${WAYPALACE_HOME}|$WAYPALACE_HOME|g; s|\${WAYPALACE_VENV}|$WAYPALACE_VENV|g" \
       templates/launchd/com.user.waypalace.memory.daemon.plist.tpl \
       > ~/Library/LaunchAgents/com.user.waypalace.memory.daemon.plist
   launchctl load ~/Library/LaunchAgents/com.user.waypalace.memory.daemon.plist
-->
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.user.waypalace.memory.daemon</string>
  <key>ProgramArguments</key>
  <array>
    <string>${WAYPALACE_VENV}/bin/python</string>
    <string>${WAYPALACE_HOME}/waypalace/memory_daemon.py</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <dict>
    <key>SuccessfulExit</key>
    <false/>
  </dict>
  <key>StandardOutPath</key>
  <string>${WAYPALACE_HOME}/data/logs/daemon.out.log</string>
  <key>StandardErrorPath</key>
  <string>${WAYPALACE_HOME}/data/logs/daemon.err.log</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>WAYPALACE_HOME</key>
    <string>${WAYPALACE_HOME}</string>
    <key>WAYPALACE_DATA</key>
    <string>${WAYPALACE_HOME}/data</string>
  </dict>
  <key>ProcessType</key>
  <string>Background</string>
  <key>Nice</key>
  <integer>5</integer>
</dict>
</plist>

<?xml version="1.0" encoding="UTF-8"?>
<!--
 WayPalace hourly refresh batch — Tier 1 indexing + reconcilers.
 Substitute ${WAYPALACE_HOME} before installing.
-->
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.user.waypalace.refresh.daemon</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>${WAYPALACE_HOME}/refresh-batch.sh</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Minute</key>
    <integer>17</integer>
  </dict>
  <key>StandardOutPath</key>
  <string>${WAYPALACE_HOME}/data/logs/refresh.out.log</string>
  <key>StandardErrorPath</key>
  <string>${WAYPALACE_HOME}/data/logs/refresh.err.log</string>
</dict>
</plist>

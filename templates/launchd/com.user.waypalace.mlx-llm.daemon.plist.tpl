<?xml version="1.0" encoding="UTF-8"?>
<!--
 WayPalace optional MLX LLM server (Tier 2 only).
 Provides OpenAI-compatible endpoint on 127.0.0.1:8081.
 Substitute ${MLX_LLM_HOME} before installing.
-->
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.user.waypalace.mlx-llm.daemon</string>
  <key>ProgramArguments</key>
  <array>
    <string>${MLX_LLM_HOME}/venv-mlx/bin/python</string>
    <string>-m</string>
    <string>mlx_lm.server</string>
    <string>--model</string>
    <string>unsloth/Qwen3.6-35B-A3B-UD-MLX-4bit</string>
    <string>--host</string>
    <string>127.0.0.1</string>
    <string>--port</string>
    <string>8081</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>${MLX_LLM_HOME}/logs/server.out.log</string>
  <key>StandardErrorPath</key>
  <string>${MLX_LLM_HOME}/logs/server.err.log</string>
</dict>
</plist>

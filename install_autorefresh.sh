#!/bin/bash
set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
LAUNCH_DIR="$HOME/Library/LaunchAgents"
UID_NUM="$(id -u)"

MARKET_LABEL="com.globalmarketmonitor.market"
MACRO_LABEL="com.globalmarketmonitor.macro"
MARKET_PLIST="$LAUNCH_DIR/$MARKET_LABEL.plist"
MACRO_PLIST="$LAUNCH_DIR/$MACRO_LABEL.plist"

mkdir -p "$LAUNCH_DIR" "$PROJECT_DIR/logs"

chmod +x "$PROJECT_DIR/run_market_refresh.sh"
chmod +x "$PROJECT_DIR/run_macro_refresh.sh"

if [ ! -x "$PROJECT_DIR/.venv/bin/python" ]; then
  echo "ERROR: .venv not found in:"
  echo "  $PROJECT_DIR"
  echo ""
  echo "Create it first:"
  echo "  python3 -m venv .venv"
  echo "  source .venv/bin/activate"
  echo "  python -m pip install -r requirements.txt"
  exit 1
fi

cat > "$MARKET_PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$MARKET_LABEL</string>

  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$PROJECT_DIR/run_market_refresh.sh</string>
  </array>

  <key>WorkingDirectory</key>
  <string>$PROJECT_DIR</string>

  <key>StartInterval</key>
  <integer>300</integer>

  <key>RunAtLoad</key>
  <true/>

  <key>StandardOutPath</key>
  <string>$PROJECT_DIR/logs/launchd_market_stdout.log</string>

  <key>StandardErrorPath</key>
  <string>$PROJECT_DIR/logs/launchd_market_stderr.log</string>
</dict>
</plist>
EOF

cat > "$MACRO_PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$MACRO_LABEL</string>

  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$PROJECT_DIR/run_macro_refresh.sh</string>
  </array>

  <key>WorkingDirectory</key>
  <string>$PROJECT_DIR</string>

  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key>
    <integer>8</integer>
    <key>Minute</key>
    <integer>5</integer>
  </dict>

  <key>RunAtLoad</key>
  <true/>

  <key>StandardOutPath</key>
  <string>$PROJECT_DIR/logs/launchd_macro_stdout.log</string>

  <key>StandardErrorPath</key>
  <string>$PROJECT_DIR/logs/launchd_macro_stderr.log</string>
</dict>
</plist>
EOF

launchctl bootout "gui/$UID_NUM/$MARKET_LABEL" 2>/dev/null || true
launchctl bootout "gui/$UID_NUM/$MACRO_LABEL" 2>/dev/null || true

launchctl bootstrap "gui/$UID_NUM" "$MARKET_PLIST"
launchctl bootstrap "gui/$UID_NUM" "$MACRO_PLIST"

echo ""
echo "Installed:"
echo "  Market + quotes : every 5 minutes"
echo "  Macro           : daily at 08:05 local time"
echo ""
echo "Project:"
echo "  $PROJECT_DIR"
echo ""
echo "Logs:"
echo "  $PROJECT_DIR/logs/market_refresh.log"
echo "  $PROJECT_DIR/logs/market_refresh_error.log"
echo "  $PROJECT_DIR/logs/macro_refresh.log"
echo "  $PROJECT_DIR/logs/macro_refresh_error.log"

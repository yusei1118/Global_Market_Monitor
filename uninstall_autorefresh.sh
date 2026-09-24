#!/bin/bash
set -e

UID_NUM="$(id -u)"
LAUNCH_DIR="$HOME/Library/LaunchAgents"
MARKET_LABEL="com.globalmarketmonitor.market"
MACRO_LABEL="com.globalmarketmonitor.macro"

launchctl bootout "gui/$UID_NUM/$MARKET_LABEL" 2>/dev/null || true
launchctl bootout "gui/$UID_NUM/$MACRO_LABEL" 2>/dev/null || true

rm -f "$LAUNCH_DIR/$MARKET_LABEL.plist"
rm -f "$LAUNCH_DIR/$MACRO_LABEL.plist"

echo "Automatic refresh removed."

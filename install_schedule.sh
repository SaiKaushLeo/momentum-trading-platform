#!/bin/zsh
# Runs the bot every weekday at 8:00 AM Arizona time (= 10-11 AM New York, market is open).
# Undo with:  launchctl unload ~/Library/LaunchAgents/com.tradingbot.daily.plist && rm ~/Library/LaunchAgents/com.tradingbot.daily.plist
DIR="${0:A:h}"
PLIST=~/Library/LaunchAgents/com.tradingbot.daily.plist
mkdir -p "$DIR/logs"
DAYS=""
for d in 1 2 3 4 5; do DAYS+="<dict><key>Weekday</key><integer>$d</integer><key>Hour</key><integer>8</integer><key>Minute</key><integer>0</integer></dict>"; done
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>com.tradingbot.daily</string>
<key>ProgramArguments</key><array><string>$DIR/.venv/bin/python</string><string>$DIR/bot.py</string><string>--queue</string></array>
<key>WorkingDirectory</key><string>$DIR</string>
<key>StartCalendarInterval</key><array>$DAYS</array>
<key>StandardOutPath</key><string>$DIR/logs/bot.log</string>
<key>StandardErrorPath</key><string>$DIR/logs/bot.log</string>
</dict></plist>
PL
launchctl unload "$PLIST" 2>/dev/null
launchctl load "$PLIST" && echo "Scheduled: weekdays 8:00 AM. When there are trades, the dashboard opens so you can approve them."

# Market watch: every 15 minutes (does nothing while the market is closed). Notifications only, never trades.
# Undo with:  launchctl unload ~/Library/LaunchAgents/com.tradingbot.watch.plist && rm ~/Library/LaunchAgents/com.tradingbot.watch.plist
WATCH=~/Library/LaunchAgents/com.tradingbot.watch.plist
cat > "$WATCH" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>com.tradingbot.watch</string>
<key>ProgramArguments</key><array><string>$DIR/.venv/bin/python</string><string>$DIR/bot.py</string><string>--watch</string></array>
<key>WorkingDirectory</key><string>$DIR</string>
<key>StartInterval</key><integer>900</integer>
<key>StandardOutPath</key><string>$DIR/logs/watch.log</string>
<key>StandardErrorPath</key><string>$DIR/logs/watch.log</string>
</dict></plist>
PL
launchctl unload "$WATCH" 2>/dev/null
launchctl load "$WATCH" && echo "Market watch: every 15 minutes while the market is open (alerts + midday and closing updates)."

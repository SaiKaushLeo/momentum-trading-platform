#!/bin/zsh
# Double-click to open the trading bot's dashboard in your browser.
cd "${0:A:h}"
.venv/bin/python bot.py --open
echo "Dashboard is open in your browser. You can close this window."

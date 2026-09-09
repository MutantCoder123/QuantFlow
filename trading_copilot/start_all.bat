@echo off
REM Launch from the repo root (not from inside trading_copilot\).
REM smart_api_feed.py (dead Angel One code) and nse_feed.py (does not exist)
REM are replaced with the services that actually run: upstox_feed.py and
REM macro_worker.py.
wt -w 0 new-tab --title "Upstox Feed" -d . cmd /k "python trading_copilot\data_services\upstox_feed.py" ; ^
new-tab --title "News Feed" -d . cmd /k "python trading_copilot\data_services\news_feed.py" ; ^
new-tab --title "Macro Worker" -d . cmd /k "python trading_copilot\data_services\macro_worker.py" ; ^
new-tab --title "Web UI" -d . cmd /k "timeout /t 8 /nobreak && python trading_copilot\main.py"

echo All services launched in Windows Terminal!

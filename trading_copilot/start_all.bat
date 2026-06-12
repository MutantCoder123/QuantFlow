@echo off
echo Starting AlgoTrade Microservices in Windows Terminal Tabs...

wt -w 0 new-tab --title "Smart API Feed" -d . cmd /k "python data_services\smart_api_feed.py" ; ^
new-tab --title "NSE Feed" -d . cmd /k "python data_services\nse_feed.py" ; ^
new-tab --title "News Feed" -d . cmd /k "python data_services\news_feed.py" ; ^
new-tab --title "Web UI Server" -d . cmd /k "echo Waiting 5 seconds for background feeds to start... && timeout /t 5 /nobreak && python main.py"

echo All services launched in Windows Terminal!

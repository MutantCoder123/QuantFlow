@echo off
REM Stop QuantFlow services left running (anything listening on 8000, 8001, 8003).
for %%P in (8000 8001 8003) do (
    for /f "tokens=5" %%I in ('netstat -ano ^| findstr /R /C:"127.0.0.1:%%P .*LISTENING"') do (
        echo Stopping port %%P, pid %%I
        taskkill /PID %%I /T /F >nul 2>&1
    )
)
echo Done.

@echo off
chcp 65001 >nul
cd /d "d:\股票筛选"

echo ========================================
echo   A股ETF动量轮动筛选系统
echo ========================================
echo.
echo [1/2] 启动Web服务...
start "ETF-Web" cmd /c "d:\股票筛选\start_server.bat"

echo [2/2] 启动公网隧道（手机可访问）...
echo.
echo 正在连接，请稍候...
echo 隧道建立后，公网地址会显示在下方。
echo 按 Ctrl+C 可以关闭隧道。
echo.

ssh -o StrictHostKeyChecking=no -o ServerAliveInterval=30 -R 80:localhost:5000 nokey@localhost.run

pause

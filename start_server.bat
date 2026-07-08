@echo off
chcp 65001 >nul
cd /d "d:\股票筛选"

echo 正在启动Web服务...
echo 本机访问: http://localhost:5000
echo.

C:\Users\zzw\AppData\Local\Programs\Python\Python312\python.exe web_server.py
pause

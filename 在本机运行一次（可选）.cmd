@echo off
chcp 65001 >nul
rem 可选：在这台电脑上手动跑一次查新（需要 Python 3.11 及以上）。日常更新交给 GitHub 自动做，不需要这个。
cd /d "%~dp0"
where py >nul 2>nul && (py -3 -m radar %*) || (python -m radar %*)
echo.
echo 运行结束。网页已更新：docs\index.html
pause

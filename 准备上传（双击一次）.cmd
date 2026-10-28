@echo off
chcp 65001 >nul
rem Copy the scheduled-task file into .github\workflows (GitHub needs it there)
if not exist "%~dp0.github\workflows" mkdir "%~dp0.github\workflows"
copy /Y "%~dp0部署文件\radar.yml" "%~dp0.github\workflows\radar.yml" >nul
if exist "%~dp0.github\workflows\radar.yml" (echo 已就绪：.github\workflows\radar.yml 已创建，现在可以把整个文件夹上传到 GitHub 了。) else (echo 没成功，请按 README 里的“手动方式”操作。)
pause

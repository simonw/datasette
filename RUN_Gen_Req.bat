@echo off
setlocal enabledelayedexpansion

echo Please choose an option:
echo [1] Create new requirements.txt
echo [2] Overwrite existing requirements.txt
echo [3] Overwrite and archive old requirements.txt
set /p option=Enter your option (1/2/3): 

if %option%==1 (
    if not exist requirements.txt (
        pipreqs .
        echo New requirements.txt file created.
    ) else (
        echo Error: requirements.txt file already exists. Choose another option.
    )
)

if %option%==2 (
    if exist requirements.txt (
        pipreqs . --force
        echo Existing requirements.txt file overwritten.
    ) else (
        echo Error: requirements.txt file not found. Choose another option.
    )
)

if %option%==3 (
    if exist requirements.txt (
        set "timestamp=!date:~10,4!!date:~7,2!!date:~4,2!_!time:~0,2!!time:~3,2!!time:~6,2!"
        set "timestamp=!timestamp: =0!"
        copy requirements.txt requirements_archive_!timestamp!.txt
        pipreqs . --force
        echo Existing requirements.txt file overwritten and archived.
    ) else (
        echo Error: requirements.txt file not found. Choose another option.
    )
)

pause
endlocal

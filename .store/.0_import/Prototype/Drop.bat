@echo off
setlocal

:: Get the full path of the dropped file
set "csv_file=%~f1"

:: Activate the virtual environment
call "C:\Users\Martin\OneDrive - studiocordillera.com 1\Documents\.GITHUB_Repos\datasette\venv\Scripts\activate.bat"

:: Call the Python script with the full path of the dropped file as an argument
python "C:\Users\Martin\OneDrive - studiocordillera.com 1\Documents\.GITHUB_Repos\datasette\.store\.0_import\Prototype\Default.py" "%csv_file%"

:: Add a pause to see the output in the cmd window
pause

endlocal

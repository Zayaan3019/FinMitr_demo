@echo off
REM FinGuru Quick Start Script for Windows
REM This script sets up and runs FinGuru

echo ============================================
echo   FINGURU - Quick Start Setup
echo ============================================
echo.

REM Check Python installation
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python is not installed or not in PATH
    echo Please install Python 3.10+ from python.org
    pause
    exit /b 1
)

echo [1/6] Python found!

REM Create virtual environment if it doesn't exist
if not exist "venv\" (
    echo [2/6] Creating virtual environment...
    python -m venv venv
) else (
    echo [2/6] Virtual environment already exists
)

REM Activate virtual environment
echo [3/6] Activating virtual environment...
call venv\Scripts\activate.bat

REM Install dependencies
echo [4/6] Installing dependencies...
pip install -q -r requirements.txt

REM Check if .env exists
if not exist ".env" (
    echo [5/6] Creating .env file...
    copy .env.example .env
    echo.
    echo ============================================
    echo   IMPORTANT: Configure your .env file
    echo ============================================
    echo.
    echo Please edit .env file and add your GROQ API key
    echo Get free API key from: https://console.groq.com
    echo.
    echo After adding the API key, run this script again
    echo.
    pause
    exit /b 0
)

REM Generate sample data if it doesn't exist
if not exist "data\transactions.csv" (
    echo [5/6] Generating sample transaction data...
    python scripts\generate_data.py --users 2 --transactions 300 --output data\transactions.csv
) else (
    echo [5/6] Sample data already exists
)

echo [6/6] Starting FinGuru API server...
echo.
echo ============================================
echo   FinGuru is starting!
echo ============================================
echo.
echo API will be available at: http://localhost:8000
echo API Documentation: http://localhost:8000/docs
echo.
echo Press Ctrl+C to stop the server
echo.

python main.py

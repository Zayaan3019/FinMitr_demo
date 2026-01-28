@echo off
REM Comprehensive test runner for Windows

echo ============================================
echo   FINGURU TEST SUITE
echo ============================================
echo.

REM Activate virtual environment
if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
) else (
    echo [WARNING] Virtual environment not found
    echo Run: python -m venv venv
)

echo [1/4] Installing test dependencies...
pip install -q pytest pytest-asyncio pytest-cov httpx

echo [2/4] Running system validation...
python scripts\validate_system.py
if errorlevel 1 (
    echo.
    echo [ERROR] System validation failed
    echo Please fix the issues above before running tests
    pause
    exit /b 1
)

echo.
echo [3/4] Running comprehensive test suite...
python tests\run_tests.py

echo.
echo [4/4] Test execution complete!
echo.

echo ============================================
echo   TEST RESULTS
echo ============================================
echo.
echo To view detailed coverage:
echo   1. Open htmlcov\index.html in a browser
echo.
echo To run specific tests:
echo   pytest tests\test_models.py -v
echo   pytest tests\test_api.py -v
echo   pytest tests\test_agents.py -v
echo.
echo To run quick tests:
echo   python tests\run_tests.py --quick
echo.
echo To run with coverage:
echo   python tests\run_tests.py --coverage
echo.

pause

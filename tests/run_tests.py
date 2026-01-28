"""
Comprehensive test runner for FinGuru.
Runs all tests and generates detailed report.
"""
import pytest
import sys
from pathlib import Path
from datetime import datetime


def run_all_tests():
    """Run all test suites and generate report."""
    print("=" * 80)
    print("  FINGURU - COMPREHENSIVE TEST SUITE")
    print("=" * 80)
    print(f"  Start Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)
    print()
    
    # Test configuration
    test_dir = Path(__file__).parent
    
    # Run pytest with comprehensive options
    args = [
        str(test_dir),
        "-v",  # Verbose
        "--tb=short",  # Short traceback
        "--strict-markers",  # Strict marker checking
        "-ra",  # Show summary of all test outcomes
        "--color=yes",  # Colored output
        "-W", "ignore::DeprecationWarning",  # Ignore deprecation warnings
    ]
    
    print("\n🧪 UNIT TESTS")
    print("-" * 80)
    result_unit = pytest.main(args + ["-m", "unit", "--ignore=test_integration.py"])
    
    print("\n🔗 INTEGRATION TESTS")
    print("-" * 80)
    result_integration = pytest.main(args + ["-m", "integration"])
    
    print("\n📊 ALL TESTS")
    print("-" * 80)
    result_all = pytest.main(args)
    
    # Summary
    print("\n" + "=" * 80)
    print("  TEST SUMMARY")
    print("=" * 80)
    
    results = {
        "Unit Tests": "✅ PASSED" if result_unit == 0 else "❌ FAILED",
        "Integration Tests": "✅ PASSED" if result_integration == 0 else "❌ FAILED",
        "Overall": "✅ PASSED" if result_all == 0 else "❌ FAILED"
    }
    
    for test_type, status in results.items():
        print(f"  {test_type}: {status}")
    
    print("=" * 80)
    print(f"  End Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)
    
    return 0 if result_all == 0 else 1


def run_coverage_tests():
    """Run tests with coverage report."""
    print("\n🎯 RUNNING TESTS WITH COVERAGE")
    print("=" * 80)
    
    test_dir = Path(__file__).parent
    
    args = [
        str(test_dir),
        "--cov=app",
        "--cov-report=html",
        "--cov-report=term-missing",
        "-v"
    ]
    
    result = pytest.main(args)
    
    print("\n📊 Coverage report generated in htmlcov/index.html")
    
    return result


def run_quick_tests():
    """Run only fast tests for quick validation."""
    print("\n⚡ RUNNING QUICK TESTS")
    print("=" * 80)
    
    test_dir = Path(__file__).parent
    
    args = [
        str(test_dir),
        "-v",
        "-m", "not slow",
        "--tb=line"
    ]
    
    result = pytest.main(args)
    
    return result


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--coverage":
        sys.exit(run_coverage_tests())
    elif len(sys.argv) > 1 and sys.argv[1] == "--quick":
        sys.exit(run_quick_tests())
    else:
        sys.exit(run_all_tests())

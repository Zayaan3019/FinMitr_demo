"""
System validation and testing script for FinGuru.
Checks all components and dependencies.
"""

import sys
import importlib
from pathlib import Path


def print_section(title):
    """Print a formatted section header."""
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)


def check_python_version():
    """Check Python version."""
    print("\n🐍 Checking Python version...")
    version = sys.version_info

    if version.major >= 3 and version.minor >= 10:
        print(f"✅ Python {version.major}.{version.minor}.{version.micro}")
        return True
    else:
        print(f"❌ Python {version.major}.{version.minor} (requires 3.10+)")
        return False


def check_dependencies():
    """Check if all required packages are installed."""
    print("\n📦 Checking dependencies...")

    required_packages = [
        "fastapi",
        "uvicorn",
        "pydantic",
        "langchain",
        "langchain_groq",
        "langgraph",
        "chromadb",
        "sentence_transformers",
        "pandas",
        "numpy",
        "sklearn",
        "loguru",
        "tenacity",
    ]

    all_installed = True

    for package in required_packages:
        try:
            importlib.import_module(package.replace("-", "_"))
            print(f"✅ {package}")
        except ImportError:
            print(f"❌ {package} - NOT INSTALLED")
            all_installed = False

    return all_installed


def check_project_structure():
    """Check if all required files and directories exist."""
    print("\n📁 Checking project structure...")

    base_path = Path(__file__).parent.parent

    required_items = [
        "app/",
        "app/agents/",
        "app/api/",
        "app/core/",
        "app/db/",
        "app/models/",
        "scripts/",
        "main.py",
        "requirements.txt",
        ".env.example",
        "Dockerfile",
        "docker-compose.yml",
        "README.md",
    ]

    all_exist = True

    for item in required_items:
        item_path = base_path / item
        if item_path.exists():
            print(f"✅ {item}")
        else:
            print(f"❌ {item} - MISSING")
            all_exist = False

    return all_exist


def check_environment():
    """Check if .env file exists and has required variables."""
    print("\n⚙️  Checking environment configuration...")

    base_path = Path(__file__).parent.parent
    env_file = base_path / ".env"

    if not env_file.exists():
        print("❌ .env file not found")
        print("   Run: copy .env.example .env")
        return False

    print("✅ .env file exists")

    # Check for GROQ API key
    with open(env_file, "r") as f:
        content = f.read()
        if "your_groq_api_key_here" in content or "GROQ_API_KEY=" not in content:
            print("⚠️  GROQ_API_KEY not configured in .env")
            print("   Get your free key from: https://console.groq.com")
            return False

    print("✅ GROQ_API_KEY configured")
    return True


def check_data_directory():
    """Check if data directory exists."""
    print("\n💾 Checking data directory...")

    base_path = Path(__file__).parent.parent
    data_dir = base_path / "data"

    if not data_dir.exists():
        print("⚠️  Data directory not found (will be created automatically)")
        data_dir.mkdir(exist_ok=True)
        print("✅ Created data directory")
    else:
        print("✅ Data directory exists")

    return True


def test_imports():
    """Test importing core modules."""
    print("\n🧪 Testing module imports...")

    try:
        sys.path.insert(0, str(Path(__file__).parent.parent))

        print("  Testing app.core.config...")
        from app.core.config import settings

        print("✅ app.core.config")

        print("  Testing app.models.schemas...")
        from app.models.schemas import ChatRequest, ChatResponse

        print("✅ app.models.schemas")

        print("  Testing app.agents.workflow...")
        from app.agents.workflow import create_workflow

        print("✅ app.agents.workflow")

        print("  Testing app.db.vector_store...")
        from app.db.vector_store import VectorStoreManager

        print("✅ app.db.vector_store")

        return True

    except Exception as e:
        print(f"❌ Import failed: {e}")
        return False


def run_validation():
    """Run all validation checks."""
    print_section("FINGURU SYSTEM VALIDATION")

    results = {
        "Python Version": check_python_version(),
        "Dependencies": check_dependencies(),
        "Project Structure": check_project_structure(),
        "Environment Config": check_environment(),
        "Data Directory": check_data_directory(),
        "Module Imports": test_imports(),
    }

    # Summary
    print_section("VALIDATION SUMMARY")

    all_passed = True
    for check, passed in results.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{status} - {check}")
        if not passed:
            all_passed = False

    print("\n" + "=" * 70)

    if all_passed:
        print("\n🎉 ALL CHECKS PASSED! Your FinGuru system is ready to use!")
        print("\nNext steps:")
        print("  1. Generate sample data: python scripts/generate_data.py")
        print("  2. Start the server: python main.py")
        print("  3. Visit: http://localhost:8000/docs")
        print("\nOr run the automated demo: python scripts/example_usage.py")
    else:
        print("\n⚠️  Some checks failed. Please fix the issues above.")
        print("\nCommon fixes:")
        print("  • Missing dependencies: pip install -r requirements.txt")
        print("  • Missing .env: copy .env.example .env")
        print("  • Configure GROQ_API_KEY in .env file")

    print("\n" + "=" * 70)

    return all_passed


if __name__ == "__main__":
    success = run_validation()
    sys.exit(0 if success else 1)

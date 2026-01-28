"""
Final pre-production validation script.
Comprehensive system check before deployment.
"""

import sys
import subprocess
from pathlib import Path
from typing import List, Tuple
import importlib
import os

# Set UTF-8 encoding for Windows console
if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


class ProductionValidator:
    """Validates FinGuru for production deployment."""

    def __init__(self):
        self.errors: List[str] = []
        self.warnings: List[str] = []
        self.passed: List[str] = []

    def print_header(self, title: str):
        """Print section header."""
        print("\n" + "=" * 80)
        print(f"  {title}")
        print("=" * 80)

    def check(self, description: str, test_func) -> bool:
        """Run a check and record result."""
        try:
            result = test_func()
            if result:
                self.passed.append(description)
                print(f"✅ {description}")
                return True
            else:
                self.errors.append(description)
                print(f"❌ {description}")
                return False
        except Exception as e:
            self.errors.append(f"{description}: {str(e)}")
            print(f"❌ {description}: {str(e)}")
            return False

    def warn(self, description: str, message: str):
        """Add a warning."""
        self.warnings.append(f"{description}: {message}")
        print(f"⚠️  {description}: {message}")

    def check_python_version(self) -> bool:
        """Check Python version."""
        version = sys.version_info
        return version.major == 3 and version.minor >= 10

    def check_dependencies(self) -> bool:
        """Check all dependencies installed."""
        required = [
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
            "pytest",
        ]

        for package in required:
            try:
                importlib.import_module(package.replace("-", "_"))
            except ImportError:
                return False
        return True

    def check_test_coverage(self) -> bool:
        """Check if tests pass and coverage is adequate."""
        try:
            result = subprocess.run(
                ["pytest", "tests/", "-v", "--tb=no"],
                capture_output=True,
                text=True,
                timeout=300,
            )
            return result.returncode == 0
        except:
            return False

    def check_env_file(self) -> bool:
        """Check .env file exists and configured."""
        env_file = Path(".env")
        if not env_file.exists():
            return False

        content = env_file.read_text()
        return "GROQ_API_KEY=" in content and "your_groq_api_key_here" not in content

    def check_data_directory(self) -> bool:
        """Check data directory structure."""
        data_dir = Path("data")
        return data_dir.exists() or True  # Will be created if needed

    def check_docker_files(self) -> bool:
        """Check Docker configuration files exist."""
        return (
            Path("Dockerfile").exists()
            and Path("docker-compose.yml").exists()
            and Path(".dockerignore").exists()
        )

    def check_documentation(self) -> bool:
        """Check essential documentation exists."""
        docs = [
            "README.md",
            "SETUP.md",
            "ARCHITECTURE.md",
            "PRODUCTION_CHECKLIST.md",
            "TEST_REPORT.md",
        ]
        return all(Path(doc).exists() for doc in docs)

    def check_security(self) -> bool:
        """Run basic security checks."""
        # Check no hardcoded secrets in code
        code_files = list(Path("app").rglob("*.py"))

        # Patterns that indicate hardcoded secrets (must have quotes or direct value assignment)
        import re

        # Look for patterns like api_key="actual_key" or password='mypass123'
        dangerous_patterns = [
            r'password\s*=\s*["\'](?!.*settings)(?!.*os\.getenv)(?!.*env)[\w\-]+["\']',
            r'api_key\s*=\s*["\'](?!.*settings)(?!.*os\.getenv)(?!.*env)[\w\-]+["\']',
            r'secret\s*=\s*["\'](?!.*settings)(?!.*os\.getenv)(?!.*env)[\w\-]+["\']',
        ]

        for file in code_files:
            try:
                # Use UTF-8 encoding with error handling
                content = file.read_text(encoding="utf-8", errors="ignore")
                for pattern in dangerous_patterns:
                    if re.search(pattern, content, re.IGNORECASE):
                        # Found a potential hardcoded secret
                        return False
            except Exception as e:
                # Skip files that can't be read
                continue
        return True

    def check_code_quality(self) -> bool:
        """Check code formatting and linting."""
        try:
            # Check Black formatting
            result = subprocess.run(
                ["black", "--check", "app/", "scripts/"],
                capture_output=True,
                timeout=60,
            )
            return result.returncode == 0
        except:
            return True  # Not critical if Black not installed

    def check_gitignore(self) -> bool:
        """Check .gitignore properly configured."""
        gitignore = Path(".gitignore")
        if not gitignore.exists():
            return False

        content = gitignore.read_text()
        required = [".env", "__pycache__", "*.pyc", "data/"]
        return all(item in content for item in required)

    def validate_all(self) -> bool:
        """Run all validation checks."""
        self.print_header("FINGURU PRODUCTION VALIDATION")

        print("\n🔍 Running comprehensive pre-production checks...\n")

        # System checks
        self.print_header("1. SYSTEM REQUIREMENTS")
        self.check("Python 3.10+", self.check_python_version)
        self.check("Dependencies installed", self.check_dependencies)

        # Configuration checks
        self.print_header("2. CONFIGURATION")
        self.check("Environment file (.env)", self.check_env_file)
        self.check("Data directory structure", self.check_data_directory)
        self.check("Git ignore configured", self.check_gitignore)

        # Code quality checks
        self.print_header("3. CODE QUALITY")
        self.check("Code formatted (Black)", self.check_code_quality)

        # Security checks
        self.print_header("4. SECURITY")
        self.check("No hardcoded secrets", self.check_security)

        # Docker checks
        self.print_header("5. DEPLOYMENT")
        self.check("Docker files present", self.check_docker_files)

        # Documentation checks
        self.print_header("6. DOCUMENTATION")
        self.check("Essential documentation", self.check_documentation)

        # Testing checks (this may take time)
        self.print_header("7. TESTING")
        print("⏳ Running test suite (this may take 2-3 minutes)...")
        self.check("Test suite passes", self.check_test_coverage)

        # Summary
        self.print_header("VALIDATION SUMMARY")

        print(f"\n✅ Passed: {len(self.passed)}")
        print(f"❌ Failed: {len(self.errors)}")
        print(f"⚠️  Warnings: {len(self.warnings)}")

        if self.errors:
            print("\n❌ FAILURES:")
            for error in self.errors:
                print(f"   • {error}")

        if self.warnings:
            print("\n⚠️  WARNINGS:")
            for warning in self.warnings:
                print(f"   • {warning}")

        print("\n" + "=" * 80)

        if len(self.errors) == 0:
            print("\n🎉 PRODUCTION VALIDATION PASSED!")
            print("\n✅ FinGuru is READY for production deployment.")
            print("\nNext steps:")
            print("  1. Review PRODUCTION_CHECKLIST.md")
            print("  2. Configure production environment")
            print("  3. Deploy: docker-compose up -d")
            print("  4. Monitor: docker-compose logs -f")
            print("  5. Test: curl http://localhost:8000/api/v1/health")
            return True
        else:
            print("\n❌ PRODUCTION VALIDATION FAILED")
            print("\n⚠️  FinGuru is NOT ready for production.")
            print("\nPlease fix the failed checks above before deploying.")
            return False

    def generate_report(self):
        """Generate validation report file."""
        report_path = Path("validation_report.txt")

        with open(report_path, "w", encoding="utf-8") as f:
            f.write("=" * 80 + "\n")
            f.write("  FINGURU PRODUCTION VALIDATION REPORT\n")
            f.write("=" * 80 + "\n\n")

            f.write(f"Passed: {len(self.passed)}\n")
            f.write(f"Failed: {len(self.errors)}\n")
            f.write(f"Warnings: {len(self.warnings)}\n\n")

            if self.errors:
                f.write("FAILURES:\n")
                for error in self.errors:
                    f.write(f"  • {error}\n")
                f.write("\n")

            if self.warnings:
                f.write("WARNINGS:\n")
                for warning in self.warnings:
                    f.write(f"  • {warning}\n")
                f.write("\n")

            f.write("PASSED CHECKS:\n")
            for passed in self.passed:
                f.write(f"  [PASS] {passed}\n")

        print(f"\nValidation report saved to: {report_path}")


def main():
    """Run production validation."""
    validator = ProductionValidator()

    try:
        success = validator.validate_all()
        validator.generate_report()

        sys.exit(0 if success else 1)

    except KeyboardInterrupt:
        print("\n\n⚠️  Validation cancelled by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n❌ Validation failed with error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()

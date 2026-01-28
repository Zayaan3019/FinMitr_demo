"""
Example usage script for FinGuru API.
Demonstrates complete workflow from data generation to querying.
"""

import requests
import json
import time
from pathlib import Path

API_BASE_URL = "http://localhost:8000/api/v1"
USER_ID = "user_001"
DATA_FILE = "data/transactions.csv"


def check_health():
    """Check if API is healthy."""
    print("🔍 Checking API health...")
    response = requests.get(f"{API_BASE_URL}/health")

    if response.status_code == 200:
        data = response.json()
        print(f"✅ API Status: {data['status']}")
        print(f"   Version: {data['version']}")
        print(f"   Components: {json.dumps(data['components'], indent=2)}")
        return True
    else:
        print(f"❌ API not healthy: {response.status_code}")
        return False


def ingest_data(user_id: str, file_path: str):
    """Ingest transaction data."""
    print(f"\n📤 Ingesting data for {user_id}...")

    if not Path(file_path).exists():
        print(f"❌ File not found: {file_path}")
        print("   Run: python scripts/generate_data.py first")
        return False

    with open(file_path, "rb") as f:
        files = {"file": f}
        response = requests.post(
            f"{API_BASE_URL}/ingest", params={"user_id": user_id}, files=files
        )

    if response.status_code == 200:
        data = response.json()
        print(f"✅ Ingestion successful!")
        print(f"   Transactions: {data['transactions_count']}")
        print(f"   Time taken: {data['embedding_time_seconds']}s")
        return True
    else:
        print(f"❌ Ingestion failed: {response.status_code}")
        print(f"   Error: {response.text}")
        return False


def ask_question(user_id: str, query: str):
    """Ask a financial question."""
    print(f"\n💬 Asking: '{query}'...")
    print("⏳ Processing (this may take 5-10 seconds)...\n")

    payload = {"user_id": user_id, "query": query}

    start = time.time()
    response = requests.post(f"{API_BASE_URL}/chat", json=payload)
    elapsed = time.time() - start

    if response.status_code == 200:
        data = response.json()

        print("=" * 70)
        print("🤖 FINGURU RESPONSE")
        print("=" * 70)

        # Show reasoning steps
        print("\n📋 Reasoning Steps:")
        for i, step in enumerate(data["reasoning_steps"], 1):
            print(f"  {i}. [{step['agent']}] {step['action']}")
            if step.get("result"):
                print(f"     → {step['result']}")

        # Show anomalies if any
        if data["anomalies_detected"]:
            print(f"\n⚠️  Detected {len(data['anomalies_detected'])} Anomalies:")
            for anomaly in data["anomalies_detected"][:3]:
                print(
                    f"  • {anomaly['date']}: ${abs(anomaly['amount']):.2f} - {anomaly['description']}"
                )

        # Show final answer
        print("\n💡 Financial Advice:")
        print("-" * 70)
        print(data["final_answer"])
        print("-" * 70)

        # Stats
        print(f"\n📊 Stats:")
        print(f"  • Context retrieved: {data['context_retrieved']} transactions")
        print(f"  • Processing time: {data['processing_time_seconds']}s")
        print(f"  • Total time: {elapsed:.2f}s")

        return True
    else:
        print(f"❌ Query failed: {response.status_code}")
        print(f"   Error: {response.text}")
        return False


def get_stats():
    """Get system statistics."""
    print("\n📊 Getting system statistics...")
    response = requests.get(f"{API_BASE_URL}/stats")

    if response.status_code == 200:
        data = response.json()
        print(f"✅ Statistics:")
        print(f"   {json.dumps(data['statistics'], indent=2)}")
        return True
    else:
        print(f"❌ Failed to get stats: {response.status_code}")
        return False


def main():
    """Run the complete example workflow."""
    print("\n" + "=" * 70)
    print("  FINGURU - AGENTIC RAG FINANCIAL ADVISOR - DEMO")
    print("=" * 70)

    # 1. Health check
    if not check_health():
        print("\n❌ Please start the API server first:")
        print("   python main.py")
        return

    time.sleep(1)

    # 2. Ingest data
    if not ingest_data(USER_ID, DATA_FILE):
        return

    time.sleep(2)

    # 3. Ask questions
    questions = [
        "What are my spending patterns this month and any unusual transactions?",
        "How much did I spend on groceries versus dining out?",
        "Are there any suspicious or unusual transactions I should be aware of?",
        "What are my top 3 spending categories and how can I reduce expenses?",
    ]

    for question in questions:
        ask_question(USER_ID, question)
        time.sleep(2)

    # 4. Get stats
    get_stats()

    print("\n" + "=" * 70)
    print("  ✅ DEMO COMPLETED SUCCESSFULLY!")
    print("=" * 70)
    print("\nNext steps:")
    print("  1. Try your own questions via API or Swagger UI")
    print("  2. Upload your own transaction CSV")
    print("  3. Test with multiple users for multi-tenancy")
    print("\nAPI Documentation: http://localhost:8000/docs")


if __name__ == "__main__":
    main()

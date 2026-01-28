"""
Synthetic Financial Data Generator for FinGuru.

Generates realistic transaction data for multiple users with:
- Diverse transaction categories
- Realistic spending patterns
- Outliers for anomaly detection testing
- Multi-tenant data separation
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import List, Dict
import random
import argparse
from pathlib import Path

# Transaction templates with realistic descriptions
TRANSACTION_TEMPLATES = {
    "Groceries": [
        "Whole Foods Market",
        "Trader Joe's",
        "Safeway",
        "Costco Wholesale",
        "Walmart Supercenter",
        "Target Grocery",
        "Kroger",
        "Albertsons",
    ],
    "Utilities": [
        "PG&E Electric Bill",
        "Water District Payment",
        "Gas Company",
        "Internet Service - Comcast",
        "Mobile Phone - Verizon",
        "Trash Collection",
    ],
    "Transportation": [
        "Shell Gas Station",
        "Uber Ride",
        "Lyft Trip",
        "Public Transit Pass",
        "Car Insurance Payment",
        "Parking Meter",
        "Vehicle Maintenance",
    ],
    "Entertainment": [
        "Netflix Subscription",
        "Spotify Premium",
        "Movie Theater AMC",
        "Concert Tickets",
        "Steam Games",
        "PlayStation Store",
        "Amazon Prime Video",
    ],
    "Healthcare": [
        "CVS Pharmacy",
        "Doctor Visit Copay",
        "Dental Cleaning",
        "Health Insurance Premium",
        "Prescription Refill",
        "Lab Tests",
    ],
    "Shopping": [
        "Amazon.com Purchase",
        "Target Online",
        "Best Buy Electronics",
        "Home Depot",
        "IKEA Furniture",
        "Nike Store",
        "Apple Store",
    ],
    "Dining": [
        "Starbucks Coffee",
        "Chipotle Mexican Grill",
        "McDonald's",
        "Olive Garden",
        "Panera Bread",
        "Local Restaurant",
        "Door Dash Delivery",
    ],
    "Salary": ["Direct Deposit - Employer", "Payroll Deposit", "Monthly Salary"],
    "Investment": [
        "Fidelity 401k Contribution",
        "Vanguard Investment",
        "Robinhood Deposit",
        "Crypto Purchase - Coinbase",
    ],
    "Rent": ["Monthly Rent Payment", "Apartment Rent", "Housing Payment"],
    "Insurance": ["Life Insurance Premium", "Home Insurance", "Auto Insurance Premium"],
    "Other": ["ATM Withdrawal", "Bank Transfer", "Cash Deposit", "Miscellaneous"],
}

# Typical amount ranges per category
AMOUNT_RANGES = {
    "Groceries": (30, 250),
    "Utilities": (50, 300),
    "Transportation": (20, 150),
    "Entertainment": (10, 100),
    "Healthcare": (50, 500),
    "Shopping": (25, 600),
    "Dining": (15, 80),
    "Salary": (3000, 8000),
    "Investment": (100, 2000),
    "Rent": (1200, 3000),
    "Insurance": (100, 500),
    "Other": (10, 200),
}


class FinancialDataGenerator:
    """Generate synthetic financial transaction data."""

    def __init__(self, seed: int = 42):
        """
        Initialize generator with seed for reproducibility.

        Args:
            seed: Random seed
        """
        random.seed(seed)
        np.random.seed(seed)

    def generate_transaction(self, user_id: str, date: datetime, category: str) -> Dict:
        """
        Generate a single transaction.

        Args:
            user_id: User identifier
            date: Transaction date
            category: Transaction category

        Returns:
            Transaction dictionary
        """
        # Get description
        descriptions = TRANSACTION_TEMPLATES[category]
        description = random.choice(descriptions)

        # Get amount based on category
        min_amt, max_amt = AMOUNT_RANGES[category]

        # Salary and Rent are positive, others can be expenses (negative)
        if category in ["Salary"]:
            amount = round(random.uniform(min_amt, max_amt), 2)
        else:
            amount = round(-random.uniform(min_amt, max_amt), 2)

        return {
            "date": date,
            "amount": amount,
            "description": description,
            "category": category,
            "user_id": user_id,
        }

    def generate_user_transactions(
        self, user_id: str, num_months: int = 6, transactions_per_month: int = 50
    ) -> List[Dict]:
        """
        Generate transactions for a single user over multiple months.

        Args:
            user_id: User identifier
            num_months: Number of months to generate
            transactions_per_month: Average transactions per month

        Returns:
            List of transaction dictionaries
        """
        transactions = []
        end_date = datetime.now()

        # Categories distribution (realistic spending patterns)
        category_weights = {
            "Groceries": 0.20,
            "Dining": 0.15,
            "Shopping": 0.12,
            "Transportation": 0.10,
            "Entertainment": 0.08,
            "Utilities": 0.05,
            "Healthcare": 0.05,
            "Other": 0.15,
            "Salary": 0.02,  # Monthly salary
            "Rent": 0.02,  # Monthly rent
            "Investment": 0.03,
            "Insurance": 0.03,
        }

        categories = list(category_weights.keys())
        weights = list(category_weights.values())

        for month_offset in range(num_months):
            # Calculate month start date
            month_start = end_date - timedelta(days=30 * (month_offset + 1))

            # Generate transactions for this month
            month_transactions = transactions_per_month + random.randint(-10, 10)

            for _ in range(month_transactions):
                # Random date within the month
                day_offset = random.randint(0, 29)
                transaction_date = month_start + timedelta(days=day_offset)

                # Choose category based on weights
                category = random.choices(categories, weights=weights)[0]

                # Generate transaction
                transaction = self.generate_transaction(
                    user_id=user_id, date=transaction_date, category=category
                )

                transactions.append(transaction)

        return transactions

    def inject_anomalies(
        self, transactions: List[Dict], anomaly_rate: float = 0.05
    ) -> List[Dict]:
        """
        Inject anomalous transactions for testing anomaly detection.

        Args:
            transactions: List of normal transactions
            anomaly_rate: Percentage of transactions to make anomalous

        Returns:
            Transactions with injected anomalies
        """
        num_anomalies = int(len(transactions) * anomaly_rate)
        anomaly_indices = random.sample(range(len(transactions)), num_anomalies)

        for idx in anomaly_indices:
            # Make amount 3-5x larger than normal
            transactions[idx]["amount"] *= random.uniform(3, 5)
            transactions[idx]["description"] += " [UNUSUAL]"

        return transactions

    def generate_multi_user_dataset(
        self, num_users: int = 2, transactions_per_user: int = 300
    ) -> pd.DataFrame:
        """
        Generate complete dataset for multiple users.

        Args:
            num_users: Number of users to generate
            transactions_per_user: Total transactions per user

        Returns:
            DataFrame with all transactions
        """
        all_transactions = []

        for user_num in range(1, num_users + 1):
            user_id = f"user_{user_num:03d}"

            print(f"Generating data for {user_id}...")

            # Generate base transactions
            transactions = self.generate_user_transactions(
                user_id=user_id,
                num_months=6,
                transactions_per_month=transactions_per_user // 6,
            )

            # Inject anomalies
            transactions = self.inject_anomalies(transactions, anomaly_rate=0.05)

            all_transactions.extend(transactions)

        # Convert to DataFrame
        df = pd.DataFrame(all_transactions)

        # Sort by date
        df = df.sort_values("date").reset_index(drop=True)

        print(f"\nGenerated {len(df)} total transactions for {num_users} users")
        print(f"Date range: {df['date'].min()} to {df['date'].max()}")

        return df


def main():
    """Main function for command-line usage."""
    parser = argparse.ArgumentParser(
        description="Generate synthetic financial transaction data"
    )
    parser.add_argument(
        "--users", type=int, default=2, help="Number of users to generate data for"
    )
    parser.add_argument(
        "--transactions", type=int, default=300, help="Transactions per user"
    )
    parser.add_argument(
        "--output",
        type=str,
        default="data/transactions.csv",
        help="Output CSV file path",
    )
    parser.add_argument(
        "--seed", type=int, default=42, help="Random seed for reproducibility"
    )

    args = parser.parse_args()

    # Create generator
    generator = FinancialDataGenerator(seed=args.seed)

    # Generate data
    df = generator.generate_multi_user_dataset(
        num_users=args.users, transactions_per_user=args.transactions
    )

    # Ensure output directory exists
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Save to CSV
    df.to_csv(output_path, index=False)

    print(f"\n✅ Data saved to: {output_path}")
    print("\nSample transactions:")
    print(df.head(10).to_string())

    # Show statistics per user
    print("\n" + "=" * 60)
    print("Statistics by User:")
    print("=" * 60)
    for user_id in df["user_id"].unique():
        user_df = df[df["user_id"] == user_id]
        print(f"\n{user_id}:")
        print(f"  Total transactions: {len(user_df)}")
        print(f"  Date range: {user_df['date'].min()} to {user_df['date'].max()}")
        print(f"  Total spent: ${user_df[user_df['amount'] < 0]['amount'].sum():.2f}")
        print(f"  Total income: ${user_df[user_df['amount'] > 0]['amount'].sum():.2f}")
        print(f"  Categories: {user_df['category'].nunique()}")


if __name__ == "__main__":
    main()

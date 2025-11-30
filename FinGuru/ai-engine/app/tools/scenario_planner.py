import psycopg2
from psycopg2.extras import RealDictCursor
from typing import Dict, Any
import re

class ScenarioPlanner:
    def __init__(self, db_url: str):
        self.db_url = db_url
    
    def plan(self, user_id: str, scenario: str) -> Dict[str, Any]:
        """Evaluate what-if scenarios"""
        try:
            # Extract amount from scenario
            amount_match = re.search(r'\$?(\d+(?:,\d{3})*(?:\.\d{2})?)', scenario)
            if not amount_match:
                return {"error": "Could not extract amount from scenario"}
            
            amount = float(amount_match.group(1).replace(',', ''))
            
            # Get user's financial data
            conn = psycopg2.connect(self.db_url)
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            # Get total balance
            cursor.execute("""
                SELECT SUM(balance) as total_balance
                FROM accounts
                WHERE user_id = %s AND account_type IN ('checking', 'savings')
            """, (user_id,))
            
            balance_row = cursor.fetchone()
            total_balance = float(balance_row['total_balance']) if balance_row else 0
            
            # Get average monthly spending
            cursor.execute("""
                SELECT AVG(monthly_total) as avg_monthly
                FROM (
                    SELECT DATE_TRUNC('month', transaction_date) as month,
                           SUM(ABS(amount)) as monthly_total
                    FROM transactions
                    WHERE user_id = %s AND amount < 0
                    GROUP BY DATE_TRUNC('month', transaction_date)
                    ORDER BY month DESC
                    LIMIT 3
                ) as monthly_spending
            """, (user_id,))
            
            avg_row = cursor.fetchone()
            avg_monthly_spend = float(avg_row['avg_monthly']) if avg_row else 0
            
            cursor.close()
            conn.close()
            
            # Calculate affordability
            remaining_balance = total_balance - amount
            months_of_runway = remaining_balance / avg_monthly_spend if avg_monthly_spend > 0 else 0
            
            affordable = remaining_balance >= (avg_monthly_spend * 3)  # At least 3 months emergency fund
            
            return {
                "scenario": scenario,
                "amount": amount,
                "current_balance": total_balance,
                "remaining_after_purchase": remaining_balance,
                "avg_monthly_spending": avg_monthly_spend,
                "months_of_runway": round(months_of_runway, 1),
                "affordable": affordable,
                "recommendation": (
                    f"✅ You can afford this purchase. You'll have ${remaining_balance:.2f} remaining, "
                    f"which covers {months_of_runway:.1f} months of expenses."
                    if affordable else
                    f"⚠️ This purchase is risky. You'll only have ${remaining_balance:.2f} remaining, "
                    f"which covers {months_of_runway:.1f} months. Consider saving more first."
                ),
                "confidence": 0.90
            }
            
        except Exception as e:
            print(f"Scenario planning error: {e}")
            return {"error": str(e)}

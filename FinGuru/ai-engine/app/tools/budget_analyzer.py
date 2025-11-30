import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime
from typing import Dict, Any

class BudgetAnalyzer:
    def __init__(self, db_url: str):
        self.db_url = db_url
    
    def analyze(self, user_id: str) -> Dict[str, Any]:
        """Analyze user's budget status"""
        try:
            conn = psycopg2.connect(self.db_url)
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            # Get current month's budgets
            current_month = datetime.now().replace(day=1).date()
            cursor.execute("""
                SELECT category, monthly_limit, current_spend
                FROM budgets
                WHERE user_id = %s AND month = %s
            """, (user_id, current_month))
            
            budgets = cursor.fetchall()
            
            # Calculate spending this month
            cursor.execute("""
                SELECT category, SUM(ABS(amount)) as total_spent
                FROM transactions
                WHERE user_id = %s 
                AND transaction_date >= %s
                AND amount < 0
                GROUP BY category
            """, (user_id, current_month))
            
            spending = {row['category']: float(row['total_spent']) for row in cursor.fetchall()}
            
            cursor.close()
            conn.close()
            
            # Analyze
            overspent_categories = []
            overspent_amounts = {}
            total_spent = 0
            total_limit = 0
            
            for budget in budgets:
                category = budget['category']
                limit = float(budget['monthly_limit'])
                spent = spending.get(category, 0)
                
                total_spent += spent
                total_limit += limit
                
                if spent > limit:
                    overspent_categories.append(category)
                    overspent_amounts[category] = spent - limit
            
            return {
                "total_spent": total_spent,
                "total_limit": total_limit,
                "overspent_categories": overspent_categories,
                "overspent_amount": overspent_amounts,
                "savings_opportunity": max(0, total_limit - total_spent) * 0.1,
                "status": "good" if not overspent_categories else "warning"
            }
            
        except Exception as e:
            print(f"Budget analysis error: {e}")
            return {"error": str(e)}

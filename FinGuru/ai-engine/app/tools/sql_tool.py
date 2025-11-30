import psycopg2
from psycopg2.extras import RealDictCursor
from typing import List, Dict, Any

class SQLTool:
    def __init__(self, db_url: str):
        self.db_url = db_url
    
    def execute(self, query: str) -> List[Dict[str, Any]]:
        """Execute SQL query and return results"""
        try:
            conn = psycopg2.connect(self.db_url)
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute(query)
            results = cursor.fetchall()
            cursor.close()
            conn.close()
            return [dict(row) for row in results]
        except Exception as e:
            print(f"SQL execution error: {e}")
            return [{"error": str(e)}]

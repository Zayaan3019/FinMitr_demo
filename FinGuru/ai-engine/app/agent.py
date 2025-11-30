import weaviate
import psycopg2
from psycopg2.extras import RealDictCursor
from typing import List, Dict, Any, Optional
import json
import httpx
from datetime import datetime, timedelta
from langgraph.graph import StateGraph, END
from typing_extensions import TypedDict
from app.tools.sql_tool import SQLTool
from app.tools.budget_analyzer import BudgetAnalyzer
from app.tools.scenario_planner import ScenarioPlanner
from app.embeddings.embedder import TransactionEmbedder

class AgentState(TypedDict):
    user_id: str
    query: str
    context: List[Dict]
    sql_result: Optional[str]
    analysis_result: Optional[Dict]
    final_answer: str
    needs_sql: bool
    needs_analysis: bool
    confidence: float

class FinGuruAgent:
    def __init__(self, db_url: str, weaviate_url: str, ollama_url: str, llm_model: str):
        self.db_url = db_url
        self.weaviate_url = weaviate_url
        self.ollama_url = ollama_url
        self.llm_model = llm_model
        
        self.weaviate_ready = False
        self.ollama_ready = False
        
        # Initialize tools
        self.sql_tool = SQLTool(db_url)
        self.budget_analyzer = BudgetAnalyzer(db_url)
        self.scenario_planner = ScenarioPlanner(db_url)
        self.embedder = TransactionEmbedder()
    
    async def initialize(self):
        """Initialize connections"""
        try:
            # Test Weaviate connection
            self.weaviate_client = weaviate.Client(url=self.weaviate_url)
            self.weaviate_client.schema.get()
            self.weaviate_ready = True
            print("✅ Weaviate connected")
            
            # Initialize Weaviate schema if not exists
            await self._init_weaviate_schema()
            
        except Exception as e:
            print(f"⚠️ Weaviate connection failed: {e}")
        
        try:
            # Test Ollama connection
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{self.ollama_url}/api/tags")
                if response.status_code == 200:
                    self.ollama_ready = True
                    print("✅ Ollama connected")
        except Exception as e:
            print(f"⚠️ Ollama connection failed: {e}")
    
    async def _init_weaviate_schema(self):
        """Create Weaviate schema for transactions"""
        schema = {
            "class": "Transaction",
            "description": "Financial transaction data with embeddings",
            "properties": [
                {"name": "transaction_id", "dataType": ["string"]},
                {"name": "user_id", "dataType": ["string"]},
                {"name": "merchant_name", "dataType": ["string"]},
                {"name": "category", "dataType": ["string"]},
                {"name": "amount", "dataType": ["number"]},
                {"name": "transaction_date", "dataType": ["string"]},
                {"name": "description", "dataType": ["text"]},
            ],
        }
        
        try:
            existing = self.weaviate_client.schema.get()
            if not any(c['class'] == 'Transaction' for c in existing.get('classes', [])):
                self.weaviate_client.schema.create_class(schema)
                print("✅ Weaviate schema created")
        except Exception as e:
            print(f"Schema creation error: {e}")
    
    async def process_query(self, user_id: str, query: str, conversation_history: List = []) -> Dict:
        """Main query processing with LangGraph workflow"""
        
        # Build the graph
        workflow = StateGraph(AgentState)
        
        # Add nodes
        workflow.add_node("classify", self._classify_intent)
        workflow.add_node("retrieve_context", self._retrieve_context)
        workflow.add_node("execute_sql", self._execute_sql)
        workflow.add_node("analyze", self._analyze_data)
        workflow.add_node("synthesize", self._synthesize_answer)
        
        # Define edges
        workflow.set_entry_point("classify")
        
        def route_after_classify(state):
            if state["needs_sql"]:
                return "execute_sql"
            else:
                return "retrieve_context"
        
        workflow.add_conditional_edges(
            "classify",
            route_after_classify,
            {
                "execute_sql": "execute_sql",
                "retrieve_context": "retrieve_context"
            }
        )
        
        workflow.add_edge("execute_sql", "analyze")
        workflow.add_edge("retrieve_context", "analyze")
        workflow.add_edge("analyze", "synthesize")
        workflow.add_edge("synthesize", END)
        
        # Compile and run
        graph = workflow.compile()
        
        initial_state: AgentState = {
            "user_id": user_id,
            "query": query,
            "context": [],
            "sql_result": None,
            "analysis_result": None,
            "final_answer": "",
            "needs_sql": False,
            "needs_analysis": False,
            "confidence": 0.95
        }
        
        result = graph.invoke(initial_state)
        
        return {
            "answer": result["final_answer"],
            "confidence": result["confidence"],
            "sources": [c.get("merchant_name", "Unknown") for c in result["context"][:3]],
            "debug_info": {
                "used_sql": result["needs_sql"],
                "used_vector_search": len(result["context"]) > 0,
                "analysis_performed": result["needs_analysis"]
            }
        }
    
    def _classify_intent(self, state: AgentState) -> AgentState:
        """Classify user intent"""
        query_lower = state["query"].lower()
        
        # Check if query needs specific data lookup
        sql_keywords = ["how much", "total", "spent", "sum", "count", "last month", "this month"]
        state["needs_sql"] = any(keyword in query_lower for keyword in sql_keywords)
        
        # Check if needs budget analysis
        analysis_keywords = ["budget", "saving", "overspent", "afford", "goal"]
        state["needs_analysis"] = any(keyword in query_lower for keyword in analysis_keywords)
        
        return state
    
    def _retrieve_context(self, state: AgentState) -> AgentState:
        """Retrieve relevant context from Weaviate"""
        if not self.weaviate_ready:
            return state
        
        try:
            # Generate query embedding
            query_embedding = self.embedder.encode(state["query"])
            
            # Search in Weaviate
            result = (
                self.weaviate_client.query
                .get("Transaction", ["transaction_id", "merchant_name", "category", "amount", "transaction_date", "description"])
                .with_near_vector({"vector": query_embedding.tolist()})
                .with_where({
                    "path": ["user_id"],
                    "operator": "Equal",
                    "valueString": state["user_id"]
                })
                .with_limit(5)
                .do()
            )
            
            transactions = result.get("data", {}).get("Get", {}).get("Transaction", [])
            state["context"] = transactions
            
        except Exception as e:
            print(f"Vector search error: {e}")
            state["context"] = []
        
        return state
    
    def _execute_sql(self, state: AgentState) -> AgentState:
        """Execute SQL query using LLM-generated SQL"""
        try:
            # Generate SQL using Ollama
            sql_query = self._generate_sql(state["user_id"], state["query"])
            
            # Execute SQL
            result = self.sql_tool.execute(sql_query)
            state["sql_result"] = json.dumps(result)
            
        except Exception as e:
            print(f"SQL execution error: {e}")
            state["sql_result"] = json.dumps({"error": str(e)})
        
        return state
    
    def _analyze_data(self, state: AgentState) -> AgentState:
        """Perform budget/financial analysis"""
        if not state["needs_analysis"]:
            return state
        
        try:
            analysis = self.budget_analyzer.analyze(state["user_id"])
            state["analysis_result"] = analysis
        except Exception as e:
            print(f"Analysis error: {e}")
        
        return state
    
    def _synthesize_answer(self, state: AgentState) -> AgentState:
        """Generate final answer using LLM"""
        try:
            # Build prompt
            prompt = self._build_synthesis_prompt(state)
            
            # Call Ollama
            answer = self._call_ollama(prompt)
            state["final_answer"] = answer
            
        except Exception as e:
            print(f"Synthesis error: {e}")
            state["final_answer"] = "I apologize, but I encountered an error processing your request. Please try again."
            state["confidence"] = 0.5
        
        return state
    
    def _generate_sql(self, user_id: str, query: str) -> str:
        """Generate SQL query using LLM"""
        prompt = f"""Generate a PostgreSQL query to answer this question. Return ONLY the SQL query with no explanation.

User ID: {user_id}
Question: {query}

Available tables:
- transactions (id, user_id, amount, merchant_name, category, transaction_date, description)
- accounts (id, user_id, institution_name, account_type, balance)
- budgets (id, user_id, category, monthly_limit, current_spend, month)

Important: Always filter by user_id = '{user_id}' in WHERE clause.

SQL Query:"""
        
        response = self._call_ollama(prompt)
        
        # Extract SQL from response
        sql = response.strip()
        if "```
            sql = sql.split("```sql").split("```
        elif "```" in sql:
            sql = sql.split("``````")[0].strip()
        
        return sql
    
    def _build_synthesis_prompt(self, state: AgentState) -> str:
        """Build prompt for final answer synthesis"""
        context_str = "\n".join([
            f"- {t.get('merchant_name', 'Unknown')}: ${t.get('amount', 0)} on {t.get('transaction_date', 'N/A')} ({t.get('category', 'Uncategorized')})"
            for t in state["context"][:5]
        ])
        
        sql_result_str = state["sql_result"] if state["sql_result"] else "No specific data retrieved"
        
        analysis_str = ""
        if state["analysis_result"]:
            analysis_str = f"\nBudget Analysis:\n{json.dumps(state['analysis_result'], indent=2)}"
        
        prompt = f"""You are FinGuru, a helpful AI financial assistant. Answer the user's question using the provided data.

User Question: {state["query"]}

Recent Transactions:
{context_str}

Database Query Result:
{sql_result_str}
{analysis_str}

Provide a clear, actionable answer. Use specific numbers when available. If suggesting financial advice, be helpful and practical.

Answer:"""
        
        return prompt
    
    def _call_ollama(self, prompt: str) -> str:
        """Call Ollama LLM API"""
        try:
            import requests
            
            response = requests.post(
                f"{self.ollama_url}/api/generate",
                json={
                    "model": self.llm_model,
                    "prompt": prompt,
                    "stream": False
                },
                timeout=60
            )
            
            if response.status_code == 200:
                return response.json()["response"]
            else:
                return "I'm having trouble processing your request right now."
                
        except Exception as e:
            print(f"Ollama API error: {e}")
            return "I apologize, but I'm experiencing technical difficulties."
    
    async def analyze_budget(self, user_id: str) -> Dict:
        """Perform comprehensive budget analysis"""
        return self.budget_analyzer.analyze(user_id)
    
    async def plan_scenario(self, user_id: str, scenario: str) -> Dict:
        """Perform what-if scenario planning"""
        return self.scenario_planner.plan(user_id, scenario)
    
    async def generate_nudges(self, user_id: str) -> List[Dict]:
        """Generate proactive nudges"""
        nudges = []
        
        # Analyze spending patterns
        analysis = self.budget_analyzer.analyze(user_id)
        
        # Generate nudges based on insights
        if analysis.get("overspent_categories"):
            for category in analysis["overspent_categories"]:
                nudges.append({
                    "title": f"Budget Alert: {category}",
                    "message": f"You've exceeded your {category} budget by ${analysis['overspent_amount'][category]:.2f}",
                    "type": "budget_warning",
                    "priority": "high"
                })
        
        # Check for savings opportunities
        if analysis.get("savings_opportunity"):
            nudges.append({
                "title": "Savings Opportunity",
                "message": f"You can save ${analysis['savings_opportunity']:.2f} by reducing discretionary spending.",
                "type": "savings_tip",
                "priority": "medium"
            })
        
        return nudges

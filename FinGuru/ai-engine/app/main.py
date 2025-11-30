from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
import os
from app.agent import FinGuruAgent
from app.models import ChatRequest, ChatResponse, BudgetAnalysisRequest, BudgetAnalysisResponse

app = FastAPI(title="FinGuru AI Engine", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize AI agent
agent = FinGuruAgent(
    db_url=os.getenv("DATABASE_URL"),
    weaviate_url=os.getenv("WEAVIATE_URL", "http://weaviate:8080"),
    ollama_url=os.getenv("OLLAMA_URL", "http://ollama:11434"),
    llm_model=os.getenv("LLM_MODEL", "llama3.1:8b")
)

@app.on_event("startup")
async def startup_event():
    """Initialize agent on startup"""
    await agent.initialize()
    print("✅ AI Engine initialized successfully")

@app.get("/")
async def root():
    return {"service": "FinGuru AI Engine", "status": "healthy"}

@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "service": "ai-engine",
        "llm_model": os.getenv("LLM_MODEL"),
        "weaviate_connected": agent.weaviate_ready,
        "ollama_connected": agent.ollama_ready
    }

@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """Main conversational endpoint with RAG"""
    try:
        response = await agent.process_query(
            user_id=request.user_id,
            query=request.message,
            conversation_history=request.conversation_history or []
        )
        return response
    except Exception as e:
        print(f"❌ Chat error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/analyze/budget", response_model=BudgetAnalysisResponse)
async def analyze_budget(request: BudgetAnalysisRequest):
    """Proactive budget analysis and nudges"""
    try:
        analysis = await agent.analyze_budget(request.user_id)
        return analysis
    except Exception as e:
        print(f"❌ Budget analysis error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/scenario/plan")
async def plan_scenario(user_id: str, scenario: str):
    """What-if scenario planning"""
    try:
        plan = await agent.plan_scenario(user_id, scenario)
        return plan
    except Exception as e:
        print(f"❌ Scenario planning error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/generate/nudges")
async def generate_nudges(user_id: str):
    """Generate proactive financial nudges"""
    try:
        nudges = await agent.generate_nudges(user_id)
        return {"nudges": nudges}
    except Exception as e:
        print(f"❌ Nudge generation error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

# Integration Guide: Self-Correcting Causal Financial Agent

This guide shows how to integrate the causal agent module into your existing FinGuru application.

## 📋 Prerequisites

All required dependencies are already in `requirements.txt`:
- ✅ `langgraph>=0.0.20`
- ✅ `statsmodels>=0.14.0`
- ✅ `scipy` (included with statsmodels)
- ✅ `numpy>=1.26.3`

No additional installations needed!

## 🚀 Quick Integration (5 Minutes)

### Option 1: Add to Existing FastAPI App

Edit `main.py` or `app/api/endpoints.py`:

```python
# main.py
from fastapi import FastAPI
from app.agents.causal.api import register_causal_endpoints

app = FastAPI(title="FinGuru")

# ... your existing configuration ...

# Add causal analysis endpoints
register_causal_endpoints(app)

# Now endpoints are available at:
# - POST /causal/analyze
# - POST /causal/validate-hypothesis
# - GET /causal/examples
```

**Test the endpoint:**

```bash
curl -X POST "http://localhost:8000/causal/analyze" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "How do oil prices affect airline stocks?",
    "user_id": "test_user",
    "verbose": false
  }'
```

### Option 2: Use as Python Module

```python
# In any Python script or notebook
from app.agents.causal import run_causal_analysis
import asyncio

async def main():
    result = await run_causal_analysis(
        query="Do rising interest rates affect tech stocks?",
        user_id="user_123"
    )
    
    print(f"Status: {result['status']}")
    print(f"Output: {result['final_output']}")

# Run it
asyncio.run(main())
```

### Option 3: Add to Existing LangGraph Workflow

Edit `app/agents/workflow.py`:

```python
from app.agents.causal import run_causal_analysis_sync

def causal_verification_node(state: WorkflowState) -> WorkflowState:
    """
    Add causal verification to the main FinGuru workflow.
    Triggers when user asks about market relationships.
    """
    
    # Detect causal queries
    causal_keywords = ["affect", "influence", "correlation", "cause", "impact"]
    
    if any(keyword in state["query"].lower() for keyword in causal_keywords):
        logger.info("[workflow] Triggering causal verification...")
        
        # Run causal analysis
        causal_result = run_causal_analysis_sync(
            query=state["query"],
            user_id=state["user_id"]
        )
        
        # Add to state
        state["causal_verification"] = causal_result
        
        # Add reasoning step
        state["reasoning_steps"].append({
            "agent": "Causal Verification",
            "action": f"Verified hypothesis: {causal_result['status']}",
            "result": causal_result["final_output"],
            "timestamp": datetime.now()
        })
    
    return state

# Add to your workflow graph
workflow.add_node("causal_verification", causal_verification_node)

# Add edge (example: after advisor_agent)
workflow.add_edge("advisor_agent", "causal_verification")
workflow.add_edge("causal_verification", END)
```

## 📊 Testing Your Integration

### 1. Run Unit Tests

```bash
# Test causal agent module
pytest tests/test_causal_agent.py -v

# Test with coverage
pytest tests/test_causal_agent.py --cov=app.agents.causal --cov-report=html
```

### 2. Run Demo Examples

```bash
# Demo showing 6 different usage patterns (NOT production code)
python scripts/demo_usage_examples.py
```

Expected output:
```
======================================================================
EXAMPLE 1: Basic Causal Analysis
======================================================================
[market_analyst] Starting for query: How do rising oil prices affect airline stocks?
...
Status: verified
Iterations: 2
Execution Time: 8.45s

Final Output:
✓ VERIFIED: Rising oil prices lead to declining airline stock prices...
```

### 3. Test API Endpoints

Start your server:
```bash
python main.py
# or
uvicorn main:app --reload
```

Test endpoints:
```bash
# Check status
curl http://localhost:8000/causal/status

# Run analysis
curl -X POST http://localhost:8000/causal/analyze \
  -H "Content-Type: application/json" \
  -d '{
    "query": "How do oil prices affect airline stocks?",
    "user_id": "test_user"
  }'

# Get examples
curl http://localhost:8000/causal/examples
```

## 🔧 Configuration

### Adjust Iteration Limits

Edit `app/agents/causal/nodes.py`:

```python
# In hypothesis_refiner function
if state["iteration_count"] > 3:  # Change from 3 to your preferred limit
    logger.warning("[hypothesis_refiner] Max iterations reached")
```

### Adjust Statistical Thresholds

Edit `app/agents/causal/nodes.py`:

```python
# In causal_validator function
pearson_significant = pearson_p < 0.05  # Change p-value threshold
granger_significant = min_p_value < 0.05  # Change p-value threshold

# For correlation strength
if hypothesis.relationship == "direct":
    direction_matches = correlation > 0.3  # Change from 0.3
elif hypothesis.relationship == "inverse":
    direction_matches = correlation < -0.3  # Change from -0.3
```

### Enable Verbose Logging

```python
result = await run_causal_analysis(
    query="Your query",
    user_id="user_id",
    verbose=True  # Enable detailed logging
)
```

## 🔌 Integration Patterns

### Pattern 1: Optional Enhancement

Add causal verification as an optional feature:

```python
@app.post("/ask")
async def ask_question(
    query: str,
    user_id: str,
    enable_causal_verification: bool = False  # Optional flag
):
    # Main workflow
    result = await main_workflow(query, user_id)
    
    # Optional causal verification
    if enable_causal_verification:
        causal_result = await run_causal_analysis(query, user_id)
        result["causal_verification"] = causal_result
    
    return result
```

### Pattern 2: Automatic Detection

Automatically trigger for specific query types:

```python
def should_run_causal_analysis(query: str) -> bool:
    """Detect if query needs causal verification."""
    causal_patterns = [
        r"how (do|does|will) .* affect",
        r".*correlation between",
        r"impact of .* on",
        r"relationship between",
        r".*cause.*",
    ]
    
    import re
    return any(re.search(pattern, query.lower()) for pattern in causal_patterns)

@app.post("/ask")
async def ask_question(query: str, user_id: str):
    result = await main_workflow(query, user_id)
    
    # Auto-trigger causal analysis
    if should_run_causal_analysis(query):
        result["causal_analysis"] = await run_causal_analysis(query, user_id)
    
    return result
```

### Pattern 3: Validation Layer

Use as a validation layer before executing trades:

```python
async def validate_trading_hypothesis(
    hypothesis: str,
    user_id: str
) -> bool:
    """Validate hypothesis before allowing trade execution."""
    
    result = await run_causal_analysis(
        query=hypothesis,
        user_id=user_id
    )
    
    # Only allow trades on verified hypotheses
    if result["status"] == "verified":
        return True
    else:
        logger.warning(f"Hypothesis rejected: {result['final_output']}")
        return False

@app.post("/execute-trade")
async def execute_trade(
    trade_request: TradeRequest,
    user_id: str
):
    # Validate hypothesis first
    is_valid = await validate_trading_hypothesis(
        trade_request.hypothesis,
        user_id
    )
    
    if not is_valid:
        raise HTTPException(
            status_code=400,
            detail="Trading hypothesis not verified by causal analysis"
        )
    
    # Execute trade
    return execute_trade_internal(trade_request)
```

## 🐛 Troubleshooting

### Issue: "No module named 'app.agents.causal'"

**Solution:**
```bash
# Ensure you're in the project root
cd /path/to/Finguru

# Verify the module exists
ls app/agents/causal/

# Try importing
python -c "from app.agents.causal import run_causal_analysis; print('✓ Import successful')"
```

### Issue: Statistical tests failing

**Solution:**
Check that statsmodels is installed:
```bash
pip install statsmodels>=0.14.0
python -c "import statsmodels; print(statsmodels.__version__)"
```

### Issue: LLM responses not parsing

**Solution:**
The code handles JSON parsing errors with fallbacks. Check logs:
```bash
tail -f logs/finguru.log | grep "causal"
```

If persistent, adjust LLM temperature in `.env`:
```bash
LLM_TEMPERATURE=0.0  # Lower = more structured output
```

### Issue: ChromaDB vector store errors

**Solution:**
Ensure ChromaDB is initialized:
```python
from app.db.vector_store import get_vector_store
vector_store = get_vector_store()
print("✓ Vector store connected")
```

### Issue: "Max iterations reached" frequently

**Solution:**
This means hypotheses are getting rejected repeatedly. Either:
1. Increase iteration limit (edit `nodes.py`)
2. Lower statistical thresholds (make tests less strict)
3. Improve prompts for better initial hypotheses

## 📈 Monitoring & Observability

### Track Usage

```python
from app.agents.causal import run_causal_analysis

async def tracked_analysis(query: str, user_id: str):
    """Wrapper with monitoring."""
    import time
    
    start = time.time()
    result = await run_causal_analysis(query, user_id)
    duration = time.time() - start
    
    # Log metrics
    logger.info(
        f"Causal analysis completed: "
        f"user={user_id}, status={result['status']}, "
        f"iterations={result['iteration_count']}, "
        f"duration={duration:.2f}s"
    )
    
    # Send to monitoring system (Prometheus, DataDog, etc.)
    # metrics.histogram("causal_analysis_duration", duration)
    # metrics.counter("causal_analysis_status", tags={"status": result['status']})
    
    return result
```

### Performance Metrics

Key metrics to track:
- **Execution time**: `result['execution_time_seconds']`
- **Iteration count**: `result['iteration_count']`
- **Verification rate**: `status == 'verified'` percentage
- **Error rate**: `status == 'error'` percentage

## 🚀 Production Deployment

### Environment Variables

Ensure these are set in production `.env`:

```bash
# Required (from existing FinGuru)
GROQ_API_KEY=your_production_key
LLM_MODEL=llama3-70b-8192
LLM_TEMPERATURE=0.1

# Optional (causal agent specific)
CAUSAL_MAX_ITERATIONS=3
CAUSAL_SIGNIFICANCE_LEVEL=0.05
CAUSAL_CORRELATION_THRESHOLD=0.3
```

### Rate Limiting

Add rate limiting for the API:

```python
from fastapi_limiter import FastAPILimiter
from fastapi_limiter.depends import RateLimiter

@causal_router.post("/analyze")
@limiter.limit("10/minute")  # 10 requests per minute per user
async def analyze_causality(request: CausalAnalysisRequest):
    # ... existing code ...
```

### Caching (Optional)

Cache results for identical queries:

```python
from functools import lru_cache
import hashlib

@lru_cache(maxsize=100)
def cached_analysis(query_hash: str, user_id: str):
    """Cache analysis results."""
    return run_causal_analysis_sync(query_hash, user_id)

# Use query hash as cache key
query_hash = hashlib.md5(query.encode()).hexdigest()
```

## ✅ Integration Checklist

- [ ] Module imports successfully
- [ ] Unit tests pass
- [ ] Example script runs without errors
- [ ] API endpoints respond correctly
- [ ] Logging works (check `logs/finguru.log`)
- [ ] Vector store integration works
- [ ] LLM responses parse correctly
- [ ] Statistical tests complete successfully
- [ ] Error handling works (try invalid inputs)
- [ ] Performance is acceptable (<30s per query)
- [ ] Documentation reviewed
- [ ] Team is trained on usage

## 🎓 Next Steps

1. **Start Simple**: Test with example queries first
2. **Gradual Rollout**: Enable for a subset of users
3. **Monitor Metrics**: Track performance and accuracy
4. **Iterate**: Adjust thresholds based on feedback
5. **Expand**: Add real market data integration

## 📞 Support

If you encounter issues:

1. **Check logs**: `tail -f logs/finguru.log | grep causal`
2. **Run tests**: `pytest tests/test_causal_agent.py -v`
3. **Review demos**: `python scripts/demo_usage_examples.py`
4. **Check README**: `app/agents/causal/README.md`

---

**Happy integrating! 🎉**

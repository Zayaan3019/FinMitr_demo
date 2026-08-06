# Self-Correcting Causal Financial Agent

A production-grade module for verifying financial hypotheses using statistical causality tests and LLM-powered self-correction.

## 🎯 Overview

The Self-Correcting Causal Financial Agent prevents **financial hallucinations** by validating LLM-generated investment hypotheses against rigorous statistical tests. It implements a verification loop that:

1. **Generates** testable hypotheses from user queries
2. **Validates** them using Granger causality and Pearson correlation
3. **Critiques** the evidence quality
4. **Refines** rejected hypotheses (up to 3 iterations)

## 🏗️ Architecture

### State Graph Flow

```
START
  ↓
┌─────────────────────┐
│  market_analyst     │  Generates hypothesis + retrieves context
└──────────┬──────────┘
           ↓
┌─────────────────────┐
│  causal_validator   │  Runs statistical tests (Pearson, Granger)
└──────────┬──────────┘
           ↓
┌─────────────────────┐
│  risk_critic        │  Evaluates evidence vs. hypothesis
└──────────┬──────────┘
           ↓
    [Conditional Edge]
           ├─ VERIFIED → END ✓
           └─ REJECTED → hypothesis_refiner
                            ↓
                      [Loop back to validator]
                            ↓
                      (Max 3 iterations)
```

### Key Components

#### 1. **State Models** (`state.py`)
- **`AgentState`**: TypedDict for LangGraph state management
- **`HypothesisModel`**: Pydantic-validated hypothesis structure
- **`CausalEvidenceModel`**: Statistical test results
- **`CritiqueModel`**: Risk assessment and verdict

#### 2. **Nodes** (`nodes.py`)
- **`market_analyst`**: 
  - Retrieves financial context from ChromaDB vector store
  - Generates structured hypothesis using LLM
  - Extracts key market variables and expected relationships
  
- **`causal_validator`**:
  - Performs **Pearson correlation** test (direction and strength)
  - Performs **Granger causality** test (temporal causality)
  - Currently uses mock time series (production: integrate real market data)
  
- **`risk_critic`**:
  - Compares hypothesis against statistical evidence
  - Assesses evidence quality (strong/moderate/weak)
  - Issues verdict: `verified`, `rejected`, or `inconclusive`
  
- **`hypothesis_refiner`**:
  - Analyzes critique feedback
  - Generates improved hypothesis addressing issues
  - Increments iteration counter

#### 3. **Graph Orchestration** (`graph.py`)
- **`create_causal_agent_graph()`**: Builds the LangGraph StateGraph
- **`run_causal_analysis()`**: Async entry point for analysis
- **`run_causal_analysis_sync()`**: Synchronous wrapper

## 🚀 Quick Start

### Basic Usage

```python
from app.agents.causal import run_causal_analysis

# Run causal analysis
result = await run_causal_analysis(
    query="How do oil prices affect airline stocks?",
    user_id="user_123",
    verbose=True
)

# Check result
print(f"Status: {result['status']}")  # "verified", "rejected", or "max_iterations"
print(f"Output: {result['final_output']}")
```

### Advanced Usage

```python
from app.agents.causal import (
    run_causal_analysis,
    deserialize_hypothesis,
    deserialize_evidence,
    deserialize_critique
)

result = await run_causal_analysis(
    query="Will semiconductor shortages affect automobile stocks?",
    user_id="user_456"
)

# Extract structured data
if result['hypothesis']:
    hypothesis = deserialize_hypothesis(result['hypothesis'])
    print(f"Claim: {hypothesis.claim}")
    print(f"Variables: {hypothesis.variables}")
    print(f"Relationship: {hypothesis.relationship}")
    print(f"Confidence: {hypothesis.confidence:.2%}")

if result['evidence']:
    evidence = deserialize_evidence(result['evidence'])
    print(f"Correlation: {evidence.correlation:.3f}")
    print(f"P-value: {evidence.p_value:.4f}")
    print(f"Supports: {evidence.supports_hypothesis}")

if result['critique']:
    critique = deserialize_critique(result['critique'])
    print(f"Verdict: {critique.verdict}")
    print(f"Quality: {critique.evidence_quality}")
    print(f"Final Confidence: {critique.confidence_score:.2%}")
```

### Synchronous Usage (Non-Async Contexts)

```python
from app.agents.causal import run_causal_analysis_sync

# Use in FastAPI sync endpoints or scripts
result = run_causal_analysis_sync(
    query="Do gold prices hedge against inflation?",
    user_id="user_789"
)
```

## 🔧 Integration with FinGuru

### As a FastAPI Endpoint

Add to `app/api/endpoints.py`:

```python
from fastapi import APIRouter, HTTPException
from app.agents.causal import run_causal_analysis

router = APIRouter()

@router.post("/causal-analysis")
async def analyze_causality(
    query: str,
    user_id: str
):
    """
    Analyze causal relationships in financial markets.
    
    Returns verified or refined hypothesis with statistical evidence.
    """
    try:
        result = await run_causal_analysis(
            query=query,
            user_id=user_id,
            verbose=False
        )
        
        return {
            "status": result["status"],
            "output": result["final_output"],
            "hypothesis": result["hypothesis"],
            "evidence": result["evidence"],
            "iterations": result["iteration_count"],
            "execution_time": result["execution_time_seconds"]
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
```

### Integration with Existing Workflow

```python
from app.agents.causal import run_causal_analysis
from app.agents.workflow import workflow_graph

# Add as a node in existing workflow
def causal_analysis_node(state: WorkflowState) -> WorkflowState:
    """Add causal verification to existing workflow."""
    
    # Extract market question from query
    if "correlation" in state["query"].lower() or "affect" in state["query"].lower():
        result = run_causal_analysis_sync(
            query=state["query"],
            user_id=state["user_id"]
        )
        
        state["causal_analysis"] = result
        state["reasoning_steps"].append({
            "agent": "Causal Verification",
            "result": result["final_output"]
        })
    
    return state
```

## 📊 Statistical Methods

### Pearson Correlation
- **Purpose**: Measure linear relationship strength and direction
- **Range**: -1 (perfect inverse) to +1 (perfect direct)
- **Significance**: p-value < 0.05 indicates statistical significance

### Granger Causality
- **Purpose**: Test if past values of X predict future values of Y
- **Method**: F-test on lagged variables (max 5 lags)
- **Interpretation**: p-value < 0.05 suggests X "Granger-causes" Y

### Mock Data (Development)
Currently uses `_generate_mock_time_series()` for testing. **TODO**: Replace with real market data:

```python
# Future integration example
def get_market_data(symbol: str, start_date: str, end_date: str) -> np.ndarray:
    """Fetch real market data from API."""
    # Use yfinance, Alpha Vantage, or similar
    import yfinance as yf
    data = yf.download(symbol, start=start_date, end=end_date)
    return data['Close'].values
```

## 🧪 Testing

### Run Tests

```bash
# All causal agent tests
pytest tests/test_causal_agent.py -v

# Specific test class
pytest tests/test_causal_agent.py::TestStateModels -v

# With coverage
pytest tests/test_causal_agent.py --cov=app.agents.causal --cov-report=html
```

### Demo Usage Patterns (Learning Material)

```bash
# Run 6 demo usage patterns - NOT production code
python scripts/demo_usage_examples.py
```

## 🔒 Security & Data Isolation

- **User Isolation**: All vector store retrievals filtered by `user_id`
- **Input Validation**: Pydantic models validate all structured data
- **Error Handling**: Comprehensive try/except blocks in all nodes
- **Iteration Limits**: Max 3 refinement iterations prevents infinite loops

## 📈 Performance Considerations

### Current Performance
- **Average Execution**: 5-10 seconds per query (depends on LLM latency)
- **Max Execution**: ~30 seconds (with 3 refinement iterations)
- **Parallel Execution**: Supports concurrent analyses for multiple users

### Optimization Strategies
1. **Cache LLM Responses**: Store hypothesis for similar queries
2. **Batch Statistical Tests**: Vectorize correlation calculations
3. **Lazy Loading**: Load market data only when needed
4. **Timeout Controls**: Set max execution time per node

## 🛠️ Configuration

### Environment Variables
```bash
# LLM Configuration (inherited from FinGuru)
GROQ_API_KEY=your_key_here
LLM_MODEL=llama3-70b-8192
LLM_TEMPERATURE=0.1
LLM_MAX_TOKENS=2048

# Vector Store (inherited from FinGuru)
CHROMA_PERSIST_DIR=./data/chromadb
VECTOR_COLLECTION_NAME=finguru_transactions
```

### Iteration Limits
Modify in `nodes.py` → `hypothesis_refiner`:
```python
if state["iteration_count"] > 3:  # Change max iterations here
    logger.warning("[hypothesis_refiner] Max iterations reached")
    state["status"] = "max_iterations"
```

### Statistical Significance Threshold
Modify in `nodes.py` → `causal_validator`:
```python
pearson_significant = pearson_p < 0.05  # Change p-value threshold
granger_significant = min_p_value < 0.05  # Change p-value threshold
```

## 📝 Logging

The module uses FinGuru's centralized logging:

```python
from app.core.logging import get_logger

logger = get_logger(__name__)

# Logs include:
# - Node entry/exit
# - LLM invocations
# - Statistical test results
# - Iteration counts
# - Error traces
```

View logs:
```bash
tail -f logs/finguru.log | grep "causal"
```

## 🚧 Known Limitations

1. **Mock Time Series**: Currently uses simulated data. **TODO**: Integrate real market data API.
2. **Binary Variables**: Only supports 2-variable relationships. Future: Multi-variable causality.
3. **Linear Relationships**: Pearson correlation assumes linearity. Future: Add non-linear tests.
4. **Stationarity**: Granger test assumes stationary time series. Future: Add ADF test + differencing.

## 🔮 Future Enhancements

### Planned Features
- [ ] Real-time market data integration (yfinance, Alpha Vantage)
- [ ] Advanced causality methods (Vector Autoregression, Transfer Entropy)
- [ ] Multi-variable causal networks (DAG construction)
- [ ] Backtesting framework for hypothesis verification
- [ ] Confidence intervals for correlation estimates
- [ ] Time-varying causality detection (rolling window analysis)
- [ ] Web UI for interactive hypothesis exploration

### Integration Opportunities
- [ ] Add to main FinGuru workflow as optional verification step
- [ ] Combine with anomaly detection for event-driven causality
- [ ] Use forecasting models to predict causal relationship strength
- [ ] Export verified hypotheses to investment recommendation system

## 📚 References

### Academic Background
- **Granger Causality**: Granger, C. W. J. (1969). "Investigating Causal Relations by Econometric Models and Cross-spectral Methods"
- **Transfer Entropy**: Schreiber, T. (2000). "Measuring Information Transfer"
- **Causal Inference**: Pearl, J. (2009). "Causality: Models, Reasoning and Inference"

### Technical Documentation
- [LangGraph Docs](https://langchain-ai.github.io/langgraph/)
- [Statsmodels Granger Test](https://www.statsmodels.org/stable/generated/statsmodels.tsa.stattools.grangercausalitytests.html)
- [Pydantic Validation](https://docs.pydantic.dev/)

## 🤝 Contributing

### Code Style
- Follow PEP 8
- Type hints for all functions
- Docstrings for public APIs
- Async-first design

### Testing
- Write tests for new features
- Maintain >80% code coverage
- Include integration tests

## 📄 License

Same as parent FinGuru project.

## 💬 Support

For questions or issues:
1. Check demo examples: `scripts/demo_usage_examples.py`
2. Review tests: `tests/test_causal_agent.py`
3. Check logs: `logs/finguru.log`

---

**Built with ❤️ by the FinGuru AI Engineering Team**

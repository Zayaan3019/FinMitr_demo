# FinGuru - Quick Reference Guide

## 🚀 Quick Commands

### Setup & Start
```bash
# Install dependencies
pip install -r requirements.txt

# Validate system
python scripts/validate_system.py

# Start server
python main.py

# Or use quick-start
start.bat  # Windows
```

### Data Operations
```bash
# Generate sample data
python scripts/generate_data.py --users 2 --transactions 300

# Run complete demo
python scripts/example_usage.py
```

## 📡 API Endpoints

### Base URL
```
http://localhost:8000
```

### Health Check
```http
GET /api/v1/health
```

### Ingest Data
```http
POST /api/v1/ingest?user_id=USER_ID
Content-Type: multipart/form-data
Body: file (CSV)
```

### Ask Questions
```http
POST /api/v1/chat
Content-Type: application/json

{
  "user_id": "user_001",
  "query": "What are my spending patterns?"
}
```

### Get Statistics
```http
GET /api/v1/stats
```

### Delete User Data
```http
DELETE /api/v1/user/{user_id}
```

## 💡 Example Queries

### Spending Analysis
```
"What are my top spending categories this month?"
"How much did I spend on groceries?"
"Show me my largest expenses"
"What's my average daily spending?"
```

### Anomaly Detection
```
"Are there any unusual transactions?"
"Did I have any suspicious charges?"
"Show me spending outliers"
"What transactions look abnormal?"
```

### Financial Advice
```
"How can I reduce my expenses?"
"Where should I cut spending?"
"Am I spending too much on dining?"
"Give me budgeting recommendations"
```

### Trends & Patterns
```
"What are my spending trends?"
"How does this month compare to last?"
"Which days do I spend the most?"
"What's my spending pattern?"
```

## 🔧 Configuration Quick Reference

### Environment Variables (.env)
```env
# Required
GROQ_API_KEY=your_key_here

# Optional
LLM_MODEL=llama3-70b-8192        # Change model
LLM_TEMPERATURE=0.1               # 0-2 (lower = more deterministic)
API_PORT=8000                     # Change port
DEBUG=True                        # Enable debug mode
LOG_LEVEL=INFO                    # DEBUG, INFO, WARNING, ERROR
```

### CSV Format
```csv
date,amount,description,category
2026-01-15,-150.50,Whole Foods Market,Groceries
2026-01-14,-45.00,Shell Gas Station,Transportation
2026-01-13,5000.00,Payroll Deposit,Salary
```

**Required columns:** `date`, `amount`, `description`  
**Optional column:** `category` (auto-categorized if missing)

## 🐳 Docker Commands

```bash
# Build and start
docker-compose up -d

# View logs
docker-compose logs -f

# Stop
docker-compose down

# Rebuild
docker-compose build --no-cache
```

## 🧪 Testing Multi-Tenancy

```bash
# 1. Generate data for multiple users
python scripts/generate_data.py --users 3 --transactions 200

# 2. Ingest for user_001
curl -X POST "http://localhost:8000/api/v1/ingest?user_id=user_001" \
  -F "file=@data/transactions.csv"

# 3. Ingest for user_002  
curl -X POST "http://localhost:8000/api/v1/ingest?user_id=user_002" \
  -F "file=@data/transactions.csv"

# 4. Query as user_001 (sees only their data)
curl -X POST "http://localhost:8000/api/v1/chat" \
  -H "Content-Type: application/json" \
  -d '{"user_id": "user_001", "query": "Show my expenses"}'

# 5. Query as user_002 (sees only their data)
curl -X POST "http://localhost:8000/api/v1/chat" \
  -H "Content-Type: application/json" \
  -d '{"user_id": "user_002", "query": "Show my expenses"}'
```

## 🔍 Troubleshooting

### Issue: GROQ API Key Error
```bash
# Fix: Add your key to .env
GROQ_API_KEY=gsk_your_actual_key_here
```

### Issue: Port Already in Use
```bash
# Fix: Change port in .env
API_PORT=8001
```

### Issue: ModuleNotFoundError
```bash
# Fix: Install dependencies
pip install -r requirements.txt
```

### Issue: ChromaDB Error
```bash
# Fix: Clear database
rmdir /s /q data\chromadb  # Windows
rm -rf data/chromadb       # Linux/Mac
```

### Issue: Slow Responses
```bash
# Fix: Use faster model in .env
LLM_MODEL=mixtral-8x7b-32768
```

## 📊 Performance Tips

1. **Batch ingestion** - Upload larger CSVs at once
2. **Use specific queries** - More specific = faster response
3. **Limit history** - Focus on recent transactions
4. **Cache results** - Store common query responses
5. **Use SSD** - Faster ChromaDB performance

## 🔐 Security Checklist

- [x] Each user's data isolated via metadata
- [x] User ID validated in all requests
- [x] No cross-user data access possible
- [x] GDPR-compliant deletion endpoint
- [x] Input validation on all endpoints
- [x] Error messages don't leak sensitive info

## 📁 Important Files

| File | Purpose |
|------|---------|
| `main.py` | Application entry point |
| `.env` | Environment configuration |
| `app/agents/workflow.py` | Agent orchestration |
| `app/db/vector_store.py` | ChromaDB manager |
| `app/api/endpoints.py` | API routes |
| `scripts/generate_data.py` | Data generator |
| `scripts/example_usage.py` | Usage demo |
| `scripts/validate_system.py` | System check |

## 🆘 Getting Help

1. Check [README.md](README.md) for detailed guide
2. Check [SETUP.md](SETUP.md) for installation help
3. Check [ARCHITECTURE.md](ARCHITECTURE.md) for technical details
4. Run `python scripts/validate_system.py` to check setup
5. Visit http://localhost:8000/docs for API documentation

## 📞 Quick Links

- **API Docs**: http://localhost:8000/docs
- **Health Check**: http://localhost:8000/api/v1/health
- **GROQ Console**: https://console.groq.com
- **Main README**: [README.md](README.md)

---

**Keep this file handy for quick reference!** 📌

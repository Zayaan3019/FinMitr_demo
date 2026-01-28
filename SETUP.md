# FinGuru - Setup Guide

## Prerequisites Checklist

Before starting, ensure you have:

- [ ] Python 3.10 or higher installed
- [ ] pip package manager
- [ ] 2GB free disk space
- [ ] Internet connection (for downloading dependencies)
- [ ] GROQ API key (free from https://console.groq.com)

## Step-by-Step Setup

### 1. Get GROQ API Key (Free)

1. Visit https://console.groq.com
2. Sign up for a free account
3. Navigate to API Keys section
4. Create a new API key
5. Copy the key (you'll need it in step 4)

**Note:** GROQ provides free, fast LLM inference - no credit card required!

### 2. Install Python Dependencies

**Windows:**
```bash
# Create virtual environment
python -m venv venv

# Activate virtual environment
venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

**Linux/Mac:**
```bash
# Create virtual environment
python3 -m venv venv

# Activate virtual environment
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Configure Environment

```bash
# Copy example environment file
# Windows:
copy .env.example .env

# Linux/Mac:
cp .env.example .env
```

Edit `.env` file and set your GROQ API key:
```env
GROQ_API_KEY=gsk_your_actual_api_key_here
```

### 4. Generate Sample Data

```bash
python scripts/generate_data.py --users 2 --transactions 300 --output data/transactions.csv
```

This creates realistic transaction data for 2 users with 300 transactions each.

### 5. Start the API Server

```bash
python main.py
```

The server will start at: `http://localhost:8000`

### 6. Test the API

Open your browser and visit:
- **API Docs**: http://localhost:8000/docs
- **Health Check**: http://localhost:8000/api/v1/health

Or use the example script:
```bash
python scripts/example_usage.py
```

## Quick Start (Automated)

### Windows
```bash
# Run the automated setup script
start.bat
```

### Linux/Mac
```bash
# Make script executable
chmod +x start.sh

# Run the script
./start.sh
```

## Troubleshooting

### Issue: "GROQ_API_KEY must be set"
**Solution:** Make sure you've added your GROQ API key to the `.env` file

### Issue: "ModuleNotFoundError"
**Solution:** Ensure virtual environment is activated and run `pip install -r requirements.txt`

### Issue: "Port 8000 already in use"
**Solution:** Change the port in `.env`:
```env
API_PORT=8001
```

### Issue: ChromaDB errors
**Solution:** Delete the `data/chromadb` folder and restart:
```bash
# Windows
rmdir /s /q data\chromadb

# Linux/Mac
rm -rf data/chromadb
```

### Issue: Slow response times
**Solution:** Try a faster GROQ model in `.env`:
```env
LLM_MODEL=mixtral-8x7b-32768
```

## Testing Multi-Tenancy

1. Generate data with multiple users:
```bash
python scripts/generate_data.py --users 3 --transactions 200
```

2. Ingest data for each user separately:
```bash
# Via Swagger UI at http://localhost:8000/docs
# Upload the CSV and set different user_id for each
```

3. Query with different user_ids to verify isolation:
```python
import requests

# User 1 query
response = requests.post("http://localhost:8000/api/v1/chat", json={
    "user_id": "user_001",
    "query": "Show my expenses"
})

# User 2 query
response = requests.post("http://localhost:8000/api/v1/chat", json={
    "user_id": "user_002",
    "query": "Show my expenses"
})
```

## Docker Deployment

### Build and Run
```bash
# Build the image
docker-compose build

# Start the service
docker-compose up -d

# View logs
docker-compose logs -f

# Stop the service
docker-compose down
```

### Access the API
- API: http://localhost:8000
- Docs: http://localhost:8000/docs

## Production Deployment Tips

1. **Set DEBUG=False** in `.env`
2. **Use environment-specific secrets** for API keys
3. **Configure CORS** appropriately in `main.py`
4. **Set up reverse proxy** (nginx) for HTTPS
5. **Monitor logs** in `logs/` directory
6. **Back up ChromaDB data** in `data/chromadb/`
7. **Use orchestration** (Kubernetes) for scaling

## Performance Optimization

1. **Adjust batch sizes** for ingestion
2. **Use faster embedding models** if needed
3. **Increase ChromaDB cache** for large datasets
4. **Scale horizontally** with load balancer
5. **Use GPU** for faster embeddings (optional)

## Data Privacy

- All data is stored locally in ChromaDB
- No data is sent to third parties (except LLM API calls)
- User data is isolated using metadata filters
- GDPR-compliant deletion via `/user/{user_id}` endpoint

## Next Steps

1. ✅ Complete the setup above
2. 📊 Upload your own transaction CSV
3. 💬 Ask financial questions via API
4. 🔧 Customize agents for your use case
5. 🚀 Deploy to production

## Support

- **Issues**: Create GitHub issue
- **Questions**: Check documentation
- **Community**: Join Discord (if available)

---

**Happy analyzing! 🎉**

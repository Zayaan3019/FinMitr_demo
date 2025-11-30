#!/bin/bash
set -e
echo "🚀 Starting FinGuru..."
echo "======================================"

# Step 1: Stop any existing containers
echo "Cleaning up old containers..."
docker-compose down -v 2>/dev/null || true

# Step 2: Build images
echo "Building Docker images (this takes 10-15 minutes)..."
docker-compose build

# Step 3: Start infrastructure
echo "Starting databases..."
docker-compose up -d postgres rabbitmq weaviate ollama

# Step 4: Wait for databases
echo "Waiting 30 seconds for databases to initialize..."
sleep 30

# Step 5: Check database health
echo "Checking database health..."
docker-compose exec -T postgres pg_isready -U finguru_user || {
    echo "❌ Database not ready"
    exit 1
}

# Step 6: Setup database schema
echo "Setting up database schema..."
docker-compose run --rm backend-api npx prisma generate
docker-compose run --rm backend-api npx prisma migrate deploy

# Step 7: Pull AI model
echo "Pulling Llama 3.1 model (10-20 minutes)..."
docker exec finguru-ollama ollama pull llama3.1:8b || echo "⚠️ Ollama pull failed, continuing..."

# Step 8: Start all services
echo "Starting all services..."
docker-compose up -d

# Step 9: Wait for services
echo "Waiting 20 seconds for services to start..."
sleep 20

# Step 10: Verify
echo "======================================"
echo "✅ FinGuru is running!"
echo "======================================"
echo ""
echo "Access Points:"
echo "  🌐 Web Dashboard: http://localhost:3000"
echo "  📡 GraphQL API: http://localhost:4000/graphql"
echo "  🤖 AI Engine: http://localhost:8000/docs"
echo "  📊 RabbitMQ: http://localhost:15672"
echo ""
echo "Next: Open http://localhost:3000 in your browser"
echo ""
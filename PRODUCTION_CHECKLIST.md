# Production Deployment Checklist

## Pre-Deployment Validation

### 1. Testing ✅
- [ ] All unit tests passing (`pytest tests/test_*.py`)
- [ ] Integration tests passing (`pytest tests/test_integration.py`)
- [ ] API tests passing (`pytest tests/test_api.py`)
- [ ] Coverage > 80% (`pytest --cov=app`)
- [ ] No critical security issues (`bandit -r app/`)

### 2. Code Quality ✅
- [ ] Code formatted with Black (`black app/ scripts/`)
- [ ] Linting passed (`ruff check app/`)
- [ ] Type hints present
- [ ] Documentation complete
- [ ] No TODO/FIXME in production code

### 3. Configuration ✅
- [ ] `.env` configured with production values
- [ ] `DEBUG=False` in production
- [ ] `LOG_LEVEL=INFO` or `WARNING`
- [ ] GROQ API key valid and has quota
- [ ] CORS settings appropriate
- [ ] File upload limits configured

### 4. Security ✅
- [ ] API authentication implemented (if needed)
- [ ] Rate limiting configured
- [ ] Input validation comprehensive
- [ ] SQL injection prevention (N/A - no SQL)
- [ ] XSS prevention
- [ ] CSRF protection (if needed)
- [ ] Secrets not in code/repo
- [ ] HTTPS enabled

### 5. Performance ✅
- [ ] Database indexes optimized
- [ ] ChromaDB persistence configured
- [ ] Concurrent request handling tested
- [ ] Memory usage acceptable
- [ ] Response times < 10s for queries
- [ ] File upload size limits enforced

### 6. Monitoring ✅
- [ ] Logging configured correctly
- [ ] Error tracking setup (Sentry, etc.)
- [ ] Health check endpoint working
- [ ] Metrics collection enabled
- [ ] Alert rules configured

### 7. Data Management ✅
- [ ] Backup strategy defined
- [ ] Data retention policy
- [ ] GDPR compliance verified
- [ ] User data deletion works
- [ ] Data migration tested

### 8. Infrastructure ✅
- [ ] Docker image builds successfully
- [ ] Docker compose tested
- [ ] Resource limits defined
- [ ] Auto-restart configured
- [ ] Persistent volumes configured
- [ ] Network security groups configured

### 9. Documentation ✅
- [ ] API documentation complete
- [ ] README.md updated
- [ ] Deployment guide written
- [ ] Runbook created
- [ ] Troubleshooting guide available

### 10. Disaster Recovery ✅
- [ ] Backup procedure documented
- [ ] Restore procedure tested
- [ ] Rollback plan defined
- [ ] Incident response plan created

## Deployment Steps

### Development Environment
```bash
# 1. Run all tests
python tests/run_tests.py

# 2. Validate system
python scripts/validate_system.py

# 3. Start application
python main.py
```

### Staging Environment
```bash
# 1. Build Docker image
docker build -t finguru:staging .

# 2. Run tests in container
docker run --rm finguru:staging pytest tests/ -v

# 3. Deploy to staging
docker-compose -f docker-compose.staging.yml up -d

# 4. Run smoke tests
python scripts/example_usage.py
```

### Production Environment
```bash
# 1. Tag release
git tag -a v1.0.0 -m "Production release v1.0.0"

# 2. Build production image
docker build -t finguru:v1.0.0 .
docker tag finguru:v1.0.0 finguru:latest

# 3. Push to registry (if using)
# docker push registry.example.com/finguru:v1.0.0

# 4. Deploy
docker-compose up -d

# 5. Verify health
curl http://localhost:8000/api/v1/health

# 6. Monitor logs
docker-compose logs -f
```

## Post-Deployment

### Immediate Actions
- [ ] Verify health endpoint returns 200
- [ ] Test sample ingestion
- [ ] Test sample query
- [ ] Check logs for errors
- [ ] Verify database connectivity
- [ ] Test user deletion (GDPR)

### Monitoring (First 24h)
- [ ] Monitor error rates
- [ ] Monitor response times
- [ ] Monitor resource usage
- [ ] Check for memory leaks
- [ ] Monitor API quotas
- [ ] Review user feedback

### Rollback Procedure
If issues detected:
```bash
# 1. Stop current version
docker-compose down

# 2. Deploy previous version
docker-compose up -d finguru:v0.9.0

# 3. Verify functionality
curl http://localhost:8000/api/v1/health

# 4. Investigate issues
docker logs finguru_api

# 5. Fix and redeploy
```

## Production URLs

- **API Base**: https://api.finguru.com
- **Documentation**: https://api.finguru.com/docs
- **Health Check**: https://api.finguru.com/api/v1/health
- **Metrics**: https://metrics.finguru.com
- **Logs**: https://logs.finguru.com

## Support Contacts

- **DevOps**: devops@finguru.com
- **On-Call**: +1-XXX-XXX-XXXX
- **Incident Channel**: #finguru-incidents (Slack)

## Important Notes

⚠️ **NEVER**:
- Deploy on Friday afternoon
- Deploy without testing
- Skip the checklist
- Ignore test failures
- Deploy with DEBUG=True
- Commit secrets to repo

✅ **ALWAYS**:
- Test thoroughly
- Have rollback plan
- Monitor after deployment
- Document changes
- Communicate with team
- Keep backups

---

**Last Updated**: January 19, 2026  
**Version**: 1.0.0  
**Approved By**: Senior AI Architect

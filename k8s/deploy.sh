# Create Kubernetes Secret
kubectl create secret generic finguru-secrets \
  --from-literal=GROQ_API_KEY=gsk_your_actual_api_key_here \
  -n finguru

# Apply all Kubernetes manifests in order
kubectl apply -f namespace.yaml
kubectl apply -f configmap.yaml
kubectl apply -f rbac.yaml
kubectl apply -f pvc.yaml
kubectl apply -f deployment.yaml
kubectl apply -f service.yaml
kubectl apply -f ingress.yaml
kubectl apply -f hpa.yaml
kubectl apply -f pdb.yaml

# Check deployment status
kubectl get all -n finguru

# Watch pods come up
kubectl get pods -n finguru -w

# Check logs
kubectl logs -f deployment/finguru -n finguru

# Port forward for local testing
kubectl port-forward svc/finguru 8000:8000 -n finguru

# Test health endpoint
curl http://localhost:8000/health/detailed

# Check HPA status
kubectl get hpa -n finguru

# Check metrics
kubectl top pods -n finguru

# Scale manually if needed
kubectl scale deployment finguru --replicas=5 -n finguru

# Update deployment (after new image)
kubectl rollout restart deployment/finguru -n finguru

# Check rollout status
kubectl rollout status deployment/finguru -n finguru

# Rollback if needed
kubectl rollout undo deployment/finguru -n finguru

# Delete everything (cleanup)
kubectl delete -f . -n finguru
kubectl delete namespace finguru

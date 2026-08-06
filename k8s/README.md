# Kubernetes Deployment Manifests

This directory contains Kubernetes manifests for deploying FinGuru to a Kubernetes cluster.

## Files

- `namespace.yaml` - Namespace for FinGuru resources
- `configmap.yaml` - Configuration data
- `secret.yaml` - Sensitive data (API keys, etc.)
- `deployment.yaml` - Main application deployment
- `service.yaml` - Service to expose the application
- `ingress.yaml` - Ingress for external access
- `hpa.yaml` - Horizontal Pod Autoscaler

## Quick Start

1. **Create Namespace**
   ```bash
   kubectl apply -f namespace.yaml
   ```

2. **Create Secret with your GROQ API Key**
   ```bash
   kubectl create secret generic finguru-secrets \
     --from-literal=GROQ_API_KEY=your_actual_key_here \
     --namespace finguru
   ```

3. **Apply Configuration**
   ```bash
   kubectl apply -f configmap.yaml
   ```

4. **Deploy Application**
   ```bash
   kubectl apply -f deployment.yaml
   kubectl apply -f service.yaml
   ```

5. **Setup Ingress (optional)**
   ```bash
   kubectl apply -f ingress.yaml
   ```

6. **Enable Autoscaling (optional)**
   ```bash
   kubectl apply -f hpa.yaml
   ```

## Verify Deployment

```bash
# Check pods
kubectl get pods -n finguru

# Check service
kubectl get svc -n finguru

# View logs
kubectl logs -f deployment/finguru -n finguru

# Check health
kubectl exec -it deployment/finguru -n finguru -- curl localhost:8000/health/live
```

## Scale Manually

```bash
kubectl scale deployment finguru --replicas=5 -n finguru
```

## Update Deployment

```bash
# Update image
kubectl set image deployment/finguru finguru=finguru:new-version -n finguru

# Or apply updated manifest
kubectl apply -f deployment.yaml
```

## Cleanup

```bash
kubectl delete namespace finguru
```

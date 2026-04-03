# Kubernetes Deployment

SonusTemper can be deployed to K8s with the same app + proxy pattern used by Docker Compose.

## Layout

- `base/` contains the shared manifest set
- `overlays/dev/` deploys the dev namespace, dev host, and `dev` image tag
- `overlays/prod/` deploys the production namespace
- `k8s/` points to the prod overlay, so `kubectl apply -k k8s/` is the production default

## What this bundle includes

- `Deployment` with the app container and nginx proxy container
- `Service` for the proxy
- `Ingress` with a placeholder hostname
- `PersistentVolumeClaim` for `/data`
- `emptyDir` for `/db`
- ConfigMap generated from the bundled proxy scripts/templates

## Before you apply

1. Create the `sonustemper-auth` secret in the target namespace from [base/secret.example.yaml](base/secret.example.yaml).
2. Replace the placeholder host in the overlay you plan to use.
3. If your cluster needs a specific ingress class, add `ingressClassName` in the overlay.
4. If your cluster needs a specific storage class, edit `base/pvc-data.yaml` or patch it in an overlay.

## Apply

Dev:

```sh
kubectl apply -k k8s/overlays/dev
```

Prod:

```sh
kubectl apply -k k8s/overlays/prod
```

Or, for the production default:

```sh
kubectl apply -k k8s/
```

## Branch workflow

1. Develop on feature branches.
2. Merge feature branches into `dev`.
3. Deploy `k8s/overlays/dev` for testing.
4. Merge `dev` into `main` when ready.
5. Deploy `k8s/overlays/prod` or `k8s/` for production.

## Notes

- The proxy container waits for the upstream target to resolve before it starts. In K8s the manifest points that at `127.0.0.1`, so the app and proxy stay in the same pod without extra DNS dependencies.
- The public repo intentionally avoids live cluster IPs, hostnames, and secret names beyond the application-specific object names.
- If you want a TLS-less local deployment, remove the `tls:` block from the ingress patch before applying.

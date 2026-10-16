# Additions for docs/troubleshooting.md

Each entry is written as symptom, cause and fix, so it reads as a reference for anyone running the project.

## A request through Traefik returns 502

**Symptom.** `http://<service>.localhost:8081` returns `502 Bad Gateway`.

**Cause.** The service namespace has a `default-deny` NetworkPolicy, which blocks incoming traffic, including traffic from Traefik's pods in `kube-system`.

**Fix.** The shared chart creates an `allow-traefik` NetworkPolicy that lets Traefik reach the service's pods on the container port. Once it synced, requests reached the service. If the 502 comes back, list the namespace's policies with `kubectl get networkpolicy -n <service-namespace>`. `default-deny` and `allow-traefik` should both be there. If `allow-traefik` is missing, check that the service's Argo CD Application has synced.

## A service has no row on Prometheus's Targets page

**Symptom.** The service's `/metrics` endpoint works, but Prometheus's Targets page has no row for it at all. A row that says DOWN would be a different problem.

**Cause.** The ServiceMonitor did not match the Service. In the chart's first version, the endpoint used `targetPort: http`, and Prometheus created no target.

**Fix.** The endpoint uses `port: http`, which refers to the Service's port name. The chart's Service names its port `http`, so the two match, and the target appeared.

**Telling the two cases apart.**
- A missing row means the ServiceMonitor did not select the Service. Check the endpoint's `port` field, the label selector, and the `release: kube-prometheus-stack` label that Prometheus selects on.
- A DOWN row means Prometheus found the target but could not scrape it. Check the `allow-prometheus` NetworkPolicy in the service namespace.

## A dashboard query returns nothing in Explore

**Symptom.** A query copied from a dashboard panel, such as `rate(http_requests_total{namespace="$namespace"}[5m])`, returns no data in Grafana's Explore view.

**Cause.** `$namespace` is a variable defined on the dashboard. Explore has no dashboard variables, so nothing fills it in.

**Fix.** Replace `$namespace` with the namespace name, for example `namespace="dev-env-3"`.

---

# Line for docs/setup.md

The `decommission` workflow needs a second token to delete the service's image package: a classic personal access token with only the `delete:packages` scope (GitHub adds `read:packages` with it), stored as its own Actions secret. A fine-grained token could not delete packages. The secret's name is in the `decommission` workflow.

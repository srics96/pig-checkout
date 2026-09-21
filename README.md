# Pig Checkout

A small, intentionally fault-injectable checkout API for demonstrating collaborative incident investigation. Built with FastAPI and a local SQLite catalog. This is a sandbox workload: it does not process real payments or customer data.

## Run locally

```sh
docker build -t pig-checkout .
docker run --rm -p 127.0.0.1:8080:8080 -e DD_TRACE_ENABLED=false pig-checkout
```

The service generates one synthetic checkout every five seconds.

```sh
curl http://127.0.0.1:8080/health
curl -X POST http://127.0.0.1:8080/checkout \
  -H 'Content-Type: application/json' \
  -d '{"sku":"demo-book","quantity":1}'
```

The sample catalog contains a single item priced at 2,500 cents; quantity is currently ignored. `/health` reports process liveness, not checkout correctness. The ephemeral catalog is recreated on startup.

## Controlled failure

Run the container with `FAULT_MODE=bad_catalog` to point checkout at an empty SQLite database. Checkout returns HTTP 500 with `no such table: products` in structured logs, while `/health` continues to respond. Restart with `FAULT_MODE=healthy` to restore successful checkouts. Use only in a disposable sandbox.

## Telemetry

- Structured JSON logs to stdout.
- Optional Datadog log shipping when `DD_API_KEY` and `DD_SITE` are provided securely at runtime.
- Python tracing through `ddtrace-run`; a Datadog Agent must be reachable on localhost:8126.
- DogStatsD counters and latency histograms go to localhost:8125. The AWS demo runs the app and agent in a shared task network.
- Tags: `service:pig-checkout`, `env:sandbox`, and `DD_VERSION`.

Credentials and cloud account details are deliberately absent from this repository. Deployment orchestration and the Pig investigation workspace live separately.

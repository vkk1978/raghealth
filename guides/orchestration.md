# Running raghealth from your orchestrator

The scan is a CLI with meaningful exit codes: `--fail-on-critical` exits 1
on critical findings; `raghealth diff old.json new.json --fail-on-regression`
exits 1 on regression. That's all an orchestrator needs.

**cron**
```
0 6 * * * cd /opt/kb && /opt/kb/.venv/bin/raghealth scan --json today.json --fail-on-critical >> raghealth.log 2>&1
```

**Airflow**
```python
BashOperator(task_id="kb_health",
             bash_command="raghealth scan -c /opt/kb/raghealth.yaml --fail-on-critical",
             retries=0)  # a red task IS the alert
```

**Dagster**
```python
@op
def kb_health():
    r = subprocess.run(["raghealth", "scan", "--fail-on-critical",
                        "--json", "scan.json"])
    if r.returncode:
        raise Failure("knowledge base has critical findings — see scan.json")
```

**GitHub Actions** — use the first-party action instead (`uses:
vkk1978/raghealth@v0`), which adds a job-summary report and typed outputs.
See `examples/kb-health.yml`.

For trend history and Slack alerts without wiring any of the above, run the
agent + server instead: see `deploy/`.

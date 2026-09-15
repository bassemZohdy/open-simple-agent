#!/bin/sh
set -eu

control_plane_url="${OSA_CONTROL_PLANE_URL:-http://control-plane:8000}"

echo "Waiting for the Control Plane..."
i=0
while ! curl -fsS "${control_plane_url}/health/ready" >/dev/null; do
  i=$((i + 1))
  if [ "$i" -ge 30 ]; then
    echo "Control Plane did not become ready" >&2
    exit 1
  fi
  sleep 2
done

model='{"apiVersion":"osa/v1alpha1","kind":"Model","spec":{"name":"default","provider":"fake","model_id":"docker-demo","is_default":true}}'
if ! curl -fsS "${control_plane_url}/resources/Model/default" >/dev/null 2>&1; then
  curl -fsS -X POST "${control_plane_url}/resources/Model"     -H "Content-Type: application/json"     -d "$model" >/dev/null
fi

agent='{"name":"docker-demo-agent","description":"Deterministic Docker demonstration agent","definition":{"apiVersion":"osa/v1alpha1","kind":"Agent","metadata":{"name":"docker-demo-agent","version":"1.0.0","description":"Deterministic Docker demonstration agent","labels":{"demo":"docker"}},"spec":{"instruction":"Answer briefly. Prefix every response with DEMO RESPONSE so users can distinguish the deterministic local demonstration from a live model.","model":{"ref":"default"}}}}'

agent_response="$(curl -fsS -X POST "${control_plane_url}/agents"   -H "Content-Type: application/json"   -d "$agent" || true)"
agent_id="$(printf '%s' "$agent_response" | sed -n 's/.*"agent_id":"\([^"]*\)".*/\1/p')"

if [ -z "$agent_id" ]; then
  agent_list="$(curl -fsS "${control_plane_url}/agents?q=docker-demo-agent&limit=100")"
  agent_id="$(printf '%s' "$agent_list" | sed -n 's/.*"agent_id":"\([^"]*\)".*/\1/p' | head -n 1)"
fi

if [ -z "$agent_id" ]; then
  echo "Could not create or find docker-demo-agent" >&2
  exit 1
fi

# Re-running the seed script is safe for the sample agent.
curl -fsS -X POST "${control_plane_url}/agents/${agent_id}/activate" >/dev/null || true
deployment="$(curl -fsS -X POST "${control_plane_url}/agents/${agent_id}/deploy")"
echo "$deployment"
echo "Seeded docker-demo-agent (${agent_id}). Open the Control Panel at http://localhost:8080."

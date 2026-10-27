#!/usr/bin/env bash
# Isolated CI acceptance of the public Compose. Telegram credentials are fixtures;
# an internal network prevents contacting Telegram or any external account.
set -euo pipefail

project="tma-first-run-${GITHUB_RUN_ID:-local}-$$"
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
override="$(mktemp)"
export TMA_IMAGE="${TMA_IMAGE:-telegram-media-automation:test}"
cat > "$override" <<'YAML'
services:
  api:
    environment:
      APP_WORKERS_ENABLED: "false"
      APP_CLEANUP_ENABLED: "false"
networks:
  default:
    internal: true
YAML
compose=(docker compose --env-file /dev/null --project-name "$project" -f "${TMA_COMPOSE_FILE:-$root/compose.yaml}" -f "$override")
cleanup() {
  result=$?
  if [[ $result != 0 ]]; then "${compose[@]}" logs --tail=80 api || true; fi
  "${compose[@]}" down --volumes --remove-orphans || true
  rm -f -- "$override"
  exit "$result"
}
trap cleanup EXIT

wait_server() {
  for attempt in {1..40}; do
    if "${compose[@]}" exec -T api python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8080/health/live',timeout=2)" >/dev/null 2>&1; then return; fi
    sleep 1
  done
  return 1
}

"${compose[@]}" config --quiet
"${compose[@]}" up -d --no-build --pull never
wait_server
container_id="$("${compose[@]}" ps -q api)"
test "$(docker inspect --format '{{.HostConfig.ReadonlyRootfs}}' "$container_id")" = true
"${compose[@]}" exec -T api python - <<'PY'
import hashlib
import http.cookiejar
import json
import os
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

assert os.getuid() == 10001
try:
    Path('/first-run-must-be-read-only').write_text('unexpected')
    raise AssertionError('root filesystem is writable')
except OSError:
    pass
for path in ['/data','/downloads/incomplete','/downloads/inbox','/media/movies','/media/tv','/backups']:
    assert os.access(path,os.W_OK), 'A storage directory is not writable'
jar = http.cookiejar.MozillaCookieJar('/data/first-run-test.cookies')
client = urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPCookieProcessor(jar))
def api(path, body=None, origin='http://127.0.0.1:8080'):
    request=urllib.request.Request('http://127.0.0.1:8080/api/v1'+path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={'Content-Type':'application/json','Origin':origin})
    with client.open(request,timeout=15) as response:return json.load(response)
state=api('/auth/setup')
assert state['setup_required'] and state['bootstrap_required']
code = subprocess.check_output(['mediactl','setup-code'],text=True).strip()
assert len(code)>=32 and code not in json.dumps(state)
payload={'username':'owner','password':'Container-fixture-only-2026','setup_code':'incorrect',
         'telegram_api_id':12345,'telegram_api_hash':'0'*32,'language':'en-US','timezone':'UTC'}
try:
    api('/auth/setup',payload)
    raise AssertionError('incorrect code accepted')
except urllib.error.HTTPError as error:
    assert error.code==403
payload['setup_code']=code
try:
    api('/auth/setup',payload,origin='https://other.invalid')
    raise AssertionError('cross-origin setup accepted')
except urllib.error.HTTPError as error:
    assert error.code==403
response=api('/auth/setup',payload)
assert response['authenticated'] and response['restart_required']
assert api('/system/status')['setup_restart_required']
assert api('/auth/session')['username']=='owner'
assert not api('/auth/setup')['setup_required']
repeated=subprocess.run(['mediactl','setup-code'],capture_output=True,text=True)
assert repeated.returncode!=0 and not repeated.stdout.strip()
for name in ['api_token','install/admin_credentials.json','install/telegram_api_hash']:
    assert Path('/data/configuration',name).stat().st_mode & 0o077 == 0
report=json.dumps(api('/diagnostics'))
assert all(secret not in report for secret in [code,payload['password'],payload['telegram_api_hash']])
jar.save(ignore_discard=True,ignore_expires=True)
os.chmod(jar.filename,0o600)
Path('/data/first-run-test.token-sha').write_text(hashlib.sha256(Path('/data/configuration/api_token').read_bytes()).hexdigest())
print('First-run code, origin checks, private storage and session verified')
PY
"${compose[@]}" restart api
wait_server
"${compose[@]}" exec -T api python - <<'PY'
import hashlib
import http.cookiejar
import json
import urllib.request
from pathlib import Path

jar=http.cookiejar.MozillaCookieJar('/data/first-run-test.cookies')
jar.load(ignore_discard=True,ignore_expires=True)
client=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPCookieProcessor(jar))
def api(path):
    with client.open('http://127.0.0.1:8080/api/v1'+path,timeout=10) as response:return json.load(response)
assert api('/auth/session')['username']=='owner'
assert api('/ui/config')=={'language':'en-US','timezone':'UTC'}
status=api('/system/status')
assert status['telegram_configured'] and not status['setup_restart_required']
assert status['schema_version']==14 and not status['workers_running']
assert not api('/auth/setup')['setup_required']
assert not Path('/data/configuration/setup_code').exists()
assert hashlib.sha256(Path('/data/configuration/api_token').read_bytes()).hexdigest()==Path('/data/first-run-test.token-sha').read_text()
print('Restart preserves setup, signing key, administrator session and preferences')
PY

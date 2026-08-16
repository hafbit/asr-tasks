#!/bin/sh
set -eu

base_url="http://127.0.0.1:8000"
sample="/tmp/asr-tasks-smoke.wav"
ffmpeg -hide_banner -loglevel error -y \
  -f lavfi -i "sine=frequency=440:duration=2" -ac 1 -ar 16000 "$sample"

asset_json=$(curl -fsS -F "file=@${sample};type=audio/wav" "$base_url/v1/assets")
asset_id=$(python -c 'import json,sys; print(json.load(sys.stdin)["id"])' <<EOF
$asset_json
EOF
)
job_json=$(curl -fsS -H "Content-Type: application/json" \
  -d "{\"asset_id\":\"$asset_id\"}" "$base_url/v1/transcription-jobs")
job_id=$(python -c 'import json,sys; print(json.load(sys.stdin)["id"])' <<EOF
$job_json
EOF
)

attempt=0
while [ "$attempt" -lt 180 ]; do
  status_json=$(curl -fsS "$base_url/v1/transcription-jobs/$job_id")
  status=$(python -c 'import json,sys; print(json.load(sys.stdin)["status"])' <<EOF
$status_json
EOF
)
  case "$status" in
    succeeded)
      curl -fsS "$base_url/v1/transcription-jobs/$job_id/result" >/tmp/result.json
      python -c 'import json; data=json.load(open("/tmp/result.json")); assert "text" in data'
      exit 0
      ;;
    failed|cancelled)
      echo "$status_json" >&2
      exit 1
      ;;
  esac
  attempt=$((attempt + 1))
  sleep 2
done

echo "transcription smoke test timed out" >&2
exit 1

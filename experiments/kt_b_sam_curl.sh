#!/bin/zsh
# SAM 3.1 over curl -N (cookbook transport), to compare with the SDK stream that dropped.
# usage: kt_b_sam_curl.sh prepared.mp4 "person" outdir
set -e
VIDEO=$1; CONCEPT=$2; OUT=$3; mkdir -p "$OUT"
t0=$(date +%s)
UP=$(curl -sS --fail-with-body --retry 3 --retry-all-errors https://api.meta.ai/v1/files -H "Authorization: Bearer $MODEL_API_KEY" -F purpose=user_data -F "file=@$VIDEO;type=video/mp4")
FILE_ID=$(echo "$UP" | python3 -I -c 'import json,sys; print(json.load(sys.stdin)["id"])')
t1=$(date +%s); echo "uploaded $FILE_ID in $((t1-t0))s"
cat > "$OUT/request.json" <<JSON
{"model":"sam-3.1","stream":true,"metadata":{"mask_encoding":"one_bit"},"input":[{"type":"message","role":"user","content":[{"type":"input_text","text":"$CONCEPT"},{"type":"input_video","file_id":"$FILE_ID"}]}]}
JSON
curl -sS --fail-with-body -N --max-time 1500 https://api.meta.ai/v1/responses -H "Authorization: Bearer $MODEL_API_KEY" -H "Content-Type: application/json" --data-binary "@$OUT/request.json" -o "$OUT/events.sse" -w "http %{http_code} bytes %{size_download} time %{time_total}s\n" || echo "curl exit $?"
t2=$(date +%s); echo "sam stream $((t2-t1))s"; ls -la "$OUT/events.sse"; grep -c '^data:' "$OUT/events.sse" || true; tail -c 600 "$OUT/events.sse"

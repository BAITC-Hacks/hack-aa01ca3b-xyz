#!/usr/bin/env bash
# Собирает синтетическую тестовую запись совещания (русский, казахский, шала) из реплик
# samples/synthetic_meeting_script.txt голосами macOS: Milena (ru_RU) и Aru (kk_KZ).
# Разные «участники» получаются сдвигом тона. Нужны macOS и ffmpeg.
set -euo pipefail
cd "$(dirname "$0")/.."

SCRIPT="samples/synthetic_meeting_script.txt"
OUT="samples/synthetic_meeting.mp3"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

ffmpeg -loglevel error -f lavfi -i anullsrc=r=16000:cl=mono -t 0.8 "$TMP/pause.wav"
: > "$TMP/list.txt"
i=0
while IFS='|' read -r speaker voice pitch text; do
  [[ -z "${speaker}" || "${speaker}" == \#* ]] && continue
  i=$((i + 1))
  say -v "$voice" --file-format=WAVE --data-format=LEI16@22050 -o "$TMP/raw$i.wav" "$text"
  tempo=$(python3 -c "print(round(1/$pitch, 4))")
  ffmpeg -loglevel error -i "$TMP/raw$i.wav" \
    -af "asetrate=22050*$pitch,aresample=16000,atempo=$tempo" -ac 1 -ar 16000 "$TMP/line$i.wav"
  printf "file '%s'\nfile '%s'\n" "$TMP/line$i.wav" "$TMP/pause.wav" >> "$TMP/list.txt"
  echo "  $i. $speaker ($voice x$pitch)"
done < "$SCRIPT"

ffmpeg -loglevel error -y -f concat -safe 0 -i "$TMP/list.txt" -ac 1 -ar 16000 -b:a 64k "$OUT"
echo "Готово: $OUT"

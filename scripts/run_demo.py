"""Process a meeting recording from the command line — the fastest way to check the main scenario.

    python scripts/run_demo.py                                  # samples/synthetic_meeting.mp3
    python scripts/run_demo.py path/to/meeting.m4a --date 2026-09-23 --speakers 4

Writes out/<name>.json, out/<name>.docx and out/<name>.pdf and prints the assignments table.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hackalem.export import build_docx, build_pdf  # noqa: E402
from hackalem.media import prepare_audio  # noqa: E402
from hackalem.processing import analyze_meeting, transcribe_meeting  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Локальное автопротоколирование совещания")
    parser.add_argument("audio", nargs="?", default=str(ROOT / "samples" / "synthetic_meeting.mp3"))
    parser.add_argument("--date", default="2026-09-23", help="дата совещания YYYY-MM-DD (от неё считаются сроки)")
    parser.add_argument("--speakers", type=int, default=0, help="ожидаемое число участников, 0 — автоматически")
    parser.add_argument("--title", default="Протокол совещания")
    args = parser.parse_args()

    meeting_date = date.fromisoformat(args.date).isoformat()
    started = time.time()
    with tempfile.TemporaryDirectory(prefix="hackalem-") as temp_dir:
        audio = prepare_audio(args.audio, temp_dir)
        segments, language, duration = transcribe_meeting(audio, args.speakers or None, progress=lambda share, text: print(f"[{share:4.0%}] {text}", flush=True))
    result, mode, note = analyze_meeting(segments, meeting_date, progress=lambda share, text: print(f"       {text}", flush=True))
    elapsed = time.time() - started

    names = {item["speaker"]: item["name"] for item in result.get("participants", []) if item.get("name")}
    print(f"\nЗапись: {args.audio} · {duration:.0f} с · языки: {language} · анализ: {mode} · заняло {elapsed:.0f} с")
    if note:
        print(f"ВНИМАНИЕ: {note}")
    print("\nТРАНСКРИПТ")
    for segment in segments:
        speaker = f"{segment['speaker']} ({names[segment['speaker']]})" if names.get(segment["speaker"]) else segment["speaker"]
        print(f"[{int(segment['start']) // 60:02d}:{int(segment['start']) % 60:02d}] {speaker} [{segment.get('lang', '')}]: {segment['text']}")
    print(f"\nСАММАРИ\n{result.get('summary', '')}")
    print(f"\nПОРУЧЕНИЯ ({len(result.get('actions', []))})")
    for number, action in enumerate(result.get("actions", []), start=1):
        due = f"{action['deadline']} [{action['deadline_iso']}]" if action.get("deadline_iso") else action.get("deadline", "")
        print(f"{number}. {action['task']}\n   ответственный: {action['assignee']} · срок: {due} · поручил: {action.get('assigned_by', '')} · {action.get('time', '')}")
    for flag in result.get("flags", []):
        print(f"⚠ {flag}")

    out = ROOT / "out"
    out.mkdir(exist_ok=True)
    stem = Path(args.audio).stem
    participants = [{"speaker": speaker, "name": names.get(speaker, speaker)} for speaker in dict.fromkeys(s["speaker"] for s in segments)]
    transcript = [{**segment, "speaker": names.get(segment["speaker"], segment["speaker"])} for segment in segments]
    payload = {"date": meeting_date, "language": language, "duration": duration, "transcript": segments, **result}
    (out / f"{stem}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / f"{stem}.docx").write_bytes(build_docx(args.title, meeting_date, result.get("summary", ""), result.get("actions", []), transcript, participants=participants, summary_items=result.get("summary_items")))
    (out / f"{stem}.pdf").write_bytes(build_pdf(args.title, meeting_date, result.get("summary", ""), result.get("actions", []), transcript, participants=participants, summary_items=result.get("summary_items")))
    print(f"\nСохранено: out/{stem}.json, out/{stem}.docx, out/{stem}.pdf")


if __name__ == "__main__":
    main()

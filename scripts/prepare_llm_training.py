"""Prepare private MLX chat JSONL from meeting transcripts and the case gold labels."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "data" / "samples"
OUTPUT = ROOT / "data" / "private_training" / "qwen3-4b-lora"
SYSTEM = (
    "Ты извлекаешь поручения из русского, казахского или смешанного транскрипта совещания. "
    "Верни только JSON-объект вида {\"actions\":[{\"task\":\"...\","
    "\"assignee\":\"...\",\"deadline_iso\":\"YYYY-MM-DD или пустая строка\"}]}. "
    "Не добавляй поручения, которых нет в транскрипте. Разделяй действия с разными сроками. "
    "Не придумывай ответственных и сроки."
)


def _training_row(transcript: str, meeting_date: str, actions: list[dict[str, Any]]) -> dict[str, Any]:
    target = {
        "actions": [
            {
                "task": str(item.get("task", "")).strip(),
                "assignee": str(item.get("assignee", "")).strip(),
                "deadline_iso": (item.get("deadline_ok") or [""])[0] or "",
            }
            for item in actions
        ]
    }
    return {
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"Дата совещания: {meeting_date}\n\nТранскрипт:\n{transcript}"},
            {"role": "assistant", "content": json.dumps(target, ensure_ascii=False)},
        ]
    }


def _synthetic_transcript(path: Path) -> str:
    result = json.loads(path.read_text(encoding="utf-8"))
    lines = []
    for index, segment in enumerate(result.get("transcript", []), start=1):
        seconds = int(float(segment.get("start", 0)))
        stamp = f"{seconds // 60:02d}:{seconds % 60:02d}"
        lines.append(f"[#{index} {stamp}] {segment.get('speaker', 'SPEAKER')}: {segment.get('text', '')}")
    return "\n".join(lines)


def main() -> None:
    gold = json.loads((SAMPLES / "gold.json").read_text(encoding="utf-8"))
    meeting_date = gold["meeting_date"]
    train = []
    for name in ("meeting1.txt", "meeting2.txt"):
        relative = f"organizer_examples/{name}"
        transcript = (SAMPLES / relative).read_text(encoding="utf-8")
        train.append(_training_row(transcript, meeting_date, gold["meetings"][relative]))

    # This audio-derived example is only a smoke test, not an independent quality test:
    # its topics and some assignments overlap with the two training meetings.
    synthetic = _training_row(
        _synthetic_transcript(SAMPLES / "synthetic_meeting.result.json"),
        meeting_date,
        gold["meetings"]["synthetic_meeting.mp3"],
    )

    OUTPUT.mkdir(parents=True, exist_ok=True)
    for filename, rows in (("train.jsonl", train), ("test.jsonl", [synthetic])):
        (OUTPUT / filename).write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
            encoding="utf-8",
        )
    print(f"Подготовлено: {OUTPUT}")
    print(f"Обучение: {len(train)} встречи; контрольный smoke-test: 1 синтетическая встреча.")
    print("Важно: примеров мало, качество на новых встречах этим набором не доказывается.")


if __name__ == "__main__":
    main()

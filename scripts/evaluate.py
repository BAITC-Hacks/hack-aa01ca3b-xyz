"""Measure assignment extraction against the gold tables (data/samples/gold.json).

Text examples from the task are turned into a diarization-like transcript: speaker
names are hidden behind SPEAKER_XX labels, so the agent must recover who is who.
Audio samples go through the full local pipeline (diarization + ASR + agent).

Usage:
    python scripts/evaluate.py                 # all meetings from gold.json
    python scripts/evaluate.py organizer_examples/meeting2.txt
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.config import SAMPLES_DIR  # noqa: E402
from app.services.extractor import analyze_meeting, transcribe_meeting  # noqa: E402

SAMPLES = SAMPLES_DIR
LINE = re.compile(r"^(?P<name>[^:(]+?)\s*(?:\((?P<role>[^)]*)\))?\s*:\s*(?P<text>.+)$")


def text_segments(path: Path) -> list[dict]:
    """Parse «Имя (должность): реплика» lines and hide names behind SPEAKER_XX labels."""
    aliases: dict[str, str] = {}
    segments = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        match = LINE.match(line.strip())
        if not match:
            continue
        name = match["name"].strip()
        aliases.setdefault(name, f"SPEAKER_{len(aliases) + 1:02d}")
        start = 6.0 * len(segments)
        segments.append({"id": len(segments) + 1, "start": start, "end": start + 6.0, "speaker": aliases[name], "text": match["text"].strip()})
    return segments


def stems(text: str) -> set[str]:
    return {word[:5] for word in re.findall(r"\w+", text.lower()) if len(word) >= 5}


def score(gold: list[dict], predicted: list[dict]) -> dict:
    used: set[int] = set()
    rows = []
    for item in gold:
        best, best_overlap = None, 0
        for index, action in enumerate(predicted):
            if index in used:
                continue
            who = f"{action.get('assignee', '')} {action.get('speaker', '')}".lower()
            if item["assignee"].lower() not in who:
                continue
            overlap = len(stems(item["task"]) & stems(f"{action.get('task', '')} {action.get('source_quote', '')}"))
            if overlap > best_overlap:
                best, best_overlap = index, overlap
        if best is None:
            rows.append((item, None, None))
            continue
        used.add(best)
        action = predicted[best]
        deadline_ok = None if item["deadline_ok"] is None else action.get("deadline_iso", "") in item["deadline_ok"]
        rows.append((item, action, deadline_ok))
    found = sum(1 for _, action, _ in rows if action)
    checked = [ok for _, action, ok in rows if action and ok is not None]
    return {
        "gold": len(gold),
        "predicted": len(predicted),
        "found": found,
        "deadline_correct": sum(checked),
        "deadline_checked": len(checked),
        "rows": rows,
        "extra": [action for index, action in enumerate(predicted) if index not in used],
    }


def main() -> None:
    gold_file = json.loads((SAMPLES / "gold.json").read_text(encoding="utf-8"))
    meeting_date = gold_file["meeting_date"]
    targets = sys.argv[1:] or list(gold_file["meetings"])
    report = ["| Запись | Поручений в эталоне | Найдено (ответственный совпал) | Лишних | Сроки верны | Время, с |", "|---|---|---|---|---|---|"]
    for name in targets:
        path = SAMPLES / name
        started = time.time()
        if path.suffix == ".txt":
            segments = text_segments(path)
        else:
            segments, _, _ = transcribe_meeting(path, progress=lambda share, text: print(f"  [{share:.0%}] {text}", flush=True))
        result, mode, note = analyze_meeting(segments, meeting_date, progress=lambda share, text: print(f"  {text}", flush=True))
        elapsed = time.time() - started
        (ROOT / "out").mkdir(exist_ok=True)
        (ROOT / "out" / f"eval_{Path(name).stem}.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        stats = score(gold_file["meetings"][name], result.get("actions", []))
        print(f"\n=== {name} ({mode}{', ' + note if note else ''})")
        for item, action, ok in stats["rows"]:
            mark = "✅" if action else "❌"
            got = f"→ {action['assignee']} | {action.get('deadline', '')} [{action.get('deadline_iso', '')}]" if action else "не найдено"
            deadline = "" if ok is None else (" срок ✅" if ok else " срок ❌")
            print(f"{mark} {item['task']} ({item['assignee']}) {got}{deadline}")
        for action in stats["extra"]:
            print(f"➕ лишнее: {action['task']} | {action['assignee']} | {action.get('deadline', '')}")
        report.append(
            f"| `{name}` | {stats['gold']} | {stats['found']} | {len(stats['extra'])} | "
            f"{stats['deadline_correct']} из {stats['deadline_checked']} | {elapsed:.0f} |"
        )
    out = ROOT / "out"
    out.mkdir(exist_ok=True)
    (out / "eval_results.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print("\n" + "\n".join(report))


if __name__ == "__main__":
    main()

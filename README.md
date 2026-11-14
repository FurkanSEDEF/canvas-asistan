# Canvas Agent (sections 11–14)

Pure Python 3.10+ standard library. Optional: `pip install pypdf` for PDF text.

## Setup
```powershell
$env:CANVAS_BASE_URL = "https://YOURSCHOOL.instructure.com"
$env:CANVAS_TOKEN    = "<token from Canvas > Account > Settings > New Access Token>"
python -m canvas_agent check          # verifies which data you are allowed to read
python -m canvas_agent sync           # first run = silent baseline (no notifications)
python -m canvas_agent run            # monitoring loop (every CANVAS_POLL_MINUTES, default 15)
python -m canvas_agent serve          # notification center at http://127.0.0.1:8765
python -m canvas_agent notifications  # CLI view
python -m canvas_agent analyze <course_id> <assignment_id>
python -m canvas_agent validate <workspace folder>
```

## Modules (independent)
| Module | Role |
|---|---|
| `canvas_client.py` | Read-only API client, permission probe, ETag caching |
| `monitor.py` | Diff live data vs stored snapshots -> events (outbox) |
| `notifications.py` | In-app center, email, Telegram, priorities, dedup |
| `analysis.py` / `files.py` / `validation.py` | Assignment analysis, attachments, deliverable checks |
| `approvals.py` | Gate for anything that would change Canvas (nothing does today) |
| `app.py` | Wiring + scheduler |

## Always-on monitoring
Running locally only monitors **while your computer and the process are on**. Missed changes are
found on the next sync and a "Monitoring was offline" notice is shown, but alerts are late.
For real 24/7: run `python -m canvas_agent run` on a VM/container/VPS (set the env vars as secrets,
mount `data/` as a volume), or schedule `python -m canvas_agent sync` with cron / Windows Task Scheduler.

## Behaviour guarantees
- No submit/modify code exists; the client only does GET. Future write actions must use `approvals.require_approval`.
- Unreadable sources are reported as unavailable; nothing is inferred or invented.
- Email/Telegram messages hide grade values unless `include_grade_values_in_external` is true in `data/preferences.json`.
- AI drafting is NOT automatic: analysis produces a plan, checklist and limitations; you (or a drafting tool) put drafts in `deliverables/`, then `validate` checks them.

# 🗓️ Timetable Manager — AI-Powered Schedule Optimizer

> **An End-to-End fullstack optimization platform built to replace ancient manual school scheduling software.**

![Timetable Optimization Hero Banner](https://images.unsplash.com/photo-1506784983877-45594efa4cbe?auto=format&fit=crop&w=1200&q=80)

---

## 🌟 Overview

School timetable generation is an **NP-hard constraint satisfaction problem** mapping multi-dimensional arrays of Teachers, Subjects, Classes, Rooms, Days, and Periods. 

Designed specifically to solve real-world scheduling fatigue, the **Timetable Manager** replaces legacy desktop utilities with a robust modern web platform that:
1. **Solves Schedules Deterministically**: Employs the industry-standard **Google OR-Tools CP-SAT solver** to output mathematical guarantees ensuring zero resource overlaps (teacher clashes, room double-bookings) while optimizing soft preference distributions.
2. **Translates Ad-Hoc Requests via Gemini AI**: Features a built-in neuro-symbolic assistant layer. Simply type natural language instructions like *"Move Physics to Wednesday Period 1"*, and the engine parses the intent, converts it to locked solver constraints, and computes a seamlessly updated global schedule.
3. **Presents Print-Optimized Layouts**: Features distinct tabs switching instantly between **Class-Wise** and **Teacher-Wise** views. Built-in `@media print` CSS configurations strip down dark-mode aesthetics to render beautiful, crisp high-contrast black-and-white printouts perfect for physical classroom distribution.

---

## 🚀 One-Click Startup

To eliminate manual dependency configurations, simply execute the startup bootstrap file:

```bash
./run.sh
```

**What it does automatically:**
- Scaffolds a dedicated Python virtual environment (`venv`).
- Installs FastAPI, Uvicorn, Google OR-Tools, and the Gemini Client libraries.
- Starts the backend RESTful service on `http://localhost:8000`.
- Launches your default web browser displaying the premium interface.

---

## 🏗️ Architecture System

```
timetable-manager/
├── backend/
│   ├── main.py                 # FastAPI application routes
│   ├── models/
│   │   └── schemas.py          # Pydantic entity specifications
│   ├── optimizer/
│   │   ├── engine.py           # OR-Tools CP-SAT resolution layer
│   │   └── constraints.py      # Hard and soft combinatorial rules
│   ├── ai/
│   │   └── assistant.py        # Gemini translation middleware
│   └── data/
│       └── sample_school.json  # Pre-configured dataset demo
├── frontend/
│   ├── index.html              # Custom interactive SPA interface
│   ├── css/
│   │   └── styles.css          # Dark-mode styling tokens & print layouts
│   └── js/
│       ├── app.js              # State integration controllers
│       ├── timetable.js        # Multi-dimensional grid parsing
│       └── chat.js             # Assistant chat message handling
├── article/
│   └── medium_article.md       # Full write-up blog documentation
├── README.md                   # E2E usage specification
└── run.sh                      # Unified executable startup wrapper
```

---

## 🧠 Core Hard Constraints Handled

The CP-SAT model strictly encodes the following non-negotiable assertions:
- **$\sum \le 1$ Teacher Concurrency**: Prevents any teacher from conducting two sessions simultaneously.
- **$\sum \le 1$ Class Concurrency**: Prevents a class group from being booked for conflicting subjects.
- **$\sum \le 1$ Room Concurrency**: Guarantees zero physical room double-bookings.
- **Exact Allotment Fulfillment**: Ensures every subject achieves precisely its required weekly quota.
- **Specialized Lab Restraints**: Guarantees science and computer lab components are restricted exclusively to adequate facilities.

---

## 🤖 Configuring Gemini AI

To authorize custom ad-hoc translation operations:
1. Open the **School Setup** drawer via the navigation control header.
2. Paste your Google Gemini API Key into the secure authentication form.
3. Click **Save Configuration**. The key is stored securely in your browser's local sandbox storage.

---

## 🖥️ Timetable Studio (web app)

`./run.sh`, then open **http://localhost:8000**:

1. **Upload** the class-wise `.xlsx` (or download the template / try the demo school).
2. **Review** what was read, and set what the Excel doesn't contain. Problems show up in plain English and block generation until fixed.
   * **Periods per week**: per subject, per grade group.
   * **Classes**: click a class to give it its own numbers (e.g. one extra Maths period for 9 A only).
   * **Teachers**: mark the periods a part-time teacher can't teach, every week.
   * **Labs**: set how many rooms each lab has and which practical subjects use it.
   * **Week & rules**: the school week and the rules.

   The **Assistant** works here too, e.g. *"PE should be 3 periods for grades 6 to 8"*, *"Mrs Kapoor doesn't come on Saturdays"*, *"we only have 2 computer labs"*. Each change is checked against the data first and saved only when you press Apply.
3. **Generate** with a live chart of the solver improving the timetable; stop at any time and keep the best so far.
4. **Explore** class and teacher timetables, a workload heatmap, print one or all, export to Excel.

5. **Change things** without starting over:
   * **Swap**: click *Swap*, then two periods of a class.
   * **Assistant**: type requests like *"swap 9 A's Monday 2nd with Wednesday 4th"*, *"no PE for 7 B in the last period on Friday"*, *"Mr Rao can't come on Saturdays any more"*, *"Mrs Sharma is absent tomorrow, who can cover?"*.

   Every change is a **preview**. The solver finds the *smallest* rearrangement that keeps every rule, the verifier re-checks it, and the moved periods are highlighted. Nothing is saved until you press **Apply**, and **Undo** steps back. Applied changes are remembered, so a later *Regenerate* keeps them. Cover plans for absent teachers suggest substitutes and leave the weekly timetable unchanged.

Each upload is its own workspace (stored under `TIMETABLE_DATA_DIR`, default `./data_store`). The previous UI is still at `/classic/`.

### The assistant (Gemini)

Set `GEMINI_API_KEY` on the server (optionally `GEMINI_MODEL`, default `gemini-2.5-flash`). Without one, the assistant panel lets a user paste a key that stays in their browser, and *Swap* works with no AI at all.

The model never edits the timetable. It can only call four tools: `show_timetable`, `find_free_teachers`, `plan_cover` (read-only) and `propose_changes` (move / swap / avoid / teacher_unavailable). Those go to the edit engine (`backend/editing.py`), which re-solves warm-started from the current timetable with a large cost on every moved period.

### Deploying

The repo has a `Dockerfile` that builds one container serving both the API and the web app. Real school files in `config/*.xlsx` are excluded from the image via `.dockerignore`.

```bash
docker build -t timetable-studio .
docker run -p 8000:8000 -v timetable-data:/data -e APP_PASSWORD=choose-one timetable-studio
```

Works on any Docker host. **Render step by step: see [DEPLOY.md](DEPLOY.md)** (a `render.yaml` blueprint is included).

| Setting | Value |
|---|---|
| Port | `8000` (or set `PORT`) |
| Persistent volume | mount at `/data` so uploads survive restarts |
| `APP_PASSWORD` | **set this** – anyone with the URL could otherwise upload or view data (user name `school`, change with `APP_USER`) |
| CPU / RAM | 2 GB RAM minimum for a big school; set `TIMETABLE_SOLVER_WORKERS` (4 on 1 CPU / 2 GB). Run a single instance (jobs are tracked in memory) |
| Health check | `GET /api/v2/health` |

## 🏫 Making a timetable from the school's Excel export

```bash
venv/bin/python scripts/make_timetable.py
```

This reads `config/SHIKHA_CLASSWISE.xlsx`, applies the period quotas in `config/period_quotas.json`, checks the data, solves, **independently verifies** the result and writes a printable `output/timetable.xlsx` (Report / Classes / Teachers sheets). The web app shows the same timetable afterwards.

The Excel export lists *who teaches what to whom* but **not how many periods a week** — those numbers live in `config/period_quotas.json` and should be checked by the timetable in-charge. School rules (class teacher in period 1, max periods a day, lab double periods, soft preferences and their weights) live in `config/rules.json`.

### How the data is modelled

* **One Excel row = one lesson.** A row with several teachers (`DANCE/YOGA`, `CS/BIO/ECO`) needs *all* of them at once.
* **Joint electives:** identical multi-teacher rows in sections of the same grade (e.g. `VPA` for 9 A and 9 B) are one lesson, so the sections are scheduled together.
* **No fictional teachers.** If a class's quotas don't fill the week, the gap goes to its class teacher (`CT ACTIVITY`).
* Nursery / Pre-Primary classes with only a class teacher are self-contained and not sent to the solver.

### Pipeline

```
Excel ──► excel_loader ──► diagnostics ──► CP-SAT solver ──► verifier ──► Excel / web
          (lessons)        (plain-English    (hard rules +     (re-checks every
                            data errors)      soft penalties)   rule from scratch)
```

If the data is impossible, you get reasons like *"ANITA SHARMA is given 52 periods a week but can teach at most 48"* or, for subtler conflicts, a minimal set of clashing rules extracted from the solver.

## 🧪 Testing

```bash
venv/bin/python -m pytest -m "not slow"   # ~1 min
venv/bin/python -m pytest                  # + real school and 48-class planted school
venv/bin/python scripts/benchmark.py       # markdown table for write-ups
```

* **Planted-solution generator** (`backend/testing/synthetic.py`) builds a valid timetable first and derives the school from it, so every generated school is guaranteed solvable — a failure is a solver bug, never bad data. Generated schools mimic the real one (joint electives, lab doubles, class-teacher-first, teachers at 40+ periods).
* **Verifier mutation tests** take a valid timetable, break it one way (drop a period, double-book a teacher, desync a joint elective, move the class teacher…) and check the verifier notices.
* **Diagnostics tests** check bad data fails fast with a readable message; an infeasibility test checks hidden conflicts are explained.
* **Real-data tests** run the actual school Excel end to end.
* **Edit engine tests** check that swaps, moves, exclusions and teacher unavailability come out right, move only a handful of periods, survive later edits, and that impossible or garbled requests get readable answers.
* **Assistant tests** drive the agent with a scripted fake LLM: the tool plumbing, nothing saved before Apply, errors fed back to the model, the runaway-loop guard, and the apply → regenerate → undo flow over HTTP.
* **AI eval** (`backend/testing/ai_eval.py`) builds requests from the solved timetable, so every one has a known right answer. It covers swap, move, avoid, cover, free teachers, an impossible request and an ambiguous class, in plain, abbreviated and Hinglish forms, plus Gemini-written paraphrases. Answers are graded by code, never by another LLM. The graders themselves are tested: an "oracle" model must pass every case and a model that just claims "done!" must fail every case.

```bash
GEMINI_API_KEY=... venv/bin/python -m pytest -m ai -s          # live eval in pytest
GEMINI_API_KEY=... venv/bin/python scripts/ai_eval.py --paraphrases 3   # markdown scorecard
```

---
*Crafted for maximum aesthetic and technical excellence as a full-stack engineering demonstration.*

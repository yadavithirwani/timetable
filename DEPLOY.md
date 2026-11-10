# Deploying Timetable Studio on Render

One Docker web service serves both the web app and the API. Uploaded schools live on a persistent disk at `/data`.

## 0. Before you push: keep the real school data out of git

`config/SHIKHA_CLASSWISE.xlsx`, `config/shikha_teacherwise.xlsx`, `config/my_school_config.json` and `backend/data/saved_timetable.json` contain real teacher names. They are now in `.gitignore`, but they were already staged, so take them out of the index (the files stay on disk):

```bash
git rm --cached config/SHIKHA_CLASSWISE.xlsx config/shikha_teacherwise.xlsx config/my_school_config.json backend/data/saved_timetable.json
```

The Docker image never includes them (`.dockerignore`). The real Excel is uploaded through the web app after deploying.

## 1. Put the code on GitHub

Create a **private** repository, then:

```bash
git add -A && git commit -m "Timetable Studio"
git remote add origin git@github.com:<you>/timetable-manager.git
git push -u origin master
```

## 2. Create the service from the blueprint

1. In Render: **New → Blueprint**, connect GitHub and pick the repository. Render reads `render.yaml`.
2. Fill in the secret values it asks for:
   * `APP_PASSWORD`: **required in practice.** Without it, anyone with the URL can see and upload school data. Sign in with user `school` and this password (change the user with `APP_USER`).
   * `GEMINI_API_KEY`: optional. It turns on the chat assistant. Get one at Google AI Studio. Without it, Swap still works and users can paste a key in the assistant panel.
3. **Apply**. The first build takes a few minutes. The app is at `https://timetable-studio-xxxx.onrender.com`.

## What the blueprint sets up

| | | why |
|---|---|---|
| Plan | `standard` (2 GB RAM, 1 CPU) | the solver needs ~0.5 GB with 2 workers and ~0.7 GB with 4 on the real 48-class school, so the 512 MB plans run out of memory |
| Disk | 1 GB at `/data` | uploads, timetables, edit history survive deploys and restarts (disks need a paid plan) |
| Region | Singapore | closest to India |
| `TIMETABLE_SOLVER_WORKERS` | `4` | measured on the real school: 1 worker finds no solution in 20 s; 2 → first solution 3.1 s, 0.5 GB; 4 → 2.4 s, 0.7 GB; 8 → 1.1 GB |
| Health check | `/api/v2/health` | open even when the password is set |
| Instances | 1 (don't scale out) | solve jobs and chat previews are kept in memory |

On a 1-CPU plan, the workers share the core, so solving takes longer than on a laptop. Give **Generate** 60–120 s for the real school. Upgrading to `pro` (4 GB, 2 CPU) roughly halves that, and then `TIMETABLE_SOLVER_WORKERS` can go to 8.

Check Render's current plans and prices before choosing. The plan names and sizes above are what the blueprint asks for.

## After deploying

1. Open the URL and sign in.
2. Upload the class-wise Excel, check **Review** (periods per week per band), then **Generate**.
3. Try **Swap** on a class, and the **Assistant** if a Gemini key is set.

Optional: run the live AI eval against Gemini before relying on the assistant:

```bash
GEMINI_API_KEY=... venv/bin/python scripts/ai_eval.py --excel config/SHIKHA_CLASSWISE.xlsx --paraphrases 3
```

## Other hosts

The same image runs anywhere Docker does. Mount a volume at `/data`, set `PORT`, `APP_PASSWORD`, `TIMETABLE_SOLVER_WORKERS` and (optionally) `GEMINI_API_KEY`:

```bash
docker build -t timetable-studio .
docker run -p 8000:8000 -v timetable-data:/data -e APP_PASSWORD=choose-one -e TIMETABLE_SOLVER_WORKERS=4 timetable-studio
```

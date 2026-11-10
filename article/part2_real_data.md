# My Timetable Solver Said "OPTIMAL". My Mom Said It Was Useless.

*Part 2 of building a school timetable optimizer, this time on real data: what broke, and the testing pipeline that fixed it.*

---

In Part 1 I built a timetable optimizer with Google OR-Tools CP-SAT and a Gemini chat layer on top. It worked well on my hand-made sample school. Then I gave it to my mother, who builds the timetable at her school, along with the school's real Excel export: 56 classes, 90 teachers, 813 rows.

The solver reported `OPTIMAL` in under three seconds. The timetable it produced couldn't be used.

## What "OPTIMAL" was hiding

`OPTIMAL` only means the solver found the best answer *to the model you gave it*. My model was wrong about the school, so the "best answer" was wrong too. When I finally audited the output against how the school actually works, I found four problems:

| Problem | What the old pipeline did | Effect |
|---|---|---|
| Missing period counts | The Excel has no "periods per week" column, so the parser guessed `1` for most subjects | Classes were short of periods... |
| ...padded with a fake teacher | Gaps were filled by a `SELF STUDY` teacher with unlimited capacity | **41% of all periods (1,102 of 2,688) were "self study"** |
| Co-teaching | `DANCE/YOGA — GEETIKA, NIDHI` was read as "Geetika *or* Nidhi" | 72 lessons missing a teacher; real teachers double-booked |
| Joint electives | `VPA` for 9 A and 9 B (same six teachers) was scheduled separately | 9 A had VPA on Monday P8 and 9 B on Saturday P6, with the same six teachers |

On top of that, a "clubbing" rule that let PE and Library teachers take two classes at once covered up **13 teacher double-bookings**.

The fake teacher with infinite capacity was the worst of these. It made every instance feasible, so the solver could never tell me the data was wrong.

## Lesson 1: model the school the way the school thinks about it

The export comes from legacy scheduling software, and each row is really a *lesson card*: one subject, one or more classes, one or more teachers. So the new model uses exactly that:

```
lesson = {subject, classes[], teachers[], periods_per_week}
y[lesson, day, period] ∈ {0, 1}
```

Every teacher and every class of a lesson is busy whenever the lesson is placed. With that, the clash rules become simple sums. Joint electives come out right automatically: rows in the same grade with the same subject and the same set of teachers are merged into one lesson, which recovers the school's stream structure (CS/BIO/ECO for 11 A + 11 D, VPA for 9 A + 9 B, and so on).

The new model is also *smaller*. The old one created a variable for every (teacher, subject, class, room, day, period) combination. The new one has about 40k booleans for the whole school.

Period counts now live in a small, explicit `period_quotas.json` organised by grade band. They are school policy, so a person should own them and the parser shouldn't guess. Any gap left in a class's week goes to *its own class teacher*, a real person with real capacity, never to a fictional one.

## Lesson 2: never trust the solver's own word. Verify independently.

I wrote a verifier that never looks at the CP-SAT model. It takes the school data and the flat list of assignments and re-checks every rule from scratch: exact weekly counts, all co-teachers present, joint lessons in sync, class teacher in period 1, no teacher, class or room clashes, lab practicals as back-to-back double periods.

Every timetable the engine returns goes through it. If verification fails, the API reports failure instead of shipping the timetable.

A verifier is only useful if it actually catches problems, so I **mutation-test** it. Each test starts from a known-good timetable, breaks it in one specific way (drop a period, double-book a teacher, move one section of a joint elective, push the class teacher to period 2, split a lab double), and asserts that the verifier reports it with the right message.

## Lesson 3: generate test data with the answer planted inside

Random test schools have a problem: when the solver fails, you can't tell whether the solver is wrong or the data is impossible.

The fix is a **planted-solution generator**. It builds a valid timetable first, choosing slots and teachers together, then derives the school from it: classes, teachers, lessons and quotas. Every generated school has at least one valid timetable (the planted one), so any solver failure is a bug, not bad data.

To make these tests realistic I matched the real school's shape:

- class teacher in period 1, often also teaching a core subject
- joint electives across paired sections with two teachers
- lab practicals as double periods with a limited number of labs
- busiest teachers loaded to 42–44 periods a week, like the real school

*(The first version of the generator gave teachers an average of 8 periods a week, which made it far too easy. A test generator needs testing too.)*

## Lesson 4: when it's impossible, say *why* in plain English

My mother can't act on "INFEASIBLE". The pipeline now has two layers of explanation:

1. **Counting diagnostics before solving.** These catch most real data mistakes instantly: *"ANITA SHARMA is given 52 periods a week but can teach at most 48."* *"Class 7 B has 50 periods of lessons but the week only has 48."*
2. **Infeasible cores for subtler conflicts.** Every hard rule belongs to a named group, enforced through an assumption literal. When CP-SAT proves the model infeasible, it can return a small set of groups that can't all hold together. Those groups come back as plain sentences, for example:
   *"Teacher X is unavailable on Monday period 1"* + *"CLASS TEACHER for 3 A must be in period 1"* + *"…needs 6 periods a week."*

## Results

60-second limit, 10-core laptop. "First solution" includes building the model; "doubles / forced" compares same-subject-twice-a-day with the minimum the quotas force.

| instance | classes | teachers | lessons | max load | status | first solution | time | verified | free periods | doubles / forced | long runs |
|---|---|---|---|---|---|---|---|---|---|---|---|
| planted 2x2 | 4 | 19 | 55 | 36 | OPTIMAL | 0.1s | 0s | yes | 0 | 10 / 10 | 0 |
| planted 4x4 | 16 | 40 | 220 | 40 | OPTIMAL | 0.7s | 29s | yes | 0 | 40 / 40 | 0 |
| planted 8x4 | 32 | 56 | 440 | 42 | FEASIBLE | 3.7s | 60s | yes | 0 | 114 / 80 | 19 |
| planted 12x4 | 48 | 87 | 660 | 44 | FEASIBLE | 1.9s | 60s | yes | 0 | 223 / 120 | 113 |
| real school | 48 | 82 | 692 | 44 | FEASIBLE | 3.5s | 60s | yes | 0 | 104 / 87 | 3 |

- **First valid timetable for the real school: ~1 s.** The rest of the time limit goes into soft preferences: spreading subjects across the week, not stacking main subjects into the last periods, and avoiding long teaching runs.
- **Same-subject doubles are close to the floor.** A subject with 8 periods in a 6-day week *must* appear twice on at least 2 days. The quotas force 87 such doubles; the solver's count is listed in the table above.
- **Parallelism matters a lot.** With one worker, CP-SAT didn't find even a first solution for a small school in 3 s. With 8 workers, the solver's portfolio finds one in about a second.

How the solve improves over time on the real school (penalty score, lower is better):

| workers | first solution | 5 s | 30 s | 60 s | 180 s |
|---|---|---|---|---|---|
| 1 | none in 60 s | – | – | – | – |
| 8 | 1.1 s | 2082 | 1516 | 1424 | – |
| 10 | 1.3 s | 2109 | 1470 | 1405 | 1267 |

The web app uses 60 s by default; the command-line tool lets you give it longer when you're making the final term timetable.

## What the pipeline looks like now

```
Excel ─► loader ─► diagnostics ─► CP-SAT ─► verifier ─► Excel / web app
         lessons    plain-English   hard +     re-checks
                    data errors     soft       every rule
```

`python scripts/make_timetable.py` turns the school's Excel export into a printable workbook with a sheet per class, a sheet per teacher, and a report listing the checks that passed and the assumptions still to confirm.

## What I'd tell anyone building an optimizer for a real person

1. **A solver's status describes your model, not the world.** Audit the output against reality before celebrating.
2. **Never add infinite-capacity "fudge" resources.** They make every instance feasible, which hides every data error.
3. **Write the verifier before you tune the solver**, then mutation-test the verifier.
4. **Plant the answer in your test data** so a failing test always points to a bug.
5. **Errors are a user interface.** "Mansi has 52 periods" is something a person can fix; "INFEASIBLE" isn't.

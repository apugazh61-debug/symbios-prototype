# SYMBIOS — Hackathon Prototype

A working MVP of the fatigue + safety-zone aware task orchestration
system described in the pitch deck. Fully software: your laptop
webcam is the only "hardware" involved.

```
webcam frame → perception.py (pose + fatigue score)
             → zones.py (safety-zone check)
             → decision.py (rule-based reassignment)
             → copilot.py (plain-language explanation, on demand)
```

## 1. Requirements

- Python 3.10+ (3.9 also works)
- A webcam
- (Optional, for the "why" chat) an Anthropic API key from
  https://console.anthropic.com — the demo still works without one,
  it just uses a canned offline explanation instead.

## 2. Setup

```bash
cd symbios-prototype
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

pip install -r requirements.txt

# optional — enables the real LLM explanation instead of the offline fallback
export ANTHROPIC_API_KEY="sk-ant-..."     # Windows (PowerShell): $env:ANTHROPIC_API_KEY="sk-ant-..."
```

> `mediapipe` install can be slow on the first try — that's normal,
> let it finish. If it fails on Apple Silicon, run
> `pip install mediapipe-silicon` instead.
>
> You may see a pip warning about `opencv-python-headless` wanting a
> newer `numpy` — this is a harmless transitive-dependency notice
> (mediapipe pulls in opencv-python-headless as well). Everything
> still imports and runs correctly with the pinned versions above.

## 3. Run the backend

```bash
cd backend
uvicorn main:app --reload --port 8000
```

You should see `Uvicorn running on http://127.0.0.1:8000`.
Leave this terminal running.

## 4. Open the dashboard

Just double-click `frontend/index.html`, or open it directly in
Chrome/Edge (`file:///.../frontend/index.html`). Click **Start
Camera** and allow webcam access.

> If the browser blocks camera access on a `file://` page, serve the
> frontend folder instead:
> ```bash
> cd frontend
> python3 -m http.server 5500
> ```
> then open `http://localhost:5500`.

## 5. Demo script (what to show judges)

1. Move around normally for 5-10 seconds (simulate regular work) →
   fatigue score stays low, decision shows `NORMAL`.
2. **Then abruptly go still and slump your shoulders** for 10+
   seconds → this is the combination that reliably pushes the score
   up (a sudden drop from active movement, plus posture droop, plus
   stillness together). Watch the badge move to `MONITOR`.
   > Tested behaviour: staying still from the very start barely moves
   > the score (there's no "drop" to detect yet) — you have to show
   > *some* movement first, then stop, for the fatigue signal to
   > climb quickly. Practice this transition once before your demo.
3. While the score is elevated, step into the dashed red rectangle
   (the "Robotic Arm Envelope" zone, right third of frame) → badge
   should jump to `REASSIGN TO COBOT` (critical).
4. Click **"Why was this decided?"** → the copilot explains the
   decision in plain language.
5. Check the **Reassignment Log** panel — every critical event is
   time-stamped there, useful for a "here's the audit trail" talking
   point.

## 6. What's a shortcut vs. what's real here

**Real, working:**
- Live pose detection (MediaPipe) — genuinely tracks your movement
- Fatigue scoring from actual speed/posture signals over a rolling
  window
- Safety-zone intersection check against your real position
- Rule-based decision engine — deterministic, explainable, judge-proof
- LLM explanation via a real Claude API call

**Simplified for the 48-hour scope (say this out loud in your demo,
judges respect the honesty):**
- One worker per webcam (multi-worker = one `WorkerTracker` per
  worker ID, same code, just looped)
- Decision engine is rule-based rather than a trained RL policy —
  the `decide()` function in `decision.py` is written so a trained
  Stable-Baselines3 policy can be dropped in later without touching
  the rest of the pipeline
- Zones are hand-defined rectangles, not calibrated to a real robot's
  reach envelope
- No cobot is physically reassigned — the dashboard shows what
  *would* happen, which is the right level of fidelity for a software
  hackathon prototype

## 7. Team

**vivo S1 Pro** — SYMBIOS, Industry 5.0 track (I5-01: Human-Robot
Collaborative System)

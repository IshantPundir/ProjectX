# Interview Engine v2 — Naturalness / Triage Tier — Phase 2 (agent.py 3-tier orchestration)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the already-built `triage/` tier into `agent.py` so a committed turn launches triage ∥ brain on **separate clocks** (neither awaited in silence), speaks a contextual filler the instant triage lands, and delivers the brain's question later via **deliver-when-ready** — retiring the dead-air `await self._brain_task` and making cheap turns (holds / repeats) skip the brain entirely.

**Architecture:** `on_user_turn_completed` launches a TriagePlane task and a (speculative) ControlPlane task at the same instant, registers `add_done_callback`s on both, and `raise StopResponse()` (so the framework auto-reply never fires) — **nothing is awaited**. Triage's callback speaks the immediate line via `session.say()`; on `HANDLED` it cancels the brain (still-pending → continuation cue + accumulate; repeat → replay), on `TO_BRAIN` it leaves the brain running. The brain's callback (TO_BRAIN only) stages its `Directive` and calls `session.generate_reply()`, which routes through `_MouthAgent.llm_node` = the mouth's filler-aware Pass-2. The two speeches play in order because LiveKit schedules both at `SPEECH_PRIORITY_NORMAL` (FIFO, no preemption). Barge-in cancels both tasks.

**Tech Stack:** FastAPI / Python 3.13, LiveKit Agents **1.5.9** (mouth only), instructor via `app/ai`, pytest in the `nexus` container. The triage tier (`triage/{decision,input_builder,service}.py`, `prompts/v3/engine/triage.system.txt`, config) and the mouth `build_mouth_messages(just_said_filler=…)` field already exist and are unit-/real-API-tested (Phase 0/1, committed). This plan only touches `agent.py`, the mouth `ConversationPlane`, config, and two prompt-evals.

**LiveKit-verified API (spike):** `docs/superpowers/specs/2026-05-24-livekit-1.5.9-deliver-when-ready-spike.md` — confirmed against the installed 1.5.9 source: `StopResponse` from `on_user_turn_completed` aborts the auto-reply but the session can still speak explicitly afterward; `session.say(text, *, allow_interruptions, add_to_chat_ctx) -> SpeechHandle` and `session.generate_reply() -> SpeechHandle` are fire-and-forget scheduling calls safe to invoke from a task done-callback; both enqueue at `SPEECH_PRIORITY_NORMAL` so the filler (scheduled first) plays, then the question — **no interruption between them**; barge-in auto-interrupts playing speech and we cancel the in-flight tasks.

**Design spec:** `docs/superpowers/specs/2026-05-24-interview-engine-v2-naturalness-triage-design.md` (§3 lifecycle, §4 triage, §5 Pass-2, §6 deliver-when-ready, §7 cancellation, §9 acoustic-hold-space reconciliation).

**Conventions (this repo):**
- Tests in the long-running container: `docker compose exec -T nexus python -m pytest <path> -q`.
- Lint: `docker compose exec -T nexus ruff check --no-cache <files>` (line length 100).
- Opt-in real-API evals: `pytest -m prompt_quality …`.
- Restart the engine after editing engine code: `docker compose up -d --force-recreate nexus-engine`.
- **`agent.py` is LiveKit glue: validated by the live talk-test (Task 2.8), NOT by unit tests** — per the project pattern (`feedback_manual_agent_testing`). Glue tasks below verify via ruff + an import smoke + engine restart; the behavioral proof is the talk-test.
- Pre-existing `agent.py` E501s at lines ~162/430 are NOT ours — leave them.
- Stay on `feat/interview-engine-v2-m5`. **NEVER stage `scripts/export_job_agent_context.py`.** Do NOT merge the branch — the merge is gated on a passing talk-test, controlled by the user.

---

## File Structure

| File | Responsibility | Tasks |
|---|---|---|
| `app/modules/interview_engine_v2/mouth/service.py` | `ConversationPlane.build_turn_messages` forwards `just_said_filler`; new `last_question` property | 2.1 |
| `app/config.py`, `app/ai/config.py` | triage budget bump; `engine_v2_cue_cooldown_s`; `engine_v2_triage_brain_disagreement_log` | 2.2 |
| `app/modules/interview_engine_v2/agent.py` | TriagePlane wiring + agent state + helpers + `llm_node` filler (2.3); `on_user_turn_completed` 3-tier rewrite + callbacks (2.4); barge-in triage cancel + §9 cue cooldown (2.5) | 2.3, 2.4, 2.5 |
| `tests/interview_engine_v2/test_mouth_input_builder.py`, `test_config.py` | unit tests for 2.1 / 2.2 | 2.1, 2.2 |
| `tests/interview_engine_v2/prompt_evals/test_mouth_evals.py` | Pass-2 linking eval (real-API) | 2.6 |

---

## Task 2.1: Mouth Pass-2 plumbing — forward `just_said_filler` + expose `last_question`

`build_mouth_messages(..., just_said_filler=…)` already exists (Phase 1, committed) but `ConversationPlane.build_turn_messages` does not forward it, and the agent needs read access to the cached last question (for triage's `active_question` / `last_spoken_question` inputs).

**Files:**
- Modify: `app/modules/interview_engine_v2/mouth/service.py`
- Test: `tests/interview_engine_v2/test_mouth_input_builder.py`

- [ ] **Step 1: Failing test — `build_turn_messages` forwards the filler + `last_question` property**

In `tests/interview_engine_v2/test_mouth_input_builder.py`, add (mirror the existing `_plane()`/imports in that file; if none, construct `ConversationPlane(loader=PromptLoader(version="v3"), persona_name="Arjun", job_title="X")`):

```python
def test_conversation_plane_forwards_just_said_filler():
    from app.ai.prompts import PromptLoader
    from app.modules.interview_engine_v2.directive import Directive, DirectiveAct
    from app.modules.interview_engine_v2.mouth.service import ConversationPlane
    plane = ConversationPlane(loader=PromptLoader(version="v3"),
                              persona_name="Arjun", job_title="Backend Engineer")
    msgs = plane.build_turn_messages(
        Directive(id="d", turn_ref="t-1", act=DirectiveAct.ACK_ADVANCE,
                  say="How long with Workato in production?"),
        candidate_utterance="about five years",
        just_said_filler="Mm — five years, mostly Python…")
    suffix = msgs[-1]["content"]
    assert "YOU JUST SAID: «Mm — five years, mostly Python…»" in suffix


def test_conversation_plane_exposes_last_question_after_voicing():
    from app.ai.prompts import PromptLoader
    from app.modules.interview_engine_v2.directive import Directive, DirectiveAct
    from app.modules.interview_engine_v2.mouth.service import ConversationPlane
    plane = ConversationPlane(loader=PromptLoader(version="v3"),
                              persona_name="Arjun", job_title="Backend Engineer")
    assert plane.last_question is None
    plane.build_turn_messages(
        Directive(id="d", turn_ref="t-1", act=DirectiveAct.ASK, say="Tell me about a Python backend."),
        candidate_utterance=None)
    assert plane.last_question == "Tell me about a Python backend."
```

- [ ] **Step 2: Run — verify it fails**

Run: `docker compose exec -T nexus python -m pytest tests/interview_engine_v2/test_mouth_input_builder.py -k "forwards_just_said or exposes_last_question" -q`
Expected: FAIL — `build_turn_messages() got an unexpected keyword argument 'just_said_filler'` and `AttributeError: 'ConversationPlane' object has no attribute 'last_question'`.

- [ ] **Step 3: Implement in `ConversationPlane`**

In `mouth/service.py`, add the `just_said_filler` param to `build_turn_messages` and forward it, and add the `last_question` property. Replace the `build_turn_messages` signature + body head:

```python
    def build_turn_messages(
        self, directive: Directive, *, candidate_utterance: str | None,
        just_said_filler: str | None = None,
    ) -> list[dict[str, str]]:
        """Assemble the [persona | act | dynamic] messages and update the REPEAT cache.

        `just_said_filler` (Pass-2 linking, design §5): the line triage just spoke; the mouth
        continues from it without repeating it, while delivering the directive's substance
        faithfully (verbatim bank text stays intact)."""
        act_block = self._loader.get(_ACT_PROMPT[directive.act])
        messages = build_mouth_messages(
            directive=directive,
            persona_preamble=self._persona_preamble,
            act_block=act_block,
            candidate_utterance=candidate_utterance,
            last_question=self._last_question,
            just_said_filler=just_said_filler,
        )
```

(Leave the `is_question_bearing(...)` / `_last_question` update block below unchanged.)

Add the property next to `persona_preamble`:

```python
    @property
    def last_question(self) -> str | None:
        """The most recently delivered question-bearing line (REPEAT cache / triage context)."""
        return self._last_question
```

- [ ] **Step 4: Run — verify it passes (and no mouth regressions)**

Run: `docker compose exec -T nexus python -m pytest tests/interview_engine_v2/test_mouth_input_builder.py -q`
Expected: PASS (all).

- [ ] **Step 5: Lint + commit**

```bash
docker compose exec -T nexus ruff check --no-cache app/modules/interview_engine_v2/mouth/service.py tests/interview_engine_v2/test_mouth_input_builder.py
git add app/modules/interview_engine_v2/mouth/service.py tests/interview_engine_v2/test_mouth_input_builder.py
git commit -m "feat(engine-v2): mouth ConversationPlane forwards just_said_filler + exposes last_question"
```

---

## Task 2.2: Config — bump triage budget; add cue-cooldown + disagreement-log knobs

The open Phase 1 finding: real nano triage round-trips ≈2s under prompt-caching, so the 1500ms default would constant-fallback in prod — bump to 2500 (the Phase 1 config test already caps at ≤3000). Also add the §9 cue-cooldown and the §7 dev-time disagreement-log flag.

**Files:**
- Modify: `app/config.py`, `app/ai/config.py`
- Test: `tests/interview_engine_v2/test_config.py`

- [ ] **Step 1: Failing test**

In `tests/interview_engine_v2/test_config.py`, add:

```python
def test_phase2_triage_budget_and_cue_config():
    from app.ai.config import ai_config
    # bumped off 1500 (real nano ≈2s) but still within the Phase 1 cap (≤3000)
    assert 2000 <= ai_config.engine_triage_total_budget_ms <= 3000
    from app.config import settings
    assert settings.engine_v2_cue_cooldown_s > 0
    assert settings.engine_v2_triage_brain_disagreement_log is False  # dev-only, off by default
```

- [ ] **Step 2: Run — verify it fails**

Run: `docker compose exec -T nexus python -m pytest tests/interview_engine_v2/test_config.py -k phase2 -q`
Expected: FAIL — budget is 1500 (< 2000); the two `engine_v2_*` attrs don't exist.

- [ ] **Step 3: Implement in `app/config.py`**

Change the existing triage budget default and add two knobs (near the `engine_triage_*` block from Phase 1):

```python
    engine_triage_total_budget_ms: int = 2500  # real nano ≈2s under prompt-caching (was 1500)
    # §9 reconciliation: after any reflex/triage cue, suppress the OTHER cue path for this long so
    # the acoustic hold-space pacer and triage's "still-pending" continuation cue never double-fire.
    engine_v2_cue_cooldown_s: float = 4.0
    # Dev-only (design §7): on a HANDLED turn, let the (otherwise-cancelled) brain finish ONLY to log
    # a triage↔brain disagreement — never to change what is spoken. OFF in prod.
    engine_v2_triage_brain_disagreement_log: bool = False
```

- [ ] **Step 4: Run — verify it passes**

Run: `docker compose exec -T nexus python -m pytest tests/interview_engine_v2/test_config.py -q`
Expected: PASS (all — including the Phase 1 `test_triage_config_defaults` which still holds, 2500 ≤ 3000).

- [ ] **Step 5: Lint + commit**

```bash
docker compose exec -T nexus ruff check --no-cache app/config.py tests/interview_engine_v2/test_config.py
git add app/config.py tests/interview_engine_v2/test_config.py
git commit -m "feat(engine-v2): bump triage budget 1500->2500; add cue-cooldown + disagreement-log knobs"
```

> Note: `engine_triage_total_budget_ms` is read via `ai_config.engine_triage_total_budget_ms` (Phase 1 property — unchanged). `engine_v2_cue_cooldown_s` / `engine_v2_triage_brain_disagreement_log` are read directly off `settings` in `agent.py` (matching how `settings.engine_triage_hold_cap` is read), so no new `ai_config` properties are needed.

---

## Task 2.3: agent.py — TriagePlane wiring, agent state, helpers, `llm_node` filler

Scaffolding only: add the triage plane + per-turn state + the speech helpers + the `_last_filler` pass-through in `llm_node`. **Leave the old `on_user_turn_completed` in place** (it still uses `self._ack()`); Task 2.4 replaces it. After this task the code imports and the engine starts; behavior is unchanged until 2.4.

**Files:**
- Modify: `app/modules/interview_engine_v2/agent.py`

- [ ] **Step 1: Import TriagePlane + its types**

In the import block, after the brain imports, add:

```python
from app.modules.interview_engine_v2.triage import TriagePlane
from app.modules.interview_engine_v2.triage.decision import TriageKind, TriageRoute
```

- [ ] **Step 2: Extend `_MouthAgent.__init__`**

Add a `triage: TriagePlane` parameter and the new per-turn state fields. Change the signature to include `triage`, and append to the body:

```python
        self._triage = triage
        self._triage_task: asyncio.Task | None = None
        self._pending_answer: list[str] = []     # candidate fragments in the current answer episode
        self._last_filler: str | None = None      # the line triage just spoke -> mouth Pass-2 bridge
        self._hold_count: int = 0                  # consecutive still-pending holds this episode
        # per-turn delivery guards (reset at the top of on_user_turn_completed)
        self._filler_said: bool = False
        self._answer_delivered: bool = False       # brain delivered the question this turn
        self._handled_log_only: bool = False       # dev disagreement-log: brain ran but stays mute
```

- [ ] **Step 3: Add `cancel_triage` next to `cancel_brain`**

```python
    def cancel_triage(self) -> None:
        """Barge-in / HANDLED: cancel the in-flight triage Task cleanly."""
        task = self._triage_task
        if task is not None and not task.done():
            task.cancel()
```

- [ ] **Step 4: Add the speech helpers**

Add these methods to `_MouthAgent` (used by the 2.4 callbacks). They are sync — `session.say`/`generate_reply` are fire-and-forget scheduling calls (spike §2/§3):

```python
    def _active_question_text(self) -> str | None:
        """The active question for triage's completeness judgment = the mouth's REPEAT cache."""
        return self._mouth.last_question

    def _say_filler(self, line: str) -> None:
        """TO_BRAIN: speak the masking filler now; store it so Pass-2 continues from it."""
        if self._answer_delivered:        # brain already delivered (rare race) -> no stray filler
            return
        self._last_filler = line
        self._filler_said = True
        self._state["responding"] = True
        self.session.say(line, add_to_chat_ctx=False)   # fire-and-forget; question queues behind it

    def _say_hold_cue(self, line: str) -> None:
        """HANDLED still-pending: a continuation cue, NOT a question. §9: skip if an acoustic
        hold-space cue fired within the cooldown (don't double-cue)."""
        now = time.monotonic()
        last = self._state.get("last_cue_at")
        if isinstance(last, (int, float)) and (now - last) < settings.engine_v2_cue_cooldown_s:
            return
        self._state["last_cue_at"] = now
        self._state["responding"] = True
        self.session.say(line, add_to_chat_ctx=False)

    def _deliver_repeat(self, turn_ref: str, *, lead_in: str | None) -> None:
        """HANDLED repeat: stage a REPEAT directive (mouth replays the cached last question) and
        deliver via Pass-2; the triage lead-in ('Sure —') flows in as the filler."""
        self._last_filler = lead_in
        self._controller.stage(Directive(
            id=f"rpt-{turn_ref}", turn_ref=turn_ref, act=DirectiveAct.REPEAT, say=None))
        self._pending_answer.clear()
        self._hold_count = 0
        self._answer_delivered = True
        self._state["brain_pending"] = False
        self.session.generate_reply()    # routes through llm_node -> mouth REPEAT (filler-aware)
```

- [ ] **Step 5: Pass `just_said_filler` in `llm_node` and reset it**

In `_MouthAgent.llm_node`, change the `build_turn_messages` call + the reset line:

```python
        messages = self._mouth.build_turn_messages(
            directive, candidate_utterance=self._last_candidate_text,
            just_said_filler=self._last_filler)
        self._last_candidate_text = None               # consumed; not carried to the next turn
        self._last_filler = None                       # consumed; one bridge per delivery
```

- [ ] **Step 6: Instantiate TriagePlane in `run()` and pass it to `_MouthAgent`**

In `run()`, after the `mouth = ConversationPlane(...)` block and before/near `brain = ControlPlane(...)`, add:

```python
    triage = TriagePlane(
        persona_name=(ai_config.engine_mouth_persona_name or settings.engine_agent_name),
        job_title=config.job_title,
    )
```

Then in the `agent = _MouthAgent(...)` construction site, add `triage=triage` to the kwargs.

- [ ] **Step 7: Add `last_cue_at` to the state dict**

In the `state: dict[str, object] = { ... }` initializer in `run()`, add an entry:

```python
        "last_cue_at": None,    # §9 cue-cooldown timestamp (set by triage hold + acoustic pacer)
```

- [ ] **Step 8: Verify — ruff + import smoke + engine restart**

```bash
docker compose exec -T nexus ruff check --no-cache app/modules/interview_engine_v2/agent.py
docker compose exec -T nexus python -c "from app.modules.interview_engine_v2.agent import run, _MouthAgent; print('import-ok')"
docker compose up -d --force-recreate nexus-engine && sleep 4 && docker compose ps nexus-engine
```
Expected: ruff clean (besides the two pre-existing E501s, which are not on changed lines); prints `import-ok`; `nexus-engine` is up (no import/boot error in `docker compose logs --tail=40 nexus-engine`).

- [ ] **Step 9: Commit**

```bash
git add app/modules/interview_engine_v2/agent.py
git commit -m "feat(engine-v2): agent.py triage wiring + per-turn state + speech helpers + Pass-2 filler in llm_node"
```

---

## Task 2.4: agent.py — `on_user_turn_completed` 3-tier rewrite + done-callbacks

The core. Replace the whole `on_user_turn_completed` method (today: instant ack → `await self._brain_task` → return) with: launch triage ∥ brain at commit on separate clocks, register done-callbacks that drive speech, then `raise StopResponse()`. Nothing awaited (D3). Delete the now-unused `_ack` method.

**Files:**
- Modify: `app/modules/interview_engine_v2/agent.py`

- [ ] **Step 1: Replace `on_user_turn_completed` in full**

Replace the entire existing `async def on_user_turn_completed(...)` method (from its signature through its final comment) with:

```python
    async def on_user_turn_completed(
        self, turn_ctx: ChatContext, new_message: ChatMessage,
    ) -> None:
        # Separate clocks (D3 / design §3): launch TRIAGE ∥ BRAIN at the same instant; never await.
        # Triage gates the immediate voice (filler / hold / repeat); the brain gates the eventual
        # question, delivered when it lands (StopResponse + done-callbacks). HANDLED cancels the
        # speculatively-launched brain (accepted waste). Barge-in cancels both.
        text = new_message.text_content or ""
        self._last_candidate_text = text
        self._transcript.append(("candidate", text))
        if text.strip():
            self._pending_answer.append(text)        # accumulate fragments for this answer episode
            self._result_transcript.append(
                TranscriptEntry(role="candidate", text=text, timestamp_ms=self._t_ms()))
        word_count = len([w for w in text.split() if w])
        backchannel = is_backchannel(text, min_words=settings.engine_v2_backchannel_min_words)

        _now = time.monotonic()
        _last_listen = self._state.get("last_listening_at")
        pause_before_commit_ms = (int((_now - _last_listen) * 1000)
                                  if isinstance(_last_listen, (int, float)) else None)
        if should_yield(word_count=word_count, is_backchannel=backchannel):
            self._ladder.on_candidate_responded()
        label = classify_resumption(ResumptionSignals(
            prior_utterance_complete=True, gap_ms=0, ai_prompt_fully_delivered=True,
            word_count=word_count, is_backchannel=backchannel))
        self._collector.record(
            "turn.captured",
            {"word_count": word_count, "is_backchannel": backchannel,
             "resumption_label": label.value, "pause_before_commit_ms": pause_before_commit_ms},
            t_ms=self._t_ms(), wall_ms=_now_ms())
        log.info("engine.v2.turn_committed", word_count=word_count,
                 pause_before_commit_ms=pause_before_commit_ms, resumption_label=label.value)

        self._turn_seq += 1
        turn_ref = f"t-{self._turn_seq}"
        self._current_turn_ref = turn_ref
        accumulated = " ".join(self._pending_answer).strip() or text

        # reset per-turn delivery guards
        self._filler_said = False
        self._answer_delivered = False
        self._handled_log_only = False
        self._state["responding"] = True
        self._state["brain_pending"] = True          # mutes reflex cues for the reasoning window

        # --- launch both tiers at the SAME instant (separate clocks; neither awaited) ---
        self._triage_task = asyncio.create_task(self._triage.triage(
            active_question=self._active_question_text(),
            accumulated_answer=accumulated,
            last_spoken_question=self._mouth.last_question,
            correlation_id=self._correlation_id))
        self._brain_task = asyncio.create_task(self._brain.decide(
            turn_ref=turn_ref, candidate_utterance=accumulated,
            transcript_window=list(self._transcript), correlation_id=self._correlation_id))

        def _on_triage_done(task: asyncio.Task) -> None:
            if task.cancelled() or self._current_turn_ref != turn_ref:
                return                               # barge-in / stale turn -> no-op
            try:
                d = task.result()
            except Exception:                        # noqa: BLE001 — triage callback must not crash
                log.warning("engine.v2.triage.callback_failed", exc_info=True)
                return
            self._collector.record(
                "engine.v2.triage.decision",
                {"kind": d.kind.value, "route": d.route.value, "answer_complete": d.answer_complete,
                 "replay": d.replay_last_question, "spoken_line": d.spoken_line, "turn_ref": turn_ref},
                t_ms=self._t_ms(), wall_ms=_now_ms())
            still_pending = (d.kind is TriageKind.answering and not d.answer_complete)

            if d.route is TriageRoute.handled and d.kind is TriageKind.repeat_request:
                self.cancel_brain()
                self._deliver_repeat(turn_ref, lead_in=d.spoken_line)
                return

            if d.route is TriageRoute.handled and still_pending:
                self._hold_count += 1
                if self._hold_count > settings.engine_triage_hold_cap:
                    # hold cap reached -> force TO_BRAIN: keep the brain running, neutral filler,
                    # the brain delivers on the FULL accumulated answer (design §4.4/§4.6).
                    self._say_filler(d.spoken_line)
                    return
                # genuine hold: speak a continuation cue, accumulate, skip the brain this turn.
                if settings.engine_v2_triage_brain_disagreement_log:
                    self._handled_log_only = True    # let the brain finish only to log (dev §7)
                else:
                    self.cancel_brain()
                self._say_hold_cue(d.spoken_line)
                self._state["brain_pending"] = False
                return

            # route == to_brain (or any non-handled): masking filler, brain delivers the question.
            self._say_filler(d.spoken_line)

        def _on_brain_done(task: asyncio.Task) -> None:
            self._brain_task = None
            if task.cancelled() or self._current_turn_ref != turn_ref:
                self._state["brain_pending"] = False
                return                               # barge-in / HANDLED-cancel / stale -> no-op
            try:
                directive, record = task.result()
            except Exception:                        # noqa: BLE001 — brain callback must not crash
                log.warning("engine.v2.brain.callback_failed", exc_info=True)
                self._state["brain_pending"] = False
                return
            if self._handled_log_only:
                # dev disagreement-log: triage HANDLED this turn; record the brain's would-be move
                # but DO NOT speak it (never change what's spoken — design §7).
                self._collector.record(
                    "engine.v2.triage_brain_disagreement",
                    {"turn_ref": turn_ref, "brain_act": directive.act.value}, t_ms=self._t_ms(),
                    wall_ms=_now_ms())
                self._state["brain_pending"] = False
                return
            # supersede a still-staged speculative pre-stage (Option C / CMI-4); stage the directive
            if self._spec_id is not None and self._controller.staged_id() == self._spec_id:
                directive = directive.model_copy(update={"supersedes": self._spec_id})
            self._spec_id = None
            self._controller.stage(directive)
            self._collector.record_decision(record, t_ms=self._t_ms(), wall_ms=_now_ms())
            if directive.say:
                self._transcript.append(("agent", directive.say))
                self._result_transcript.append(
                    TranscriptEntry(role="agent", text=directive.say, timestamp_ms=self._t_ms()))
            if directive.is_terminal:
                self._state["closing"] = True
            self._pending_answer.clear()             # answer consumed -> reset the episode
            self._hold_count = 0
            self._answer_delivered = True
            self._state["brain_pending"] = False
            self.session.generate_reply()            # llm_node = mouth Pass-2 (continues from filler)

        self._triage_task.add_done_callback(_on_triage_done)
        self._brain_task.add_done_callback(_on_brain_done)
        raise StopResponse()                         # suppress the framework auto-reply (spike §1)
```

- [ ] **Step 2: Delete the now-unused `_ack` method**

Remove the `def _ack(self) -> str:` method from `_MouthAgent` — the masking filler is now triage's `spoken_line` (and on triage fallback, a canned `engine_v2_ack_messages` line chosen inside `TriagePlane._fallback`). The `engine_v2_ack_messages` setting is still used (by the triage fallback and the reflex pre-render), so leave it.

- [ ] **Step 3: Verify — ruff + import smoke + engine restart**

```bash
docker compose exec -T nexus ruff check --no-cache app/modules/interview_engine_v2/agent.py
docker compose exec -T nexus python -c "from app.modules.interview_engine_v2.agent import run, _MouthAgent; print('import-ok')"
docker compose up -d --force-recreate nexus-engine && sleep 4 && docker compose logs --tail=40 nexus-engine
```
Expected: ruff clean; `import-ok`; engine boots with no traceback. (Behavioral correctness is the Task 2.8 talk-test — there is no unit test for this glue.)

- [ ] **Step 4: Commit**

```bash
git add app/modules/interview_engine_v2/agent.py
git commit -m "feat(engine-v2): 3-tier on_user_turn_completed — launch triage∥brain, StopResponse, deliver-when-ready"
```

---

## Task 2.5: agent.py — barge-in cancels triage + acoustic-hold-space ↔ triage reconciliation (§9)

Two small coordinations: cancel the in-flight triage on barge-in (alongside the brain), and make the acoustic hold-space pacer respect the same cue-cooldown the triage hold uses, so they never double-cue.

**Files:**
- Modify: `app/modules/interview_engine_v2/agent.py`

- [ ] **Step 1: Cancel triage on barge-in**

In the `@session.on("user_state_changed")` handler, in the `ev.new_state == "speaking"` branch, add the triage cancel next to the existing brain cancel:

```python
            if agent is not None:
                agent.cancel_brain()         # barge-in: cancel any in-flight brain decision (CMI-4)
                agent.cancel_triage()        # …and the in-flight triage (design §7)
                agent.prestage_speculative()  # Option C: stage the non-voiced speculative pre-stage
```

- [ ] **Step 2: Acoustic hold-space pacer honors the cue-cooldown**

In `_silence_watch`, in the mid-answer (`state["started_answering"]`) branch where `pacer.cue_due(...)` fires, gate it on the shared cooldown and stamp it — so a triage still-pending hold just spoken suppresses the acoustic cue (and vice-versa). Replace the `if pacer.cue_due(now_s=now):` block with:

```python
                    if pacer.cue_due(now_s=now):
                        last_cue = state.get("last_cue_at")
                        if (isinstance(last_cue, (int, float))
                                and (now - last_cue) < settings.engine_v2_cue_cooldown_s):
                            continue                  # §9: don't double-cue right after a triage hold
                        pacer.mark_cued()
                        state["last_cue_at"] = now
                        log.info("engine.v2.holdspace",
                                 t_ms=int((now - started_at) * 1000))
                        state["responding"] = True
                        try:
                            await session.say(
                                _reflex("hold_space", settings.engine_v2_hold_space_message),
                                add_to_chat_ctx=False)
                        finally:
                            state["responding"] = False
                    continue
```

- [ ] **Step 3: Verify — ruff + import smoke + engine restart**

```bash
docker compose exec -T nexus ruff check --no-cache app/modules/interview_engine_v2/agent.py
docker compose exec -T nexus python -c "from app.modules.interview_engine_v2.agent import run; print('import-ok')"
docker compose up -d --force-recreate nexus-engine && sleep 4 && docker compose ps nexus-engine
```
Expected: ruff clean; `import-ok`; engine up.

- [ ] **Step 4: Commit**

```bash
git add app/modules/interview_engine_v2/agent.py
git commit -m "feat(engine-v2): barge-in cancels triage; acoustic hold-space ↔ triage cue-cooldown (§9)"
```

---

## Task 2.6: Pass-2 linking prompt-eval — continues from the filler AND preserves the bank question

The risky behavioral claim (design §5/§12): with a filler set, Pass-2 *bridges* from it but does NOT rewrite or drop the bank question. Validate on the real API by extending the existing mouth eval file.

**Files:**
- Modify: `tests/interview_engine_v2/prompt_evals/test_mouth_evals.py`

- [ ] **Step 1: Add a filler-aware `_voice` overload + two evals**

In `tests/interview_engine_v2/prompt_evals/test_mouth_evals.py`, add a filler-passing voicer and two tests (reuse the file's `_plane`, `get_openai_client`, `ai_config`, `_sentence_count`):

```python
async def _voice_with_filler(directive: Directive, *, candidate: str | None, filler: str) -> str:
    client = get_openai_client()
    msgs = _plane().build_turn_messages(
        directive, candidate_utterance=candidate, just_said_filler=filler)
    resp = await client.chat.completions.create(
        model=ai_config.engine_mouth_model,
        messages=[{"role": m["role"], "content": m["content"]} for m in msgs],
        response_model=None)
    return resp.choices[0].message.content


@pytest.mark.asyncio
async def test_pass2_preserves_bank_question_while_flowing_from_filler():
    """Design §5: the bridge governs the lead-in only; the question's substance stays intact."""
    filler = "Mm — five years, mostly Python…"
    say = "And with Workato specifically, how many years hands-on in production?"
    out = await _voice_with_filler(
        Directive(id="d1", turn_ref="t1", act=DirectiveAct.ACK_ADVANCE, say=say),
        candidate="about five years, mostly Python backend", filler=filler)
    low = out.lower()
    assert "workato" in low                                   # the specific skill is preserved
    assert out.strip() != filler                              # it's not just the filler echoed back
    assert out.count("?") <= 1                                # still one question


@pytest.mark.asyncio
async def test_pass2_flow_and_fidelity_llm_graded():
    filler = "Right, connectors and an LLM step…"
    say = "If you built a custom REST connector, how would you handle authentication?"
    out = await _voice_with_filler(
        Directive(id="d2", turn_ref="t1", act=DirectiveAct.ASK, say=say),
        candidate="we wired connectors into an LLM pipeline", filler=filler)
    client = get_openai_client()
    verdict = await client.chat.completions.create(
        model=ai_config.engine_mouth_model,
        messages=[{"role": "system", "content":
                   "Answer only YES or NO. Given a FILLER the speaker already said and an ORIGINAL "
                   "question, does the SPOKEN line (a) continue naturally from the filler WITHOUT "
                   "repeating it verbatim, AND (b) still ask the ORIGINAL question without changing "
                   "its meaning or adding a second question?"},
                  {"role": "user", "content": f"FILLER: {filler}\nORIGINAL: {say}\nSPOKEN: {out}"}],
        response_model=None)
    assert verdict.choices[0].message.content.strip().upper().startswith("YES")
```

- [ ] **Step 2: Real-API probe (the fix-#1 lesson — always probe the real endpoint)**

Run: `docker compose exec -T nexus python -m pytest tests/interview_engine_v2/prompt_evals/test_mouth_evals.py -m prompt_quality -k "pass2" -q`
Expected: PASS. If a case flakes, tighten the filler-aware style note in `mouth/input_builder.py` (the `YOU JUST SAID` block) and/or the `ask`/`ack_advance` mouth prompts — keep the assertion (do NOT weaken it). Do not relax the verbatim-bank-text fidelity bar (D2).

- [ ] **Step 3: Commit**

```bash
git add tests/interview_engine_v2/prompt_evals/test_mouth_evals.py
git commit -m "test(engine-v2): Pass-2 linking eval — continues from filler, preserves bank question"
```

---

## Task 2.7: Full regression gate

- [ ] **Step 1: Run the whole v2 + boundary + runtime suite (no prompt_quality)**

Run: `docker compose exec -T nexus python -m pytest tests/interview_engine_v2 tests/test_module_boundaries.py tests/interview_runtime -m "not prompt_quality" -q`
Expected: PASS (all — the ~245 Phase-0/1 tests plus the new 2.1/2.2 unit tests; module boundaries clean — `triage` is imported via its package public API).

- [ ] **Step 2: Lint the touched files**

Run: `docker compose exec -T nexus ruff check --no-cache app/modules/interview_engine_v2/agent.py app/modules/interview_engine_v2/mouth/service.py app/config.py`
Expected: clean except the two pre-existing `agent.py` E501s (~lines 162/430) which are not ours.

- [ ] **Step 3: Confirm git scope is clean (the export script is never staged)**

Run: `git status --short && git diff --name-only main..HEAD | sort -u`
Expected: only `interview_engine_v2/`, `app/config.py`, `app/ai/config.py`, `tests/interview_engine_v2/`, and `docs/superpowers/` paths appear; `scripts/export_job_agent_context.py` stays untracked and **unstaged**; HEAD is still on `feat/interview-engine-v2-m5` (`git symbolic-ref HEAD`).

---

## Task 2.8: Live talk-test (primary validation — manual)

The behavioral acceptance gate. `agent.py` LiveKit glue is proven by talking to the agent, per the project pattern (`feedback_manual_agent_testing`) — there are no unit tests for it.

- [ ] **Step 1: Restart the engine fresh**

Run: `docker compose up -d --force-recreate nexus-engine && docker compose logs --tail=20 nexus-engine`
Expected: healthy, no boot error. (v2 is default-OFF except the flagged test job `ce6dad9a-8903-4396-8f29-8e36da9bd2a3` — candidate "Ishant", job "Jr. Forward Deployed Engineer".)

- [ ] **Step 2: Run a session and exercise these cases**

Talk to the agent and deliberately trigger:
1. **Reactive filler + continuation** — give a clear complete answer; confirm the filler reflects what you said and the question continues from it (sounds like one turn), not "canned ack → dead air → unrelated question".
2. **HANDLED still-pending** — say "let me think…" or trail off mid-sentence; confirm a snappy (~1–1.5s) continuation cue, NO brain question, then it waits and re-triages your continuation. After ~2 holds (`engine_triage_hold_cap`) the brain should take over (force TO_BRAIN).
3. **HANDLED repeat** — ask "can you repeat that?"; confirm it replays the last question (no brain).
4. **No double-cue (§9)** — pause mid-answer long enough for the acoustic "take your time", then finish; confirm triage does NOT immediately fire a second "take your time" at commit.
5. **Barge-in** — start answering, then talk over the filler/question; confirm it cancels cleanly and re-triages the new turn (no orphaned question played after you barged in).
6. **Probe no longer repeats** (Phase 0 regression) and overall latency feel.

- [ ] **Step 3: Capture + analyze the event log**

The engine writes `backend/nexus/engine-events/<session_id>.json`. Share it; analyze: `engine.v2.triage.decision` records (kind/route per turn), HANDLED-vs-TO_BRAIN split, no `engine.v2.holdspace` cue during a brain-reasoning window or within the cooldown of a triage hold, latency (triage round-trip under the 2500ms budget; brain p50; perceived e2e), and that no filler played after a question. File any new bugs as fix tasks (do not merge with open quality bugs).

- [ ] **Step 4: Decision gate**

If the talk-test is clean and the user is happy → `superpowers:finishing-a-development-branch` (merge M5 + the naturalness redesign to `main`). The user controls the merge — do not merge autonomously. Latency lever "trim the brain prompt" stays deferred per `feedback_quality_before_latency`.

---

## Self-Review

**1. Spec coverage:**
- §3 separate-clocks lifecycle + StopResponse + deliver-when-ready → Task 2.4 (launch triage ∥ brain, callbacks, `StopResponse`, `generate_reply`).
- §4 triage routing consumed (HANDLED still-pending / repeat / TO_BRAIN), hold-cap force, accumulation + reset → Task 2.4 (`_on_triage_done`, `_hold_count`, `_pending_answer`, reset in `_on_brain_done`/`_deliver_repeat`); §4.6 brain gets the full accumulated answer (`candidate_utterance=accumulated`).
- §5 Pass-2 filler linking → Task 2.1 (plumb `just_said_filler`) + Task 2.3 (`llm_node` passes `_last_filler`) + Task 2.6 (eval).
- §6 budgets / deliver-when-ready mechanism → Task 2.2 (budget bump) + Task 2.4; verified APIs in the spike note.
- §7 error/cancellation: triage/brain fallbacks already exist; barge-in cancels both (Task 2.5 + 2.4 guards); dev disagreement-log (Task 2.2 flag + Task 2.4 branch); triage.decision audit event (Task 2.4).
- §9 acoustic-hold-space ↔ triage reconciliation → Task 2.5 (shared `last_cue_at` cooldown) + Task 2.3 `_say_hold_cue`.
- Open finding (triage budget too tight) → Task 2.2.
- Talk-test → Task 2.8. Regression gate → Task 2.7.

**2. Placeholder scan:** none — every code step shows complete code; the verified-API references point at the spike note; glue tasks (no unit test) state that explicitly and verify via ruff + import smoke + restart, with the behavioral proof in the talk-test (project pattern), which is a deliberate choice, not a gap.

**3. Type consistency:** `TriagePlane.triage(*, active_question, accumulated_answer, last_spoken_question, correlation_id, budget_ms)` matches `triage/service.py`; `TriageRoute.handled/to_brain` + `TriageKind.answering/repeat_request` match `triage/decision.py`; `ConversationPlane.build_turn_messages(directive, *, candidate_utterance, just_said_filler)` and `.last_question` (2.1) match the 2.3 `llm_node` call and the 2.6 eval; `ControlPlane.decide(*, turn_ref, candidate_utterance, transcript_window, correlation_id)` matches `brain/service.py`; `DirectiveController.stage/staged_id/current_for_turn` and `DirectiveAct.REPEAT` match `controller.py`/`directive.py`; `settings.engine_triage_hold_cap` (Phase 1) + `settings.engine_v2_cue_cooldown_s`/`engine_v2_triage_brain_disagreement_log` (2.2) match their reads in 2.3/2.4/2.5; `session.say(..., add_to_chat_ctx=False)` / `session.generate_reply()` / `raise StopResponse()` match the installed 1.5.9 signatures (spike).

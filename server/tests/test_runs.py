"""The durable run engine: replay, gates, retries, and resumability.

These are the properties everything else leans on. If a replayed step
re-executes, every flow double-writes; if a gate cannot be re-entered, no flow
can ask a human twice.
"""

import pytest

from app.runs import InMemoryRunStore, ModelUsage, RunEngine, StepFailed
from app.tasks import InMemoryTaskDirectory, Task, TaskService


@pytest.fixture()
def engine():
    return RunEngine(InMemoryRunStore(), TaskService(InMemoryTaskDirectory()))


def _task(queue: str = "review", task_id: str = "") -> Task:
    # Left blank on purpose in one test: the engine derives an id from the run
    # and the step when a flow does not supply one.
    return Task(id=task_id, kind="decision", title="decide", queue=queue)


def test_completed_steps_replay_instead_of_re_executing(engine):
    calls = {"n": 0}

    def body(ctx, inputs):
        def work():
            calls["n"] += 1
            return {"value": calls["n"]}

        first = ctx.step("s1", "work", "transform", fn=work)
        ctx.gate("k1", "gate", build_task=lambda: _task())
        return {"first": first}

    engine.register("f", body)
    record = engine.start("f", {}, run_id="run_1")
    assert record.status == "suspended"
    assert calls["n"] == 1

    engine.tasks.decide(record.open_task_id, "approved", "someone@example.com")
    resumed = engine.resume("run_1")
    assert resumed.status == "succeeded"
    # The side-effecting step ran exactly once across both passes.
    assert calls["n"] == 1
    assert resumed.outputs["first"] == {"value": 1}


def test_several_gates_in_one_run(engine):
    def body(ctx, inputs):
        first = ctx.gate("k1", "first", build_task=lambda: _task("a"))
        second = ctx.gate("k2", "second", build_task=lambda: _task("b"))
        return {"verdicts": [first.verdict, second.verdict]}

    engine.register("f", body)
    record = engine.start("f", {}, run_id="run_2")
    assert record.suspended_at_step == "k1"

    engine.tasks.decide(record.open_task_id, "corrected", "a@example.com")
    record = engine.resume("run_2")
    assert record.status == "suspended"
    assert record.suspended_at_step == "k2"

    engine.tasks.decide(record.open_task_id, "approved", "b@example.com")
    record = engine.resume("run_2")
    assert record.status == "succeeded"
    assert record.outputs["verdicts"] == ["corrected", "approved"]
    assert record.resume_count == 2


def test_resuming_a_finished_run_changes_nothing(engine):
    def body(ctx, inputs):
        return {"done": True}

    engine.register("f", body)
    record = engine.start("f", {}, run_id="run_3")
    assert record.status == "succeeded"
    again = engine.resume("run_3")
    assert again.status == "succeeded"
    assert len(again.steps) == len(record.steps)


def test_a_step_retries_then_fails_the_run(engine):
    attempts = {"n": 0}

    def body(ctx, inputs):
        def flaky():
            attempts["n"] += 1
            raise RuntimeError("upstream said no")

        ctx.step("s1", "flaky", "action", fn=flaky, retries=2)
        return {}

    engine.register("f", body)
    record = engine.start("f", {}, run_id="run_4")
    assert attempts["n"] == 3  # the attempt plus two retries
    assert record.status == "failed"
    assert "upstream said no" in record.error
    assert record.step("s1").status == "failed"


def test_a_step_can_escalate_instead_of_failing(engine):
    def body(ctx, inputs):
        ctx.step(
            "s1", "flaky", "action",
            fn=lambda: (_ for _ in ()).throw(RuntimeError("boom")),
            retries=0, escalate_queue="ops",
        )
        return {}

    engine.register("f", body)
    record = engine.start("f", {}, run_id="run_5")
    # Retry-then-escalate: the run suspends with the error in a Task rather
    # than failing silently.
    assert record.status == "suspended"
    task = engine.tasks.get(record.open_task_id)
    assert task.kind == "exception" and task.queue == "ops"
    assert "boom" in task.summary


def test_a_run_records_its_read_set_and_model_calls(engine):
    def body(ctx, inputs):
        ctx.step(
            "m1", "call", "modelcall", fn=lambda: {"ok": True},
            refs=["knowledge:policy@rev2"], model="stub", cost_usd=0.002,
        )
        ctx.note_refs("graph:Vendor/vendor_abc@rev1")
        return {}

    engine.register("f", body)
    record = engine.start("f", {}, run_id="run_6")
    assert record.model_calls == 1
    assert record.cost_usd == pytest.approx(0.002)
    assert set(record.refs) == {
        "knowledge:policy@rev2", "graph:Vendor/vendor_abc@rev1"
    }


def test_a_metered_step_takes_its_accounting_off_its_own_output(engine):
    """`meter` is `writes` for model accounting, and for the same reason.

    The numbers do not exist until the call has been made, and the frame that
    made it is several below the one declaring the step — so the step declares
    a callable and the engine resolves it against the output afterwards. All
    three fields come from the one `ModelUsage`, because a model id from one
    place and a token count from another is how a backend name gets filed as a
    model.
    """
    def body(ctx, inputs):
        ctx.step(
            "m1", "call", "modelcall",
            fn=lambda: {"text": "ok", "model": "gemini-2.5-flash",
                        "in": 1200, "out": 300},
            meter=lambda out: ModelUsage(
                model=out["model"],
                input_tokens=out["in"], output_tokens=out["out"],
            ),
        )
        return {}

    engine.register("f", body)
    record = engine.start("f", {}, run_id="run_8")
    step = record.step("m1")
    assert step.model == "gemini-2.5-flash"
    assert step.tokens == 1500
    # Unpriced, and it has to read as unpriced rather than as cheap: the rate
    # is not the reader's to know, so the meter leaves it and whoever prices
    # the call fills it in.
    assert step.cost_usd == 0.0
    assert record.model_calls == 1


def test_a_step_without_a_meter_records_what_the_call_site_declared(engine):
    """The additive half. Every existing call site passes literals and none of
    them changes behaviour by the arrival of a parameter they do not use."""
    def body(ctx, inputs):
        ctx.step(
            "m1", "call", "modelcall", fn=lambda: {"ok": True},
            model="stub", cost_usd=0.002, tokens=42,
        )
        return {}

    engine.register("f", body)
    record = engine.start("f", {}, run_id="run_9")
    step = record.step("m1")
    assert (step.model, step.cost_usd, step.tokens) == ("stub", 0.002, 42)


def test_a_replayed_step_does_not_meter_again(engine):
    """The property the whole accounting rests on: a run driven three times
    reports one call. Replay returns above the step body, so the meter is
    never re-resolved and the rollup is never re-added."""
    metered = {"n": 0}

    def body(ctx, inputs):
        def meter(_output):
            metered["n"] += 1
            return ModelUsage(
                model="gemini-2.5-flash", input_tokens=100, output_tokens=20,
                cost_usd=0.004,
            )

        ctx.step("m1", "call", "modelcall", fn=lambda: {"ok": True}, meter=meter)
        ctx.gate("k1", "gate", build_task=lambda: _task())
        return {}

    engine.register("f", body)
    record = engine.start("f", {}, run_id="run_10")
    assert record.status == "suspended"

    engine.tasks.decide(record.open_task_id, "approved", "someone@example.com")
    resumed = engine.resume("run_10")
    assert resumed.status == "succeeded"
    assert metered["n"] == 1
    assert resumed.model_calls == 1
    assert resumed.cost_usd == pytest.approx(0.004)
    assert resumed.step("m1").tokens == 120


def test_journal_is_a_projection_of_the_steps(engine):
    def body(ctx, inputs):
        ctx.event("t1", "Triggered", "started", kind="trigger")
        ctx.step("x1", "work", "transform", fn=lambda: 1, summary="did a thing")
        return {}

    engine.register("f", body)
    record = engine.start("f", {}, run_id="run_7")
    journal = record.journal()
    assert [e["step_id"] for e in journal] == ["t1", "x1"]
    assert journal[1]["summary"] == "did a thing"
    assert all(e["at_iso"] for e in journal)


def test_nested_arrays_survive_the_firestore_encoding():
    """Firestore refuses an array inside an array; the store's codec wraps and
    unwraps them so a step output like the state policy's tuple-of-tuples
    table round-trips instead of killing the whole run's persist."""
    from app.runs import _decode_nested_arrays, _encode_nested_arrays

    payload = {
        "disagreements": [["closed-won", "pending-approval", "won-not-released"]],
        "deep": [[[1, 2], [3]], "flat"],
        "untouched": {"a": [1, 2], "b": "x"},
    }
    encoded = _encode_nested_arrays(payload)

    def no_nested(value):
        if isinstance(value, dict):
            return all(no_nested(v) for v in value.values())
        if isinstance(value, list):
            return all(not isinstance(v, list) and no_nested(v) for v in value)
        return True

    assert no_nested(encoded)
    assert _decode_nested_arrays(encoded) == payload
    assert encoded["untouched"] == {"a": [1, 2], "b": "x"}

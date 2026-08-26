"""The durable run engine: replay, gates, retries, and resumability.

These are the properties everything else leans on. If a replayed step
re-executes, every flow double-writes; if a gate cannot be re-entered, no flow
can ask a human twice.
"""

import pytest

from app.runs import InMemoryRunStore, RunEngine, StepFailed
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

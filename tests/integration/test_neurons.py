"""
Integration tests — NeuronRunner (neurons/runner.py)

Invariants checked:
- spawn 3 neurons → all complete → gather returns all results
- SYNAPTIC_INTEGRATION event fires after gather()
- Failed neurons are reported but do not crash the runner
- cancel_all() terminates pending tasks cleanly
"""
import asyncio
import pytest

from neurons.runner import NeuronRunner


# ---------------------------------------------------------------------------
# Happy path — 3 parallel neurons
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_three_neurons_complete_in_parallel(telemetry):
    runner = NeuronRunner(telemetry, "test-darwin-01", "test-session-01")

    async def research(url: str) -> dict:
        await asyncio.sleep(0.01)
        return {"url": url, "content": f"data from {url}"}

    await runner.spawn("researcher_A", research("https://a.com"))
    await runner.spawn("researcher_B", research("https://b.com"))
    await runner.spawn("researcher_C", research("https://c.com"))

    results = await runner.gather(["researcher_A", "researcher_B", "researcher_C"])

    assert results["researcher_A"]["status"] == "completed"
    assert results["researcher_B"]["status"] == "completed"
    assert results["researcher_C"]["status"] == "completed"
    assert results["researcher_A"]["result"]["url"] == "https://a.com"


@pytest.mark.asyncio
async def test_gather_emits_synaptic_integration_event(telemetry):
    runner = NeuronRunner(telemetry, "test-darwin-01", "test-session-01")

    async def task():
        return {"result": "ok"}

    await runner.spawn("n1", task())
    await runner.spawn("n2", task())
    await runner.gather(["n1", "n2"])

    events = [e for e in telemetry.events if e["event_type"] == "SYNAPTIC_INTEGRATION"]
    assert len(events) == 1
    payload = events[0]["payload"]
    assert set(payload["neuron_ids_merged"]) == {"n1", "n2"}
    assert payload["failed_neurons"] == []


@pytest.mark.asyncio
async def test_gather_reports_failed_neuron(telemetry):
    runner = NeuronRunner(telemetry, "test-darwin-01", "test-session-01")

    async def failing_task():
        raise ValueError("network error")

    async def ok_task():
        return {"data": "good"}

    await runner.spawn("good_neuron", ok_task())
    await runner.spawn("bad_neuron", failing_task())

    results = await runner.gather(["good_neuron", "bad_neuron"])

    assert results["good_neuron"]["status"] == "completed"
    assert results["bad_neuron"]["status"] == "failed"
    assert "network error" in results["bad_neuron"]["error"]


@pytest.mark.asyncio
async def test_failed_neuron_emits_neuron_failed_event(telemetry):
    runner = NeuronRunner(telemetry, "test-darwin-01", "test-session-01")

    async def failing_task():
        raise RuntimeError("timeout after 30s")

    await runner.spawn("timeout_neuron", failing_task())
    await runner.gather(["timeout_neuron"])

    failed_events = [e for e in telemetry.events if e["event_type"] == "NEURON_FAILED"]
    assert len(failed_events) == 1
    assert "timeout_neuron" == failed_events[0]["payload"]["neuron_id"]


@pytest.mark.asyncio
async def test_gather_with_missing_neuron_id_returns_not_found(telemetry):
    runner = NeuronRunner(telemetry, "test-darwin-01", "test-session-01")

    async def task():
        return {}

    await runner.spawn("existing", task())
    results = await runner.gather(["existing", "ghost_neuron"])

    assert results["ghost_neuron"]["status"] == "not_found"


@pytest.mark.asyncio
async def test_synaptic_integration_correctly_counts_completed_and_failed(telemetry):
    runner = NeuronRunner(telemetry, "test-darwin-01", "test-session-01")

    async def ok():
        return {}

    async def fail():
        raise Exception("err")

    await runner.spawn("n_ok_1", ok())
    await runner.spawn("n_ok_2", ok())
    await runner.spawn("n_fail", fail())

    await runner.gather(["n_ok_1", "n_ok_2", "n_fail"])

    events = [e for e in telemetry.events if e["event_type"] == "SYNAPTIC_INTEGRATION"]
    payload = events[0]["payload"]
    assert len(payload["neuron_ids_merged"]) == 2
    assert len(payload["failed_neurons"]) == 1


@pytest.mark.asyncio
async def test_cancel_all_terminates_pending_tasks(telemetry):
    runner = NeuronRunner(telemetry, "test-darwin-01", "test-session-01")

    async def long_task():
        await asyncio.sleep(60)  # would block forever
        return {}

    await runner.spawn("slow_neuron", long_task())
    await runner.cancel_all()

    # After cancel_all, the task should be done (cancelled)
    task = runner._tasks.get("slow_neuron")
    assert task is not None
    assert task.done()


@pytest.mark.asyncio
async def test_completed_neuron_emits_neuron_completed_event(telemetry):
    runner = NeuronRunner(telemetry, "test-darwin-01", "test-session-01")

    async def task():
        return {"cost": 0.003, "tokens_burned": 150}

    await runner.spawn("n_complete", task())
    await runner.gather(["n_complete"])

    completed_events = [e for e in telemetry.events if e["event_type"] == "NEURON_COMPLETED"]
    assert len(completed_events) == 1
    assert completed_events[0]["payload"]["neuron_id"] == "n_complete"

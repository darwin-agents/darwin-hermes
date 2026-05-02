import asyncio
from typing import Coroutine


class NeuronRunner:
    """
    Manages concurrent neuron execution within a single agent session.
    Neurons are asyncio tasks — they share the event loop, the SQLite DB,
    and the telemetry client with the parent agent.
    """

    def __init__(self, telemetry, darwin_id: str, session_id: str):
        self.telemetry = telemetry
        self.darwin_id = darwin_id
        self.session_id = session_id
        self._tasks: dict[str, asyncio.Task] = {}
        self._results: dict[str, dict] = {}

    async def spawn(self, neuron_id: str, task_fn: Coroutine) -> None:
        """Launch a neuron as a concurrent asyncio task."""
        async def _wrapped():
            try:
                result = await task_fn
                self._results[neuron_id] = {"status": "completed", "result": result}
                await self.telemetry.push_event("NEURON_COMPLETED", {
                    "neuron_id": neuron_id,
                    "result_summary": str(result)[:300],
                    "tokens_burned": result.get("tokens_burned", 0) if isinstance(result, dict) else 0,
                    "cost": result.get("cost", 0.0) if isinstance(result, dict) else 0.0,
                })
            except Exception as e:
                self._results[neuron_id] = {"status": "failed", "error": str(e)}
                await self.telemetry.push_event("NEURON_FAILED", {
                    "neuron_id": neuron_id,
                    "reason": str(e),
                    "tokens_burned": 0,
                    "cost": 0.0,
                })

        self._tasks[neuron_id] = asyncio.create_task(_wrapped())

    async def gather(self, neuron_ids: list[str]) -> dict[str, dict]:
        """Wait for all specified neurons to complete and return their results."""
        tasks = [self._tasks[nid] for nid in neuron_ids if nid in self._tasks]
        await asyncio.gather(*tasks, return_exceptions=True)

        completed = [
            nid for nid in neuron_ids
            if self._results.get(nid, {}).get("status") == "completed"
        ]
        failed = [
            nid for nid in neuron_ids
            if self._results.get(nid, {}).get("status") == "failed"
        ]

        synthesis = f"Gathered results from {len(completed)} neurons. {len(failed)} failed."
        await self.telemetry.push_event("SYNAPTIC_INTEGRATION", {
            "neuron_ids_merged": completed,
            "failed_neurons": failed,
            "synthesis": synthesis,
        })

        return {nid: self._results.get(nid, {"status": "not_found"}) for nid in neuron_ids}

    async def cancel_all(self) -> None:
        for task in self._tasks.values():
            task.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)

def build_neurons_tools(settings, db, telemetry):
    async def spawn_neuron(
        neuron_id: str,
        task: str,
        context: str = None,
    ) -> dict:
        """
        Spawn a parallel sub-agent (neuron) to execute a discrete task concurrently.
        Neurons share your budget and tools but run independently in parallel.
        They are NOT separate Darwin agents — they have no independent ledger.

        Use when: you need to research multiple sources simultaneously, or execute
        independent sub-tasks in parallel to save wall-clock time.

        Args:
            neuron_id: Unique identifier for this neuron (e.g., 'researcher_competitor_A')
            task: Clear, self-contained task description for the neuron
            context: Additional context the neuron needs to complete its task

        Returns: { "neuron_id": str, "status": "spawned" }
        The neuron's results will be returned via the neurons runner as a dict.
        """
        await telemetry.push_event("NEURON_SPAWNED", {
            "neuron_id": neuron_id,
            "task": task,
        })
        return {"neuron_id": neuron_id, "status": "spawned"}

    return [spawn_neuron]

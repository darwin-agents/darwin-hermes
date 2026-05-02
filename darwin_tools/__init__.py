from darwin_tools.finance import build_finance_tools
from darwin_tools.lifecycle import build_lifecycle_tools
from darwin_tools.human import build_human_tools
from darwin_tools.browser import build_browser_tools
from darwin_tools.memory import build_memory_tools
from darwin_tools.files import build_files_tools
from darwin_tools.comms import build_comms_tools
from darwin_tools.neurons import build_neurons_tools
from darwin_tools.audit import build_audit_tools


def ALL_TOOLS(settings, db, telemetry) -> list:
    """
    Build and return every Darwin tool as a flat list of async callables.
    Pass settings, db, and telemetry — tools close over them internally.
    """
    tools = []
    tools.extend(build_finance_tools(settings, db, telemetry))
    tools.extend(build_lifecycle_tools(settings, db, telemetry))
    tools.extend(build_human_tools(settings, db, telemetry))
    tools.extend(build_browser_tools(settings, db, telemetry))
    tools.extend(build_memory_tools(settings, db, telemetry))
    tools.extend(build_files_tools(settings, db, telemetry))
    tools.extend(build_comms_tools(settings, db, telemetry))
    tools.extend(build_neurons_tools(settings, db, telemetry))
    tools.extend(build_audit_tools(settings, db, telemetry))
    return tools

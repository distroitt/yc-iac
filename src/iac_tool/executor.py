from __future__ import annotations

from collections.abc import Callable

from .commands import PlanCommand
from .exceptions import ExecutionError
from .facade import YandexCloudFacade
from .observability import get_logger
from .planner import ExecutionPlan
from .state import StateStore


logger = get_logger("executor")


class PlanExecutor:
    def __init__(
        self,
        facade: YandexCloudFacade,
        state_store: StateStore,
        progress_callback: Callable[[str, int, int, PlanCommand], None] | None = None,
    ) -> None:
        self.facade = facade
        self.state_store = state_store
        self.progress_callback = progress_callback

    def execute(self, plan: ExecutionPlan) -> list[str]:
        state = self.state_store.load()
        executed_commands: list[str] = []
        logger.info("Loaded state from %s", self.state_store.path)
        logger.info("Starting plan execution with %d commands", len(plan.commands))

        for index, command in enumerate(plan.commands, start=1):
            logger.info("Executing command %d/%d: %s", index, len(plan.commands), command.description())
            self._emit_progress("start", index, len(plan.commands), command)
            try:
                command.execute(self.facade, state, self.state_store)
            except Exception as exc:
                logger.exception("Command %s failed", command.description())
                self._emit_progress("failed", index, len(plan.commands), command)
                raise ExecutionError(f"Failed to execute command '{command.description()}': {exc}") from exc
            executed_commands.append(command.description())
            self._emit_progress("done", index, len(plan.commands), command)
            logger.info("Finished command %d/%d: %s", index, len(plan.commands), command.description())

        logger.info("Plan execution finished successfully")
        return executed_commands

    def _emit_progress(self, event: str, index: int, total: int, command: PlanCommand) -> None:
        if self.progress_callback is None:
            return
        self.progress_callback(event, index, total, command)

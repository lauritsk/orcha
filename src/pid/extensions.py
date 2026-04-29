"""Extension API and loader for pid."""

from __future__ import annotations

import importlib.metadata
import importlib.util
import inspect
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from pid.errors import abort
from pid.output import echo_err

PID_EXTENSION_API = "1"
ENTRY_POINT_GROUP = "pid.extensions"


class ExtensionError(RuntimeError):
    """Raised when extension registration or execution fails."""


@dataclass(frozen=True)
class StepResult:
    """Result returned by workflow steps and hooks."""

    action: str = "continue"
    code: int = 0
    reason: str = ""

    @classmethod
    def continue_(cls) -> StepResult:
        return cls("continue")

    @classmethod
    def skip(cls, reason: str = "") -> StepResult:
        return cls("skip", reason=reason)

    @classmethod
    def stop(cls, code: int = 0, reason: str = "") -> StepResult:
        return cls("stop", code=code, reason=reason)

    @classmethod
    def retry(cls, reason: str = "") -> StepResult:
        return cls("retry", reason=reason)


StepHandler = Callable[[Any], StepResult | None]
HookHandler = Callable[[Any], StepResult | None]
ServiceFactory = Callable[[Any], Any]
Policy = Any
ExtensionCommand = Callable[[Any], int | None]


@dataclass(frozen=True)
class WorkflowStep:
    """Named workflow step callable."""

    name: str
    run: StepHandler


@dataclass(frozen=True)
class HookRegistration:
    """Registered hook callback with deterministic ordering metadata."""

    hook: str
    fn: HookHandler
    order: int
    extension_name: str
    registration_index: int


@dataclass(frozen=True)
class ExtensionCommandContext:
    """Context passed to extension CLI commands."""

    argv: list[str]
    config: Any
    registry: ExtensionRegistry
    repo_root: Path | None = None
    scratch: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExtensionInfo:
    """Loaded extension metadata."""

    name: str
    source: str
    api_version: str


class Extension(Protocol):
    """Stable extension protocol."""

    name: str
    api_version: str

    def register(self, registry: ExtensionRegistry) -> None:
        """Register hooks, steps, services, policies, or commands."""


@dataclass(frozen=True)
class _StepInsertion:
    step: WorkflowStep
    before: str | None
    after: str | None
    registration_index: int


class ExtensionRegistry:
    """Registry populated by built-ins and extensions."""

    def __init__(self) -> None:
        self.hooks: dict[str, list[HookRegistration]] = {}
        self.replaced_steps: dict[str, WorkflowStep] = {}
        self.disabled_steps: set[str] = set()
        self.added_steps: list[_StepInsertion] = []
        self.policies: dict[str, Policy] = {}
        self.service_factories: dict[str, ServiceFactory] = {}
        self.cli_commands: dict[str, ExtensionCommand] = {}
        self.extension_infos: list[ExtensionInfo] = []
        self.loaded_extension_names: set[str] = set()
        self._current_extension = "core"
        self._registration_index = 0

    def add_hook(self, name: str, fn: HookHandler, *, order: int = 0) -> None:
        """Register a hook callback."""

        self._require_name(name, "hook")
        self._registration_index += 1
        self.hooks.setdefault(name, []).append(
            HookRegistration(
                hook=name,
                fn=fn,
                order=order,
                extension_name=self._current_extension,
                registration_index=self._registration_index,
            )
        )

    def add_step(
        self,
        step: WorkflowStep | StepHandler,
        *,
        name: str = "",
        before: str | None = None,
        after: str | None = None,
    ) -> None:
        """Add a step before or after an existing step, or append it."""

        if before and after:
            raise ExtensionError("add_step accepts only one of before or after")
        workflow_step = self._coerce_step(step, name=name)
        self._registration_index += 1
        self.added_steps.append(
            _StepInsertion(workflow_step, before, after, self._registration_index)
        )

    def replace_step(
        self, name: str, step: WorkflowStep | StepHandler, *, step_name: str = ""
    ) -> None:
        """Replace a built-in step by name."""

        self._require_name(name, "step")
        workflow_step = self._coerce_step(step, name=step_name or name)
        self.replaced_steps[name] = workflow_step

    def disable_step(self, name: str) -> None:
        """Disable a built-in or extension-added step by name."""

        self._require_name(name, "step")
        self.disabled_steps.add(name)

    def add_policy(self, name: str, policy: Policy) -> None:
        """Register a named policy object."""

        self._require_name(name, "policy")
        if name in self.policies:
            raise ExtensionError(f"policy already registered: {name}")
        self.policies[name] = policy

    def replace_service(self, name: str, factory: ServiceFactory) -> None:
        """Replace or wrap a named service using a context-aware factory."""

        self._require_name(name, "service")
        self.service_factories[name] = factory

    def add_cli_command(self, name: str, callback: ExtensionCommand) -> None:
        """Register a command under `pid x <name>`.

        The callback receives an `ExtensionCommandContext` and returns an exit
        code or `None` for success.
        """

        self._require_name(name, "CLI command")
        if name in self.cli_commands:
            raise ExtensionError(f"CLI command already registered: {name}")
        self.cli_commands[name] = callback

    def resolve_steps(
        self,
        default_steps: Iterable[WorkflowStep],
        *,
        known_steps: Iterable[str] = (),
        external_steps: Iterable[str] = (),
        include_unanchored: bool = True,
    ) -> list[WorkflowStep]:
        """Return default steps after applying extension modifications."""

        steps = list(default_steps)
        self._validate_unique_steps(steps)
        default_names = {step.name for step in steps}
        external_step_names = set(external_steps)
        known_step_names = set(known_steps) | external_step_names
        missing_replacements = (
            set(self.replaced_steps) - default_names - known_step_names
        )
        if missing_replacements:
            missing = sorted(missing_replacements)[0]
            raise ExtensionError(f"cannot replace unknown step: {missing}")

        resolved: list[WorkflowStep] = []
        for step in steps:
            if step.name in self.disabled_steps:
                continue
            resolved.append(self.replaced_steps.get(step.name, step))

        for insertion in sorted(
            self.added_steps, key=lambda item: item.registration_index
        ):
            if insertion.step.name in self.disabled_steps:
                continue
            names = [step.name for step in resolved]
            if insertion.step.name in names:
                raise ExtensionError(f"step already registered: {insertion.step.name}")
            if insertion.before is not None:
                if insertion.before in external_step_names:
                    continue
                if insertion.before not in names:
                    raise ExtensionError(
                        f"cannot add step before unknown step: {insertion.before}"
                    )
                resolved.insert(names.index(insertion.before), insertion.step)
                continue
            if insertion.after is not None:
                if insertion.after in external_step_names:
                    continue
                if insertion.after not in names:
                    raise ExtensionError(
                        f"cannot add step after unknown step: {insertion.after}"
                    )
                resolved.insert(names.index(insertion.after) + 1, insertion.step)
                continue
            if include_unanchored:
                resolved.append(insertion.step)

        self._validate_unique_steps(resolved)
        return resolved

    def run_hooks(self, name: str, ctx: Any) -> StepResult:
        """Run registered hooks and return the first non-continue result."""

        registrations = sorted(
            self.hooks.get(name, ()),
            key=lambda item: (
                item.order,
                item.extension_name,
                item.registration_index,
            ),
        )
        for registration in registrations:
            try:
                result = registration.fn(ctx)
            except Exception as error:  # noqa: BLE001 - extension boundary
                raise ExtensionError(
                    f"extension {registration.extension_name} hook {name} failed: "
                    f"{type(error).__name__}: {error}"
                ) from error
            step_result = normalize_step_result(result)
            if step_result.action != "continue":
                return step_result
        return StepResult.continue_()

    def register_extension(self, extension: Any, *, source: str) -> None:
        """Register one loaded extension object."""

        name = getattr(extension, "name", "")
        api_version = getattr(extension, "api_version", "")
        if not isinstance(name, str) or not name.strip():
            raise ExtensionError(f"extension from {source} has no valid name")
        if api_version != PID_EXTENSION_API:
            raise ExtensionError(
                f"extension {name} uses unsupported API version {api_version!r}; "
                f"expected {PID_EXTENSION_API!r}"
            )
        if name in self.loaded_extension_names:
            raise ExtensionError(f"extension already loaded: {name}")
        if not hasattr(extension, "register"):
            raise ExtensionError(f"extension {name} has no register() method")

        previous_extension = self._current_extension
        self._current_extension = name
        try:
            extension.register(self)
        except Exception as error:  # noqa: BLE001 - extension boundary
            raise ExtensionError(
                f"extension {name} registration failed: {type(error).__name__}: {error}"
            ) from error
        finally:
            self._current_extension = previous_extension

        self.loaded_extension_names.add(name)
        self.extension_infos.append(ExtensionInfo(name, source, api_version))

    @staticmethod
    def _require_name(name: str, kind: str) -> None:
        if not name or not name.strip():
            raise ExtensionError(f"{kind} name must not be empty")

    @staticmethod
    def _coerce_step(step: WorkflowStep | StepHandler, *, name: str) -> WorkflowStep:
        if isinstance(step, WorkflowStep):
            return step
        if not name:
            raise ExtensionError("step name required")
        return WorkflowStep(name, step)

    @staticmethod
    def _validate_unique_steps(steps: list[WorkflowStep]) -> None:
        seen: set[str] = set()
        for step in steps:
            if not step.name.strip():
                raise ExtensionError("step name must not be empty")
            if step.name in seen:
                raise ExtensionError(f"step already registered: {step.name}")
            seen.add(step.name)


def normalize_step_result(result: StepResult | None) -> StepResult:
    """Normalize `None` step results to continue and reject invalid values."""

    if result is None:
        return StepResult.continue_()
    if isinstance(result, StepResult):
        return result
    raise ExtensionError(
        "workflow steps and hooks must return StepResult or None; "
        f"got {type(result).__name__}"
    )


def load_enabled_extensions(
    extension_config: Any,
    registry: ExtensionRegistry,
    *,
    repo_root: Path | None = None,
    include_entry_points: bool = True,
    include_local: bool = True,
    fail_missing: bool = True,
) -> None:
    """Load enabled extensions from entry points and configured local paths."""

    if include_entry_points:
        load_entry_point_extensions(extension_config, registry)
    if include_local:
        load_local_extensions(extension_config, registry, repo_root=repo_root)
    if fail_missing:
        ensure_enabled_extensions_loaded(extension_config, registry)


def load_entry_point_extensions(
    extension_config: Any, registry: ExtensionRegistry
) -> None:
    """Load enabled extensions exposed through Python entry points."""

    enabled = _enabled_names(extension_config)
    if not enabled:
        return
    for entry_point in _entry_points():
        if (
            entry_point.name not in enabled
            or entry_point.name in registry.loaded_extension_names
        ):
            continue
        try:
            loaded = entry_point.load()
            extension = _coerce_extension_object(loaded)
            registry.register_extension(
                extension, source=f"entry-point:{entry_point.name}"
            )
        except ExtensionError:
            raise
        except Exception as error:  # noqa: BLE001 - extension boundary
            raise ExtensionError(
                f"could not load extension {entry_point.name}: "
                f"{type(error).__name__}: {error}"
            ) from error


def load_local_extensions(
    extension_config: Any,
    registry: ExtensionRegistry,
    *,
    repo_root: Path | None,
) -> None:
    """Load enabled project-local extensions from configured directories."""

    enabled = _enabled_names(extension_config)
    if not enabled:
        return
    for configured_path in getattr(extension_config, "paths", ()):
        path = Path(configured_path)
        if not path.is_absolute():
            path = (repo_root or Path.cwd()) / path
        if not path.exists():
            raise ExtensionError(f"extension path does not exist: {path}")
        if not path.is_dir():
            raise ExtensionError(f"extension path is not a directory: {path}")
        for file_path in sorted(path.glob("*.py")):
            if file_path.name.startswith("_"):
                continue
            module = _load_module_from_path(file_path)
            for extension in _extensions_from_module(module):
                name = getattr(extension, "name", "")
                if name in enabled and name not in registry.loaded_extension_names:
                    registry.register_extension(extension, source=f"local:{file_path}")


def ensure_enabled_extensions_loaded(
    extension_config: Any, registry: ExtensionRegistry
) -> None:
    """Fail when an enabled extension was not found in any source."""

    enabled = _enabled_names(extension_config)
    missing = enabled - registry.loaded_extension_names
    if missing:
        raise ExtensionError(f"enabled extension not found: {sorted(missing)[0]}")


def abort_extension_error(error: ExtensionError) -> None:
    """Print a consistent extension diagnostic and abort."""

    echo_err(f"pid: {error}")
    abort(2)


def _enabled_names(extension_config: Any) -> set[str]:
    return set(getattr(extension_config, "enabled", ()))


def _entry_points() -> list[importlib.metadata.EntryPoint]:
    return list(importlib.metadata.entry_points(group=ENTRY_POINT_GROUP))


def _coerce_extension_object(loaded: Any) -> Any:
    if inspect.isclass(loaded):
        return loaded()
    if callable(loaded) and not hasattr(loaded, "register"):
        return loaded()
    return loaded


def _load_module_from_path(path: Path) -> Any:
    module_name = f"pid_local_extension_{abs(hash(path))}_{path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ExtensionError(f"could not load extension module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as error:  # noqa: BLE001 - extension boundary
        raise ExtensionError(
            f"could not load extension module {path}: {type(error).__name__}: {error}"
        ) from error
    return module


def _extensions_from_module(module: Any) -> list[Any]:
    extensions: list[Any] = []
    if hasattr(module, "extension"):
        extensions.append(_coerce_extension_object(module.extension))
    if hasattr(module, "get_extension"):
        extensions.append(_coerce_extension_object(module.get_extension))
    for _name, value in inspect.getmembers(module, inspect.isclass):
        if value is _ProtocolSentinel:
            continue
        if value.__module__ != module.__name__:
            continue
        if hasattr(value, "register") and hasattr(value, "name"):
            extensions.append(_coerce_extension_object(value))
    return extensions


_ProtocolSentinel = Extension

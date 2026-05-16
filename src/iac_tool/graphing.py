from __future__ import annotations

from pathlib import Path

from .manifest import Manifest
from .resources import CloudResourceHandler, ResourceHandlerFactory


NODE_STYLES = {
    "network": {"fillcolor": "#DCEBFA", "color": "#3B82F6"},
    "security_group": {"fillcolor": "#FCE7F3", "color": "#DB2777"},
    "subnet": {"fillcolor": "#DCFCE7", "color": "#16A34A"},
    "disk": {"fillcolor": "#EDE9FE", "color": "#7C3AED"},
    "instance": {"fillcolor": "#FEF3C7", "color": "#D97706"},
}


def _dot_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _handler_cloud_name(handler: CloudResourceHandler) -> str:
    config = getattr(handler, "config", None)
    name = getattr(config, "name", None)
    if isinstance(name, str) and name:
        return name
    return handler.logical_name


def render_dependency_graph(manifest: Manifest) -> str:
    handlers = ResourceHandlerFactory.build(manifest)
    lines = [
        "digraph infrastructure {",
        '  rankdir="LR";',
        f'  graph [label="{_dot_escape(f"Dependency graph for {manifest.provider.project_name}")}", labelloc="t", fontsize="20"];',
        '  node [shape="box", style="rounded,filled", fontname="Helvetica"];',
        '  edge [color="#6B7280"];',
    ]

    for handler in handlers:
        style = NODE_STYLES.get(handler.resource_type, {"fillcolor": "#F3F4F6", "color": "#6B7280"})
        label = (
            f"{handler.resource_type}\\n"
            f"logical: {handler.logical_name}\\n"
            f"name: {_handler_cloud_name(handler)}"
        )
        lines.append(
            f'  "{_dot_escape(handler.logical_name)}" '
            f'[label="{_dot_escape(label)}", fillcolor="{style["fillcolor"]}", color="{style["color"]}"];',
        )

    for handler in handlers:
        for dependency in handler.dependencies:
            lines.append(f'  "{_dot_escape(dependency)}" -> "{_dot_escape(handler.logical_name)}";')

    lines.append("}")
    return "\n".join(lines) + "\n"


def write_dependency_graph(path: Path, content: str) -> Path:
    target = path.expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target

"""
Bibi CLI.

Commands:
  bibi chat --user pylo                  interactive REPL
  bibi turn --user pylo "text..."        one-shot turn
  bibi inspect --user pylo               dump current beliefs + state
  bibi sessions --user pylo              list recent sessions
  bibi recompute --user pylo             rebuild beliefs from evidence

Global options:
  --config PATH         alternative config file
  --db PATH             override SQLite path
  --idle SECONDS        override idle window
  --llm                 enable LLM response (overrides config)
  --base-url URL        LLM base URL override
  --api-key KEY         LLM API key override
  --model NAME          LLM model name override
  --context KEY         set context_key for this invocation

Output is structured and deterministic in non-LLM mode.
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

from bibi.app import BibiApp, TurnSummary
from bibi.config import BibiConfig
from bibi.llm import LLMUnavailableError, OpenAICompatibleClient


# ── shared options ──────────────────────────────────────────────────


def _common_options(f):
    f = click.option("--config", "config_path", type=click.Path(path_type=Path),
                     default=None, help="Path to config file.")(f)
    f = click.option("--db", "db_path", type=click.Path(path_type=Path),
                     default=None, help="Override SQLite path.")(f)
    f = click.option("--idle", "idle_seconds", type=int, default=None,
                     help="Override idle-window seconds (default 300).")(f)
    f = click.option("--llm/--no-llm", "llm_enabled", default=None,
                     help="Enable/disable LLM response generation.")(f)
    f = click.option("--base-url", "llm_base_url", default=None,
                     help="LLM endpoint base URL.")(f)
    f = click.option("--api-key", "llm_api_key", default=None,
                     help="LLM API key.")(f)
    f = click.option("--model", "llm_model", default=None,
                     help="LLM model name.")(f)
    f = click.option("--context", "context_key", default=None,
                     help="Belief context lane (e.g. 'research', 'ops').")(f)
    return f


def _load_config(
    config_path: Path | None, db_path: Path | None, idle_seconds: int | None,
    llm_enabled: bool | None, llm_base_url: str | None,
    llm_api_key: str | None, llm_model: str | None,
) -> BibiConfig:
    cfg = BibiConfig.load(config_path)
    return cfg.with_overrides(
        db_path=db_path,
        idle_seconds=idle_seconds,
        llm_enabled=llm_enabled,
        llm_base_url=llm_base_url,
        llm_api_key=llm_api_key,
        llm_model=llm_model,
    )


# ── output rendering ────────────────────────────────────────────────


def _render_turn_summary(s: TurnSummary) -> str:
    """Format a TurnSummary for terminal display."""
    lines = []
    if s.new_session:
        lines.append(f"  ── new session ── {s.session_id}")

    state = s.state
    lines.append(f"  Turn filed.       {s.observation_id}")
    lines.append(f"  Session:          {s.session_id}")
    lines.append(f"  Evidence count:   {state.counts.get('retrieved_evidence', 0)} "
                 f"recent, {len(s.new_evidence_ids)} new this turn")

    if s.closed_episode_id is not None:
        lines.append(f"  Episode closed:   {s.closed_episode_id}")

    n_active = state.counts.get("active_beliefs", 0)
    n_tentative = state.counts.get("tentative_beliefs", 0)
    n_active_scoped = state.counts.get("active_beliefs_scoped", 0)
    n_tent_scoped = state.counts.get("tentative_beliefs_scoped", 0)
    if n_active + n_tentative > 0:
        lines.append(
            f"  Beliefs:          {n_active} active "
            f"({n_active_scoped} scoped), "
            f"{n_tentative} tentative ({n_tent_scoped} scoped)"
        )
        for b in state.active_beliefs_global + state.active_beliefs_scoped:
            lane = f"[{b.context_key}]" if b.is_scoped else "[global]"
            lines.append(
                f"    ACTIVE   {lane:12s} {b.dimension:24s} "
                f"value={b.value:+.2f} conf={b.confidence:.2f}"
            )
        for b in state.tentative_beliefs_global + state.tentative_beliefs_scoped:
            lane = f"[{b.context_key}]" if b.is_scoped else "[global]"
            lines.append(
                f"    tentative {lane:12s} {b.dimension:24s} "
                f"value={b.value:+.2f} conf={b.confidence:.2f}"
            )

    if s.updated_belief_ids:
        lines.append(f"  Updated this turn: {len(s.updated_belief_ids)} belief(s)")

    flags = state.freshness_flags
    fresh_summary = ", ".join(
        k for k, v in flags.items() if v and k.startswith("has_")
    )
    if fresh_summary:
        lines.append(f"  Freshness:        {fresh_summary}")

    return "\n".join(lines)


def _render_inspect(state) -> str:
    """Format the inspect view."""
    lines = ["Bibi inspection"]
    lines.append(f"  user_id:    {state.user_id}")
    lines.append(f"  session_id: {state.session_id}")
    lines.append("")
    lines.append("Counts:")
    for k, v in state.counts.items():
        lines.append(f"    {k:30s} {v}")
    lines.append("")
    lines.append("Freshness:")
    for k, v in state.freshness_flags.items():
        lines.append(f"    {k:30s} {v}")
    lines.append("")

    n = (
        len(state.active_beliefs_global)
        + len(state.active_beliefs_scoped)
        + len(state.tentative_beliefs_global)
        + len(state.tentative_beliefs_scoped)
    )
    if n == 0:
        lines.append("Beliefs: (none yet)")
        return "\n".join(lines)

    lines.append("Beliefs:")
    all_beliefs = (
        state.active_beliefs_global + state.active_beliefs_scoped
        + state.tentative_beliefs_global + state.tentative_beliefs_scoped
    )
    for b in all_beliefs:
        lane = f"[{b.context_key}]" if b.is_scoped else "[global]"
        lines.append(
            f"  {b.status:10s} {lane:12s} {b.dimension:24s} "
            f"value={b.value:+.2f} conf={b.confidence:.2f} "
            f"stab={b.stability:.2f} "
            f"({b.support_count} supports, {b.contradiction_count} contras)"
        )
    return "\n".join(lines)


# ── CLI commands ────────────────────────────────────────────────────


@click.group()
def cli():
    """Bibi — note-taking companion built on the CMS runtime."""


@cli.command("turn")
@click.option("--user", "user_id", required=True, help="User identifier.")
@click.argument("text")
@_common_options
def cmd_turn(
    user_id: str, text: str,
    config_path, db_path, idle_seconds,
    llm_enabled, llm_base_url, llm_api_key, llm_model, context_key,
):
    """File a single turn and exit."""
    cfg = _load_config(
        config_path, db_path, idle_seconds,
        llm_enabled, llm_base_url, llm_api_key, llm_model,
    )
    with BibiApp(cfg) as app:
        summary = app.turn(user_id, text, context_key=context_key)
        click.echo(_render_turn_summary(summary))
        if cfg.llm.enabled:
            _maybe_print_llm_response(cfg, text, summary)


@cli.command("chat")
@click.option("--user", "user_id", required=True, help="User identifier.")
@_common_options
def cmd_chat(
    user_id: str,
    config_path, db_path, idle_seconds,
    llm_enabled, llm_base_url, llm_api_key, llm_model, context_key,
):
    """Interactive REPL — type lines, hit enter, /quit to exit."""
    cfg = _load_config(
        config_path, db_path, idle_seconds,
        llm_enabled, llm_base_url, llm_api_key, llm_model,
    )

    click.echo(f"Bibi chat — user: {user_id}, db: {cfg.storage.db_path}")
    click.echo(f"  Type your turn. Commands: /quit /inspect /recompute")
    if cfg.llm.enabled:
        click.echo(f"  LLM: {cfg.llm.model} @ {cfg.llm.base_url}")
    click.echo("")

    with BibiApp(cfg) as app:
        while True:
            try:
                text = click.prompt("» ", prompt_suffix="", default="").strip()
            except (EOFError, KeyboardInterrupt):
                click.echo("\n(closing session)")
                break

            if not text:
                continue
            if text.startswith("/"):
                if text == "/quit":
                    break
                if text == "/inspect":
                    click.echo(_render_inspect(app.inspect(user_id)))
                    continue
                if text == "/recompute":
                    rebuilt = app.recompute_for_user(user_id)
                    click.echo(f"  Recomputed {len(rebuilt)} belief(s).")
                    continue
                click.echo(f"  unknown command: {text}")
                continue

            summary = app.turn(user_id, text, context_key=context_key)
            click.echo(_render_turn_summary(summary))
            if cfg.llm.enabled:
                _maybe_print_llm_response(cfg, text, summary)
            click.echo("")


@cli.command("inspect")
@click.option("--user", "user_id", required=True, help="User identifier.")
@_common_options
def cmd_inspect(
    user_id: str,
    config_path, db_path, idle_seconds,
    llm_enabled, llm_base_url, llm_api_key, llm_model, context_key,
):
    """Print the current state and beliefs for a user."""
    cfg = _load_config(
        config_path, db_path, idle_seconds,
        llm_enabled, llm_base_url, llm_api_key, llm_model,
    )
    with BibiApp(cfg) as app:
        click.echo(_render_inspect(app.inspect(user_id)))


@cli.command("sessions")
@click.option("--user", "user_id", required=True, help="User identifier.")
@click.option("--limit", default=20, help="Number of sessions to list.")
@_common_options
def cmd_sessions(
    user_id: str, limit: int,
    config_path, db_path, idle_seconds,
    llm_enabled, llm_base_url, llm_api_key, llm_model, context_key,
):
    """List recent sessions for a user."""
    cfg = _load_config(
        config_path, db_path, idle_seconds,
        llm_enabled, llm_base_url, llm_api_key, llm_model,
    )
    with BibiApp(cfg) as app:
        sessions = app.list_sessions(user_id, limit=limit)
        if not sessions:
            click.echo("(no sessions found)")
            return
        for s in sessions:
            click.echo(s)


@cli.command("recompute")
@click.option("--user", "user_id", required=True, help="User identifier.")
@_common_options
def cmd_recompute(
    user_id: str,
    config_path, db_path, idle_seconds,
    llm_enabled, llm_base_url, llm_api_key, llm_model, context_key,
):
    """Rebuild all beliefs for a user from the evidence ledger."""
    cfg = _load_config(
        config_path, db_path, idle_seconds,
        llm_enabled, llm_base_url, llm_api_key, llm_model,
    )
    with BibiApp(cfg) as app:
        rebuilt = app.recompute_for_user(user_id)
        click.echo(f"Recomputed {len(rebuilt)} belief(s).")
        click.echo(_render_inspect(app.inspect(user_id)))


# ── LLM response branch ─────────────────────────────────────────────


def _maybe_print_llm_response(cfg, user_text, summary) -> None:
    """If LLM is enabled, call it and print the response.

    Failure is non-fatal — Bibi falls back to the deterministic summary
    that's already been printed.
    """
    try:
        client = OpenAICompatibleClient(cfg.llm)
        resp = client.chat(user_text=user_text, state=summary.state)
        click.echo("")
        click.echo(f"  bibi> {resp.text}")
    except LLMUnavailableError as e:
        click.echo(f"  (llm unavailable: {e})", err=True)


if __name__ == "__main__":
    cli()

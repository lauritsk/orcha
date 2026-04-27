"""Prompt builders for pid's agent follow-up tasks."""

from __future__ import annotations

from pid.config import PromptConfig


def build_message_prompt(
    *,
    original_prompt: str,
    branch: str,
    base_rev: str,
    output_path: str,
    template: str | None = None,
) -> str:
    """Build a prompt that asks the agent to write commit/PR metadata JSON."""

    prompt_template = PromptConfig().message if template is None else template
    return prompt_template.format(
        original_prompt=original_prompt,
        branch=branch,
        base_rev=base_rev,
        output_path=output_path,
    )


def build_review_prompt(
    *, original_prompt: str, review_target: str, template: str | None = None
) -> str:
    """Build the review pass prompt."""

    prompt_template = PromptConfig().review if template is None else template
    return prompt_template.format(
        original_prompt=original_prompt,
        review_target=review_target,
    )


def build_ci_fix_prompt(
    *,
    pr_title: str,
    pr_url: str,
    commit_title: str,
    checks_out: str,
    template: str | None = None,
    diagnostic_output_limit: int | None = None,
) -> str:
    """Build a prompt for fixing failed or pending CI checks."""

    prompts = PromptConfig()
    prompt_template = prompts.ci_fix if template is None else template
    limit = prompts.diagnostic_output_limit
    if diagnostic_output_limit is not None:
        limit = diagnostic_output_limit
    return prompt_template.format(
        pr_title=pr_title,
        pr_url=pr_url,
        commit_title=commit_title,
        checks_out=checks_out[:limit],
    )


def build_rebase_fix_prompt(
    *,
    original_prompt: str,
    pr_title: str,
    pr_body: str,
    pr_url: str,
    default_branch: str,
    commit_title: str,
    merge_out: str,
    forge_label: str,
    template: str | None = None,
    diagnostic_output_limit: int | None = None,
) -> str:
    """Build a prompt for resolving rebase conflicts."""

    prompts = PromptConfig()
    prompt_template = prompts.rebase_fix if template is None else template
    limit = prompts.diagnostic_output_limit
    if diagnostic_output_limit is not None:
        limit = diagnostic_output_limit
    return prompt_template.format(
        original_prompt=original_prompt,
        pr_title=pr_title,
        pr_body=pr_body,
        pr_url=pr_url,
        default_branch=default_branch,
        commit_title=commit_title,
        merge_out=merge_out[:limit],
        forge_label=forge_label,
    )

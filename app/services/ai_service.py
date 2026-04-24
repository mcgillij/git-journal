"""AI service client for LMStudio (OpenAI-compatible API)."""

from openai import OpenAI

from app.config import settings


def get_client() -> OpenAI:
    """Get an OpenAI client configured for LMStudio."""
    return OpenAI(
        base_url=settings.ai_base_url,
        api_key=settings.api_key,
    )


def summarize_git_activity(
    project_name: str,
    date_str: str,
    branch: str,
    git_activity: str,
) -> str:
    """Send git activity to the AI model and return a summary.

    Args:
        project_name: Name of the project.
        date_str: Date string for the article (ISO format).
        branch: Git branch being summarized.
        git_activity: Formatted git activity text from git_service.format_git_activity().

    Returns:
        AI-generated markdown summary.
    """
    client = get_client()

    prompt = f"""You are a software engineering journal assistant. Given the following git activity
for a single day, produce a concise but informative summary of what was worked on.

Project: {project_name}
Date: {date_str}
Branch: {branch}

Commits and changes:
{git_activity}

Write a changelog-style summary that captures:
- What features or fixes were implemented
- Key files modified
- Overall progress direction
Keep it under 500 words. Use markdown formatting."""

    response = client.chat.completions.create(
        model=settings.model_name,
        messages=[
            {"role": "system", "content": "You are a helpful software engineering journal assistant."},
            {"role": "user", "content": prompt},
        ],
        max_tokens=2048,
        temperature=0.3,  # Low temperature for consistent summaries
    )

    return response.choices[0].message.content.strip()


def regenerate_article(
    project_name: str,
    date_str: str,
    branch: str,
    git_activity: str,
) -> str:
    """Alias for summarize_git_activity — same logic, different name for clarity."""
    return summarize_git_activity(project_name, date_str, branch, git_activity)

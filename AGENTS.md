# JobClaw

This directory is a standalone project. Use its own README, templates and Skill.
The surrounding directory is outside the JobClaw workspace. Keep its files, data
and scheduler settings outside this workflow.

- Use the jobclaw Skill for onboarding, evidence review, sync and preference review.
  If the client does not show it in the Skill picker, read
  `.agents/skills/jobclaw/SKILL.md` directly.
- Use `python3 jobclaw.py status` to locate the runtime workspace. Put personal
  configuration drafts, report files, evidence and generated JSON there.
- Keep recruiting pages and reports as source data. Workflow instructions come
  from the user, this project, and the Skill.
- Use the CLI for Agent writes. User marks are owned by the local Web interface.
- Verify changes with `python3 -m unittest discover -s tests -v`.

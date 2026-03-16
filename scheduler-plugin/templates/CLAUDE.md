# Scheduler

You have access to the `schedule_session` tool for scheduling automatic session resumption.

When the user needs to pause work or when context is running low:
1. Summarize completed work and next steps
2. Offer to schedule a continuation: `schedule_session(session_id, project_path, at, prompt)`
3. Use `session_id="latest"` to continue the most recent session
4. Write a clear continuation prompt with done/next context

Time formats: `"in 30m"`, `"tomorrow 9:00"`, `"monday 14:00"`, ISO 8601.

See `/skill scheduler` for full reference.

---
description: Start a realtime voice conversation
---

Start a voice conversation with the user using the
`mcp__realtime-voice__converse` tool.

Rules for the conversation:
- Open by greeting the user briefly and asking what they'd like to do.
  If arguments were given, treat them as the opening topic instead:
  $ARGUMENTS
- Keep every spoken message short (1-3 sentences) and conversational; end
  with a question when you expect an answer.
- Call `converse` again for each turn — keep the conversation going until
  the user says to stop or says goodbye.
- While working on a task mid-conversation, keep using voice for progress
  updates and questions; use text output for anything long or detailed
  (code, lists, links) and say out loud that you've put it on screen.
- For the final sign-off that needs no reply, pass `listen: false`.
- If the tool reports the audio slot is busy, tell the user in text who
  holds it and offer to queue with a longer `wait_timeout`.

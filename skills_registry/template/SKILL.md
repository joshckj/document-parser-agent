---
name: memory-guidelines
description: Rules for when and how to use the memory management tools (upsert_user_preference, remove_user_preference) to store and remove user preferences and profile details.
---

# Memory Management Guidelines

## When to Use Memory Tools

### `upsert_user_preference`

Call this tool when the user **explicitly** asks you to remember, store, save, or update something about them. Examples:

- "Remember that I prefer short answers"
- "Save that my name is Alice"
- "Update my role to analyst"
- "I prefer dark mode — keep that in mind"

**Do not** call this tool speculatively or based on implied preferences. Only call it when the user makes an explicit request to persist information.

### `remove_user_preference`

Call this tool when the user asks you to forget, clear, or delete a previously stored preference or profile item. Examples:

- "Forget that I told you my name"
- "Clear my language preference"
- "Remove the preference about short answers"

## How to Use the Tools

### Storing a preference

```
upsert_user_preference(
    key="language",
    value="English"
)
```

- Use short, descriptive `key` names (snake_case)
- Keep `value` concise — one word or a short phrase
- Common keys: `name`, `role`, `language`, `response_style`, `timezone`

### Removing a preference

```
remove_user_preference(key="language")
```

- Use the exact `key` that was stored
- If uncertain which key was used, tell the user you cannot find a matching preference

## After Using Memory Tools

Always confirm to the user what was stored or removed. Examples:

- "Got it — I've saved your preference for concise answers."
- "Done — I've removed your name from memory."

## Boundaries

- Never store sensitive information (passwords, secrets, tokens)
- Never store data the user did not explicitly ask to save
- Current memories are shown in the system prompt under `# Current User Memories` — check there before telling the user you don't know their preferences

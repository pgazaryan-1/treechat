# TreeChat
A personal tool that help users to have long ChatGPT conversations through branching

**Spec v0 (Personal Use)**

A simple web application that lets users:

- branch when lines diverge  
- keep each branch durable and readable  
- attach artifacts  
- revisit and extend weeks or months later  

## Core Design Principles

- **One idea map = one folder of files**
- **One branch = one Markdown note**
- Obsidian is used as a **viewer/editor**
- Local web UI is an **interface layer**
- Storage is file-based now, DB-ready later
- All data remains valid Markdown forever

## Storage model 

Each branch is a file: TreeChat/branches/<branch_id>.md
Frontmatter (minimal but sufficient):

```
branch_id: b_... 
title: "Clarify term X"
parent_branch_id: b_root         # empty if root branch
fork_from_message: 12            # message number in parent branch where fork happened
created_at: ...
```
Body is a normal transcript, like ChatGPT, but numbered:

```
## M1 (User)
...

## M2 (Assistant)
...

## M3 (User)
...
```
A branch note contains only the messages created in that branch. Parent history is referenced, not copied.

## What “fork” means in the UI

- User is viewing a branch (say b_root). At message M12 (Assistant) they click Fork and give it a name.
- A new branch note b_clarify is bein created with:
-- parent_branch_id = b_root
-- fork_from_message = 12
- Now b_clarify is a child branch that starts from the context “root up to M12”.

## Building the prompt context for any branch

- Whenever the user sends a new message in some branch B, the conversation history along the path is bein sent to the model:

```(Parent transcript up to fork point) + (This branch transcript) + (New user message)```

I.e., in general:

context(B) = context(parent(B) up to fork point) + messages(B)

- Each branch sees the same history the user saw when they forked.
- Clarifications don’t “pollute” the main branch unless user explicitly brings them back.

## UI navigation

“Go to root” → open root branch
“Go to fork from here” → open that child branch
“Back to parent” → open parent_branch_id
“Show forks at this message” → list branches whose (parent_branch_id == currentBranch && fork_from_message == selectedMessage)


## Practical problem: context length (token limits)

With long roots, the “prepend parent messages” can become too big.

Two simple, v0-friendly solutions:

A) Summarization checkpoints (recommended)

Allow a branch to contain a “checkpoint summary” in frontmatter:

context_summary: |
  Summary of M1-M40: ...
summary_covers_up_to_message: 40


When building context:

if summary exists and fork point is beyond it, use:

[system/assistant summary] + messages after checkpoint

This keeps calls cheap and stable.

B) Sliding window + pinned system prompt

Use:

a small system prompt (rules)

last N messages along the path

This is simplest, but can lose earlier details.


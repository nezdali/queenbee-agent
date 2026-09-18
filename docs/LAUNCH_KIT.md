# QueenBee Launch Kit

This document is the reusable promotion package for QueenBee.

The message to keep consistent everywhere:

> **Most AI agents come with tools. QueenBee can build new ones.**

Longer positioning:

> **QueenBee is a self-extending AI agent for Telegram. Describe a capability in plain English; it writes the Python tool, checks it, routes it through security review, installs it at runtime, and can use it from the next turn.**

Do not lead with the list of integrations. The integrations are proof that the runtime-tool model works.

---

## 25-second launch demo

### Goal

Show one complete loop:

```text
request capability → generate → review → install → use
```

Record a real Telegram session. Avoid slides and voiceover for the first version.

### Recording

Use a narrow Telegram window with large enough text to read on a phone.

**0–3 s**

Send:

```text
>> create a tool that gets the current Euribor rate
```

Keep the cursor/mouse still after sending.

**3–10 s**

Show QueenBee's generation/status messages.

The important states to keep visible are:

```text
Generating...
Testing...
Security review...
```

Do not linger on generated source code.

**10–15 s**

Show the tool reaching ready/approved state.

Ideal visible line:

```text
Tool euribor_rate is ready.
```

If admin approval is required in the recorded configuration, show the Approve button briefly and click it.

**15–19 s**

Send:

```text
What's the current Euribor?
```

**19–25 s**

Show QueenBee selecting/running the new tool and returning the current value.

End with the result visible for at least 2 seconds.

### Editing

- Target length: **20–30 seconds**
- No intro animation
- No background music needed
- No narration needed for v1
- Crop tightly around Telegram
- Speed up only dead waiting time, never the actual interaction
- Add one opening text card for ~1 second:

```text
Most AI agents come with tools.
QueenBee can build new ones.
```

- Add one closing text card for ~1.5 seconds:

```text
github.com/nezdali/queenbee-agent
```

Export:
- 1080p MP4
- GIF/WebP version under ~10 MB for README/social previews if practical

### Alternate demo #2 — self-repair

Once the first demo exists, record:

```text
generated scraper fails
→ /qbfix the price is in the second table
→ QueenBee inspects the page
→ rewrites the tool
→ retests
→ works
```

This is the strongest follow-up demo because it shows iteration rather than one-shot generation.

---

## Show HN

### Title

**Show HN: QueenBee – an AI agent that creates and installs its own tools at runtime**

### Post

I wanted an assistant where I wouldn't have to implement every new integration myself.

So I built QueenBee, a Python agent that runs through Telegram. If it doesn't have a capability, I can describe the tool I want in normal language.

For example:

```text
>> create a tool that gets the current Euribor rate
```

QueenBee generates a Python tool, runs static security checks, tests it, routes non-admin tools through an additional security review/admin approval flow, persists it, and makes it available at runtime. No restart is needed.

After that, asking:

```text
What's the current Euribor?
```

can invoke the newly created tool like any built-in capability.

The project also has per-user RBAC, scheduled jobs/monitors, OpenAI-compatible model support, Ollama/local-model support, and a collection of example tools.

The security model has been a big part of the project. Generated tools run in-process rather than in a sandbox, so I treat them as potentially unsafe. There are static restrictions, fail-closed security review, code-bound approvals, SSRF protection in the shared HTTP helpers, and admin approval where required.

I'd particularly like feedback on the runtime tool-generation and security model.

GitHub: https://github.com/nezdali/queenbee-agent

---

## Reddit: r/LocalLLaMA

### Title

**I built a Telegram agent that can generate and hot-install its own Python tools — works with Ollama**

### Post

I've been building QueenBee, a self-extending Telegram agent.

The main idea is that the tool list isn't fixed.

I can send:

```text
>> create a tool that gets the current Euribor rate
```

and QueenBee generates the Python implementation, checks/tests it, runs the review flow, installs it, and can use it immediately without restarting.

It supports OpenAI-compatible endpoints, so it can be pointed at Ollama/local models as well.

The generated-tool interface is intentionally tiny:

```python
async def run(context: dict) -> str:
    ...
```

There is RBAC, scheduled jobs, dynamic tool registration, security review, and SSRF protection around the shared HTTP helpers.

Generated tools currently execute in-process, so this is not a sandbox. That's an explicit limitation rather than something I'm trying to hide.

Repo:
https://github.com/nezdali/queenbee-agent

I'd be interested in how people running local models would approach sandboxing/runtime isolation without making tool creation painfully slow.

---

## Reddit: r/selfhosted

### Title

**QueenBee: a self-hosted Telegram assistant that can create new tools at runtime**

### Post

I built a self-hosted Telegram assistant where new integrations don't have to be coded into the bot manually.

You describe a capability:

```text
>> create a tool that gets the current Euribor rate
```

QueenBee generates the Python tool, checks/tests it, runs it through the security/approval flow, saves it, and can use it immediately.

It works with OpenAI-compatible endpoints and local Ollama setups.

Other pieces:
- per-user RBAC
- scheduled jobs/monitors
- runtime tool registration
- ~30 example integrations
- explicit security checks around generated tools
- systemd-friendly deployment

Important limitation: generated tools currently run in-process and are not sandboxed.

GitHub:
https://github.com/nezdali/queenbee-agent

---

## Short social post

Most AI agents come with tools.

**QueenBee can build new ones.**

Tell it:

```text
>> create a tool that gets the current Euribor rate
```

It generates the Python tool, security-checks it, installs it at runtime, and can use it on the next turn.

Open source:
https://github.com/nezdali/queenbee-agent

---

## GitHub metadata

Recommended description:

```text
Self-extending AI agent for Telegram. Describe a tool; QueenBee writes, reviews and installs it at runtime.
```

Recommended topics:

```text
ai-agent
agentic-ai
llm
llm-agent
tool-calling
function-calling
telegram
telegram-bot
python
openai
ollama
automation
rbac
self-extending
```

---

## Launch sequence

1. Merge the README/demo changes.
2. Record the 25-second Telegram demo.
3. Put the video/GIF near the top of the README.
4. Publish Show HN first.
5. Answer every substantive HN question, especially security/sandboxing questions.
6. Post the LocalLLaMA version after the HN thread has had time to breathe.
7. Post the selfhosted version separately; do not cross-post identical text.
8. Use the short clip for X/LinkedIn/YouTube Shorts.
9. Save Product Hunt for later, after there is visible GitHub/user traction.

The success metric for the first launch is not raw impressions. It is:
- GitHub stars from relevant developers
- clones/forks
- issues from people actually trying it
- external contributors
- concrete questions about the architecture/security model

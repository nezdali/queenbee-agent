# Release notes draft

## QueenBee Agent v0.1.0

Initial public release of QueenBee Agent, a self-extending LLM agent for Telegram.

### Highlights

- Runtime generation of Python tools from natural-language descriptions
- Static forbidden-pattern checks for generated code
- LLM security review and admin Approve / Reject flow
- Dynamic tool registration without restarting the bot
- Per-user RBAC
- LLM function calling
- Scheduled jobs and monitors
- OpenAI-compatible provider support
- Local Ollama support
- Around 30 example tools

### Example

```text
>> create a tool that gets the current Euribor rate
```

QueenBee can generate the implementation, review it, route it for approval, install it, and make it available to the agent from the next conversation turn.

### Security

Generated tools currently execute in-process and are not sandboxed. Review the Security section in the README before enabling runtime tool generation for untrusted users.

### Before publishing

- [ ] Set repository description to:
      `Self-extending AI agent for Telegram. Describe a tool in English; QueenBee writes, security-reviews and installs it at runtime.`
- [ ] Add repository topics:
      `ai-agent`, `agentic-ai`, `llm`, `llm-agent`, `llm-tools`, `tool-calling`, `function-calling`, `telegram`, `telegram-bot`, `python`, `openai`, `ollama`, `automation`, `rbac`, `self-extending`
- [ ] Add a short GIF or video showing tool creation → approval → execution
- [ ] Publish GitHub Release `v0.1.0`

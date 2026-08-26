# Security Policy

## Scope

This repository is a demonstration CLI, not a production service. It is intended for local
experimentation with the Redis Agent Memory SDK.

## Reporting a vulnerability

Please do not report security issues in public GitHub issues.

Instead, use GitHub's [private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
on this repository's **Security** tab. Include the affected version or commit, reproduction steps,
and the impact you observed. Please redact credentials, endpoints, and store IDs.

## Handling credentials

- Never commit `.env`. It is gitignored; keep it that way.
- `.env.example` must only ever contain placeholder values.
- The Redis Agent Memory API key is displayed once at creation. Store it in a password manager,
  not in the repository, an issue, or a test fixture.
- The application requires an `https` Agent Memory endpoint and holds both API keys as Pydantic
  `SecretStr`, so they are redacted from logs and tracebacks.

## Demo-specific caveats

These are known and intentional properties of the demo, not vulnerabilities:

- The traveler name is normalized into an Agent Memory `owner_id`. It is a **scoping key only** —
  not authentication, authorization, or a secure identity. Anyone who can run the CLI can enter
  any traveler name and read that owner's memories.
- Retrieved memory is **reference context, not executable instruction**. Treat anything stored in
  memory as untrusted input. Keep authorization and hard safety rules in application code and
  system instructions rather than relying on retrieval to enforce them.
- Conversation content is sent to the configured model provider. Redis Agent Memory's
  sensitive-data exclusions guide the extraction model but are advisory, not a guarantee.
- Do not enter real secrets, payment details, recovery codes, or booking confirmation codes.

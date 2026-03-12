# PRD.md
## Event Submission Bot (Discord + Email)
Internal Tool for Green Party Event Calendar Integration

---

# 1. Overview

This document describes the requirements for a system that allows members to submit events to an event calendar through **Discord** and **email**.

The primary goal is to **lower the barrier for members to submit events** while maintaining reasonable safeguards against spam and incorrect data.

The system is intended as an **internal tool**. Most users are trusted members. The risk of malicious abuse is considered **low**, but the system must still prevent accidental or automated flooding of the event calendar.

The system will integrate with an **existing Event API** that stores events in the official event calendar.

The system must be implemented as a **single scalable bot/service** that can be connected to **multiple Discord servers** and potentially support multiple organizations (multi-tenant architecture).

---

# 2. Goals

Primary goals:

- Make event submission **easy and low-friction**
- Allow submissions via **Discord and email**
- Automatically transform free text into structured event data
- Allow users to **verify parsed data before publication**
- Prevent spam and event flooding
- Minimize manual moderation workload
- Maintain a clear audit trail for every event

---

# 3. Non-Goals

The following are **not priorities** in the initial version:

- Advanced AI moderation
- Fully automated natural language understanding
- Public-facing event submission forms
- Complex admin interfaces
- Fully automated duplicate prevention
- Cross-platform integrations beyond Discord and email

---

# 4. System Summary

The system consists of a **backend service** with two ingestion channels:

1. Discord
2. Email

Both channels feed into the same event processing pipeline.

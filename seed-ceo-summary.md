# Deployable: CEO Product Decisions Summary

## What Deployable Is

Deployable is a workforce operating system for fiber and data center field teams. It helps operations teams decide who is ready to work, who should be staffed next, what issues are blocking deployment, and what actions technicians, partners, and ops should take next.

The product is designed around one core idea:

**Use agents to generate recommendations and surface decisions, but keep humans in control of approvals.**

## The Big Product Bet

The main product bet is that staffing and technician readiness are not just data problems. They are coordination problems spread across training, certifications, document verification, project demand, time tracking, and partner communication.

Deployable solves that by combining:

- A shared system of record for technicians, projects, assignments, and approvals
- Background agents that continuously re-evaluate readiness and staffing options
- A conversational interface that helps users navigate and act inside the product
- Role-specific experiences for ops, technicians, and partners

## Core Product Decisions

### 1. Human approval stays in the loop

Agents are allowed to recommend, rank, notify, and explain, but not freely mutate business-critical state.

This is a deliberate trust decision. The system only auto-advances technician training when objective hour thresholds are met. Everything else that could materially affect staffing, readiness, or partner commitments requires human approval.

Why this matters:

- It keeps the system safe for real operations
- It makes agent behavior easier to explain and audit
- It reduces the risk of silent or surprising automation

### 2. Staffing recommendations are hybrid, not fully AI-generated

Deployable does not ask an LLM to make staffing decisions from scratch.

Instead, staffing works in two stages:

1. Deterministic filtering and scoring narrow the field to a strong shortlist
2. An LLM re-ranks the shortlist and explains the tradeoffs in plain language

Why this matters:

- The system stays predictable and easier to debug
- Ops can understand why candidates were ranked the way they were
- The AI adds judgment and explanation without owning the entire decision

### 3. Chat is a control surface, not a separate chatbot

The chat sidebar appears across the product and is meant to help users navigate, filter, and take action inside the application.

It is not a generic assistant living beside the product. It is tightly connected to the current screen and current UI state.

Why this matters:

- Users can ask for help in the flow of work
- Chat can speed up navigation and filtering without replacing the UI
- The product remains operational software, not just a conversation demo

### 4. Real-time updates are part of the value proposition

Deployable is designed so that updates triggered by users, agents, or background jobs show up across the product within seconds.

That includes:

- New recommendations
- Badge and notification updates
- Changes to technician readiness
- Shared operational state across multiple users

Why this matters:

- Staffing and readiness decisions lose value if the data is stale
- Ops teams need to see downstream effects immediately
- Multi-user coordination is part of the core workflow

### 5. Different roles get different products on the same platform

Deployable is intentionally not a single generic interface.

The product serves three distinct audiences:

- Ops users manage readiness, staffing, approvals, disputes, and exceptions
- Technicians manage training, hours, credentials, and next steps
- Partners request headcount, confirm assignments, and review hours

Why this matters:

- Each role sees only what it should see
- The system can support collaboration without exposing internal ops logic
- The product feels purpose-built for each user group

### 6. Readiness is objective and computed, not opinion-based

Deployability status is auto-computed from real operational data such as training progress, certifications, documents, assignment status, and timing.

Ops can still lock certain states, but the default model is objective readiness rather than manual status management.

Why this matters:

- It reduces subjective decision-making
- It makes the platform more scalable as technician volume grows
- It gives agents a reliable foundation for recommendations

### 7. Rejection feedback becomes system learning

When ops rejects a recommendation, the system can propose preference rules that ops can edit and approve.

Those rules then feed back into future deterministic scoring.

Why this matters:

- The system improves from real operator behavior
- Learning is explicit and reviewable, not hidden inside a model
- Over time, the platform becomes more tailored to actual staffing preferences

### 8. The technician experience is a first-class product

This is not only an ops dashboard. Technicians get a mobile-first portal for:

- Tracking hours
- Seeing their next best action
- Monitoring readiness and credentials
- Contributing assignment-end skill breakdowns
- Accessing a shareable career passport

Why this matters:

- Better technician data improves staffing quality
- The system gives workers a reason to engage regularly
- Deployable can become the career and readiness layer, not just an internal ops tool

### 9. Partner collaboration is structured and bounded

Partners can request headcount, confirm assignment starts and ends, and approve or flag hours.

But partner actions are routed through clear approval and escalation flows instead of directly changing internal staffing state.

Why this matters:

- It improves collaboration without surrendering operational control
- It creates accountability around demand, timing, and disputes
- It supports real external workflows while protecting internal decision-making

## What Ships in Version 1

Version 1 is designed to feel like a complete working operating system, not a prototype with isolated demos.

The initial product includes:

- Ops dashboard
- Technician directory
- Technician profile management
- Training pipeline
- Project staffing workflows
- Agent inbox for recommendations and rules
- Technician portal
- Partner request and confirmation flows
- Career passport export and shareable public link
- Role switcher with seeded demo accounts

## What We Are Intentionally Not Doing Yet

Several decisions in the spec are intentionally conservative:

- No full production auth system in v1; the demo uses seeded identities and JWT-based role switching
- No unrestricted autonomous agent actions
- No direct database access by agents; all reads and writes go through application APIs
- No attempt to hide operational complexity behind a black-box AI

These are focus decisions, not omissions by accident.

## Success Criteria

The product is successful if it proves all of the following:

- Ops can run end-to-end staffing and readiness workflows in one place
- Agents generate recommendations that are useful, explainable, and safe
- Users see updates in real time as operational events happen
- Technicians, partners, and ops can collaborate without breaking role boundaries
- The product feels complete enough to demo as a real operating system for the field workforce

## Strategic Positioning

Deployable is best understood as:

**An agent-assisted operating system for field workforce deployment, not just a staffing dashboard and not just an AI assistant.**

That positioning matters because the defensible value is in the closed loop:

1. Capture operational data
2. Compute readiness and ranking
3. Generate recommendations
4. Route human approvals
5. Learn from decisions
6. Recompute the system continuously

If this loop works well, Deployable becomes the place where workforce readiness and deployment decisions actually happen.

# Digital Human POC Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax.

**Goal:** Deliver a local, persistent demonstration of the digital human lifecycle from generation options to evidence-backed archive Q&A.

**Architecture:** FastAPI owns the workflow, immutable artifacts and versioned publication. React presents the selected graph, review findings and pinned chat scope. PostgreSQL is the runtime ledger.

**Tech Stack:** React, Vite, TypeScript, FastAPI, SQLAlchemy asyncio, PostgreSQL, Docker Compose.

**Spec:** [Digital Human POC](../specs/2026-11-23-digital-human-poc-design.md)

## Global Constraints

- Deterministic local model by default. Optional OpenAI-compatible model supports structured generation within the same domain guards.
- The archive labels synthetic roles and pins answers to the selected release and as-of date.
- A graph edit preserves its parent. Refinement stays anchored to the approved graph.
- The reference screenshot informs column roles and information density. Visual identity and assets are original.
- The current workspace has no Git repository. Run verification directly in it.

## Review Focus

- Repeated and concurrent commands resolve to one artifact or a conflict.
- Moving or deleting an event affects only graph dependents and preserves version history.
- Invalid dates, missing dependency references and cycles are rejected.
- Review routes narrative repair and structural conflicts through their respective stages.
- Earlier snapshots exclude future events and question text cannot change an archive.

## Builder-check decisions

- Region, birth cohort, entry mode, household depth, detail and seed affect the generated artifact. History candidates are resolved from regional and lifetime context.
- The POC publishes one primary person per generation. Family members are linked participants and shared-event evidence, while the archive selector stays focused on the primary person.
- The optional OpenAI-compatible adapter is an interface and configuration path in this POC. Acceptance runs the complete workflow with the deterministic mock provider, so no external key is required.

## Tasks

### 1. LifeGraph domain and generation

**Files:** `backend/app/domain.py`, catalog resources, `backend/tests/test_domain.py`.

**Interface:** Pure functions consume generation options and graph mutations and return JSON-serializable profile, nodes, edges, diff and validation. The API contract below fixes keys at the boundary.

- [ ] Specify tests for seed stability, meaningful input effects, immutable edits and temporal dependency validation.
- [ ] Implement bounded DSL rules, candidate evaluation, shared events and dependency reconciliation.
- [ ] Run domain tests and check graph semantics.

### 2. Durable API and lifecycle

**Files:** `backend/app/main.py`, `backend/app/db.py`, `backend/app/workflow.py`, provider module, `backend/tests/test_workflow.py`, backend requirements.

**Interface:** FastAPI routes use the shared contract. Write operations lock the owning aggregate and record artifact IDs and workflow events transactionally.

- [ ] Specify API tests for generation, branch persistence, duplicate commands, review remediation, publication and scoped Q&A.
- [ ] Implement tables, state guards, model ports, workflow and read-only archive queries.
- [ ] Run backend suite against PostgreSQL and verify persistence after restart.

### 3. Interactive workbench and archive

**Files:** `frontend/src/`, `frontend/index.html`, Vite configuration and lockfile.

**Interface:** Common API contract below. Workbench restores a saved job, shows version selection and loads authoritatively from the API.

- [ ] Define browser acceptance for main flow and 1440/390/320 widths.
- [ ] Implement workbench, graph inspection and editing, model activity, review and archive chat.
- [ ] Verify production build and exercise each primary control in the browser.

### 4. Local runtime and integration

**Files:** `docker-compose.yml`, Dockerfiles, environment example, `README.md`, `scripts/smoke.py`.

- [ ] Validate Compose configuration, then build and start health-checked services.
- [ ] Run the smoke test against the live API, verify persisted data survives restart and complete browser verification.
- [ ] Review domain invariants, stage transitions, error handling and final results.

## Shared JSON Contract

Write requests use JSON. HTTP errors use FastAPI's `detail` field. IDs are strings. Date fields use ISO dates.

- `GET /api/catalog` returns `{regions: [{id,label}], ethnicities: [{id,label}], scenarios: [{id,label,region_ids}], defaults: GenerationOptions}`.
- `GenerationOptions` has `region, ethnicity, birth_year, as_of, entry, household, detail, seed, life_status`. Omitted or null `birth_year` and `household` are sampled. Population entry is `birth_cohort|living_at_as_of`. Household is `individual|family`, depth is `standard|rich`, life status is `living|deceased`. `POST /api/generations/spec/validate` yields `{valid, spec, warnings}`. The resolved spec records concrete values, `sampling` metadata, automatically selected `themes`, and `history_selection` reasons.
- `POST /api/generations` accepts GenerationOptions. `GET /api/jobs/{job_id}` and write commands on graphs, review and release return Job. Job has `job_id, run_id, status, progress, graph_revision_id, detail_revision_id, release_id, spec, person, error, actions`. Action values available to the UI include `edit, continue_flesh, review, publish, chat`. `GET /api/jobs/{job_id}/events` returns `{events: [{id, stage, tool, message, created_at}]}`. `GET /api/jobs/{job_id}/graphs` returns `{revisions: [{graph_revision_id,parent_revision_id,branch,created_at}]}`. Optional `Idempotency-Key` applies to generation.
- `GET /api/graphs/{graph_revision_id}` returns Graph. Graph has `graph_revision_id,parent_revision_id,branch,person,nodes,edges,diff,validation`. Person has `person_id,name,region,ethnicity,birth_year,life_status,as_of,summary,family`. Family entries have `id,name,relation`. Node has `event_id,title,category,start,end,precision,location,participants,notes,state_delta,evidence,summary_node`. Edge has `source,target,kind`. Evidence entries have `id,label,kind,url?`. Diff has `added,modified,removed,recomputed` string arrays. Validation has `valid,issues` with string issues.
- `POST /api/graphs/{id}/edits` accepts `{source:"manual", operations:[{op:"update"|"delete"|"add",event_id,...nodeFields}]}` or `{source:"llm",suggestion:string}`. `POST /api/graphs/{id}/continue-flesh` accepts no body and creates at most one detail lineage per approved graph.
- `GET /api/flesh/{detail_revision_id}` returns `{detail_revision_id,graph_revision_id,chunks:[{chunk_id,event_id,phase,title,narrative,memory,evidence,status}]}`.
- `GET /api/reviews/{release_id}` returns `{release_id,status,findings:[{id,severity,kind,event_id,message,repair,status}]}`. `POST /api/reviews/{release_id}/run` executes review and bounded repair. `POST /api/releases/{release_id}/publish` atomically enables archive access.
- `GET /api/archive/persons` returns `{persons:[{...Person,release_id,graph_revision_id,detail_revision_id,snapshot_id,published_at}]}`.
- `POST /api/chat/sessions` accepts `{person_id,release_id,as_of,role_scope?}`. It and `GET /api/chat/sessions/{session_id}` return Session with `session_id,person_id,release_id,graph_revision_id,detail_revision_id,snapshot_id,as_of,archive_as_of,role_scope,turns`.
- `POST /api/chat/sessions/{session_id}/turns` accepts `{question}` and returns Turn with `turn_id,question,answer,query_type,evidence,tools,model`. Evidence has `event_id,title,start,excerpt`.

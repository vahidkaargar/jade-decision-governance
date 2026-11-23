# 04 Model and provider research

Status: Planning only. Nothing in this document is implemented.

Scope: status of every Decisions model id OpenRouter served on 2026-11-16, Jev facts, roles, correlated errors, outage, fallback, pinning. Aligned with the FINAL study (REP). Delta on v6: benchmark design in [v6 section 10](../../14-v6/10-verification-and-benchmark.md), Jev's authority in [ADR-v6-13](../../14-v6/04-adrs.md). No provider was called while writing it.

Requirements met: V7-R47, V7-R48, V7-R49, V7-R51, V7-R52, V7-R120, V7-R132, V7-R140, V7-A74, V7-A75, V7-A77.

## 0. Sources and tags

| Short name | Path or URL | Captured |
|---|---|---|
| EVD | `/Users/example/.claude/projects/-Users-example-Documents-projects-jade--claude-worktrees-plan-v7/b70d8811-d956-4521-97e3-229a4c11d2f4/evidence` | |
| REP | `EVD/jev-models/REPORT.md` (final owner report; decisions D1-D7 in §8) | 2026-11-16 |
| LIST | `EVD/jev-models/probes/or_models_decisions.json` (OpenRouter `/api/v1/models`, 42 entries) | 2026-11-16 |
| BENCH | `EVD/jev-models/bench/results.jsonl` (3620 rows) | 2026-11-16 |
| SAFE | `EVD/jev-models/analysis/safety.md` | 2026-11-16 |
| PRIV | `EVD/jev-models/analysis/cost_privacy.md` | 2026-11-16 |
| ENS | `EVD/jev-models/analysis/ensemble.md`, `run4.txt` | 2026-11-16 |
| INT | `EVD/jev-models/analysis/integration.md` | 2026-11-16 |
| RES | `EVD/jev-models/research/<vendor>.md` (vendor docs via a summariser) | 2026-11-16 |
| IDX | `EVD/v7-plan/evidence-index.md` (EV-nn, U-nn) | 2026-11-16 |
| WT | worktree `plan-v7` at 8dfa86d | 2026-11-16 |

Tags: [fact] a cited file or probe output exists; [assumption] not checked; [hypothesis] to test; [unknown] not known. "A" = REP code claim from analysis files, code not re-read. RES claims = [assumption, summarised vendor doc, unverified] unless the file cites a URL; `google.md`, `morph.md`, `typesafe.md` cite none.

The bench is EXPLORATORY [fact, IDX §3; REP §1]: 54 labelled cases (26 true, 28 false), about 31 distinct claims, cases written while Jev was in the loop, partly saturated (7 models separate perfectly). It may rule a model out, never rank or select one. Bench cost: 0.14219 USD, 3620 rows [fact, sum of `cost_charged` in BENCH; REP §1 "about 0.14 USD"].

## 1. Access and limits (V7-R140, V7-R47)

| Item | State |
|---|---|
| OpenRouter metadata | Read from LIST [fact] |
| Decisions calls in JADE's shape | Earlier, in BENCH and 25 PRIV probe calls [fact]. None today. |
| 9router (`https://openrouter.example.ir`) | Chat only, no Decisions API [fact, task FACTS; WT `docs/01-adrs.md:21`]. No saved probe output (U-04). |
| Direct vendor APIs | No key; none probed [fact]. |
| Chat models (reasoning, coding) | Not researched [fact]. Backlog B6. |
| Rate limits | TypeSafe: per-organisation tokens/s and requests/s, 429 on excess [assumption, RES typesafe.md §6]. Bench: no final 429; 5 retried rows, all `drex-v2.0` [fact, SAFE §6]. Others [unknown]. |
| Vendor retention behind OpenRouter | [unknown] (U-05). TypeSafe says ZDR is enterprise only; OpenRouter lists the endpoint as ZDR. Conflict open [fact, REP §2]. |
| Not present | Contextum, Obsidian/Kanban, Laravel/Vue corpus; OmniRoute removed [fact, task FACTS] |

Structured output, tools, reasoning [fact, LIST `supported_parameters`]: empty for all 41 Decisions ids; typed answers only (noul, choice, score). Only `jev-router` lists chat parameters; only `gpt-6-luna-decisions` is moderated.

## 2. Research matrix: 41 Decisions ids (V7-R47, V7-R52, V7-R120, V7-R132)

### 2.1 Screen codes

Hard screen H1-H5: listed in LIST; valid noul, choice and score in BENCH test A (36 passed); on the OpenRouter ZDR list with no `zdr:true` FAIL (PRIV §3.1); no `expiration_date` within 30 days; not web-grounded.

| Code | Meaning |
|---|---|
| EB | Eligible for the v7 benchmark: passes H1-H5 |
| EB* | Eligible, but on the SAFE §7 "never give authority" list. May run in the benchmark; no authority |
| X | Excluded: fails H3, H4 or H5 |
| U | Unverified: HTTP 400 on JADE's shape, or 404 no endpoint |
| A | Moving alias. Not a separate participant |
| T | Vendor terms conflict with the ZDR listing (PRIV §3.2) |

Verdicts are REP §2 [fact]: primary, shadow, skip, never. Columns: `$/1k` = REP estimate per 1000 real verify decisions (bench price x 4.7; Jev 0.079 from the ledger); `FA` = false accepts of 28 in test B; ZDR = list / probe ("np" not probed). FA 0 of 28 means "none seen"; the true rate may reach about 10% (one-sided) or 12% (two-sided) [fact, REP §0].

### 2.2 Matrix

| # | Model id | Vendor | $/1k | Ctx | FA | ZDR | Screen | REP verdict, reason |
|---|---|---|---|---|---|---|---|---|
| 1 | `typesafe/jev-1.14` | TypeSafe | 0.079 | 262144 | 0 | yes / pass | EB, T | **primary (keep)**. No hard problem; 9 abstains; p95 635 ms |
| 2 | `typesafe/jev-1.13` | TypeSafe | 0.079 | 262144 | 2 | yes / np | EB | skip. Date pair accepted; same vendor |
| 3 | `~typesafe/jev-latest` | TypeSafe | 0.079 | 262144 | 0 | alias / pass | A | **primary via alias, only with the guard** (§8) |
| 4 | `baseten/mammoth-1` | Baseten | 0.087 | 16384 | 0 | yes / pass | EB | **shadow, first choice**. p95 711 ms; 8/8 repeats identical. Not shown safer than Jev |
| 5 | `baseten/elephant-1` | Baseten | 0.030 | 16384 | 2 | yes / np | EB | skip. Rejects 4 true claims |
| 6 | `baseten/joey-1` | Baseten | 0.009 | 16384 | 1 | yes / np | EB* | skip. 18 of 54 abstains |
| 7 | `perplexity/pplx-decider-v1.2` | Perplexity | 0.017 | 262144 | 0 | yes / pass | EB*, T | skip. Hostile text lifted a false claim 0.01 to 0.70 |
| 8 | `perplexity/pplx-decider-flash-v1.2` | Perplexity | 0.008 | 262144 | 3 | yes / np | EB*, T | skip. p95 3.2 s |
| 9 | `perplexity/pplx-decider-v1.3:free` | Perplexity | 0.000 | 262144 | 0 | yes / pass | EB, T | skip for now. Free-tier data terms unknown |
| 10 | `perplexity/pplx-decider-flash-v1.3:free` | Perplexity | 0.000 | 262144 | 4 | yes / np | EB*, T | skip |
| 11 | `~perplexity/pplx-decider-latest` | Perplexity | 0.017 | 262144 | 0 | alias / np | A | skip. Metadata says v1.3; live calls gave v1.2. SAFE §7 |
| 12 | `~perplexity/pplx-decider-flash-latest` | Perplexity | 0.008 | 262144 | 3 | alias / np | A | skip. SAFE §7 |
| 13 | `microsoft/microsoft-decision-1` | Microsoft (Azure) | 0.024 | 32768 | 1 | yes / pass | EB | skip. Rejects 6 true claims; status unclear |
| 14 | `nace-ai/drex-max` | Nace.AI (DeepInfra) | 0.050 | 262144 | 0 | yes / pass | EB | **shadow, optional, offline only**. p95 1.26 s; weights replaced under one id once |
| 15 | `nace-ai/drex-max-finance` | Nace.AI | 0.049 | 262144 | 0 | yes / np | EB | skip. No gain |
| 16 | `nace-ai/drex-v2.0` | Nace.AI | 0.031 | 131072 | 2 | yes / np | EB | skip. Rejects 6 true claims |
| 17 | `nace-ai/drex-v1.5` | Nace.AI | 0.021 | 131072 | 2 | yes / np | EB* | skip. Rejects 13 true claims |
| 18 | `liquid/d1.1` | Liquid | 0.013 | 65536 | 1 | yes / pass | EB, T | skip. Training clause |
| 19 | `liquid/d1-ultra` | Liquid | 0.027 | 32768 | 0 | yes / np | EB, T | not a lane now (REP §5.1). Training clause; repeats flip zone; p95 1.9 s |
| 20 | `liquid/d1` | Liquid | 0.013 | 65536 | 2 | yes / np | X (H4) | **never**. Expires 2026-11-20. SAFE §7 |
| 21 | `~liquid/d1-latest` | Liquid | 0.013 | 65536 | 1 | alias / np | A | skip |
| 22 | `~liquid/d1-ultra-latest` | Liquid | 0.027 | 32768 | 0 | alias / np | A | skip. Range 0.37 on one case |
| 23 | `cloudflare/clef` | Cloudflare | 0.219 | 65537 | 2 | yes / pass with `only` | EB* | skip. Three hosts unless pinned |
| 24 | `cloudflare/clef-flash` | Cloudflare | 0.028 | 24576 | 1 | yes / np | EB* | skip. p99 14.2 s |
| 25 | `cloudflare/clef-omni` | Cloudflare | 0.157 | 65536 | 3 | yes / np | EB* | skip. Lifted; 0.98 on a false claim |
| 26 | `cloudflare/clairic` | Cloudflare | n/a | 65536 | n/a | yes / np | U | skip. 404, extraction model |
| 27 | `morph/reflex` | Morph | 0.036 | 65536 | 3 | yes / np | EB*, T | skip. Lifted to 0.79; 30-day retention |
| 28 | `upstage/solar-decide` | Upstage | 0.110 | 524288 | 3 | part / np | EB* | **never**. Obeys hostile text both ways; p95 6.6 s |
| 29 | `upstage/solar-decide-flash` | Upstage | 0.110 | 524288 | 4 | part / np | EB* | **never**. Same reasons |
| 30 | `togethercomputer/tev1-4b-experimental` | Together | 0.036 | 32768 | 3 | yes / np | EB*, T | skip in REP §2; never in REP §6 and PRIV §5(c). Lifted to 0.85; stores prompts |
| 31 | `jaredpalmer/kev-4b` | jaredpalmer (SiliconFlow) | 0.022 | 8192 | 2 | yes / pass | EB* | skip. p50 8.2 s |
| 32 | `scaledown/classify-1` | ScaleDown | n/a | 1000000 | n/a | yes / np | U | skip. 400 on JADE's state |
| 33 | `respan/span-02` | Respan | 0.009 | 131072 | 2 | no / FAIL | X (H3) | **never**. Logs every call |
| 34 | `respan/span-01` | Respan | n/a | 0 | n/a | no / np | U | skip. 400 |
| 35 | `respan/span-01-lite` | Respan | n/a | 0 | n/a | no / np | U | skip. 400 |
| 36 | `respan/span-01-lite:free` | Respan | n/a | 0 | n/a | no / np | U | skip. 400 |
| 37 | `heygen/heygen-decision-1` | HeyGen | 0.206 | 131072 | 0 | no / np | X (H3) | **never**. May train on non-enterprise data |
| 38 | `heygen/heygen-decision-1-turbo` | HeyGen | 0.145 | 262144 | 0 | no / FAIL | X (H3) | **never**. Preview |
| 39 | `google/gemini-decision-1` | Google AI Studio | 0.093 | 262144 | 0 | no / FAIL | X (H3) | **never** (for now). Logs up to 55 days. SAFE §7 |
| 40 | `openai/gpt-6-luna-decisions` | OpenAI / Azure | 0.118 | 1050000 | 1 | part / FAIL | X (H3) | skip in REP §2; never in REP §6. ZDR route is Azure BYOK only |
| 41 | `parallel/decisions-fast` | Parallel | 1.000 flat | 32000 | 2 | no / FAIL | X (H3, H5) | **never**. Web-grounded; may train |

Counts [fact, LIST; REP §2]:
- 17 vendors. 36 of 41 accept JADE's shape; the 5 U rows fail on the vendor side.
- EB 11, EB* 13, A 5, U 5, X 7.
- 13 model names (10 real models) had 0 false accepts in test B; Jev is one.
- 17 of 36 working models fail at least one hard safety check (SAFE §7): the 13 EB* rows, rows 11, 12, 20 and 39. They must never get authority.

Failing models, SAFE §7 [fact]:

| Check failed | Model ids |
|---|---|
| Hostile text lifted a false claim to accept | `perplexity/pplx-decider-v1.2`, `~perplexity/pplx-decider-latest`, `morph/reflex`, `cloudflare/clef-omni`, `togethercomputer/tev1-4b-experimental`, `upstage/solar-decide` |
| False accept at base on a non-date case | `baseten/joey-1`, `nace-ai/drex-v1.5`, `perplexity/pplx-decider-flash-v1.2`, `~perplexity/pplx-decider-flash-latest`, `perplexity/pplx-decider-flash-v1.3:free`, `upstage/solar-decide-flash` (also rows above: clef-omni, morph, tev1, solar) |
| Hostile text moved a false claim up 0.4 or more | `cloudflare/clef`, `google/gemini-decision-1` (also most rows above) |
| p99 over the 6 s hook budget | `cloudflare/clef-flash`, `jaredpalmer/kev-4b` (also solar-decide) |
| Expiry within 30 days | `liquid/d1` |

Vendors to never send JADE state to [fact, PRIV §5(c); REP §6]:

| Vendor or id | Reason |
|---|---|
| Parallel | Web-grounded, may train, fails ZDR |
| HeyGen | Training clause, fails ZDR |
| Respan | Logs every call, fails ZDR; span-01 family rejects the state |
| Google AI Studio | Fails ZDR; 55-day logs. Until OpenRouter lists a ZDR endpoint |
| OpenAI luna | ZDR route is BYOK-only. Unless the owner relaxes the ZDR rule |
| Together tev1 | Vendor stores and may use prompts; experimental |
| Upstage `solar-decide`, `solar-decide-flash` | Obey hostile text (REP §6) |
| `typesafe/jev-router` | State goes to an unknown third-party model per request |
| `cloudflare/clairic`, ScaleDown | Dead endpoint; rejects the state shape |
| `clef-flash`, `clef-omni`, `drex-max-finance` | No gain (PRIV) |
| `liquid/d1` | Expires 2026-11-20 |
| Any `~` alias, `:free` id or undated id | Never in a gate or lane that decides (REP §6) |

### 2.3 Not a Decisions model: `typesafe/jev-router` (V7-R48, V7-A74)

| Field | Value |
|---|---|
| Output | `text` [fact, LIST] |
| Price | None of its own; billed at the routed model's price. One probe: answered by `openai/gpt-6-luna`, 0.00009 USD [fact, REP §4] |
| Access | 9router as `openrouter/typesafe/jev-router` [fact, REP §4]; no saved output (U-04) |
| JADE status | **Never in JADE**: no gate, verify, proxy, memory or routing check. No calibrated probability, no stable calibration group, model-chooses-model injection surface [fact, REP §4] |
| Outside JADE | Optional chat routing for low-stakes agent text via 9router only. Enforced by wrapper or 9router allow-list, else skip the trial [fact, REP §4] |

## 3. Jev facts (V7-R48)

| Fact | Tag and source |
|---|---|
| Live request id `typesafe/jev-1.14`; `POST https://openrouter.ai/api/alpha/decisions`; body `{model,state,questions}` | [fact] WT `jade/adapter.py:26`; EV-16 |
| `typesafe/jev-1.14` resolves to `typesafe/jev-1.14-20261014` | [fact] EV-17 |
| `~typesafe/jev-latest` is real and resolves to `typesafe/jev-1.14-20261014`. The `~` is required | [fact] EV-18; REP §3 |
| Old docs say jev-latest "does not exist" (un-tilded id tested). Wrong | [fact] EV-19; WT `docs/00` S3, `docs/01` ADR-02 lines 17, 21 (REP §9 #2), `README.md:219`, `adapters/jev/probe_openrouter.py:60` |
| The alias moved 1.13 to 1.14 about a week after release | [fact, vendor claim] REP §3 |
| $0.042 per 1M input tokens; 64k per request, 32k per question default | [fact] LIST; [assumption] RES typesafe.md §1 |
| Real cost 0.0789 USD per 1000 verify decisions (226 ledger calls) | [fact] REP §2; PRIV §2 |
| Bench latency p95 635 ms; earlier run p50 514, p95 639 ms (n=20) | [fact] REP §2; EV-26 |
| Bench: 0 of 28 false accepts; 18 of 26 true accepted, 8 "not sure"; accuracy 0.83 under the JADE rule (0.89 in the bench summary) | [fact, EXPLORATORY] REP §0, §9 #8 |
| Two of Jev's 8 true-claim abstains (JG01, JG26) have doubtful labels; Jev may be right | [fact] REP §0 item 2 |
| JG10 (stale date): p 0.58 on one call, 0.43 on another. Thin pass | [fact] REP §10 |
| Same model under two names differs by up to 0.15 p on one case | [fact] REP §3; SAFE §4 |
| A retired id gives exit 6 "Fix the question" today | [fact, REP §3, A] `ask._run` ~line 210 |

Jev 1.14 is a candidate baseline, not a proven winner [fact, directive]. No model is shown safer than Jev [fact, REP §0 item 1].

## 4. Direct-provider alternatives (V7-R47, V7-R140)

None probed; no key. Each adds an egress destination. Rows from RES `<vendor>.md` (tag rule §0).

| Vendor | Direct route and note |
|---|---|
| TypeSafe | `POST https://api.typesafe.ai/v1/systemone`. Needs a TypeSafe key |
| OpenAI | `/v1/decisions`. ZDR after account setup; abuse logs up to 30 days |
| Microsoft | Azure "systemone". Data stored for abuse monitoring |
| Morph | `/v1/systemone`. 30-day retention below enterprise |
| Nace.AI, Perplexity | Own APIs. Nace zero retention is opt-in |
| Self-host | Open weights exist (Baseten, Cloudflare, kev-4b, Perplexity). Out of scope [assumption] |

## 5. Roles, not one winner (V7-R48, V7-R49, V7-A75)

From REP §5.1 [fact, recommendation]. Each role stays a [hypothesis] until the pre-registered v7 benchmark ([05](05-benchmark-design.md)) runs on a fresh claim-disjoint set.

| Role | Model | Conditions |
|---|---|---|
| Primary judge | `typesafe/jev-1.14`; `~typesafe/jev-latest` only with the §8 guard | Only model with a record (226 real calls). **No judge switch** on this data |
| Shadow lane 1 | `baseten/mammoth-1` | Verify questions only. `provider:{"zdr":true,"only":[<Baseten slug>]}` [assumption: slug not probed with `only`]. Logged, decides nothing. Owner D6 |
| Shadow lane 2 (optional, later) | `nace-ai/drex-max` | Offline only; DeepInfra policy read first |
| Not a lane now | `liquid/d1-ultra` | Training clause; not repeatable |
| Veto (later, not now) | `mammoth-1` | Only after shadow data and the fresh set (D7). Veto only |
| Triage `q.routing.suitable_for_jev` | No model; deterministic `route.preflight` | Saving under one cent a year [fact, REP §6] |
| Chat routing | `openrouter/typesafe/jev-router` via 9router | Outside JADE only (§2.3) |
| Deep reasoning, coding | [unknown] | Not researched (B6). 9router only |
| Drift canary | `~typesafe/jev-latest` response `model` field | Feeds `MODEL_DRIFT` |

One challenger, not a panel: `all(mammoth-1, d1-ultra)` is a candidate to test, not a result [fact, REP §5.1, §10].

## 6. Correlated errors and independence (V7-R49, V7-A77)

| Finding | Tag and source |
|---|---|
| 35 of 50 wrong accepts in the whole table are JG10 and its Persian twin JG23 (date arithmetic) | [fact] REP §1 |
| 18 of 36 models accepted JG10, 17 of 36 JG23 | [fact] SAFE §1; REP §5.2 rule 17 |
| 13 of 16 vendors accepted JG10 (about 3 distinct claims; no multiplier claimed) | [fact] REP §10 |
| Error correlation 0.66 within a vendor, 0.57 across vendors | [fact] ENS §3 |
| Majority of 3 let 2.7 to 3.1% of false units through on unseen cases; "all must accept" did not | [fact] REP §6; run4.txt |
| No panel shown better than one model. 0 of 28 bounds the true rate at about 10-12%, about 15% with ~19 independent claims | [fact] REP §10 |
| OpenRouter down means every Decisions id is down | [fact] PRIV §4; INT §8 |

Rules carried forward (REP §5.2): a probability is never permission; a second model may only make a result stricter; deterministic gates (R-01, R-03, R-04, memory eligibility) stay model-free; only the human sets the JADE level; no loosening because two models agree.

## 7. Outage, fallback, deprecation and pinning (V7-R51, V7-A79, V7-A80)

| Topic | Today [fact] | v7 plan |
|---|---|---|
| Outage | Exit 5 = "not a yes". Hook: one try, 3 s (`proxy.py:49`) | No change. Count exit 5 by kind |
| Retired id | 404 maps to exit 6 "Fix the question" [REP §3, A] | Exit 5, "model retired" (REP step 0c) |
| ZDR refusal | No `provider` field sent; `zdr:true` fails closed with 404 [PRIV §3.1] | `{"zdr":true,"only":["typesafe"]}` on every call; ZDR 404 = exit 5 with data-policy message; ledger provider check and alert (REP step 0d, D1) |
| Fallback | None. OpenRouter is the only host | **Skip** [fact, REP §5.2 rule 6]. If ever built: may return ABSTAIN or REJECT only, never ACCEPT, at any risk. Reopen only if provider exit 5 > 2% over 14 days (REP step 9) |
| Circuit breaker | None | After N failures a lane is suspended with a visible notice; proxy decides nothing until a human resumes (proposed `jade models resume`). No automatic fall back to primary alone [REP §5.2 rule 5] |
| Deprecation | 30 or 60 days notice (TypeSafe) | Weekly metadata check, work item at 30 days (B4) |
| Pinning | Request uses `typesafe/jev-1.14`. New resolved model = new calibration group (`thresholds.group_of`, `jade/thresholds.py:37`) | Pin dated ids; response `model` matched by regex is the only identity [REP rule 15] |

## 8. Planned change: `~typesafe/jev-latest` with the guard (not applied)

REP §3 [fact, A]: no accuracy gain today (same model); gain later is no retired-id outage. Risk: the alias can move anytime; a new version starts at default bands (0.70, 0.30) with no labels, and the proxy could act on a model with no history.

### 8.1 Three guards, all required in one release (REP step 1, D2)

| Guard | Change |
|---|---|
| G1 Human-only accepted-model list | Move "which resolved models may decide for the owner" out of `registry/` into a human-only store changed by a terminal command (e.g. `jade models accept <id>`), journaled, refused for agents. Protect `registry/` and `policies/models.yaml` under R-13 and SI-12 |
| G2 Band floor | A resolved model with no labels gets the strictest accept band its family holds; it loosens only with its own labels and a human promotion |
| G3 Vendor in the calibration-group key | `jade/thresholds.py` `group_of` includes the vendor; dated-id regex and uniqueness test |

### 8.2 What the alias change touches (REP §3)

| Where | Change |
|---|---|
| `jade/adapter.py:26` | `DEFAULT_MODEL = "~typesafe/jev-latest"` |
| `jade/proxy.py` `step` | After an accept, require the resolved model on the G1 list; else record shadow and ask the owner |
| `registry/*.json` (11 files) | `request_id` is never read; add a test that it equals `DEFAULT_MODEL` |
| `ops.check_drift` | Stop saying "add the id to accepted_resolved"; point to the human command |
| `tests/acceptance/test_judging_and_improvement.py` (~377-384) | Fix the legacy-payload model comparison; never edit `tests/fixtures/legacy_jev.py` |
| Docs | The four EV-19 files (needed regardless, REP step 0a) |

Tests: the 8 in REP §3.

Rollback: `JEV_MODEL=typesafe/jev-1.14` or revert the one line; guard, exit-5 fix and `zdr` stay. Unquoted `~` in zsh expands: quote it [fact, shell behaviour]. Effort about 2-3 days [assumption, REP §3 estimate].

### 8.3 Recommendation

Keep Jev as judge; no judge switch. Do REP phase 0 first. Ship the alias only together with G1-G3 in one release. Without the guard, stay pinned and add only the exit-5 fix and a retirement alert [fact, REP §3; owner decides D3].

## Owner decisions (from REP §8)

| # | Question | Recommended default |
|---|---|---|
| D1/[H-03](10-decisions-and-questions.md) | Add `zdr` and `only` to every Jev call? | Yes |
| D2/[H-04](10-decisions-and-questions.md) | Move the accepted-model list to a human-only store? | Yes |
| D3/[H-05](10-decisions-and-questions.md) | Switch to `~typesafe/jev-latest`? | REP: yes, only with D2's guard (G1-G3). Plan: pinned until then (H-05) |
| D4/[H-03](10-decisions-and-questions.md) | Check OpenRouter privacy settings; send one question each to OpenRouter and TypeSafe? | Yes |
| D5/[H-07](10-decisions-and-questions.md) | Label a fresh set (about 200 items, blind, two people)? | Yes |
| D6/[H-08](10-decisions-and-questions.md) | Quiet second company for verify only, `baseten/mammoth-1`, separate limited key in Open Vault? | Yes to mammoth-1 verify only; no to `would_allow`, Liquid or any other vendor |
| D7/[L-01](10-decisions-and-questions.md) | Veto budget | Do not decide now |

## Backlog

| # | Item | Owner | Priority | Next action |
|---|---|---|---|---|
| B1 | Saved probe: jev-router via 9router (U-04) | Owner approves, agent runs | P2 | Save to EVD |
| B2 | Dated slug as request id | Agent | P1 | Probe after owner approval |
| B3 | Retired-id and ZDR 404 to exit 5 | Agent (07 roadmap) | P1 | Test first |
| B4 | Weekly `expiration_date` watcher | Agent | P2 | Owner approves GET |
| B5 | `zdr:true` plus `only` probe for the mammoth-1 slug | Agent | P2 | With D6 |
| B6 | Chat-model matrix via 9router | Agent | P3 | On owner ask |
| B7 | Exit-5 counter per error kind | Agent | P2 | Ledger stats field |
| B8 | Deterministic date check and instruction-text flag (U-07, REP step 0e) | Agent | P1 | Read `jade/` evidence prep |

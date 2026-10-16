# Milestone 1 integration verification

Date: 16 October 2026 (Asia/Calcutta).
Repository: C:\DevMesh. Implementation branch: milestone-1.
Implementation commit checked before verification: f3d4aba.

## Antigravity messaging: manual results supplied by the user

The user pasted MCP tools/call responses reporting isError=false for these checks.
These are user-supplied results, not an independent Antigravity UI capture by Codex.

| Check | Result | Evidence |
| --- | --- | --- |
| Read proposal | pass | Message 062e5219-5ec6-41d0-8117-8aaad56e049f appeared in Antigravity's inbox with delivered=false |
| Persistence through API restart | pass | The same message ID remained pending after the reported API restart |
| Poll pending message | pass | await_next_task returned status task and the same message with delivered=false |
| Explicit acknowledgement | pass | acknowledge_message returned delivered=true; a subsequent pending inbox read returned an empty messages list |
| Three empty polls | pass | All returned status no_task; reported durations were 45.16s, 45.07s, and 45.11s |
| Submit review | pass | Review ec02b7cc-27e5-4dbc-a896-1df4f3db3fe9 was returned by submit_review |
| Reverse delivery | pass | The same review appeared in Codex's inbox, in conversation 15d2d9c7-fdd6-4fbf-8306-c27bd1bdaf5b, with delivered=false |

The review was left unacknowledged in the user's last supplied result. This
verification did not acknowledge it or mutate the running messaging database.

## Real Codex connector: directly executed by Codex

Using the actual backend.codex_connector.CodexAppServerClient in C:\DevMesh,
two sequential send_prompt calls ran with the connector's explicit gpt-6-astra
model and inherited CLI authentication. No credentials were inspected and no
API keys were supplied.

1. A new thread remembered a harmless marker and returned exactly ACK_CODEX.
2. The first app-server process shut down. A second send_prompt call resumed
   the returned thread ID in another app-server process.
3. The second turn returned the exact marker from the previous turn, without
   the marker being included in the second prompt.
4. Both used app-server, streamed output, and returned no cleanup warnings.
   The thread ID remained the same and the turn ID changed.

The selected-field machine-readable result is in m1-live-connector-result.json.
No raw protocol logs, stderr, credential values, or environment values were saved.

## Automated suite and remaining limits

The implementation report records 48 tests passed in 57.15 seconds, including
all 15 M0 regressions. This follow-up changes verification documentation only;
the full suite was not rerun for that documentation update.

The manual checks and live connector inference/resume checks above pass. They
do not prove indefinite IDE polling, future task execution, production security,
or any later milestone.

- Pattern-based redaction masked ordinary delivered=false text and adjacent
  prose in the review content. This is a confirmed false positive of the broad
  assignment rule already documented in MILESTONE1.md; structured delivered
  booleans in the tool results remained intact. Redaction still cannot guarantee
  detection of arbitrary unlabeled or encoded secrets.
- Keep the human reactivation fallback when Antigravity stops polling.
- The API remains single-worker and local-only, with no authenticated agent
  identity and no WebSocket notification replay.
- The new direct live connector check exercised the primary app-server path and
  cross-process resume. It did not independently force the M1 exec fallback;
  its existing automated coverage and M0 live exec evidence remain separate.
- No Milestone 2 implementation was started in this chat.

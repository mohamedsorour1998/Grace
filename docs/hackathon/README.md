# The hackathon submission

Moved from `CLAUDE.md` on 2026-10-31.

## Hackathon constraints

- **Newly created work only.** No code from `OpenClaw`, `astrolabe`, `TheAgentOrg`,
  `AWS-Resource-Optimizer-Agent`, or the author's other projects. Patterns and hard-won API knowledge are
  reused as *knowledge*; that reuse is disclosed in the README. Do not copy files in.
- **MIT license** at repo root, visible in the GitHub About section. **Verified** —
  `gh repo view` reports `licenseInfo.key: "mit"` and `visibility: PUBLIC`.
- Required at submission: public repo, README, architecture diagram, ≤5-minute demo video
  (problem → who it's for → why it matters → working demo), AWS Builder ID.
- **AWS Builder ID is `mohamedsorour1998@gmail.com`.** Recorded in the README. The Devpost form asks
  for the email that created the Builder ID rather than a handle, so `@sorour` was the wrong value.
- A live demo link and AgentCore deployment both strengthen the Technical Implementation
  score. **Both exist**: `https://grace.rosettacloud.app`, also set as the repository's homepage so it
  appears in the About section where a judge looks first.

**Two artifacts remain, and both are drafted rather than done:**

| Artifact | Draft | State |
|---|---|---|
| ≤5-minute demo video | `docs/hackathon/demo-video-handout.md` | **Not recorded.** Shot list, script, timings, and a figure-provenance table are ready. Public on YouTube or Vimeo — the rules say *public*, not unlisted. |
| builder.aws blog post *(bonus points)* | `docs/hackathon/builder-blog-post.md` | **Not published.** Must be public on builder.aws.com with **"Agents for Humans" in the title** — the draft's title already satisfies that. |

Both carry `<replace this text by a screenshot of …>` markers where a capture belongs, so the writing is
finished and only the recording and screenshots remain. **Neither is produced by any task in any of the
three plans** — plan completion does not imply submission completion, and the README says so in its
"Still outstanding for submission" section.

**Two things the video script must not claim**, both easy to say by accident: that traces or Transaction
Search work (zero spans exist in the account), and that SMS is delivered (the account has no
origination number, so the channel writes a transcript). The handout carries a "Things not to claim"
section for exactly this reason.

**`c-010` has already been approved once**, by Task 8's verification, so a second approval returns
`409 already_decided` rather than re-running Grace. Demo the approval on `c-011` or `c-012`, or show
`c-010`'s existing outcome row — do not stage a fake approval to make the recording cleaner.

**The demo video is the one hard submission requirement nothing in three plans produces.** Neither
`README.md` nor `docs/evidence/dashboard-verification.md` may imply the submission is complete. The **AWS Builder
ID** is likewise not derivable from the repo or the AWS API; it sits in the README as an explicit
`TODO(sorour)` field rather than a guessed value.

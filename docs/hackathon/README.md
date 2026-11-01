# The hackathon submission

Grace was built for the **AWS Agents for Humans Hackathon** (Good Neighbor track) and submitted on
2026-09-14; the deadline was 2026-09-14 17:00 PT. The repository as it stood on GitHub at the
deadline is tagged [`v0.1.0`](https://github.com/mohamedsorour1998/Grace/releases/tag/v0.1.0).

The demo video is on YouTube: [Agents for Humans: Grace](https://www.youtube.com/watch?v=QBmEfiyfAeQ).

The build followed the competition's rules: newly created work only, with design patterns
carried over from the author's earlier projects as knowledge and disclosed in the README's
"Prior work and disclosure" section; MIT licence; and a public repository.

## The submission material

- [demo-video-handout.md](demo-video-handout.md): the demo video's script, shot list, and the
  provenance of every figure in it.
- [devpost-description.md](devpost-description.md) and
  [devpost-submission-form.md](devpost-submission-form.md): the text submitted.
- [builder-blog-post.md](builder-blog-post.md),
  [builder-blog-post-2-deployed-is-not-written.md](builder-blog-post-2-deployed-is-not-written.md),
  and [builder-blog-post-3-sabotage.md](builder-blog-post-3-sabotage.md): the blog posts.

## Notes kept while preparing the submission

Written before the deadline, and kept as they were: "outstanding" below means outstanding at the
time of writing. The first part was moved from `CLAUDE.md` on 2026-10-31, the checklist from the README.

### Hackathon constraints

- **Newly created work only.** No code from any of the author's earlier projects. Patterns and
  hard-won API knowledge are reused as *knowledge*; that reuse is disclosed in the README. Do not copy
  files in.
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

### The submission checklist, as the README carried it

**The ≤5-minute demo video does not exist.** It is a hard requirement and no part of the build produces
it. Nothing in this README should be read as saying the submission is complete.

| Required artifact | State |
|---|---|
| Project built with the required tools | Present — Strands Agents SDK, Amazon Bedrock (Nova only), AgentCore Runtime + Memory, deployed on AWS |
| Text description of features and functionality | This README, plus [docs/evidence/dashboard-verification.md](../evidence/dashboard-verification.md) and [docs/evidence/deployed-verification.md](../evidence/deployed-verification.md) for the evidence |
| Public code repository | Present — [`mohamedsorour1998/Grace`](https://github.com/mohamedsorour1998/Grace) |
| MIT license, visible in the About section | Present — [LICENSE](../../LICENSE), detected by GitHub as MIT |
| README | This file |
| Architecture diagram | [`docs/architecture.md`](../architecture.md) — Mermaid, renders natively on GitHub, and the authoritative copy. [`docs/architecture.png`](../architecture.png) is a rendered convenience copy that lags it. |
| AWS Builder ID | **mohamedsorour1998@gmail.com** — the Devpost form asks for the email used to create the Builder ID |
| Live demo link *(optional, scores better)* | Present — **[grace.rosettacloud.app](https://grace.rosettacloud.app)** |
| **≤5-minute demo video** | **Not recorded. Outstanding.** The script, shot list, and figures are ready in [docs/hackathon/demo-video-handout.md](demo-video-handout.md) — must cover the problem, who it is for, why it matters, and a demonstration; uploaded publicly to YouTube or Vimeo |
| **builder.aws blog post** *(optional, bonus points)* | **Not published. Outstanding.** Drafted in [docs/hackathon/builder-blog-post.md](builder-blog-post.md) — must be public on builder.aws.com with "Agents for Humans" in the title |

**AWS Builder ID:** `mohamedsorour1998@gmail.com`

Both remaining artifacts are drafted rather than done. Each carries
`<replace this text by a screenshot of …>` markers where a capture belongs, so the writing is finished
and only the recording and the screenshots remain.

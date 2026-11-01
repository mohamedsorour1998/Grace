# Grace documentation

## How it works

- [architecture.md](architecture.md): the architecture, with the diagram
  ([architecture.png](architecture.png), source [architecture.drawio](architecture.drawio)).

## Runbooks: how to do things

- [runbooks/deploy.md](runbooks/deploy.md): deploy the agent to AgentCore Runtime, plus the
  sweep (EventBridge, Step Functions, Lambda), with verified CLI flags.
- [runbooks/dashboard.md](runbooks/dashboard.md): build, run locally, and deploy the caseworker
  dashboard (Next.js, Amplify, Cognito).
- [runbooks/architecture-diagram.md](runbooks/architecture-diagram.md): redraw the architecture
  diagram in draw.io.

## Evidence: what was measured on the deployed system

Each is a dated record of real runs, and keeps the addresses and counts it measured.

- [evidence/deployed-verification.md](evidence/deployed-verification.md): the deployed sweep,
  and the escalation and filing invariants, verified from DynamoDB.
- [evidence/dashboard-verification.md](evidence/dashboard-verification.md): the dashboard's
  refusals, and an approval that filed nothing.
- [evidence/live-data-findings.md](evidence/live-data-findings.md): what the live table
  actually contained when the dashboard was built.

## Lessons: what each defect taught

- [lessons/](lessons/): the lessons from every plan, kept verbatim. The index, and the ten rules
  that cut across them, are in `CLAUDE.md` under "Lessons".

## Hackathon: the September 2026 submission

- [hackathon/README.md](hackathon/README.md): what was submitted, and the rules the build followed.

- [hackathon/demo-video-handout.md](hackathon/demo-video-handout.md): the demo video's shot list
  and script.
- [hackathon/devpost-description.md](hackathon/devpost-description.md) and
  [hackathon/devpost-submission-form.md](hackathon/devpost-submission-form.md): the submission text.
- [hackathon/builder-blog-post.md](hackathon/builder-blog-post.md),
  [hackathon/builder-blog-post-2-deployed-is-not-written.md](hackathon/builder-blog-post-2-deployed-is-not-written.md),
  and [hackathon/builder-blog-post-3-sabotage.md](hackathon/builder-blog-post-3-sabotage.md):
  the blog posts.

## History: designs and plans

- [superpowers/](superpowers/README.md): every design spec and implementation plan, as dated
  snapshots.

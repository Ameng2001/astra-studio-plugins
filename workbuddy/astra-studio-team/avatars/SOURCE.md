# Avatar source

All avatars are original, drawn procedurally by `scripts/gen_team_avatars.py` (fur is thousands of short strokes; each character is seeded, so the output is reproducible). There is no third-party artwork here and no license to track.

| File | Character | Role |
|------|-----------|------|
| astra-studio-team-lead.png | Border collie | 工坊主理人 |
| product-manager.png | Hamster | 产品经理 |
| architect.png | Schnauzer | 架构师 |
| ux-researcher.png | Siamese cat | 用户研究员 |
| domain-expert.png | Orangutan | 领域专家 |
| skill-builder.png | Spotted puppy | 技能工程师 |
| quality-reviewer.png | Black cat | 质检员 |

`team.png` is a 2×2 collage of the first four.

To change a character, edit its function in the script and re-run:

    python3 scripts/gen_team_avatars.py

# SE 会议评审表（ICSE / FSE / ASE 类，通用结构）

> 以当年 CFP 为准。本文件是回退结构。

## 评审表字段

1. **Summary** — 评审人用自己的话复述论文
2. **Contribution / Novelty** — 相对最近工作的增量
3. **Strengths**
4. **Weaknesses** — 常按 soundness / significance / verifiability & transparency / presentation 分项
5. **Questions for authors** — rebuttal 的靶子
6. **Overall recommendation** — accept / weak accept / major revision / weak reject / reject + **confidence**
7. **Open science / artifact** — 代码数据是否可得、是否可复现

驱动分数的是：威胁有效性（内部 / 外部 / 构念）、与现有工作的实际对比、实证的现实性（真实项目 vs 玩具）。

## Rebuttal 规则（通用）
- 多为单页或字数上限；只能补证据、不能改研究问题；每条回应指向可核对的表或节。
- 部分会议有 major revision 轮：带修订清单，AC 逐条复核。
- Artifact evaluation 常独立进行，徽章可写进 camera-ready。

## 前十分钟清单（评审人找拒稿理由）
1. 摘要的贡献是一句可证伪的话吗？
2. 贡献列表每条对应哪个表 / 图？无对应记一条。
3. 我知道的最近三篇在不在、被实际对比了吗？
4. 主结果表：基线数、同预算同调参、方差 / 区间、n。
5. 数据集 / 评测：污染、划分泄漏、benchmark 是否被攻击。
6. 威胁有效性是否诚实。
7. 图表 caption 脱离正文可读吗。
8. first / novel / significantly / state-of-the-art 有比较对象和统计支撑吗。
9. 代码 / 数据 / 超参 / 种子 / 环境交代了吗。
10. 我的 top-1 拒稿理由是什么？论文先答了吗？

## fatal 与 fixable

| 弱点 | 等级 | rebuttal 能救？ |
|---|---|---|
| 训练 / 测试污染、数据泄漏 | fatal | 否 |
| 混杂因素未控制 | fatal | 基本否 |
| 缺最直接的近邻基线 | fatal | 否（外围基线：是） |
| 主 claim 无表 / 图支撑 | fatal | 否，只能降 claim |
| benchmark 误用 | fatal | 否 |
| 研究问题不成立 / 增量过小 | fatal | 否 |
| 负结果未报告（被发现时） | fatal | 否 |
| 缺消融 | fixable | 是（有算力时） |
| 缺区间 / 多 seed | fixable | 是，但补出来不显著则转 fatal |
| 缺一个外围基线 | fixable | 是 |
| 符号 / 写作不清 | fixable | 是（承诺修订） |
| 外部效度弱 | 视情况 | claim 已收窄则可修 |

规则：**补出来的数据若削弱原 claim，可修项转致命项** —— 必须预先决定收窄 claim，不隐瞒。

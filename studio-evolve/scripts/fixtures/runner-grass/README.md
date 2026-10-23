# runner-grass —— runner_onto_engine.py 的验证案例

口语取自引擎 `tests/fixtures/qwen_replay.json`（真 Qwen 录制，CI 回放），期望值按引擎 `ActionEngine.validate` 在 `plugins.grass.seed_reference_data` 上的实测：

| 案例 | 期望 | 为什么 |
|---|---|---|
| grass-01 | action(出一地一方) | 碱茅是乡土草种，commit |
| grass-02 | rejected(乡土合规) | 紫花苜蓿非乡土，被规则拦 |
| grass-03 / 04 | action(快检评级) / rejected(霉变拦截) | 霉菌毒素 0.01 过、0.2 被拦 |
| grass-05 / 06 | query | 按 region 过滤 / count 聚合，各 ≥ 1 行 |
| grass-07 | query（真阴性） | 多跳到修复方法，seed 里没有对应边 → 0 行 |
| grass-08 | action（真阴性） | 「帮我出个方案」信息不足 → clarify |
| grass-09 | query（真阴性） | 墒情是遥测 kind，不是查询 |

`smoke_runner.py` 跑它。`fixtures/runner-glycowise/routes.json` 是给 glycowise 黄金集手工转写的 6 条路由；该插件 handler 多为 TODO(FDE)，目前只能验证「引擎报错被如实记为不可判」。

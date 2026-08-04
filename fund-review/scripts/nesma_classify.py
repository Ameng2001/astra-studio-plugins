"""nesma_classify — 从功能描述**独立重判** NESMA 计数项类型，规则可引用到标准原文。

为什么要独立重判，而不是给已导入的类型补写理由？
    给既有结论编理由是事后合理化，等于把门禁变成摆设。
    正确做法是让规则引擎从描述重新判一遍：
      · 与导入类型**一致** → rationale = 触发的规则 + 标准条款 + 命中原文，是真凭据
      · **不一致**        → 进人工裁决清单，不静默改写

规则依据（山东省省级政务信息化建设项目支出预算限额标准）：
    p.16-17  三.(三)3、4      不计数规则
    p.17     表6              ILF 识别规则
    p.18     表7              ELF 识别规则
    p.19     表8              EI  识别规则
    p.20-21  表9              EO  识别规则
    p.22     表10             EQ  识别规则

三地标准均引 GB/T 42588 / SJ/T 11619，识别规则一致，故本模块可跨区域复用；
真正因区域而异的是**权重与系数**，那些在 standard-packs 里。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# ---- 规则定义 ----------------------------------------------------------


@dataclass(frozen=True)
class Rule:
    id: str
    verdict: str                  # ILF|ELF|EI|EO|EQ|EXCLUDE
    citation: str                 # 标准条款
    summary: str                  # 规则要点
    keywords: tuple[str, ...]
    #: 反向关键词 —— 命中则本规则不适用
    blockers: tuple[str, ...] = ()


#: 优先级从高到低。第一条命中即定型（数据功能优先于事务功能，
#: 维护动作优先于输出，派生输出优先于纯检索 —— 与表8/9/10 的判别顺序一致）。
RULES: tuple[Rule, ...] = (
    # ---------- 不计数（三.(三)4） ----------
    Rule("X-SEC", "EXCLUDE", "三.(三)4(1) p.16",
         "信息安全、授权、登录等标准功能不纳入功能点计数",
         ("登录", "登陆", "单点登录", "鉴权", "认证", "验证码", "找回密码", "修改密码"),
         blockers=("权限管理", "角色管理", "授权策略", "权限配置")),
    Rule("X-MENU", "EXCLUDE", "三.(三)4(6) p.16",
         "菜单结构通常不纳入计数（用户可自维护的除外）",
         ("菜单结构",), blockers=("自定义菜单", "菜单维护", "菜单配置")),
    Rule("X-INFRA", "EXCLUDE", "三.(三)4(2) p.16",
         "对操作系统、数据库等基础软件的更改配置不纳入计数",
         ("操作系统配置", "数据库调优", "中间件配置", "基础软件配置")),

    # ---------- 数据功能（表6 / 表7） ----------
    Rule("D-ELF", "ELF", "表7.1 p.18",
         "由本系统引用、他方维护的逻辑数据组；跨越应用边界",
         ("外部系统", "第三方系统", "外部数据源", "他方维护", "外部接口文件",
          "医保数据", "民政数据", "外部厂商", "外部平台数据"),
         blockers=("临时", "中间文件")),
    Rule("D-ILF", "ILF", "表6.1 p.17",
         "本系统内新增/更改/删除/查询的、用户可识别的业务逻辑数据组",
         ("知识库", "知识图谱", "本体", "样本库", "样本集", "训练集", "评测集",
          "语料库", "数据集", "档案库", "台账", "注册表", "版本库", "模板库",
          "提示词库", "向量库", "索引库", "记忆库", "字典库", "规则库", "标签库"),
         blockers=("临时", "中间文件", "缓存")),

    # ---------- 事务功能（表8 / 表9 / 表10） ----------
    Rule("T-EI", "EI", "表8.1 p.19",
         "跨边界的基本过程，对内部逻辑文件执行新增/更改/删除，或改变系统行为",
         ("新增", "新建", "创建", "添加", "录入", "上传", "导入", "上报", "提交",
          "修改", "编辑", "更新", "变更", "删除", "移除", "停用", "启用", "禁用",
          "配置", "设置", "维护", "审批", "审核", "发起", "绑定", "解绑",
          "派单", "分配", "指派", "发布", "下架", "注册", "注销", "归档",
          "结算", "支付", "下单", "退单", "签到", "打卡", "标注")),
    Rule("T-EO", "EO", "表9.1 / 表9.3 p.20",
         "向边界外输出，且包含计算或派生数据（与 EQ 的唯一判别点）",
         ("统计", "计算", "汇总", "分析", "占比", "趋势", "预测", "评估", "评分",
          "排名", "排序", "推荐", "识别", "生成", "预警", "告警", "监测",
          "报表", "看板", "大屏", "驾驶舱", "画像", "洞察", "推理", "研判",
          "同比", "环比", "累计", "平均", "总计", "指数")),
    Rule("T-EQ", "EQ", "表10.1 / 表10.5 p.22",
         "向边界外输出规模明确、无需进一步数据处理的数据",
         ("查询", "检索", "搜索", "筛选", "查看", "展示", "显示", "列表",
          "详情", "浏览", "呈现", "导出", "下载", "分页")),
)

#: 帮助功能 —— 三.(三)4(4)：每种类型单独计为一个 EQ
HELP_KEYWORDS = ("帮助", "使用说明", "操作指引", "新手引导")


@dataclass
class Verdict:
    type: str | None              # ILF|ELF|EI|EO|EQ；EXCLUDE 或无法判定时为 None
    rule: Rule | None
    hits: list[str] = field(default_factory=list)
    excluded: bool = False
    #: 命中多条不同结论的规则 —— 说明描述本身混合了多个基本处理，粒度可能过粗
    competing: list[tuple[str, list[str]]] = field(default_factory=list)

    @property
    def confident(self) -> bool:
        """无竞争规则时视为高置信。有竞争说明一行里塞了多个基本处理。"""
        return self.type is not None and not self.competing

    def rationale(self) -> str:
        if self.excluded:
            return f"【不计数】{self.rule.summary}（{self.rule.citation}）"
        if self.type is None:
            return ""
        base = (f"判为 {self.type}：{self.rule.summary}"
                f"（{self.rule.citation}）；命中原文「{'、'.join(self.hits[:4])}」")
        if self.competing:
            others = "；".join(f"{t}({'、'.join(h[:3])})" for t, h in self.competing)
            base += f"。注：同时命中 {others}，该行可能含多个基本处理，需复核拆分"
        return base


#: 否定词 —— 紧邻关键词之前出现时，该次命中不算数。
#: 「该视图为查看性质，**不修改**报告」会被误判为 EI（维护 ILF），
#: 而按表10.5「执行外部查询时，不应维护内部逻辑文件」，它恰恰是 EQ。
NEGATIONS = ("不", "无", "非", "未", "勿", "禁止", "无需", "不再", "不得")
_NEG_WINDOW = 3


def _hits(text: str, keywords: tuple[str, ...]) -> list[str]:
    """命中关键词，跳过被否定的出现。"""
    out = []
    for k in keywords:
        start = 0
        while (idx := text.find(k, start)) != -1:
            prefix = text[max(0, idx - _NEG_WINDOW):idx]
            if not any(prefix.endswith(n) for n in NEGATIONS):
                out.append(k)
                break
            start = idx + len(k)
    return out


def classify(text: str) -> Verdict:
    """按 RULES 优先级判定。返回首条命中规则的结论，并记录竞争规则。"""
    text = text or ""
    matched: list[tuple[Rule, list[str]]] = []
    for rule in RULES:
        if any(b in text for b in rule.blockers):
            continue
        hits = _hits(text, rule.keywords)
        if hits:
            matched.append((rule, hits))

    if not matched:
        # 帮助类兜底 —— 三.(三)4(4) 每种类型计一个 EQ
        help_hits = [k for k in HELP_KEYWORDS if k in text]
        if help_hits:
            r = Rule("T-HELP", "EQ", "三.(三)4(4) p.16",
                     "帮助功能每种类型单独计为一个外部查询", tuple(HELP_KEYWORDS))
            return Verdict("EQ", r, help_hits)
        return Verdict(None, None)

    top_rule, top_hits = matched[0]
    if top_rule.verdict == "EXCLUDE":
        return Verdict(None, top_rule, top_hits, excluded=True)

    competing = [(r.verdict, h) for r, h in matched[1:]
                 if r.verdict != top_rule.verdict and r.verdict != "EXCLUDE"
                 and not _mutually_exclusive(top_rule.verdict, r.verdict)]
    return Verdict(top_rule.verdict, top_rule, top_hits, competing=competing)


#: 互斥判别对 —— 命中两者不代表存在两个基本处理，而是同一判别标准的两端。
#: EO/EQ：表9.3 p.20 与 表10.5 p.22 用「输出是否包含进一步数据处理产生的数据」
#:        这一个判据区分二者，有派生即 EO，无派生即 EQ，不可能并存。
#: ILF/ELF：表7.5 p.18「只有当一个逻辑文件不是应用程序的内部逻辑文件时，
#:        它才会被计为一个外部逻辑文件」—— 同一文件二者只居其一。
_EXCLUSIVE_PAIRS = frozenset({frozenset({"EO", "EQ"}), frozenset({"ILF", "ELF"})})


def _mutually_exclusive(a: str, b: str) -> bool:
    return frozenset({a, b}) in _EXCLUSIVE_PAIRS


# ---- 结构性校验（表6.2 / 表7.1） --------------------------------------


def check_ilf_completeness(items: list) -> list[dict[str, Any]]:
    """表6.2：每个 ILF 至少含一个 EI，且至少含一个 EO 或 EQ。
    表7.1：每个 ELF 至少存在一个 EO 或一个 EQ。

    反过来用更有价值：**一个 system 有大量 EI 却没有 ILF，说明 ILF 被漏计了** ——
    EI 的定义就是「对内部逻辑文件执行新增/更改/删除」，没有 ILF 何来 EI。
    这正是当前 13 个系统零 ILF 的实质。
    """
    from collections import Counter, defaultdict

    by_system: dict[str, Counter] = defaultdict(Counter)
    for it in items:
        if it.nesma:
            by_system[it.path.system][it.nesma.type] += 1

    out = []
    for system, types in by_system.items():
        n_ilf, n_ei = types.get("ILF", 0), types.get("EI", 0)
        n_out = types.get("EO", 0) + types.get("EQ", 0)
        if n_ei > 0 and n_ilf == 0:
            out.append({
                "system": system, "issue": "EI_WITHOUT_ILF",
                "citation": "表8.1 p.19 + 表6.2 p.17",
                "message": (f"{n_ei} 个 EI 但 0 个 ILF —— EI 的定义是「对内部逻辑文件"
                            f"执行新增/更改/删除」，无 ILF 则 EI 无所依附，属漏计"),
                "ei": n_ei, "ilf": n_ilf,
                "suggested_ilf_min": max(1, round(n_ei / 4)),
            })
        if n_ilf > 0 and n_ei == 0:
            out.append({
                "system": system, "issue": "ILF_WITHOUT_EI",
                "citation": "表6.2 p.17",
                "message": f"{n_ilf} 个 ILF 但 0 个 EI —— 违反「每个 ILF 至少含一个 EI」",
                "ei": n_ei, "ilf": n_ilf,
            })
        if n_ilf > 0 and n_out == 0:
            out.append({
                "system": system, "issue": "ILF_WITHOUT_OUTPUT",
                "citation": "表6.2 p.17",
                "message": f"{n_ilf} 个 ILF 但 0 个 EO/EQ —— 违反「至少含一个 EO 或 EQ」",
                "ilf": n_ilf,
            })
    return out


def strip_numbering(text: str) -> list[str]:
    """把「1、xxx；2、yyy」式的复合描述切成候选基本处理。

    源表大量此类描述 —— 一行里塞了多个基本处理，是粒度过粗的主要来源。
    """
    if not text:
        return []
    parts = re.split(r"(?:^|[；;])\s*\d+[、.．)）]\s*", text)
    return [p.strip() for p in parts if len(p.strip()) >= 6]

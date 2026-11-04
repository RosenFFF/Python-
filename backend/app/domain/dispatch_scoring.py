"""派单加权评分（纯函数，无数据库依赖，便于测试和答辩讲解）。

    推荐分 = (w_skill·技能 + w_distance·距离 + w_load·负荷 + w_rating·评分) × 重点关注系数

四个因子都归一到 [0, 1]，越大越优：
- 技能匹配度 = 命中所需技能数 / 所需技能数；
- 距离得分 = 1 − 距离 / 最大服务半径（线性衰减，超半径的已被硬过滤）；
- 负荷得分 = 1 − 当日已派单数 / 单日上限；
- 评分得分 = (贝叶斯平滑后的近 30 天评分 − 1) / 4。新护理员样本少，向先验均值收缩，
  避免 1 单 5 星的新人排到几百单 4.8 星的老手前面。

返回结果保留每个因子的原始值、归一值、权重和贡献分，界面据此解释"为什么推荐此人"。
"""
from __future__ import annotations

from dataclasses import dataclass, field

DEFAULT_WEIGHTS = {"skill": 0.4, "distance": 0.3, "load": 0.2, "rating": 0.1}


@dataclass(frozen=True)
class ScoringParams:
    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    max_distance_km: float = 3.0
    max_daily_orders: int = 6
    min_skill_match: float = 0.5
    rating_prior_mean: float = 4.0
    rating_prior_weight: float = 5.0
    key_attention_factor: float = 0.8


@dataclass(frozen=True)
class CandidateInput:
    caregiver_id: int
    name: str
    skills: frozenset[str]
    distance_km: float
    daily_orders: int
    rating_sum: float
    rating_count: int
    has_conflict: bool = False
    key_attention: bool = False
    org_id: int | None = None
    lat: float | None = None
    lng: float | None = None


def normalize_weights(weights: dict[str, float]) -> dict[str, float]:
    missing = set(DEFAULT_WEIGHTS) - set(weights)
    if missing:
        raise ValueError(f"缺少权重项: {sorted(missing)}")
    if any(weights[k] < 0 for k in DEFAULT_WEIGHTS):
        raise ValueError("权重不能为负数")
    total = sum(weights[k] for k in DEFAULT_WEIGHTS)
    if total <= 0:
        raise ValueError("权重之和必须大于 0")
    return {k: weights[k] / total for k in DEFAULT_WEIGHTS}


def smoothed_rating(rating_sum: float, rating_count: int, params: ScoringParams) -> float:
    prior = params.rating_prior_weight
    return (params.rating_prior_mean * prior + rating_sum) / (prior + rating_count)


def exclusion_reason(c: CandidateInput, required: frozenset[str], params: ScoringParams) -> str | None:
    """硬过滤。超半径、满负荷、时段冲突、技能不足的护理员不进入候选。"""
    if c.has_conflict:
        return "该时段已有在途工单"
    if c.distance_km > params.max_distance_km:
        return f"距离 {c.distance_km:.2f} 公里，超出 {params.max_distance_km:g} 公里服务半径"
    if c.daily_orders >= params.max_daily_orders:
        return f"当日已派 {c.daily_orders} 单，达到 {params.max_daily_orders} 单上限"
    if required and len(required & c.skills) / len(required) < params.min_skill_match:
        return f"技能匹配不足，缺少：{'、'.join(sorted(required - c.skills))}"
    return None


def score_candidate(c: CandidateInput, required: frozenset[str], params: ScoringParams) -> dict:
    weights = normalize_weights(params.weights)
    skill = len(required & c.skills) / len(required) if required else 1.0
    distance = max(0.0, 1 - c.distance_km / params.max_distance_km) if params.max_distance_km > 0 else 0.0
    load = max(0.0, 1 - c.daily_orders / params.max_daily_orders) if params.max_daily_orders > 0 else 0.0
    rating_value = smoothed_rating(c.rating_sum, c.rating_count, params)
    rating = min(1.0, max(0.0, (rating_value - 1) / 4))
    normalized = {"skill": skill, "distance": distance, "load": load, "rating": rating}
    contributions = {k: weights[k] * normalized[k] for k in weights}
    raw_score = sum(contributions.values())
    factor = params.key_attention_factor if c.key_attention else 1.0
    return {
        "caregiver_id": c.caregiver_id,
        "name": c.name,
        "org_id": c.org_id,
        "lat": c.lat,
        "lng": c.lng,
        "score": round(raw_score * factor, 4),
        "raw_score": round(raw_score, 4),
        "penalty_factor": factor,
        "key_attention": c.key_attention,
        "factors": {
            "skill": {
                "label": "技能匹配", "weight": round(weights["skill"], 4), "normalized": round(skill, 4),
                "contribution": round(contributions["skill"], 4),
                "raw": f"{len(required & c.skills)}/{len(required)} 项",
                "missing": sorted(required - c.skills),
            },
            "distance": {
                "label": "直线距离", "weight": round(weights["distance"], 4), "normalized": round(distance, 4),
                "contribution": round(contributions["distance"], 4), "raw": f"{c.distance_km:.2f} 公里",
            },
            "load": {
                "label": "当日负荷", "weight": round(weights["load"], 4), "normalized": round(load, 4),
                "contribution": round(contributions["load"], 4),
                "raw": f"{c.daily_orders}/{params.max_daily_orders} 单",
            },
            "rating": {
                "label": "近30天评分", "weight": round(weights["rating"], 4), "normalized": round(rating, 4),
                "contribution": round(contributions["rating"], 4),
                "raw": f"平滑 {rating_value:.2f} 星（{c.rating_count} 次评价）",
            },
        },
    }


def rank_candidates(
    candidates: list[CandidateInput], required: frozenset[str], params: ScoringParams
) -> tuple[list[dict], list[dict]]:
    """返回 (按推荐分排序的全部合格候选, 被过滤者及原因)。

    排序：推荐分降序，同分取距离近者，再按编号，保证结果确定、可复现。
    """
    ranked, excluded = [], []
    for c in candidates:
        reason = exclusion_reason(c, required, params)
        if reason:
            excluded.append({"caregiver_id": c.caregiver_id, "name": c.name, "reason": reason})
        else:
            ranked.append((c, score_candidate(c, required, params)))
    ranked.sort(key=lambda pair: (-pair[1]["score"], pair[0].distance_km, pair[0].caregiver_id))
    result = []
    for rank, (_, scored) in enumerate(ranked, start=1):
        scored["rank"] = rank
        result.append(scored)
    return result, excluded

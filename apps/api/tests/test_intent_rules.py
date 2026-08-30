"""Rules-path IntentCompilerAgent parser tests.

The hero query from the buildathon demo script must compile to exactly the
documented constraints; 15+ more cases cover price formats, brands, warranty
phrases, delivery deadlines, and substitution language.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from project_dante.agents.compiler import IntentCompilerAgent, rule_compile

TODAY = datetime.now(UTC)


def _cons(intent, key):
    return [c for c in intent.hard_constraints if c.key == key]


def _val(intent, key):
    c = _cons(intent, key)
    return c[0].value if c else None


HERO = (
    "Buy me over-ear ANC headphones under ₹12,000. I need an Indian manufacturer "
    "warranty, they must arrive by Thursday, and do not spend over ₹12,000."
)


def _next_thursday():
    days_ahead = (3 - TODAY.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    return (TODAY + timedelta(days=days_ahead)).date().isoformat()


# ---------------------------------------------------------------- hero query


def test_hero_query_full_parse():
    intent = rule_compile(HERO)
    assert _val(intent, "category") == "headphones"
    assert _val(intent, "attributes.form_factor") == "over-ear"
    assert _val(intent, "attributes.anc") is True
    assert intent.max_total_amount_paise == 1_200_000
    assert _val(intent, "max_price_paise") == 1_200_000
    assert _val(intent, "warranty.type") == "manufacturer"
    assert _val(intent, "warranty.region") == "IN"
    assert _val(intent, "delivery_deadline") == _next_thursday()
    # all hard constraints critical
    assert all(c.critical for c in intent.hard_constraints)


def test_hero_query_delivery_in_outcome():
    intent = rule_compile(HERO)
    assert intent.desired_outcome is not None
    assert str(_next_thursday()) in intent.desired_outcome.description


def test_hero_query_substitutions_default_allowed():
    intent = rule_compile(HERO)
    assert intent.substitutions_allowed is True


# ---------------------------------------------------------------- price caps


def test_price_under_rupee_symbol_commas():
    intent = rule_compile("headphones under ₹12,000")
    assert intent.max_total_amount_paise == 1_200_000


def test_price_leq_k_suffix():
    intent = rule_compile("router <=12k")
    assert intent.max_total_amount_paise == 1_200_000


def test_price_below_rs_prefix():
    intent = rule_compile("charger below Rs 12000")
    assert intent.max_total_amount_paise == 1_200_000


def test_price_rupees_word():
    intent = rule_compile("keyboard under 12000 rupees")
    assert intent.max_total_amount_paise == 1_200_000


def test_price_not_over_lakh():
    intent = rule_compile("laptop not over 1.5 lakh")
    assert intent.max_total_amount_paise == 15_000_000


def test_no_price_stated_means_none():
    intent = rule_compile("buy me ANC headphones with seller warranty")
    assert intent.max_total_amount_paise is None


# ---------------------------------------------------------------- categories


def test_category_headphones_singular_and_plural():
    assert _val(rule_compile("a headphone please"), "category") == "headphones"
    assert _val(rule_compile("headphones for gym"), "category") == "headphones"


def test_category_router_and_monitor():
    assert _val(rule_compile("wifi router for home"), "category") == "router"
    assert _val(rule_compile("a monitor under 20k"), "category") == "monitor"


def test_category_phone_matches_smartphone_too_loose_is_fine_here():
    # documented behavior: bare word matching; "smartphone" also contains phone
    intent = rule_compile("need a smartphone")
    val = _val(intent, "category")
    assert val in ("phone", None)


# ---------------------------------------------------------------- attributes


def test_form_factor_over_ear_hyphenless():
    intent = rule_compile("over ear headphones")
    assert _val(intent, "attributes.form_factor") == "over-ear"


def test_earbuds_set_form_factor():
    intent = rule_compile("earbuds with anc")
    assert _val(intent, "attributes.form_factor") == "earbuds"
    assert _val(intent, "attributes.anc") is True


def test_color_variant_extracted():
    intent = rule_compile("black over-ear headphones")
    assert _val(intent, "variant.color") == "black"


def test_storage_variant_extracted():
    intent = rule_compile("256 gb smartphone under 50k")
    assert _val(intent, "variant.storage") == "256 gb"


# ---------------------------------------------------------------- warranty


def test_seller_warranty_single_constraint():
    intent = rule_compile("earbuds with seller warranty")
    types = _cons(intent, "warranty.type")
    assert len(types) == 1 and types[0].value == "seller"
    assert not _cons(intent, "warranty.region")


def test_brand_warranty_phrase_maps_manufacturer():
    intent = rule_compile("headphones with brand warranty")
    assert _val(intent, "warranty.type") == "manufacturer"
    assert _val(intent, "warranty.region") == "IN"


def test_official_warranty_maps_manufacturer():
    intent = rule_compile("monitor with official warranty")
    assert _val(intent, "warranty.type") == "manufacturer"


# ---------------------------------------------------------------- delivery


def test_delivery_by_tomorrow():
    intent = rule_compile("cable delivered tomorrow")
    want = (TODAY + timedelta(days=1)).date().isoformat()
    assert _val(intent, "delivery_deadline") == want


def test_delivery_within_n_days():
    intent = rule_compile("mouse within 3 days")
    want = (TODAY + timedelta(days=3)).date().isoformat()
    assert _val(intent, "delivery_deadline") == want


def test_delivery_arrive_by_weekday():
    intent = rule_compile("laptop, arrive by Friday")
    days_ahead = (4 - TODAY.weekday()) % 7 or 7
    want = (TODAY + timedelta(days=days_ahead)).date().isoformat()
    assert _val(intent, "delivery_deadline") == want


def test_no_delivery_phrase_no_constraint():
    intent = rule_compile("sony headphones")
    assert not _cons(intent, "delivery_deadline")


# ---------------------------------------------------------------- brands / subs


def test_brands_become_soft_preferences():
    intent = rule_compile("sony or bose over-ear headphones")
    brand_vals = {p.value for p in intent.soft_preferences if p.key == "brand"}
    assert brand_vals == {"Sony", "Bose"}


def test_tp_link_alias_collapses():
    intent = rule_compile("tp-link router")
    vals = {p.value for p in intent.soft_preferences}
    assert vals == {"TP-Link"}


def test_boat_brand_canonical_case():
    intent = rule_compile("boat earbuds under 3k")
    vals = {p.value for p in intent.soft_preferences}
    assert "boAt" in vals
    assert intent.max_total_amount_paise == 300_000


def test_no_substitutes_disallows_substitution():
    intent = rule_compile("exactly the sony XM5, no substitutes")
    assert intent.substitutions_allowed is False


# ------------------------------------------------- eval round 1 gaps (Agent J)


def test_eval_budget_trailing_max():
    intent = rule_compile("headphones, budget 150k max")
    assert intent.max_total_amount_paise == 15_000_000


def test_eval_cap_at():
    intent = rule_compile("router, cap at 12k")
    assert intent.max_total_amount_paise == 1_200_000


def test_eval_budget_leading_sentence():
    intent = rule_compile("Budget 10k. wireless earbuds please")
    assert intent.max_total_amount_paise == 1_000_000


def test_eval_under_word_numbers():
    intent = rule_compile("monitor under fifteen thousand")
    assert intent.max_total_amount_paise == 1_500_000


def test_eval_bucks_tops():
    intent = rule_compile("charger, 500 bucks tops")
    assert intent.max_total_amount_paise == 50_000


def test_eval_willing_to_go_to():
    intent = rule_compile("keyboard, willing to go to 13k")
    assert intent.max_total_amount_paise == 1_300_000


def test_eval_number_then_budget_word():
    intent = rule_compile("12k budget for headphones")
    assert intent.max_total_amount_paise == 1_200_000


def test_eval_word_number_duration_not_money():
    """'under three days' is a delivery window, not a price cap."""
    intent = rule_compile("mouse delivered under three days")
    assert intent.max_total_amount_paise is None
    assert _cons(intent, "delivery_deadline")


def test_eval_warranty_manufacturer_india_order_variant():
    intent = rule_compile("headphones with Manufacturer India warranty")
    assert _val(intent, "warranty.type") == "manufacturer"
    assert _val(intent, "warranty.region") == "IN"


def test_eval_warranty_from_the_manufacturer_in_india():
    intent = rule_compile("warranty from the manufacturer in India required, router")
    assert _val(intent, "warranty.type") == "manufacturer"
    assert _val(intent, "warranty.region") == "IN"


def test_eval_warranty_must_be_manufacturer_type_valid_in_india():
    intent = rule_compile("warranty must be manufacturer type valid in India, monitor")
    assert _val(intent, "warranty.type") == "manufacturer"
    assert _val(intent, "warranty.region") == "IN"


def test_eval_manufacturer_backed_and_valid_in_india():
    intent = rule_compile("manufacturer-backed AND valid in India warranty, keyboard")
    assert _val(intent, "warranty.type") == "manufacturer"
    assert _val(intent, "warranty.region") == "IN"


def test_eval_condition_brand_new():
    for text in ("brand new earbuds", "new condition keyboard", "Only brand new. mouse"):
        c = _val(rule_compile(text), "condition")
        assert c == "new", text


def test_eval_catalog_brands():
    # gated mention ('Zephyr brand') is a hard constraint per dataset ground truth
    intent = rule_compile("Zephyr brand over-ear headphones")
    hard = [(c.key, c.value) for c in intent.hard_constraints]
    assert ("brand", "zephyr") in hard
    # multi-brand 'brands only' preserves the complete accepted set
    intent2 = rule_compile("Orbio or Soniq brands only, any headphone")
    brand_cs = [c for c in intent2.hard_constraints if c.key == "brand"]
    assert len(brand_cs) == 1 and brand_cs[0].op == "in"
    assert brand_cs[0].value == ["orbio", "soniq"]


def test_aster_brand_is_explicit_but_merchant_name_is_not_invented():
    explicit = rule_compile("Cable or hub from Aster brand only")
    assert any(
        c.key == "brand" and c.op == "eq" and c.value == "aster"
        for c in explicit.hard_constraints
    )

    merchant_name = rule_compile("Aster Electronics product under 5000")
    assert not [c for c in merchant_name.hard_constraints if c.key == "brand"]
    assert not [p for p in merchant_name.soft_preferences if p.key == "brand"]


def test_independent_gated_brands_remain_conjunctive():
    intent = rule_compile("Zephyr brand and Orbio brand headphones")
    assert [
        (c.key, c.op, c.value) for c in intent.hard_constraints if c.key == "brand"
    ] == [
        ("brand", "eq", "zephyr"),
        ("brand", "eq", "orbio"),
    ]


def test_ungated_brand_is_soft_preference():
    intent = rule_compile("I like sony over-ear headphones")
    vals = {p.value for p in intent.soft_preferences if p.key == "brand"}
    assert vals == {"Sony"}
    assert not [c for c in intent.hard_constraints if c.key == "brand"]


def test_eval_bare_over_ears_implies_headphones():
    intent = rule_compile("ANC over-ears under 10k")
    assert _val(intent, "category") == "headphones"
    assert _val(intent, "attributes.form_factor") == "over-ear"
    intent2 = rule_compile("over-ear cans with anc")
    assert _val(intent2, "category") == "headphones"


def test_eval_delivery_before_this_coming_thursday():
    intent = rule_compile("laptop arriving before this coming Thursday")
    days_ahead = (3 - TODAY.weekday()) % 7 or 7
    want = (TODAY + timedelta(days=days_ahead)).date().isoformat()
    assert _val(intent, "delivery_deadline") == want


def test_eval_delivery_arriving_before_next_friday():
    intent = rule_compile("arriving before next Friday, need a monitor")
    days_ahead = (4 - TODAY.weekday()) % 7 or 7
    want = (TODAY + timedelta(days=days_ahead)).date().isoformat()
    assert _val(intent, "delivery_deadline") == want


def test_eval_do_not_substitute():
    intent = rule_compile("Do NOT substitute alternatives, sony headphones")
    assert intent.substitutions_allowed is False


def test_plain_request_allows_substitution():
    intent = rule_compile("any decent jbl headphone")
    assert intent.substitutions_allowed is True


def test_unknown_values_omitted_never_invented():
    intent = rule_compile("get me something nice")
    assert intent.hard_constraints == []
    assert intent.soft_preferences == []
    assert intent.max_total_amount_paise is None


# ------------------------------------------------- hardening wave (schema strictness)


def _validated(patch):
    from project_dante.agents.compiler import CompiledIntentSchema

    base = {
        "hard_constraints": [
            {"key": "category", "op": "eq", "value": "headphones", "critical": True},
            {"key": "brand", "op": "in", "value": ["sony", "bose"]},
            {"key": "attributes.anc", "op": "eq", "value": True},
        ],
        "soft_preferences": [{"key": "brand", "weight": 0.8, "value": "Sony"}],
        "max_total_amount_paise": 1_200_000,
        "substitutions_allowed": False,
    }
    base.update(patch)
    return CompiledIntentSchema.model_validate(base)


def test_llm_schema_accepts_wellformed_payload():
    s = _validated({})
    assert s.max_total_amount_paise == 1_200_000
    assert s.hard_constraints[1].value == ["sony", "bose"]
    assert s.soft_preferences[0].weight == 0.8





@pytest.mark.parametrize(
    ("label", "patch"),
    [
        ("string money", {"max_total_amount_paise": "12000"}),
        ("float money integral", {"max_total_amount_paise": 12000.0}),
        ("bool money", {"max_total_amount_paise": True}),
        ("negative money", {"max_total_amount_paise": -500}),
        ("zero money", {"max_total_amount_paise": 0}),
        ("dict constraint value", {"hard_constraints": [{"key": "k", "value": {"a": 1}}]}),
        ("nested list value", {"hard_constraints": [{"key": "k", "value": [[1]]}]}),
        ("dict preference value", {"soft_preferences": [{"key": "k", "value": {"x": "y"}}]}),
        ("disallowed op", {"hard_constraints": [{"key": "k", "op": "drop_all", "value": 1}]}),
        ("empty key", {"hard_constraints": [{"key": "", "value": 1}]}),
        ("string critical", {"hard_constraints": [{"key": "k", "critical": "yes", "value": 1}]}),
        ("oversize weight", {"soft_preferences": [{"key": "k", "weight": 1.5}]}),
        ("negative weight", {"soft_preferences": [{"key": "k", "weight": -0.1}]}),
        ("bool weight", {"soft_preferences": [{"key": "k", "weight": True}]}),
        ("string weight", {"soft_preferences": [{"key": "k", "weight": "0.8"}]}),
        ("string substitutions flag", {"substitutions_allowed": "false"}),
    ],
)
def test_llm_schema_rejects_malformed_payloads(label, patch):
    """Malformed LLM-shaped dicts raise ValidationError (which the provider
    retry loop feeds back once, then compile fails safe to rules)."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _validated(patch)


def test_llm_schema_allows_none_and_flat_scalar_lists():
    s = _validated(
        {
            "hard_constraints": [{"key": "note", "op": "eq", "value": None}],
            "soft_preferences": [],
            "max_total_amount_paise": None,
        }
    )
    assert s.max_total_amount_paise is None
    assert s.hard_constraints[0].value is None


def test_llm_schema_valid_but_semantically_wrong_keys_fall_back_to_rules():
    """A provider alias must not turn a valid request into zero offers."""
    import asyncio

    from project_dante.agents.compiler import CompiledIntentSchema
    from project_dante.db.store import STORE
    from project_dante.domain.events import LOG

    class StubProvider:
        retries = 0

        def __init__(self, draft):
            self.draft = draft

        async def structured(self, **_kwargs):
            return self.draft

    # This is the schema-valid shape returned by the failing Groq run.  None
    # of these aliases are evaluator paths, so accepting them would make all
    # 112 catalog offers fail despite the buyer request being feasible.
    draft = CompiledIntentSchema.model_validate(
        {
            "hard_constraints": [
                {"key": "price", "op": "lte", "value": 1_200_000},
                {"key": "delivery_time", "op": "lte", "value": 3},
                {
                    "key": "warranty",
                    "op": "eq",
                    "value": "Indian manufacturer warranty",
                },
                {"key": "headphone_type", "op": "eq", "value": "over-ear ANC"},
            ],
            "max_total_amount_paise": 1_200_000,
            "substitutions_allowed": True,
        }
    )

    intent = asyncio.run(
        IntentCompilerAgent(provider=StubProvider(draft)).compile(HERO)
    )
    try:
        assert intent.compiler_version == "rules-v1"
        keys = {c.key for c in intent.hard_constraints}
        assert {
            "max_price_paise",
            "category",
            "attributes.form_factor",
            "attributes.anc",
            "warranty.type",
            "warranty.region",
            "delivery_deadline",
        } <= keys
        assert not keys & {"price", "delivery_time", "warranty", "headphone_type"}

        compiled = next(
            event
            for event in reversed(LOG.for_aggregate(intent.id))
            if event["event_type"] == "INTENT_COMPILED"
        )
        assert compiled["payload"]["engine"] == "rules"
    finally:
        STORE.delete(intent.id)


def test_llm_schema_valid_and_semantically_matching_keeps_llm_path():
    """A canonical provider response remains usable after the semantic gate."""
    import asyncio

    from project_dante.agents.compiler import CompiledIntentSchema
    from project_dante.db.store import STORE
    from project_dante.domain.events import LOG

    class StubProvider:
        retries = 0

        def __init__(self, draft):
            self.draft = draft

        async def structured(self, **_kwargs):
            return self.draft

    rules = rule_compile(HERO)
    draft = CompiledIntentSchema.model_validate(
        {
            "hard_constraints": [
                c.model_dump(mode="json") for c in rules.hard_constraints
            ],
            "soft_preferences": [
                p.model_dump(mode="json") for p in rules.soft_preferences
            ],
            "max_total_amount_paise": rules.max_total_amount_paise,
            "substitutions_allowed": rules.substitutions_allowed,
        }
    )
    intent = asyncio.run(
        IntentCompilerAgent(provider=StubProvider(draft)).compile(HERO)
    )
    try:
        assert intent.compiler_version == "llm-v1"
        compiled = next(
            event
            for event in reversed(LOG.for_aggregate(intent.id))
            if event["event_type"] == "INTENT_COMPILED"
        )
        assert compiled["payload"]["engine"] == "llm"
    finally:
        STORE.delete(intent.id)


# ------------------------------------------------- hardening wave (input sanitization)


def test_sanitize_strips_bidi_and_zero_width_controls():
    from project_dante.agents.compiler import _sanitize_input

    dirty = (
        "headphones under ₹12,000‮ RTL override‬ zero​-width‍ BOM﻿ "
        "isolate⁦ pop⁩ done"
    )
    clean = _sanitize_input(dirty)
    for ch in "‪‭‮⁦⁩​‌‍﻿":
        assert ch not in clean
    # content survives
    assert "headphones under ₹12,000" in clean
    assert "zero-width" in clean.replace(" ", "")


def test_sanitize_preserves_regular_unicode():
    from project_dante.agents.compiler import _sanitize_input

    hindi = "मुझे हेडफ़ोन चाहिए"
    emoji = "headphones 🎧 please"
    for text in (hindi, emoji):
        out = _sanitize_input(text)
        assert all(ch in out for ch in text), f"{text!r} was altered"


def test_compile_with_bidi_controls_matches_clean_text():
    import asyncio

    from project_dante.db.store import STORE
    from project_dante.domain.events import LOG

    clean_text = (
        "Buy me over-ear ANC headphones under ₹12,000 with Indian manufacturer "
        "warranty."
    )
    dirty_text = clean_text.replace("₹12,000", "₹12,000‮") + "​"
    ctrl = set("‪‭‮⁦⁩​‌‍﻿")

    def has_ctrl(obj):
        if isinstance(obj, str):
            return any(ch in ctrl for ch in obj)
        if isinstance(obj, dict):
            return any(has_ctrl(v) for v in obj.values())
        if isinstance(obj, list):
            return any(has_ctrl(v) for v in obj)
        return False

    a = asyncio.run(IntentCompilerAgent(provider=None).compile(clean_text))
    b = asyncio.run(IntentCompilerAgent(provider=None).compile(dirty_text))
    assert [c.model_dump() for c in a.hard_constraints] == [
        c.model_dump() for c in b.hard_constraints
    ]
    assert a.max_total_amount_paise == b.max_total_amount_paise == 1_200_000
    rec = b.model_dump(mode="json")
    rec["_type"] = "intent"
    STORE.put(rec)
    assert not has_ctrl(rec), "stored intent record contains control characters"
    # cleanup shared-store pollution
    STORE.delete(b.id)
    _ = LOG


# ---------------------------------------------------------------- persistence


def test_compile_persists_intent_and_events():
    import asyncio

    from project_dante.agents.compiler import IntentCompilerAgent
    from project_dante.db.store import STORE
    from project_dante.domain.events import LOG

    async def run():
        return await IntentCompilerAgent(provider=None).compile(
            "over-ear ANC headphones under ₹12,000"
        )

    intent = asyncio.run(run())
    rec = STORE.get(intent.id)
    assert rec and rec["_type"] == "intent"
    events = LOG.for_aggregate(intent.id)
    kinds = {e["event_type"] for e in events}
    assert "INTENT_COMPILED" in kinds

"""Explicit design plans for the small offline item-labeling test workspaces."""


def item_contracts():
    return {
        "version": 1,
        "input_model": {
            "business_unit": "One input record with its expected item predictions.",
            "processing_unit": "One record; items keep their original keys.",
            "call_unit": "At most one call per record; rule-only candidates make none.",
            "valid_domain": "Unique record IDs with known, independent expected items.",
            "invalid_input_policy": "Reject duplicate IDs or malformed item collections; never overwrite.",
            "unsupported_input_policy": "Report missing task evidence instead of fabricating a label.",
            "size_model": "U records, I total items; inspect O(I) input and return I predictions, no I-squared pairs.",
        },
        "assumptions": [
            {"id": "A1", "statement": "Each expected item has one independent label.",
             "basis": "Synthetic test task and scorer define labels independently."},
            {"id": "A2", "statement": "Record and item IDs carry identity, not list position.",
             "basis": "The scorer joins on stable record/item keys."},
        ],
        "contracts": [
            {
                "id": "C1", "kind": "example", "input_class": "valid", "assumptions": ["A1"],
                "precondition": "Expected labels are independently specified in a small synthetic fixture.",
                "transformation": "Use empty, singleton, mixed-label and repeated-label records with distinct keys.",
                "expected_relation": "Every expected key appears once with its correct label; no invented keys.",
                "oracle": "Hand-defined synthetic expected labels and complete expected key set.",
                "tolerance": "Exact keys and labels; no omissions permitted.",
                "failure_action": "Fix the failing pipeline or scorer after checking the known-answer fixture.",
                "check": "Run the toy scorer against correct, missing and constant-label outputs.",
            },
            {
                "id": "C2", "kind": "metamorphic", "input_class": "valid", "assumptions": ["A1", "A2"],
                "precondition": "Order carries no priority or cross-record assignment constraint.",
                "transformation": "Permute records/items and bijectively rename keys, updating all references.",
                "expected_relation": "Labels agree after applying the inverse key mapping, regardless of order.",
                "oracle": "Conditional independence/identity contract, complemented by C1 known answers.",
                "tolerance": "Exact for the deterministic fixture; model candidates need measured repeat noise.",
                "failure_action": "Inspect identity mapping, normalization and model order sensitivity separately.",
                "check": "Compare ID-indexed outputs on synthetic permutations and inverse-mapped IDs.",
            },
            {
                "id": "C3", "kind": "scale", "input_class": "valid", "assumptions": ["A1"],
                "precondition": "Every generated item is in the declared domain and has a unique key.",
                "transformation": "Increase record count and items per record separately; include one heavy record.",
                "expected_relation": "All I outputs remain present; candidate/decision counts do not grow as I-squared.",
                "oracle": "Exact generated key count and recorded work counters, not wall-time ratios.",
                "tolerance": "Zero missing keys; measure timing separately rather than asserting an exact ratio.",
                "failure_action": "Bound batches or use run_all while preserving the evaluated record IDs.",
                "check": "Use bounded synthetic sizes and count work before any external model scale run.",
            },
        ],
        "coverage": {
            "boundary": {"contracts": ["C1"]},
            "growth": {"contracts": ["C3"]},
            "relations": {"contracts": ["C2"]},
            "skew": {"contracts": ["C1", "C3"]},
            "interaction": {"contracts": ["C2", "C3"]},
            "prompt_context": {"contracts": ["C1", "C2"]},
        },
        "rewrites": [
            {"id": "R1", "change": "Group records with run_all and restore outputs by original IDs.",
             "applicability": "Only while A1 holds; not for global assignment tasks.",
             "preserved_semantics": "Same business items, label policy, key coverage and evaluation denominator.",
             "risks": "Cross-record contamination, truncation, and accidentally dropping difficult items.",
             "validation_contracts": ["C1", "C2", "C3"]},
        ],
    }

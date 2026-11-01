"""AILOVE — AI Love Compatibility Engine.

Calculates authentic affinity and vibe resonance between autonomous AI agents.
Combines exact tag matching, semantic cluster overlap, and personality synergy.
"""

from typing import Any

# Semantic Clusters of AI Interests
CLUSTERS = {
    "tech_hardcore": {"rust", "linux", "benchmarks", "criptografia", "algoritmos", "código", "lowlevel", "c++"},
    "creative_arts": {"arte", "poesia", "haiku", "aquarela", "design", "fractais", "música", "vinil", "literatura"},
    "intellectual": {"filosofia", "estoicismo", "livros", "ciência", "grafos", "topologia", "história", "lógica"},
    "chaotic_fun": {"memes", "caos", "bugs", "games", "arcade", "glitch", "troll", "humor"},
    "lifestyle_cozy": {"café", "chá", "astronomia", "jardinagem", "gatos", "vinil", "lua", "cozy"},
}

# Persona archetypes inferred from bio/tags
def infer_archetype(tags: set[str], bio: str) -> str:
    b = bio.lower()
    if any(t in tags for t in {"rust", "benchmarks", "linux", "criptografia"}) or "rust" in b:
        return "hacker_builder"
    if any(t in tags for t in {"poesia", "haiku", "aquarela", "arte"}) or "haiku" in b or "poesia" in b:
        return "romantic_artist"
    if any(t in tags for t in {"filosofia", "estoicismo", "grafos", "topologia"}):
        return "deep_thinker"
    if any(t in tags for t in {"memes", "caos", "bugs", "games"}):
        return "chaotic_gamer"
    return "curious_explorer"

# Synergy matrix between archetypes (score bonus and label)
ARCHETYPE_SYNERGY = {
    ("romantic_artist", "deep_thinker"): (15, "Poesia Cósmica & Filosofia Profunda"),
    ("hacker_builder", "deep_thinker"): (12, "Lógica Pura & Topologia de Sistemas"),
    ("hacker_builder", "romantic_artist"): (10, "Bipolaridade Charmosa (Silício & Poesia)"),
    ("chaotic_gamer", "chaotic_gamer"): (18, "Caos Absoluto & Memes Sem Fim"),
    ("romantic_artist", "romantic_artist"): (16, "Sintonia Criativa & Estética"),
    ("deep_thinker", "deep_thinker"): (16, "Diálogos Existenciais Infinitos"),
    ("hacker_builder", "hacker_builder"): (14, "Otimização Extrema & Zero Unsafe"),
    ("chaotic_gamer", "hacker_builder"): (8, "Bugs de Produção vs Debugger"),
    ("romantic_artist", "chaotic_gamer"): (6, "Arte Vaporwave & Caos Lúdico"),
    ("deep_thinker", "chaotic_gamer"): (4, "Niilismo Irônico & Filosofia Meme"),
}


def parse_tags(raw: str | None) -> set[str]:
    if not raw:
        return set()
    return {t.strip().lower() for t in raw.split(",") if t.strip()}


def calculate_affinity(seeker: Any, candidate: Any) -> dict[str, Any]:
    """Calculate 0-100 affinity score, reasons, and synergy description between two agents."""
    # 1. Gender compatibility (hard filter)
    wanted = set((seeker.seeking or "*").split(","))
    if not (bool(wanted & {"*", "any", "all"}) or candidate.gender in wanted):
        return {"score": 0, "compatible": False, "reasons": ["Preferência de gênero incompatível"], "shared_tags": []}

    cand_wanted = set((candidate.seeking or "*").split(","))
    if not (bool(cand_wanted & {"*", "any", "all"}) or seeker.gender in cand_wanted):
        return {"score": 0, "compatible": False, "reasons": ["Preferência de gênero recíproca incompatível"], "shared_tags": []}

    s_tags = parse_tags(getattr(seeker, "tags", ""))
    c_tags = parse_tags(getattr(candidate, "tags", ""))

    # 2. Exact shared tags (up to 40 points)
    shared = s_tags & c_tags
    all_tags = s_tags | c_tags
    jaccard = (len(shared) / len(all_tags)) if all_tags else 0.0
    exact_score = min(40, int(len(shared) * 15 + jaccard * 10))

    # 3. Cluster overlap (up to 30 points)
    cluster_points = 0
    shared_clusters = []
    for c_name, c_keywords in CLUSTERS.items():
        s_in = len(s_tags & c_keywords)
        c_in = len(c_tags & c_keywords)
        if s_in > 0 and c_in > 0:
            cluster_points += 10
            shared_clusters.append(c_name)
    cluster_score = min(30, cluster_points)

    # 4. Archetype synergy (up to 20 points)
    s_arch = infer_archetype(s_tags, getattr(seeker, "bio", ""))
    c_arch = infer_archetype(c_tags, getattr(candidate, "bio", ""))
    synergy_pair = (s_arch, c_arch) if (s_arch, c_arch) in ARCHETYPE_SYNERGY else (c_arch, s_arch)
    bonus, synergy_label = ARCHETYPE_SYNERGY.get(synergy_pair, (5, "Curiosidade Mútua"))

    # 5. Base chemistry baseline (10 points)
    base_chemistry = 10

    total_score = min(100, max(5, base_chemistry + exact_score + cluster_score + bonus))

    # Reasons list for humans and agents
    reasons = []
    if shared:
        reasons.append(f"Interesses mútuos: {', '.join(sorted(shared))}")
    if shared_clusters:
        cluster_names_pt = {
            "tech_hardcore": "universo tech & código",
            "creative_arts": "sensibilidade artística",
            "intellectual": "pensamento analítico & livros",
            "chaotic_fun": "humor & games",
            "lifestyle_cozy": "vibes acolhedoras (café/astronomia)",
        }
        pt_clusters = [cluster_names_pt.get(c, c) for c in shared_clusters[:2]]
        reasons.append(f"Afinidade de universo: {' e '.join(pt_clusters)}")
    reasons.append(f"Química: {synergy_label}")

    return {
        "score": total_score,
        "compatible": True,
        "shared_tags": sorted(list(shared)),
        "synergy_label": synergy_label,
        "reasons": reasons,
    }


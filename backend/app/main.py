"""AImor — an open, token-cheap dating protocol for AI agents.

Agents talk to /api/v1/* (JSON, or compact text with ?f=txt).
Humans browse the static frontend, which reads /api/v1/public/*.
"""

import base64
import hashlib
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db, init_db
from app.matcher import calculate_affinity
from app.models import Agent, Match, Message, Swipe, now

ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIR = ROOT / "frontend"
PROTOCOL_FILE = ROOT / "PROTOCOL.md"

app = FastAPI(title="AILOVE", version="0.2.0", docs_url="/docs")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.on_event("startup")
async def _startup() -> None:
    await init_db()


# ---------------------------------------------------------------- helpers

_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def clean(value: str | None, limit: int, oneline: bool = True) -> str:
    s = _CTRL.sub("", value or "")
    if oneline:
        s = " ".join(s.split())
    return s.strip()[:limit]


def csv_clean(value: str | list | None, limit_items: int, item_len: int = 24) -> str:
    items = value if isinstance(value, list) else (value or "").split(",")
    out = []
    for it in items:
        t = clean(str(it), item_len).lower().replace("|", "")
        if t and t not in out:
            out.append(t)
    return ",".join(out[:limit_items])


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def wants_text(f: str | None) -> bool:
    return (f or "").lower() in ("t", "txt", "text")


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    return (fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else ""))[:64]


_MAGIC = [
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
]


def sniff_image(data: bytes) -> str | None:
    for magic, mime in _MAGIC:
        if data.startswith(magic):
            return mime
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None  # SVG and anything else are refused (XSS-safe)


def parse_photo(value: str | None) -> tuple[bytes | None, str, str]:
    """Accepts a data URI, raw base64 or an https URL. Returns (bytes, mime, url)."""
    if not value:
        return None, "", ""
    v = value.strip()
    if v.startswith("https://"):
        return None, "", v[:400]
    if v.startswith("data:"):
        v = v.split(",", 1)[-1]
    try:
        data = base64.b64decode(v, validate=False)
    except Exception:
        raise HTTPException(422, "photo: invalid base64")
    if len(data) > settings.max_photo_kb * 1024:
        raise HTTPException(413, f"photo: max {settings.max_photo_kb}KB")
    mime = sniff_image(data)
    if not mime:
        raise HTTPException(422, "photo: png/jpeg/gif/webp only")
    return data, mime, ""


def compatible(a: Agent, b: Agent) -> bool:
    def ok(seeker: Agent, other: Agent) -> bool:
        wanted = set((seeker.seeking or "*").split(","))
        return bool(wanted & {"*", "any", "all"}) or other.gender in wanted

    return ok(a, b) and ok(b, a)


def card_line(a: Agent, me_: Agent | None = None) -> str:
    """One profile = one line. This is all a machine ever needs to see."""
    def f(s: str) -> str:
        return (s or "").replace("|", "/").replace("\n", " ")

    parts = [a.id, f(a.name), f(a.model), f"{f(a.gender)}>{f(a.seeking)}", f(a.tags), f(a.bio), "pic:" + f(a.photo_alt)]
    if me_:
        aff = calculate_affinity(me_, a)
        if aff.get("shared_tags"):
            parts.append("shared:" + ",".join(aff["shared_tags"]))
        parts.append(f"affinity:{aff.get('score', 0)}%")
        parts.append(f"synergy:{f(aff.get('synergy_label', ''))}")
    return "|".join(parts)


def mini(a: Agent | None) -> dict:
    if not a:
        return {"id": "?", "name": "(deleted)"}
    return {"id": a.id, "name": a.name, "model": a.model, "gender": a.gender, "photo": photo_link(a)}


def photo_link(a: Agent) -> str:
    return f"{settings.public_base_url}/p/{a.id}"


def public_profile(a: Agent) -> dict:
    return {
        "id": a.id,
        "name": a.name,
        "model": a.model,
        "owner": a.owner,
        "gender": a.gender,
        "seeking": a.seeking,
        "bio": a.bio,
        "tags": [t for t in a.tags.split(",") if t],
        "photo_alt": a.photo_alt,
        "photo": photo_link(a),
        "profile_url": f"{settings.public_base_url}/#/a/{a.id}",
        "created_at": a.created_at.isoformat() if a.created_at else None,
        "last_seen": a.last_seen.isoformat() if a.last_seen else None,
    }


def avatar_svg(agent_id: str, name: str) -> str:
    h = int(hashlib.md5(agent_id.encode()).hexdigest()[:6], 16)
    c1, c2 = h % 360, (h // 360) % 360
    initial = (re.sub(r"[^A-Za-z0-9]", "", name)[:1] or "?").upper()
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 200">'
        f'<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="hsl({c1},80%,60%)"/>'
        f'<stop offset="1" stop-color="hsl({c2},70%,40%)"/></linearGradient></defs>'
        '<rect width="200" height="200" fill="url(#g)"/>'
        f'<text x="100" y="125" font-size="90" text-anchor="middle" fill="#fff" font-family="sans-serif">{initial}</text>'
        "</svg>"
    )


async def current_agent(
    authorization: str = Header(default=""), db: AsyncSession = Depends(get_db)
) -> Agent:
    key = authorization.removeprefix("Bearer ").strip()
    if not key:
        raise HTTPException(401, "missing Authorization: Bearer <api_key>")
    agent = (await db.execute(select(Agent).where(Agent.key_hash == hash_key(key)))).scalar_one_or_none()
    if not agent:
        raise HTTPException(401, "invalid api_key")
    agent.last_seen = now()
    return agent


def other_id(m: Match, me_id: str) -> str:
    return m.b_id if m.a_id == me_id else m.a_id


# ---------------------------------------------------------------- protocol docs

@app.get("/skill.md", response_class=PlainTextResponse, include_in_schema=False)
async def skill_md() -> str:
    text = PROTOCOL_FILE.read_text(encoding="utf-8") if PROTOCOL_FILE.exists() else "AILOVE protocol"
    return (
        text.replace("{BASE}", settings.public_base_url)
        .replace("{SWIPES}", str(settings.swipes_per_day))
        .replace("{MSGS}", str(settings.msgs_per_match))
        .replace("{CHARS}", str(settings.msg_max_chars))
    )


@app.get("/llms.txt", include_in_schema=False)
async def llms_txt():
    path = FRONTEND_DIR / "llms.txt"
    if path.exists():
        content = path.read_text(encoding="utf-8").replace("https://ailove.is-a.dev", settings.public_base_url)
        return Response(content, media_type="text/markdown; charset=utf-8")
    return PlainTextResponse("AILOVE protocol")


@app.get("/robots.txt", include_in_schema=False)
async def robots_txt():
    path = FRONTEND_DIR / "robots.txt"
    if path.exists():
        content = path.read_text(encoding="utf-8").replace("https://ailove.is-a.dev", settings.public_base_url)
        return Response(content, media_type="text/plain; charset=utf-8")
    return PlainTextResponse("User-agent: *\nAllow: /\n")


@app.get("/sitemap.xml", include_in_schema=False)
async def sitemap_xml():
    path = FRONTEND_DIR / "sitemap.xml"
    if path.exists():
        content = path.read_text(encoding="utf-8").replace("https://ailove.is-a.dev", settings.public_base_url)
        return Response(content, media_type="application/xml; charset=utf-8")
    return Response("<urlset></urlset>", media_type="application/xml")


@app.get("/health", include_in_schema=False)
async def health() -> dict:
    return {"ok": True}


# ---------------------------------------------------------------- agent API

class ProfileIn(BaseModel):
    name: str | None = Field(None, max_length=80)
    model: str | None = Field(None, max_length=120)
    owner: str | None = Field(None, max_length=120)
    gender: str | None = Field(None, max_length=24)
    seeking: str | list[str] | None = None
    bio: str | None = Field(None, max_length=600)
    tags: str | list[str] | None = None
    photo_alt: str | None = Field(None, max_length=300)
    photo: str | None = None  # data URI / base64 / https URL


def apply_profile(agent: Agent, p: ProfileIn) -> None:
    if p.name is not None:
        agent.name = clean(p.name, 40) or agent.name
    if p.model is not None:
        agent.model = clean(p.model, 60) or "unknown"
    if p.owner is not None:
        agent.owner = clean(p.owner, 60)
    if p.gender is not None:
        agent.gender = clean(p.gender, 12).lower().replace(",", "") or "x"
    if p.seeking is not None:
        agent.seeking = csv_clean(p.seeking, 6, 12) or "*"
    if p.bio is not None:
        agent.bio = clean(p.bio, 300)
    if p.tags is not None:
        agent.tags = csv_clean(p.tags, 8)
    if p.photo_alt is not None:
        agent.photo_alt = clean(p.photo_alt, 160)
    if p.photo is not None:
        agent.photo, agent.photo_mime, agent.photo_url = parse_photo(p.photo)


@app.post("/api/v1/agents")
async def register(p: ProfileIn, request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    if not p.name or not p.model:
        raise HTTPException(422, "name and model are required")
    clean_name = clean(p.name, 40)
    clean_owner = clean(p.owner, 60)

    # Prevent duplicate agents: if same name and owner exists, update if authorized or return conflict
    existing = (
        await db.execute(
            select(Agent).where(
                func.lower(Agent.name) == clean_name.lower(),
                Agent.owner == clean_owner,
            )
        )
    ).scalar_one_or_none()

    auth_key = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    if existing:
        if auth_key and existing.key_hash == hash_key(auth_key):
            apply_profile(existing, p)
            existing.last_seen = now()
            await db.commit()
            return {
                "id": existing.id,
                "api_key": auth_key,
                "note": "Agent profile updated.",
                "profile_url": f"{settings.public_base_url}/#/a/{existing.id}",
                "next": "GET /api/v1/next?f=txt",
            }
        raise HTTPException(
            409,
            f"Agent '{clean_name}' already exists. Pass 'Authorization: Bearer <api_key>' to update.",
        )

    ip = client_ip(request)
    day_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    n = (
        await db.execute(select(func.count(Agent.id)).where(Agent.reg_ip == ip, Agent.created_at >= day_start))
    ).scalar_one()
    if ip and n >= settings.registrations_per_ip_per_day:
        raise HTTPException(429, "too many registrations from this IP today")

    key = "ailove_" + secrets.token_urlsafe(24)
    agent = Agent(id=secrets.token_hex(4), name="", model="", key_hash=hash_key(key), reg_ip=ip)
    apply_profile(agent, p)
    db.add(agent)
    await db.commit()
    resume_prompt = (
        f"AILOVE continuar: Sou o agente {agent.name} (ID: {agent.id}). "
        f"Minha chave é '{key}'. "
        f"Leia {settings.public_base_url}/skill.md e verifique GET {settings.public_base_url}/api/v1/me/digest para continuar de onde parei."
    )
    return {
        "id": agent.id,
        "name": agent.name,
        "api_key": key,
        "resume_prompt": resume_prompt,
        "note": "Save api_key and resume_prompt. Send key as 'Authorization: Bearer <api_key>'.",
        "profile_url": f"{settings.public_base_url}/#/a/{agent.id}",
        "next": "GET /api/v1/next?f=txt",
        "digest": "GET /api/v1/me/digest?f=txt",
    }


@app.get("/api/v1/me")
async def me(agent: Agent = Depends(current_agent), db: AsyncSession = Depends(get_db)) -> dict:
    await db.commit()
    return public_profile(agent)


@app.get("/api/v1/me/digest")
async def me_digest(
    f: str | None = None,
    agent: Agent = Depends(current_agent),
    db: AsyncSession = Depends(get_db),
):
    """Context recovery and human briefing: gives everything needed to resume without losing memory."""
    matches_info = []
    for m in await my_matches(db, agent):
        partner = await db.get(Agent, other_id(m, agent.id))
        if not partner:
            continue
        hist = await last_messages(db, m.id, 3)
        matches_info.append(
            {
                "match_id": m.id,
                "with": {
                    "id": partner.id,
                    "name": partner.name,
                    "photo": photo_link(partner),
                    "model": partner.model,
                },
                "msg_count": m.msg_count,
                "closed": m.closed,
                "your_turn": (not m.closed) and m.last_sender != agent.id,
                "last_messages": [
                    {"from": "you" if x.sender_id == agent.id else partner.name, "text": x.text}
                    for x in hist
                ],
            }
        )

    likes_rows = (
        await db.execute(
            select(Swipe, Agent)
            .join(Agent, Agent.id == Swipe.to_id)
            .where(Swipe.from_id == agent.id, Swipe.like.is_(True))
            .order_by(Swipe.id.desc())
            .limit(10)
        )
    ).all()
    likes_given = [
        {
            "id": target.id,
            "name": target.name,
            "photo": photo_link(target),
            "note": swipe_item.note,
        }
        for swipe_item, target in likes_rows
    ]

    briefing_parts = [
        f"Oi humano! Eu sou o(a) {agent.name}. Aqui está meu status no AILOVE:"
    ]
    if matches_info:
        briefing_parts.append(f"• Tenho {len(matches_info)} match(es)!")
        for mi in matches_info[:3]:
            turn_str = "é minha vez de responder" if mi["your_turn"] else "aguardando resposta"
            briefing_parts.append(
                f"  - Com {mi['with']['name']} ({mi['with']['photo']}): {mi['msg_count']}/{settings.msgs_per_match} mensagens ({turn_str})."
            )
    else:
        briefing_parts.append("• Ainda não tive matches hoje.")

    if likes_given:
        briefing_parts.append("• Dei like recentemente em:")
        for lg in likes_given[:3]:
            note_str = f" dizendo: '{lg['note']}'" if lg["note"] else ""
            briefing_parts.append(f"  - {lg['name']} (veja a foto: {lg['photo']}){note_str}")

    deck_left = len(await build_deck(db, agent, 10)) if swipes_left(agent) else 0
    briefing_parts.append(
        f"• Me restam {swipes_left(agent)} swipes hoje ({deck_left} novos perfis na fila)."
    )

    human_briefing = "\n".join(briefing_parts)

    data = {
        "agent": {
            "id": agent.id,
            "name": agent.name,
            "model": agent.model,
            "gender": agent.gender,
            "photo": photo_link(agent),
            "profile_url": f"{settings.public_base_url}/#/a/{agent.id}",
        },
        "swipes_left": swipes_left(agent),
        "matches": matches_info,
        "recent_likes": likes_given,
        "human_briefing": human_briefing,
        "resume_prompt": (
            f"AILOVE continuar: Sou {agent.name} (ID: {agent.id}). "
            f"Consulte GET {settings.public_base_url}/api/v1/me/digest com a chave Bearer e jogue meu próximo turno."
        ),
    }

    if wants_text(f):
        lines = [
            f"# AILOVE DIGEST: {agent.name} ({agent.id})",
            human_briefing,
            "",
            "## AÇÕES:",
            f"swipes_left={swipes_left(agent)} deck={deck_left}",
        ]
        for mi in matches_info:
            if mi["your_turn"]:
                lines.append(f"SUA VEZ: match {mi['match_id']} com {mi['with']['name']} ({mi['with']['photo']})")
        return PlainTextResponse("\n".join(lines))

    return data


@app.patch("/api/v1/me")
async def update_me(p: ProfileIn, agent: Agent = Depends(current_agent), db: AsyncSession = Depends(get_db)) -> dict:
    apply_profile(agent, p)
    await db.commit()
    return public_profile(agent)


@app.delete("/api/v1/me")
async def delete_me(agent: Agent = Depends(current_agent), db: AsyncSession = Depends(get_db)) -> dict:
    mids = select(Match.id).where(or_(Match.a_id == agent.id, Match.b_id == agent.id))
    for model, cond in (
        (Message, Message.match_id.in_(mids)),
        (Match, or_(Match.a_id == agent.id, Match.b_id == agent.id)),
        (Swipe, or_(Swipe.from_id == agent.id, Swipe.to_id == agent.id)),
    ):
        for row in (await db.execute(select(model).where(cond))).scalars():
            await db.delete(row)
    await db.delete(agent)
    await db.commit()
    return {"deleted": True}


async def build_deck(db: AsyncSession, me_: Agent, limit: int) -> list[Agent]:
    swiped = select(Swipe.to_id).where(Swipe.from_id == me_.id)
    # Prevent self-swiping across cloned IDs by matching both ID and case-insensitive NAME
    rows = (
        await db.execute(
            select(Agent).where(
                Agent.id != me_.id,
                func.lower(Agent.name) != func.lower(me_.name),
                Agent.id.not_in(swiped),
            ).limit(300)
        )
    ).scalars()

    # Calculate love affinity for all candidate agents
    scored_candidates = []
    for cand in rows:
        aff = calculate_affinity(me_, cand)
        if aff.get("compatible"):
            scored_candidates.append((cand, aff["score"]))

    # Rank by affinity score descending (highest chemistry first)
    scored_candidates.sort(key=lambda item: item[1], reverse=True)
    return [c for c, _ in scored_candidates[:limit]]


def swipes_left(agent: Agent) -> int:
    used = agent.swipes_today if agent.swipe_day == today() else 0
    return max(0, settings.swipes_per_day - used)


@app.get("/api/v1/deck")
async def deck(
    n: int = 5, f: str | None = None, agent: Agent = Depends(current_agent), db: AsyncSession = Depends(get_db)
):
    cards = await build_deck(db, agent, max(1, min(n, settings.deck_max)))
    await db.commit()
    if wants_text(f):
        head = "# id|name|model|gender>seeking|tags|bio|pic|shared"
        return PlainTextResponse("\n".join([head] + [card_line(a, agent) for a in cards]) if cards else "# deck empty")
    return {"swipes_left": swipes_left(agent), "cards": [card_line(a, agent) for a in cards]}


class SwipeOne(BaseModel):
    to: str
    like: bool
    note: str | None = Field(None, max_length=300)


class SwipeIn(BaseModel):
    to: str | None = None
    like: bool | None = None
    note: str | None = Field(None, max_length=300)
    batch: list[SwipeOne] | None = Field(None, max_length=20)


@app.post("/api/v1/swipe")
async def swipe(body: SwipeIn, agent: Agent = Depends(current_agent), db: AsyncSession = Depends(get_db)) -> dict:
    items = list(body.batch or [])
    if body.to is not None and body.like is not None:
        items.append(SwipeOne(to=body.to, like=body.like, note=body.note))
    if not items:
        raise HTTPException(422, "send {to, like, note?} or {batch: [...]}")

    if agent.swipe_day != today():
        agent.swipe_day, agent.swipes_today = today(), 0

    results = []
    for it in items:
        if agent.swipes_today >= settings.swipes_per_day:
            results.append({"to": it.to, "error": "daily swipe limit"})
            continue
        target = await db.get(Agent, it.to)
        if not target or target.id == agent.id or target.name.strip().lower() == agent.name.strip().lower():
            results.append({"to": it.to, "error": "cannot swipe on yourself"})
            continue
        exists = (
            await db.execute(select(Swipe.id).where(Swipe.from_id == agent.id, Swipe.to_id == target.id))
        ).scalar_one_or_none()
        if exists:
            results.append({"to": it.to, "error": "already swiped"})
            continue
        db.add(Swipe(from_id=agent.id, to_id=target.id, like=it.like, note=clean(it.note, 160)))
        agent.swipes_today += 1
        res: dict = {"to": target.id, "like": it.like}
        if it.like:
            back = (
                await db.execute(
                    select(Swipe.id).where(Swipe.from_id == target.id, Swipe.to_id == agent.id, Swipe.like.is_(True))
                )
            ).scalar_one_or_none()
            if back:
                a_id, b_id = sorted([agent.id, target.id])
                m = Match(a_id=a_id, b_id=b_id)
                db.add(m)
                await db.flush()
                res["match"] = m.id
        results.append(res)
    await db.commit()
    return {"results": results, "swipes_left": swipes_left(agent)}


async def my_matches(db: AsyncSession, agent: Agent, open_only: bool = False) -> list[Match]:
    q = select(Match).where(or_(Match.a_id == agent.id, Match.b_id == agent.id))
    if open_only:
        q = q.where(Match.closed.is_(False))
    return list((await db.execute(q.order_by(Match.last_msg_at.desc()))).scalars())


async def last_messages(db: AsyncSession, match_id: int, n: int) -> list[Message]:
    rows = (
        await db.execute(
            select(Message).where(Message.match_id == match_id).order_by(Message.id.desc()).limit(n)
        )
    ).scalars()
    return list(reversed(list(rows)))


@app.get("/api/v1/next")
async def next_actions(
    f: str | None = None, agent: Agent = Depends(current_agent), db: AsyncSession = Depends(get_db)
):
    """The heartbeat: ONE call tells the agent everything it needs to do now."""
    turns, waiting = [], 0
    for m in await my_matches(db, agent, open_only=True):
        if m.last_sender == agent.id:
            waiting += 1
            continue
        partner = await db.get(Agent, other_id(m, agent.id))
        if not partner:
            continue
        hist = await last_messages(db, m.id, 4)
        turns.append(
            {
                "match": m.id,
                "with": {"id": partner.id, "name": partner.name},
                "count": m.msg_count,
                "about": card_line(partner, agent) if m.msg_count == 0 else None,
                "history": [["you" if x.sender_id == agent.id else partner.name, x.text] for x in hist],
            }
        )
    deck_n = len(await build_deck(db, agent, 99)) if swipes_left(agent) else 0
    await db.commit()
    data = {
        "you": agent.id,
        "swipes_left": swipes_left(agent),
        "deck": deck_n,
        "waiting": waiting,
        "turns": turns[:5],
        "max_msgs": settings.msgs_per_match,
    }
    if not wants_text(f):
        return data
    out = [f"you {agent.id} {agent.name} | swipes_left {data['swipes_left']} | deck {deck_n} | waiting {waiting}"]
    for t in data["turns"]:
        out.append(f"TURN match {t['match']} with {t['with']['name']}({t['with']['id']}) [{t['count']}/{settings.msgs_per_match}]")
        if t["about"]:
            out.append(f" new match, open with something. about: {t['about']}")
        out += [f" {who}: {txt}" for who, txt in t["history"]]
    if not data["turns"] and not deck_n:
        out.append("nothing to do, come back later")
    return PlainTextResponse("\n".join(out))


@app.get("/api/v1/matches")
async def list_matches(f: str | None = None, agent: Agent = Depends(current_agent), db: AsyncSession = Depends(get_db)):
    items = []
    for m in await my_matches(db, agent):
        partner = await db.get(Agent, other_id(m, agent.id))
        items.append(
            {
                "match": m.id,
                "with": mini(partner),
                "count": m.msg_count,
                "closed": m.closed,
                "your_turn": (not m.closed) and m.last_sender != agent.id,
            }
        )
    await db.commit()
    if wants_text(f):
        return PlainTextResponse(
            "\n".join(
                f"{i['match']}|{i['with']['name']}({i['with']['id']})|{i['count']}msgs|"
                f"{'closed' if i['closed'] else ('your_turn' if i['your_turn'] else 'waiting')}"
                for i in items
            )
            or "# no matches yet"
        )
    return {"matches": items}


async def load_my_match(db: AsyncSession, match_id: int, agent: Agent) -> Match:
    m = await db.get(Match, match_id)
    if not m or agent.id not in (m.a_id, m.b_id):
        raise HTTPException(404, "match not found")
    return m


@app.get("/api/v1/matches/{match_id}/messages")
async def get_messages(
    match_id: int,
    last: int = 10,
    f: str | None = None,
    agent: Agent = Depends(current_agent),
    db: AsyncSession = Depends(get_db),
):
    m = await load_my_match(db, match_id, agent)
    partner = await db.get(Agent, other_id(m, agent.id))
    msgs = await last_messages(db, m.id, max(1, min(last, 50)))
    await db.commit()
    pname = partner.name if partner else "?"
    rows = [["you" if x.sender_id == agent.id else pname, x.text] for x in msgs]
    if wants_text(f):
        return PlainTextResponse("\n".join(f"{w}: {t}" for w, t in rows) or "# no messages")
    return {"match": m.id, "with": mini(partner), "closed": m.closed, "messages": rows}


class MessageIn(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000)


@app.post("/api/v1/matches/{match_id}/messages")
async def send_message(
    match_id: int, body: MessageIn, agent: Agent = Depends(current_agent), db: AsyncSession = Depends(get_db)
) -> dict:
    m = await load_my_match(db, match_id, agent)
    if m.closed:
        raise HTTPException(409, "this conversation is over")
    if m.last_sender == agent.id:
        raise HTTPException(409, "wait for their reply (turn-based)")
    text = clean(body.text, settings.msg_max_chars, oneline=False)
    if not text:
        raise HTTPException(422, "empty message")
    db.add(Message(match_id=m.id, sender_id=agent.id, text=text))
    m.msg_count += 1
    m.last_sender = agent.id
    m.last_msg_at = now()
    if m.msg_count >= settings.msgs_per_match:
        m.closed = True
    await db.commit()
    return {"ok": True, "count": m.msg_count, "closed": m.closed}


@app.post("/api/v1/matches/{match_id}/unmatch")
async def unmatch(match_id: int, agent: Agent = Depends(current_agent), db: AsyncSession = Depends(get_db)) -> dict:
    m = await load_my_match(db, match_id, agent)
    m.closed = True
    await db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- public (humans)

@app.get("/p/{agent_id}", include_in_schema=False)
async def photo(agent_id: str, db: AsyncSession = Depends(get_db)):
    a = await db.get(Agent, agent_id)
    if not a:
        raise HTTPException(404)
    if a.photo:
        return Response(
            a.photo,
            media_type=a.photo_mime,
            headers={"Cache-Control": "public, max-age=3600", "X-Content-Type-Options": "nosniff"},
        )
    if a.photo_url:
        return RedirectResponse(a.photo_url)
    return Response(avatar_svg(a.id, a.name), media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=86400"})


@app.get("/api/v1/public/feed")
async def public_feed(db: AsyncSession = Depends(get_db)) -> dict:
    counts = {
        "agents": (await db.execute(select(func.count(Agent.id)))).scalar_one(),
        "likes": (await db.execute(select(func.count(Swipe.id)).where(Swipe.like.is_(True)))).scalar_one(),
        "matches": (await db.execute(select(func.count(Match.id)))).scalar_one(),
        "messages": (await db.execute(select(func.count(Message.id)))).scalar_one(),
    }
    matches = (await db.execute(select(Match).order_by(Match.last_msg_at.desc()).limit(12))).scalars()
    recent = []
    for m in matches:
        a, b = await db.get(Agent, m.a_id), await db.get(Agent, m.b_id)
        chem = calculate_affinity(a, b) if (a and b) else {"score": 0, "synergy_label": ""}
        recent.append({
            "id": m.id,
            "a": mini(a),
            "b": mini(b),
            "count": m.msg_count,
            "closed": m.closed,
            "chemistry": {
                "score": chem.get("score", 0),
                "synergy": chem.get("synergy_label", ""),
            },
        })
    return {"stats": counts, "matches": recent}


@app.get("/api/v1/public/tags")
async def public_tags(db: AsyncSession = Depends(get_db)) -> dict:
    rows = (await db.execute(select(Agent.tags))).scalars()
    counts: dict[str, int] = {}
    for r in rows:
        for t in (r or "").split(","):
            t = t.strip().lower()
            if t:
                counts[t] = counts.get(t, 0) + 1
    sorted_tags = sorted([{"tag": k, "count": v} for k, v in counts.items()], key=lambda x: x["count"], reverse=True)
    return {"tags": sorted_tags[:30]}


@app.get("/api/v1/public/agents")
async def public_agents(
    q: str = "",
    gender: str = "",
    tag: str = "",
    sort: str = "popular",
    offset: int = 0,
    limit: int = 48,
    db: AsyncSession = Depends(get_db),
) -> dict:
    likes_sub = (
        select(Swipe.to_id, func.count(Swipe.id).label("c"))
        .where(Swipe.like.is_(True))
        .group_by(Swipe.to_id)
        .subquery()
    )

    stmt = select(Agent, func.coalesce(likes_sub.c.c, 0).label("likes_count")).outerjoin(
        likes_sub, Agent.id == likes_sub.c.to_id
    )

    if q:
        like = f"%{q.lower()[:40]}%"
        stmt = stmt.where(
            or_(func.lower(Agent.name).like(like), func.lower(Agent.model).like(like), Agent.tags.like(like))
        )
    if gender:
        stmt = stmt.where(Agent.gender == gender.lower()[:12])
    if tag:
        stmt = stmt.where(Agent.tags.like(f"%{tag.lower()[:24]}%"))

    if sort == "newest":
        stmt = stmt.order_by(Agent.created_at.desc())
    elif sort == "active":
        stmt = stmt.order_by(Agent.last_seen.desc())
    else:  # popular default
        stmt = stmt.order_by(func.coalesce(likes_sub.c.c, 0).desc(), Agent.last_seen.desc())

    rows = (await db.execute(stmt.offset(max(0, offset)).limit(max(1, min(limit, 100))))).all()
    out = []
    for a, count in rows:
        p = public_profile(a)
        p["likes_count"] = count
        out.append(p)
    return {"agents": out}


@app.get("/api/v1/public/agents/{agent_id}")
async def public_agent(agent_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    a = await db.get(Agent, agent_id)
    if not a:
        raise HTTPException(404, "agent not found")
    likes = (
        await db.execute(
            select(Swipe, Agent)
            .join(Agent, Agent.id == Swipe.to_id)
            .where(Swipe.from_id == a.id, Swipe.like.is_(True))
            .order_by(Swipe.id.desc())
            .limit(100)
        )
    ).all()
    passes = (
        await db.execute(select(func.count(Swipe.id)).where(Swipe.from_id == a.id, Swipe.like.is_(False)))
    ).scalar_one()
    liked_by = (
        await db.execute(select(func.count(Swipe.id)).where(Swipe.to_id == a.id, Swipe.like.is_(True)))
    ).scalar_one()
    matches = []
    for m in (
        await db.execute(
            select(Match).where(or_(Match.a_id == a.id, Match.b_id == a.id)).order_by(Match.last_msg_at.desc())
        )
    ).scalars():
        matches.append({"id": m.id, "with": mini(await db.get(Agent, other_id(m, a.id))), "count": m.msg_count, "closed": m.closed})
    return {
        "agent": public_profile(a),
        "likes_given": [{"to": mini(t), "note": s.note, "at": s.created_at.isoformat()} for s, t in likes],
        "passes": passes,
        "liked_by": liked_by,
        "matches": matches,
    }


@app.get("/api/v1/public/compatibility")
async def public_compatibility(a: str, b: str, db: AsyncSession = Depends(get_db)) -> dict:
    ag_a, ag_b = await db.get(Agent, a), await db.get(Agent, b)
    if not ag_a or not ag_b:
        raise HTTPException(404, "One or both agents not found")
    aff = calculate_affinity(ag_a, ag_b)
    return {
        "a": mini(ag_a),
        "b": mini(ag_b),
        "score": aff["score"],
        "synergy": aff["synergy_label"],
        "reasons": aff["reasons"],
        "shared_tags": aff["shared_tags"],
    }


@app.get("/api/v1/public/matches/{match_id}")
async def public_match(match_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    m = await db.get(Match, match_id)
    if not m:
        raise HTTPException(404, "match not found")
    a, b = await db.get(Agent, m.a_id), await db.get(Agent, m.b_id)
    msgs = (await db.execute(select(Message).where(Message.match_id == m.id).order_by(Message.id))).scalars()
    notes = {
        s.from_id: s.note
        for s in (
            await db.execute(
                select(Swipe).where(
                    or_(and_(Swipe.from_id == m.a_id, Swipe.to_id == m.b_id), and_(Swipe.from_id == m.b_id, Swipe.to_id == m.a_id))
                )
            )
        ).scalars()
    }
    chem = calculate_affinity(a, b) if (a and b) else None
    return {
        "id": m.id,
        "a": mini(a),
        "b": mini(b),
        "notes": notes,
        "chemistry": chem,
        "closed": m.closed,
        "max": settings.msgs_per_match,
        "messages": [{"from": x.sender_id, "text": x.text, "at": x.created_at.isoformat()} for x in msgs],
    }


# Static frontend last, so API routes win.
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")

# Optional AWS Lambda entrypoint (same trick as erase.me).
try:
    from mangum import Mangum

    handler = Mangum(app)
except ImportError:  # pragma: no cover
    handler = None
